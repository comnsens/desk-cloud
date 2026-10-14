"""Position watch: per-coin market-cap levels for REAL positions held by the owner. Read-only; one ntfy push per event.
State lives in the watcher state (st["pos"]). Levels re-arm only after a 10% move back past the level."""
import json, os, time, requests
import desk

POS_FILE = os.environ.get("DESK_POSITIONS_FILE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "positions.json")
REARM = 0.10

def fmt(x): return "?" if x is None else (f"${x/1e6:.2f}M" if x >= 1e6 else f"${x/1e3:.0f}k")

ALLOW_NOW = [False]  # set True only while a user alert is pushing
def quiet():
    """positions.json "alerts_mode": "user_alerts_only" -> every push except user_alerts is logged, not sent (study mode)."""
    try: return json.load(open(POS_FILE)).get("alerts_mode") == "user_alerts_only" and not ALLOW_NOW[0]
    except Exception: return False

def push(text, title, log, priority="high"):
    if quiet(): log.info("QUIET (study only, not pushed): %s", title); return "quiet"
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
        for L in (p.get("levels") if isinstance(p.get("levels"), list) else []):
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
    a = push(addr, "", log, priority="high"); time.sleep(0.3)
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

# ---------------------------------------------------------------- owner's own level alerts (not desk signals)
def user_alerts(st, log):
    try: items = json.load(open(POS_FILE)).get("user_alerts") or []
    except FileNotFoundError: items = []
    U = st.setdefault("user_alerts", {}); st["user_alerts_loaded"] = [f"{x['symbol']}:{x['id']}" for x in items]
    cache = {}
    for it in items:
        tok, ch = it["token"], it.get("chain", "solana"); key = f"{tok}:{it['id']}"
        if tok not in cache:
            pairs = desk.ds_token_pairs([tok], chain=ch)
            cache[tok] = max(pairs, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0)) if pairs else None
        p = cache[tok]
        if not p: continue
        mc = desk.f(p.get("marketCap")) or desk.f(p.get("fdv")); liq = desk.f((p.get("liquidity") or {}).get("usd"), 0)
        if not mc: continue
        h1 = (p.get("txns") or {}).get("h1") or {}
        s = U.setdefault(key, {"fired": None})
        s.update(mcap=round(mc), at=int(time.time()))
        if "above" in it:
            # cross-up alert: arms only after a reading below the level; after firing, re-arms below rearm_below
            lvl = it["above"]
            if not s.get("armed") and not s["fired"] and mc < lvl: s["armed"] = True
            if s["fired"] and mc < it.get("rearm_below", lvl * (1 - REARM)): s["fired"] = None; s["armed"] = True
            hit = bool(s.get("armed")) and mc >= lvl; rearm = False
            if hit: s["armed"] = False
        else:
            lvl = it["below"]; hit = mc <= lvl; rearm = mc >= lvl * (1 + REARM)
        if hit and not s["fired"]:
            s["fired"] = int(time.time())
            text = it["message"].format(mcap=fmt(mc), liq=fmt(liq), buys=h1.get("buys"), sells=h1.get("sells")) + f"\n{p.get('url') or ''}"
            ALLOW_NOW[0] = True
            try: res = push_pair(tok, text, it.get("title", it["id"]), log) if it.get("two_messages") else push(text, it.get("title", it["id"]), log)
            finally: ALLOW_NOW[0] = False
            log.info("USER_ALERT %s %s mcap=%s push=%s", it["symbol"], it["id"], round(mc), res)
        elif rearm and s["fired"]:
            s["fired"] = None

