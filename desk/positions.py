"""Position watch: per-coin market-cap levels for REAL positions held by the owner. Read-only; one ntfy push per event.
State lives in the watcher state (st["pos"]). Levels re-arm only after a 10% move back past the level."""
import json, os, time, requests
import desk

POS_FILE = os.environ.get("DESK_POSITIONS_FILE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "positions.json")
REARM = 0.10

def fmt(x): return "?" if x is None else (f"${x/1e6:.2f}M" if x >= 1e6 else f"${x/1e3:.0f}k")

def push(text, title, log, priority="high"):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic: log.info("position push (no NTFY_TOPIC): %s", title); return "not_configured"
    for a in range(3):
        try:
            r = requests.post(f"https://ntfy.sh/{topic}", data=text.encode(), headers={"Title": title, "Priority": priority}, timeout=15)
            if r.status_code < 300: return "ok"
        except requests.RequestException: pass
        time.sleep(2 * (a + 1))
    return "fail"

def load():
    try: return json.load(open(POS_FILE)).get("positions") or []
    except FileNotFoundError: return []

def check(st, log):
    S = st.setdefault("pos", {})
    pos = load(); st["pos_loaded"] = [p["symbol"] for p in pos]
    for p in pos:
        tok, ch = p["token"], p.get("chain", "solana")
        s = S.setdefault(tok, {"fired": {}, "high": None, "liq_max": None, "rc_at": 0})
        pairs = desk.ds_token_pairs([tok], chain=ch)
        if not pairs: s["last_err"] = "no DexScreener pair"; continue
        best = max(pairs, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0))
        mc = desk.f(best.get("marketCap")) or desk.f(best.get("fdv")); liq = desk.f((best.get("liquidity") or {}).get("usd"), 0)
        if not mc: continue
        s.update(mcap=round(mc), liq=round(liq), at=int(time.time()), url=best.get("url"))
        events = []
        for L in p.get("levels", []):
            lid = L["id"]
            if "below" in L:
                hit, rearm = mc <= L["below"], mc >= L["below"] * (1 + REARM); lvl = L["below"]
            else:
                hit, rearm = mc >= L["above"], mc <= L["above"] * (1 - REARM); lvl = L["above"]
            if hit and not s["fired"].get(lid): s["fired"][lid] = int(time.time()); events.append((lid, f"{lid} ({'<=' if 'below' in L else '>='} {fmt(lvl)})", L["action"]))
            elif rearm and s["fired"].get(lid): s["fired"].pop(lid)
        tr = p.get("trail")
        if tr:
            if mc >= tr["arm_above"] or s.get("high"): s["high"] = max(s.get("high") or 0, mc)
            if s.get("high") and s["high"] >= tr["arm_above"]:
                stop = s["high"] * (1 - tr["drop_pct"] / 100)
                if mc <= stop and not s["fired"].get("TRAIL"):
                    s["fired"]["TRAIL"] = int(time.time()); events.append(("TRAIL", f"TRAIL (-{tr['drop_pct']}% from high {fmt(s['high'])})", tr["action"]))
                elif mc >= stop * (1 + REARM) and s["fired"].get("TRAIL"): s["fired"].pop("TRAIL")
        ld = p.get("liquidity_drop_pct")
        if ld and liq:
            s["liq_max"] = max(s.get("liq_max") or 0, liq)
            if liq <= s["liq_max"] * (1 - ld / 100) and not s["fired"].get("LIQ"):
                s["fired"]["LIQ"] = int(time.time()); events.append(("LIQ", f"LIQUIDITY down {ld}%+ ({fmt(s['liq_max'])} -> {fmt(liq)})", "Exit risk: sell everything now"))
            elif liq >= s["liq_max"] * (1 - ld / 100) * (1 + REARM) and s["fired"].get("LIQ"): s["fired"].pop("LIQ")
        if p.get("rugcheck") and ch == "solana" and time.time() - s.get("rc_at", 0) > 900:
            s["rc_at"] = time.time(); r = desk.rugcheck(tok)
            if r:
                bad = [x.get("name") for x in r.get("risks") or [] if x.get("level") == "danger"]
                if r.get("rugged") or r.get("mintAuthority") or r.get("freezeAuthority"): bad.append("rugged/authority live")
                if bad and not s["fired"].get("RUG"):
                    s["fired"]["RUG"] = int(time.time()); events.append(("RUG", "RUGCHECK: " + ", ".join(bad)[:120], "Sell everything now"))
                elif not bad: s["fired"].pop("RUG", None)
        for lid, lvl, action in events:
            text = f"{p['symbol']} (REAL position, {ch})\nLevel hit: {lvl}\nMarket cap now: {fmt(mc)} | Liquidity: {fmt(liq)}\nSuggested: {action}\nCost basis: {fmt(p.get('cost_basis_mcap'))}\n{s.get('url') or ''}"
            res = push(text, f"{p['symbol']} {lid}", log)
            log.info("POSITION %s %s mcap=%s push=%s", p["symbol"], lid, round(mc), res)

