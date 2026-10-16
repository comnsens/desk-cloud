"""Shadow "copy top traders" practice book (LOG-ONLY, user-approved 2026-10-15).
Separate from paper-book.md / paper rules: never pushes ntfy, never trades, never touches book.md.
- top ~8 wallets ranked by FULL realized-history scorecard (smart.scorecard_one): proven first, then realized pnl,
  win rate, trades. Wallets with no realized history only fill empty slots (tagged "thin").
- each new buy by a top wallet (Solana / Base) -> safety check (Solana RugCheck sol_audit, Base GoPlus+honeypot audit_basic)
  -> COPY (verdict PASS) or SKIP (anything else, reason logged).
- COPY opens a hypothetical position: 5% of copy cash, slippage 5% (liq < $100k) else 3%, $0.50 gas each side.
- sells are mirrored: when the wallet's balance of the token drops >=50% (or to 0) we close at the live price.
State: $DESK_STATE_DIR/copybook.json (state branch). Weekly study reads it."""
import json, os, time
import desk

START_CASH = 1000.0; SIZE_PCT = 0.05; GAS = 0.50; TOP_N = 8
CHAINS = ("solana", "base")
F = os.path.join(os.environ.get("DESK_STATE_DIR") or os.path.dirname(os.path.abspath(__file__)), "copybook.json")

def load():
    try: return json.load(open(F))
    except Exception: return {"start_cash": START_CASH, "cash": START_CASH, "open": {}, "closed": [], "events": [], "seen": {}, "top": [], "cyc": 0}

def save(b):
    b["events"] = b["events"][-400:]; b["closed"] = b["closed"][-500:]
    json.dump(b, open(F + ".tmp", "w"), indent=1); os.replace(F + ".tmp", F)

def top_wallets(W):
    import smart
    rows = []
    for w, e in W.items():
        if e.get("chain") not in CHAINS: continue
        sc = e.get("scorecard") or smart.scorecard_one(e)
        if "dumper" in (sc.get("label") or ""): continue
        if sc.get("pnl_usd") is not None and sc["pnl_usd"] <= 0: continue
        rows.append({"wallet": w, "chain": e["chain"], "proven": bool(sc.get("proven")), "pnl": sc.get("pnl_usd"),
                     "wr": sc.get("win_rate"), "trades": sc.get("trades") or 0, "runners": sc.get("runners") or 0,
                     "thin": sc.get("pnl_usd") is None})
    rows.sort(key=lambda r: (-r["proven"], r["thin"], -(r["pnl"] or 0), -(r["wr"] or 0), -r["trades"], -r["runners"]))
    return rows[:TOP_N]

def quote(tok, ch):
    ps = desk.ds_token_pairs([tok], chain=ch)
    if not ps: return None
    p = max(ps, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0))
    return {"px": desk.f(p.get("priceUsd"), 0), "mc": desk.f(p.get("marketCap") or p.get("fdv"), 0),
            "liq": desk.f((p.get("liquidity") or {}).get("usd"), 0), "sym": (p.get("baseToken") or {}).get("symbol")}

def safety(tok, ch):
    try:
        if ch == "solana":
            v = desk.sol_audit(tok); bad = [c["check"] for c in v.get("checks", []) if c.get("status") == "REJECT"]
            return v.get("verdict"), (bad or v.get("because") or [])[:4]
        import evm; v = evm.audit_basic(tok, ch)
        return v.get("verdict"), (v.get("reject") or v.get("because") or v.get("unknown") or [])[:4]
    except Exception as e: return "UNKNOWN", [f"{type(e).__name__}"]

