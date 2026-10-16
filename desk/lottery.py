"""LOTTERY paper sleeve (Craig approved 2026-10-15). Ultra-early coins ($5k-$100k mcap, bonding curve allowed) that pass
ONLY the hard safety checks (sellable / not honeypot, mint+freeze revoked where applicable, no transfer-tax trap, no rug flag).
1% of pot per trade, max 10/day, open lottery cost <= 10% of pot, stop -60%, TPs 3x/10x/50x/100x (25% each).
Official paper BUY lines go to paper-fills with sleeve "LOTTERY"; kept out of the main book, main caps and the 30-trade freeze."""
import re, time
import desk

MIN_MC, MAX_MC = 5_000, 100_000
SIZE_PCT, MAX_PER_DAY, BUDGET_PCT = 0.01, 10, 0.10
AUDITS_PER_CYCLE = 3
HARD = re.compile(r"mint|freeze|fee|tax|sell|route|honeypot|rug|blacklist|pause|owner_change|proxy", re.I)

def hard_safety(tok, ch):
    try:
        if ch == desk.SOL: a = desk.sol_audit(tok)
        else:
            import evm; a = evm.audit_basic(tok, ch)       # GoPlus + honeypot hard checks only (sellable, tax, mint, owner)
            return "PASS" if a.get("verdict") == "PASS" else "FAIL:" + a.get("verdict", "?") + ":" + ",".join(map(str, a.get("because") or []))
    except Exception as e: return "FAIL:audit_error:" + type(e).__name__
    bad = [c["check"] for c in a.get("checks") or [] if c.get("status") in ("REJECT", "UNKNOWN") and HARD.search(c.get("check", ""))]
    if not a.get("checks"): return "FAIL:no_audit:" + ",".join(a.get("because") or [])
    return "FAIL:" + ",".join(bad) if bad else "PASS"

def gates(pot, cash):
    import paperstops
    from watcher import PT, now
    today = now().astimezone(PT).strftime("%Y-%m-%d")
    L = {i: b for i, b in paperstops.BUYS.items() if b.get("sleeve") == "LOTTERY"}
    n_today = sum(1 for b in L.values() if b.get("opened_pt", "").startswith(today))
    open_cost = sum(b["size_usd"] for i, b in L.items() if "STOP" not in paperstops.EVENTS.get(i, set())
                    and sum(1 for e in paperstops.EVENTS.get(i, set()) if e.startswith("TP")) < 4)
    size = round((pot or 0) * SIZE_PCT, 2)
    why = []
    if n_today >= MAX_PER_DAY: why.append(f"{MAX_PER_DAY} lottery buys today")
    if open_cost + size > BUDGET_PCT * (pot or 0): why.append("lottery budget 10% of pot used")
    return {"ok": not why, "reasons": why, "size": size, "open_tokens": [b["token"] for b in L.values()]}

def step(st, cands, main_gates, log):
    """cands = watch entries (tok -> w) refreshed by prefilter this cycle."""
    import paperstops, watcher, shadow
    if not main_gates.get("book"): return
    pot = main_gates["book"].get("pot"); done = 0
    pause = [r for r in main_gates.get("reasons", []) if "circuit breaker" in r]     # daily/weekly loss pause applies
    for tok, w in cands:
        if done >= AUDITS_PER_CYCLE: break
        mc = w.get("mcap") or 0
        if not (MIN_MC <= mc <= MAX_MC) or w.get("lot_seen"): continue
        w["lot_seen"] = time.time(); done += 1
        g = gates(pot, None)
        rc = {"sleeve": "LOTTERY", "mcap_5k_100k": "PASS", "hard_safety": hard_safety(tok, w.get("chain", "base"))}
        dec = "SKIP"
        if rc["hard_safety"] != "PASS": why = "lottery_safety:" + rc["hard_safety"]
        elif tok in g["open_tokens"]: why = "lottery_already_bought"
        elif pause or not g["ok"]: why = "lottery_blocked:" + ";".join(pause + g["reasons"]); dec = "ENTER_SHADOW"
        else:
            why = None; dec = "ENTER"
            t = watcher.now(); liq = w.get("liq") or 0; slip = 0.05 if liq < 100_000 else 0.03
            day = t.astimezone(watcher.PT).strftime("%Y%m%d")
            n = 1 + sum(1 for i in paperstops.BUYS if i.startswith(f"LOT-{day}-"))
            b = {"id": f"LOT-{day}-{n:02d}", "event": "BUY", "sleeve": "LOTTERY", "tag": "LOTTERY", "source": "cloud watcher 24/7",
                 "symbol": w.get("sym"), "token": tok, "chain": w.get("chain", "base"), "pair": w.get("pair"), "link": w.get("url"),
                 "flag_mcap": mc, "entry_mcap": round(mc * (1 + slip)), "slippage": slip, "gas_usd": 0.5, "size_usd": g["size"],
                 "position_value_at_entry_usd": round(g["size"] - 0.5, 2), "remaining_pct": 100, "stop_pct": 0.60,
                 "tp_multiples": [3, 10, 50, 100], "rule_checks": rc, "time_utc": watcher.stamp(t)["time_utc"],
                 "opened_pt": watcher.stamp(t)["time_pt"]}
            if paperstops.append_fill(b): paperstops.BUYS[b["id"]] = b; log.info("LOTTERY PAPER BUY %s %s mc=%s $%s", b["id"], b["symbol"], mc, b["size_usd"])
            else: dec, why = "SKIP", "lottery_write_failed"
        w2 = dict(w, why=why or "", rule_checks=rc)
        try: shadow.record(st, tok, w2, None if dec != "ENTER" else {"lottery": 1})
        except Exception: pass