# ---------------------------------------------------------------- re-entry watch (coins the owner exited)
def load_reentry():
    try: return json.load(open(POS_FILE)).get("reentry") or []
    except FileNotFoundError: return []

def plan(floor, peak):
    """Entry zone, stop and targets per paper-limits.md buy-back rules."""
    return {"entry_zone": f"{fmt(floor)} - {fmt(floor * 1.15)} mcap", "stop": f"{fmt(floor * 0.85)} mcap (15% below floor)",
            "targets": f"25% at 2x ({fmt(floor * 2)}), 25% at 3x ({fmt(floor * 3)}), rest trails -35% from high, all out near old peak ({fmt(peak)})"}

def push_pair(addr, text, title, log):
    a = push(addr, "", log, priority="high"); time.sleep(1)
    return a + "/" + push(text, title, log, priority="high")

def reentry_check(st, log, watcher):
    R = st.setdefault("reentry", {}); items = load_reentry(); st["reentry_loaded"] = [x["symbol"] for x in items]
    L = st.setdefault("long", {})
    for it in items:
        tok, ch = it["token"], it.get("chain", "solana")
        s = R.setdefault(tok, {"fired": {}, "next": 0})
        e = L.get(tok)
        if not e:
            pairs = desk.ds_token_pairs([tok], chain=ch)
            if not pairs: continue
            best = max(pairs, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0))
            L[tok] = e = {"chain": ch, "pair": best["pairAddress"], "sym": it["symbol"], "name": it["symbol"], "url": best.get("url"),
                          "added": time.time(), "next_daily": 0, "next_check": 0, "holders": [], "state": "tracking"}
        e["pinned"] = True; e["added"] = max(e["added"], time.time() - 86400)     # pinned: never ages out
        if time.time() < s["next"]: continue
        s["next"] = time.time() + 3600                                           # hourly
        c = desk.gt_ohlcv(e["pair"], ch, tf="day", agg=1, limit=60, ttl=3 * 3600)
        if not c or len(c) < 4: continue
        peak = max(float(x[2]) for x in c); done = c[:-1]
        floor_days = 0; floor = None
        for x in reversed(done):                      # days since the last new low
            lo = float(x[3])
            if floor is None or lo >= floor * 0.97: floor = lo if floor is None else min(floor, lo); floor_days += 1
            else: break
        pairs = desk.ds_token_pairs([tok], chain=ch)
        best = max(pairs, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0)) if pairs else {}
        price = desk.f(best.get("priceUsd")); mc = desk.f(best.get("marketCap")); liq = desk.f((best.get("liquidity") or {}).get("usd"), 0)
        if not (price and mc and floor): continue
        ratio = mc / price; dd = 1 - price / peak
        s.update(peak_mcap=round(peak * ratio), floor_mcap=round(floor * ratio), floor_days=floor_days, dd=round(dd, 3), mcap=round(mc), at=int(time.time()))
        if not (0.50 <= dd <= 0.90 and floor_days >= 3 and liq >= 50_000): s["why"] = "buy-back conditions not met"; continue
        a = desk.sol_audit(tok, price) if ch == "solana" else None
        if a and a["verdict"] != "PASS": s["why"] = "audit " + a["verdict"] + ":" + ",".join(a["because"]); continue
        r = desk.rugcheck(tok) if ch == "solana" else None
        known = (r or {}).get("knownAccounts") or {}
        pools = {m.get("pubkey") for m in (r or {}).get("markets") or []} | {k for k, v in known.items() if (v or {}).get("type") in ("AMM", "LOCKER")}
        bigs = [h for h in (r or {}).get("topHolders") or [] if h.get("owner") not in pools and desk.f(h.get("pct"), 0) >= 1.0]
        w = desk.sol_wallets(tok) if ch == "solana" else {}
        smart = (w.get("birdeye") or {}).get("smart_money")
        if not (len(bigs) >= 3 and smart != "SELLING"):
            s["why"] = f"big wallets not clearly in ({len(bigs)} holders >=1%, smart={smart})"; continue
        key = f"BUYBACK_{int(s['floor_mcap'])}"
        if s["fired"].get(key): continue
        s["fired"][key] = int(time.time()); pl = plan(s["floor_mcap"], s["peak_mcap"])
        text = (f"{it['symbol']} | {ch.upper()} | RE-ENTRY: buy-back setup (paper-limits.md)\n{tok}\n"
                f"Peak {fmt(s['peak_mcap'])} -> now {fmt(mc)} (down {dd*100:.0f}%), floor {fmt(s['floor_mcap'])} held {floor_days} days\n"
                f"Liquidity {fmt(liq)} | RugCheck + Jupiter sell PASS | {len(bigs)} wallets hold >=1% | smart money {smart}\n"
                f"Entry zone: {pl['entry_zone']}\nStop: {pl['stop']}\nTargets: {pl['targets']}\nSize: 5% of pot (rules)\n{best.get('url') or ''}")
        log.info("REENTRY %s buyback push=%s", it["symbol"], push_pair(tok, text, f"{it['symbol']} re-entry setup", log))