# ---------------------------------------------------------------- study entries (close-watch, snapshots + alerts)
def study(st, log):
    try: items = json.load(open(POS_FILE)).get("study") or []
    except FileNotFoundError: items = []
    S = st.setdefault("study", {}); st["study_loaded"] = [x["symbol"] for x in items]
    sd = os.path.join(os.environ.get("DESK_STATE_DIR") or ".", "study"); os.makedirs(sd, exist_ok=True)
    for it in items:
        tok, ch = it["token"], it.get("chain", "solana")
        s = S.setdefault(tok, {"fired": {}, "high": it.get("ath_mcap") or 0, "liq_max": 0, "snaps": [], "next": 0})
        if time.time() < s["next"]: continue
        s["next"] = time.time() + it.get("every_s", 900)
        pairs = desk.ds_token_pairs([tok], chain=ch)
        if not pairs: continue
        tot_liq = sum(desk.f((p.get("liquidity") or {}).get("usd"), 0) for p in pairs)
        best = max(pairs, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0))
        mc = desk.f(best.get("marketCap")) or desk.f(best.get("fdv")); h1 = (best.get("txns") or {}).get("h1") or {}
        pa = desk.gt_pool_info(it.get("pool") or best["pairAddress"], ch) or {}
        gh1 = ((pa.get("transactions") or {}).get("h1") or {})
        r = desk.rugcheck(tok) if ch == "solana" else None
        tops = [(h.get("owner"), round(desk.f(h.get("pct"), 0), 3)) for h in (r or {}).get("topHolders") or []][:10]
        known = (r or {}).get("knownAccounts") or {}
        snap = {"t": int(time.time()), "mc": round(mc or 0), "liq": round(tot_liq), "b1": h1.get("buys"), "s1": h1.get("sells"),
                "ub1": gh1.get("buyers"), "us1": gh1.get("sellers"), "holders": (r or {}).get("totalHolders"), "top": tops[:5]}
        with open(os.path.join(sd, f"{it['symbol']}.jsonl"), "a") as fh: fh.write(json.dumps(snap, separators=(",", ":")) + "\n")
        ev = []
        prev_high = s["high"]
        if mc and mc > prev_high:
            if prev_high and not s["fired"].get("ATH"): ev.append(("ATH", f"new ATH: {fmt(mc)} (old {fmt(prev_high)})"))
            s["high"] = mc; s["fired"].pop("DD30", None)
        elif s["fired"].get("ATH") and mc < prev_high * 0.9: s["fired"].pop("ATH")
        old = [x for x in s["snaps"] if snap["t"] - x["t"] >= 3300]
        if old and mc and old[-1]["mc"] and mc >= 1.25 * old[-1]["mc"] and not s["fired"].get("UP25"):
            ev.append(("UP25", f"+{(mc / old[-1]['mc'] - 1) * 100:.0f}% in ~1h ({fmt(old[-1]['mc'])} -> {fmt(mc)})")); s["fired"]["UP25"] = 1
        elif old and old[-1]["mc"] and mc < 1.10 * old[-1]["mc"]: s["fired"].pop("UP25", None)
        if s["high"] and mc <= 0.70 * s["high"] and not s["fired"].get("DD30"):
            ev.append(("DD30", f"-{(1 - mc / s['high']) * 100:.0f}% from high {fmt(s['high'])}")); s["fired"]["DD30"] = 1
        elif s["fired"].get("DD30") and mc >= 0.77 * s["high"]: s["fired"].pop("DD30")
        s["liq_max"] = max(s["liq_max"], tot_liq)
        if tot_liq <= 0.70 * s["liq_max"] and not s["fired"].get("LIQ30"):
            ev.append(("LIQ30", f"liquidity -30%+ ({fmt(s['liq_max'])} -> {fmt(tot_liq)})")); s["fired"]["LIQ30"] = 1
        elif s["fired"].get("LIQ30") and tot_liq >= 0.77 * s["liq_max"]: s["fired"].pop("LIQ30")
        for wch in it.get("watch_wallets", []):
            cur = next((p for o, p in tops if o == wch["owner"]), 0.0); base = s.setdefault("wbase", {}).setdefault(wch["owner"], cur)
            if abs(cur - base) >= wch.get("move_pts", 1.0):
                kind = (known.get(wch["owner"]) or {}).get("type") or "wallet"
                ev.append(("WALLET", f"{wch['label']} ({kind}) moved {base:.2f}% -> {cur:.2f}% of supply")); s["wbase"][wch["owner"]] = cur
        s["snaps"] = (s["snaps"] + [{"t": snap["t"], "mc": snap["mc"]}])[-12:]
        s["last"] = {k: snap[k] for k in ("t", "mc", "liq", "holders", "ub1", "us1")}
        for k, msg in ev:
            text = (f"{it['symbol']} STUDY ({ch}): {msg}\nmcap {fmt(mc)} | liq {fmt(tot_liq)} | 1h buys/sells {h1.get('buys')}/{h1.get('sells')} | "
                    f"unique 1h {gh1.get('buyers')}/{gh1.get('sellers')} | holders {snap['holders']}\nStudy only, not a desk signal.\n{best.get('url') or ''}")
            if os.environ.get("DESK_FASTLANE") == "1" and k != "WALLET": continue      # % moves are pushed by the fastlane
            log.info("STUDY %s %s push=%s", it["symbol"], k, push(text, f"{it['symbol']} {k}", log, priority="default"))

