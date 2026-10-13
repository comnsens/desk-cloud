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