def balance(w, tok, ch):
    if ch == "solana":
        import smart
        r = smart.srpc("getTokenAccountsByOwner", [w, {"mint": tok}, {"encoding": "jsonParsed"}])
        return sum(desk.f(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"], 0) for a in (r or {}).get("value", []))
    import evm
    out = evm.rpc(ch, "eth_call", [{"to": tok, "data": "0x70a08231" + "0" * 24 + w[2:].lower()}, "latest"])
    return int(out or "0x0", 16)

def sol_new_buys(w, b, now):
    import smart
    sigs = smart.srpc("getSignaturesForAddress", [w, {"limit": 4}]); out = []
    first = w in b.setdefault("init", {}); b["init"][w] = int(now)
    for s in sigs:
        if s.get("err") or s["signature"] in b["seen"]: continue
        b["seen"][s["signature"]] = int(now)
        if not first: continue                     # first look at a wallet: baseline only, don't copy old buys
        tx = smart.srpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        for bb in ((tx or {}).get("meta") or {}).get("postTokenBalances") or []:
            if bb.get("owner") == w and bb["mint"] not in smart.SKIP_MINTS and smart._deltas(tx, bb["mint"]).get(w, 0) > 0:
                out.append(bb["mint"])
    return set(out)

def step(st, log, every=3):
    b = load(); b["cyc"] = b.get("cyc", 0) + 1
    if b["cyc"] % every: save(b); return
    import smart
    W = (smart.load().get("wallets") or {}); now = time.time()
    top = top_wallets(W); b["top"] = top; b["top_at"] = int(now)
    S = (st.get("smart") or {}).get("buys") or {}
    cands = []
    for t in top:
        w, ch = t["wallet"], t["chain"]
        try:
            if ch == "solana": toks = sol_new_buys(w, b, now)
            else: toks = {tok for tok, ws in S.items() if w in ws and now - ws[w] < 900}
        except Exception as e: log.info("copybook buys %s: %s", w[:6], str(e)[:80]); continue
        cands += [(w, ch, tok) for tok in toks]
    for w, ch, tok in cands:
        key = f"{w}:{tok}"
        if key in b["open"] or key in b["seen"]: continue
        b["seen"][key] = int(now)
        q = quote(tok, ch)
        verdict, why = safety(tok, ch)
        tag = "COPY" if verdict == "PASS" and q and q["px"] > 0 else "SKIP"
        if tag == "COPY" and b["cash"] < 5: tag, why = "SKIP", ["no_cash"]
        ev = {"t": int(now), "side": "buy", "wallet": w, "ch": ch, "tok": tok, "sym": (q or {}).get("sym"), "mc": (q or {}).get("mc"),
              "liq": (q or {}).get("liq"), "tag": tag, "verdict": verdict, "why": why if tag == "SKIP" else []}
        b["events"].append(ev)
        if tag == "COPY":
            size = round(b["cash"] * SIZE_PCT, 2); slip = 0.05 if q["liq"] < 100_000 else 0.03
            try: bal0 = balance(w, tok, ch)
            except Exception: bal0 = None
            b["cash"] = round(b["cash"] - size, 2)
            b["open"][key] = {"wallet": w, "ch": ch, "tok": tok, "sym": q["sym"], "opened": int(now), "entry_px": q["px"], "entry_mc": q["mc"],
                              "size": size, "value_in": round(size * (1 - slip) - GAS, 4), "slip": slip, "wbal0": bal0}
        log.info("COPYBOOK %s buy %s %s by %s (log-only)", tag, ch, tok[:8], w[:6])
    for key, p in list(b["open"].items()):          # mirror sells
        try: bal = balance(p["wallet"], p["tok"], p["ch"])
        except Exception: continue
        if p.get("wbal0") is None: p["wbal0"] = bal; continue
        if p["wbal0"] and bal > 0.5 * p["wbal0"]: continue
        q = quote(p["tok"], p["ch"]) or {"px": 0, "mc": 0, "liq": 0}
        slip = 0.05 if q["liq"] < 100_000 else 0.03
        val = p["value_in"] * (q["px"] / p["entry_px"]) if p["entry_px"] else 0
        net = max(0.0, val * (1 - slip) - GAS); pnl = round(net - p["size"], 2)
        b["cash"] = round(b["cash"] + net, 2)
        b["closed"].append({**p, "closed": int(now), "exit_px": q["px"], "exit_mc": q["mc"], "net": round(net, 2), "pnl": pnl,
                            "pnl_pct": round(pnl / p["size"] * 100, 1) if p["size"] else None})
        b["events"].append({"t": int(now), "side": "sell", "wallet": p["wallet"], "ch": p["ch"], "tok": p["tok"], "sym": p["sym"], "pnl": pnl})
        del b["open"][key]; log.info("COPYBOOK mirror sell %s pnl=%s (log-only)", p["tok"][:8], pnl)
    b["seen"] = {k: v for k, v in b["seen"].items() if now - v < 3 * 86400}
    save(b)

def summary(b):
    c = b.get("closed") or []; wins = sum(1 for x in c if x["pnl"] > 0)
    ev = b.get("events") or []
    return {"closed": len(c), "open": len(b.get("open") or {}), "wins": wins, "win_rate": round(wins / len(c), 2) if c else None,
            "pnl": round(sum(x["pnl"] for x in c), 2), "cash": b.get("cash"), "start": b.get("start_cash", START_CASH),
            "copy": sum(1 for e in ev if e.get("tag") == "COPY"), "skip": sum(1 for e in ev if e.get("tag") == "SKIP")}