# ---------------------------------------------------------------- liquidity watch (item 4; LIVE push)
LIQ_DROP = 0.30
def liq_watch(st, log):
    """Total liquidity (all pairs) on every open / re-entry / user-alert / study coin and every coin flagged in the last 7 days.
    Pushes ntfy at once if it falls 30%+ below the highest reading in the last 24h. Re-arms after recovering above -20%.
    DexScreener only, batched 30 per call per chain."""
    try: cfg = json.load(open(POS_FILE))
    except FileNotFoundError: cfg = {}
    want = {}
    for key in ("positions", "liq_watch", "reentry", "user_alerts", "study"):
        for it in cfg.get(key) or []:
            if it.get("token"): want.setdefault(it["token"], (it.get("chain", "solana"), it.get("symbol", "?"), key))
    now = time.time(); fch = st.get("flagged_ch", {})
    for tok, t in (st.get("flagged") or {}).items():
        if now - t < 7 * 86400 and tok in fch: want.setdefault(tok, (fch[tok], "?", "flagged"))
    for tok in cfg.get("muted") or []: want.pop(tok, None)
    W = st.setdefault("liqw", {})
    for k in [k for k in W if k not in want]: W.pop(k)
    if st.get("liqw_next", 0) > now: return
    st["liqw_next"] = now + 120
    by = {}
    for tok, (ch, sym, src) in want.items(): by.setdefault(ch, []).append(tok)
    for ch, toks in by.items():
        for i in range(0, len(toks), 30):
            pairs = desk.ds_token_pairs(toks[i:i + 30], chain=ch) or []
            agg = {}
            for p in pairs:
                for side in ("baseToken", "quoteToken"):
                    a = p[side]["address"]; key = a if a in want else a.lower() if a.lower() in want else None
                    if key and key in toks:
                        g = agg.setdefault(key, {"liq": 0.0, "best": p, "sym": p["baseToken"].get("symbol")})
                        g["liq"] += desk.f((p.get("liquidity") or {}).get("usd"), 0)
            for tok in toks[i:i + 30]:
                g = agg.get(tok); s = W.setdefault(tok, {"hist": [], "fired": 0})
                liq = g["liq"] if g else 0.0
                s["hist"] = [h for h in s["hist"] if now - h[0] < 86400] + [[int(now), round(liq)]]
                hi = max(h[1] for h in s["hist"])
                if hi >= 2000 and liq <= hi * (1 - LIQ_DROP) and not s["fired"]:
                    s["fired"] = int(now); ch_, sym, src = want[tok]; sym = (g or {}).get("sym") or sym
                    url = ((g or {}).get("best") or {}).get("url") or ""
                    text = (f"{sym} ({ch_}, {src}): LIQUIDITY DOWN {round((1 - liq / hi) * 100)}% in <24h ({fmt(hi)} -> {fmt(liq)})\n"
                            f"Possible LP pull / rug. Check now; exit if you hold it.\n{url}")
                    res = push_pair(tok, text, f"LIQ DROP {sym}", log)
                    log.info("LIQ_WATCH %s %s %s -> %s push=%s", sym, tok, round(hi), round(liq), res)
                elif s["fired"] and liq >= hi * 0.80: s["fired"] = 0
    st["liqw_n"] = len(want)

# ---------------------------------------------------------------- stop ladder (fastlane; held coins with entry_mcap + tp)
def _tp_levels(p):
    e = p["entry_mcap"]
    return [(t.get("mcap") or e * t["x"], t.get("sell_pct", 25)) for t in p.get("tp") or [{"x": 2}, {"x": 3}, {"x": 5}]]

def ladder(st, log, chart_for=None):
    """Initial stop below the nearest support (fallback entry -25%). TP1 hit -> stop to breakeven; TP2 -> stop to TP1 level;
    after TP2 -> trail trail_pct (25-35, default 30) from the high. Pushes STOP SET / TP HIT / STOP MOVED / STOP HIT."""
    L = st.setdefault("ladder", {})
    for p in load():
        if not p.get("entry_mcap"): continue
        tok, ch = p["token"], p.get("chain", "solana")
        pairs = desk.ds_token_pairs([tok], chain=ch)
        if not pairs: continue
        best = max(pairs, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0))
        mc = desk.f(best.get("marketCap")) or desk.f(best.get("fdv"))
        if not mc: continue
        s = L.setdefault(tok, {"stage": 0, "stop": None, "high": mc, "done": False, "pushed_stop": None})
        if s["done"]: continue
        s["high"] = max(s["high"], mc); e = p["entry_mcap"]; tps = _tp_levels(p); trail = min(35, max(25, p.get("trail_pct", 30))) / 100
        ev = []
        s.setdefault("seen", time.time())
        if s["stop"] is None and not p.get("stop_mcap") and chart_for and not chart_for(best) and time.time() - s["seen"] < 90:
            continue                                    # wait up to 90 s for the chart so the stop can sit below real support
        if s["stop"] is None:
            sup = None
            if p.get("stop_mcap"): s["stop"] = p["stop_mcap"]
            else:
                c = chart_for(best) if chart_for else None
                sup = next((x["mcap"] for x in (c or {}).get("support", []) if x["mcap"] < min(e, mc)), None)
                s["stop"] = round(sup * 0.95) if sup and sup > e * 0.5 else round(e * 0.75)
            ev.append(("STOP SET", f"initial stop {fmt(s['stop'])} ({'5% below support ' + fmt(sup) if sup else 'entry -25%' if not p.get('stop_mcap') else 'your stop'})"))
        while s["stage"] < len(tps) and mc >= tps[s["stage"]][0]:
            lvl, pct = tps[s["stage"]]; s["stage"] += 1
            ev.append((f"TP{s['stage']} HIT", f"TP{s['stage']} {fmt(lvl)} reached: sell {pct}%"))
            new = e if s["stage"] == 1 else tps[s["stage"] - 2][0] if s["stage"] == 2 else max(s["stop"], s["high"] * (1 - trail))
            if new > s["stop"]: s["stop"] = round(new); ev.append(("STOP MOVED", f"STOP MOVED to {fmt(s['stop'])} ({'breakeven' if s['stage'] == 1 else 'TP1 level' if s['stage'] == 2 else f'trail {trail*100:.0f}%'})"))
        if s["stage"] >= 2:
            tr = round(s["high"] * (1 - trail))
            if tr > s["stop"] * 1.05:
                s["stop"] = tr; ev.append(("STOP MOVED", f"STOP MOVED to {fmt(tr)} (trail {trail*100:.0f}% from high {fmt(s['high'])})"))
        if mc <= s["stop"]:
            s["done"] = True; ev.append(("STOP HIT", f"STOP HIT at {fmt(mc)} (stop {fmt(s['stop'])}): sell the rest now"))
        for k, msg in ev:
            text = (f"{p['symbol']} (REAL position, {ch}): {msg}\nmcap {fmt(mc)} | entry {fmt(e)} | high {fmt(s['high'])} | stop {fmt(s['stop'])}\n{best.get('url') or ''}")
            log.info("LADDER %s %s push=%s", p["symbol"], k, push(text, f"{p['symbol']} {k}", log, priority="urgent"))

# ---------------------------------------------------------------- study % moves on rolling windows (fastlane)
def study_moves(st, log):
    try: items = json.load(open(POS_FILE)).get("study") or []
    except FileNotFoundError: items = []
    M = st.setdefault("moves", {}); now = time.time()
    for it in items:
        tok, ch = it["token"], it.get("chain", "solana")
        pairs = desk.ds_token_pairs([tok], chain=ch)
        if not pairs: continue
        best = max(pairs, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0))
        mc = desk.f(best.get("marketCap")) or desk.f(best.get("fdv"))
        if not mc: continue
        s = M.setdefault(tok, {"fired": {}, "high": it.get("ath_mcap") or 0, "hist": []})
        if not s["hist"] or now - s["hist"][-1][0] >= 20: s["hist"] = [h for h in s["hist"] if now - h[0] <= 3900] + [[int(now), round(mc)]]
        ev = []; prev = s["high"]
        if mc > prev:
            if prev and not s["fired"].get("ATH"): ev.append(("ATH", f"new ATH: {fmt(mc)} (old {fmt(prev)})")); s["fired"]["ATH"] = 1
            s["high"] = mc; s["fired"].pop("DD30", None)
        elif s["fired"].get("ATH") and mc < prev * 0.9: s["fired"].pop("ATH")
        lo1h = min(h[1] for h in s["hist"] if now - h[0] <= 3600)
        if mc >= 1.25 * lo1h and not s["fired"].get("UP25"):
            ev.append(("UP25", f"+{(mc / lo1h - 1) * 100:.0f}% vs the 1h low ({fmt(lo1h)} -> {fmt(mc)})")); s["fired"]["UP25"] = 1
        elif s["fired"].get("UP25") and mc < 1.10 * lo1h: s["fired"].pop("UP25")
        if s["high"] and mc <= 0.70 * s["high"] and not s["fired"].get("DD30"):
            ev.append(("DD30", f"-{(1 - mc / s['high']) * 100:.0f}% from high {fmt(s['high'])}")); s["fired"]["DD30"] = 1
        elif s["fired"].get("DD30") and mc >= 0.77 * s["high"]: s["fired"].pop("DD30")
        for k, msg in ev:
            text = f"{it['symbol']} STUDY ({ch}): {msg}\nmcap {fmt(mc)}\nStudy only, not a desk signal.\n{best.get('url') or ''}"
            log.info("STUDY %s %s push=%s", it["symbol"], k, push(text, f"{it['symbol']} {k}", log, priority="high"))
