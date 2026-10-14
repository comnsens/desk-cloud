#!/usr/bin/env python3
"""Fastlane: every ~2.5 s, one batched DexScreener call per chain (30 tokens/call) for every coin in positions.json
(positions, liq_watch, user_alerts, re-entry, study) plus every coin flagged in the last 48h. Runs the level / stop / trail /
liquidity-drop / user alerts on that data and pushes ntfy at once (priority urgent). Zero AI, no keys besides NTFY_TOPIC.
Hand-off between overlapping runs: the outgoing run posts its alert state to a private control topic (<topic>-ctl, not
subscribed on the phone); the incoming run waits for the outgoing run's end time, then loads that state, so nothing double-fires."""
import json, logging, os, sys, time, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desk, positions, chart

log = logging.getLogger("fastlane"); logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
TICK = float(os.environ.get("FAST_TICK_S", "2.5"))
RUN_S = float(os.environ.get("FAST_RUN_S", "330"))
PREV_END = float(os.environ.get("FAST_PREV_END") or 0)
TOPIC = os.environ.get("NTFY_TOPIC")
CTL = (TOPIC + "-ctl") if TOPIC else None
FLAGS_URL = os.environ.get("FAST_FLAGS_URL", "https://raw.githubusercontent.com/comnsens/desk-cloud/state/flags.jsonl")
DS = "https://api.dexscreener.com/tokens/v1"
GT = {"solana": "solana", "base": "base", "bsc": "bsc", "ethereum": "eth"}
S = requests.Session(); S.headers["User-Agent"] = "desk-fastlane/1"
TICK_CACHE = {}
STATS = {"ticks": 0, "ds_calls": 0, "gt_calls": 0, "errors": 0, "pushes": 0, "max_tick_s": 0.0}

def fetch(ch, toks):
    out = []
    for i in range(0, len(toks), 30):
        chunk = toks[i:i + 30]
        try:
            STATS["ds_calls"] += 1
            r = S.get(f"{DS}/{ch}/{','.join(chunk)}?_={int(time.time() * 1000)}", timeout=6)   # cache-buster: the plain URL is edge-cached ~30 s
            if r.status_code == 200: out += [p for p in r.json() if p.get("chainId") == ch]; continue
        except Exception: pass
        STATS["errors"] += 1
        for t in chunk[:5]:                                              # GeckoTerminal fallback (slow, limited)
            try:
                STATS["gt_calls"] += 1
                d = S.get(f"https://api.geckoterminal.com/api/v2/networks/{GT[ch]}/tokens/{t}/pools?page=1", timeout=6).json()
                for p in (d.get("data") or [])[:3]:
                    a = p["attributes"]
                    out.append({"chainId": ch, "pairAddress": a["address"], "baseToken": {"address": t, "symbol": a["name"].split(" /")[0]},
                                "quoteToken": {"address": ""}, "marketCap": a.get("market_cap_usd") or a.get("fdv_usd"), "fdv": a.get("fdv_usd"),
                                "liquidity": {"usd": a.get("reserve_in_usd")}, "priceUsd": a.get("base_token_price_usd"),
                                "url": f"https://www.geckoterminal.com/{GT[ch]}/pools/{a['address']}", "_src": "gt"})
            except Exception: STATS["errors"] += 1
    return out

def cached_pairs(tokens, chain=None):
    chain = chain or desk.CHAIN; got = []
    for t in tokens:
        ps = TICK_CACHE.get((chain, t))
        if ps is None:                                                   # not in this tick's batch: fetch now
            ps = fetch(chain, [t]); TICK_CACHE[(chain, t)] = ps
        got += ps
    return got
desk.ds_token_pairs = cached_pairs

def watch_set(st):
    try: cfg = json.load(open(positions.POS_FILE))
    except FileNotFoundError: cfg = {}
    w = {}
    for key in ("positions", "liq_watch", "user_alerts", "reentry", "study"):
        for it in cfg.get(key) or []:
            if it.get("token"): w[(it.get("chain", "solana"), it["token"])] = 1
    for tok, ch in st.get("flagged_ch", {}).items():
        if time.time() - st["flagged"].get(tok, 0) < 48 * 3600: w[(ch, tok)] = 1
    muted = set(cfg.get("muted") or [])  # tokens the user said to stop watching: no pushes at all
    return {k: v for k, v in w.items() if k[1] not in muted}

def load_flags(st):
    try:
        r = S.get(FLAGS_URL, timeout=8)
        for line in r.text.splitlines()[-300:]:
            try: x = json.loads(line)
            except Exception: continue
            try: t = time.mktime(time.strptime(x["time_utc"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
            except Exception: continue
            if x.get("token") and x.get("chain") and time.time() - t < 48 * 3600:
                st.setdefault("flagged", {})[x["token"]] = t; st.setdefault("flagged_ch", {})[x["token"]] = x["chain"]
    except Exception: STATS["errors"] += 1

KEEP = ("pos", "user_alerts", "liqw", "moves", "ladder", "chart")
def compact(st):
    lw = {k: {"hist": v["hist"][-4:], "fired": v["fired"]} for k, v in st.get("liqw", {}).items()}
    return {"pos": {k: {x: v.get(x) for x in ("fired", "high", "liq_max")} for k, v in st.get("pos", {}).items()},
            "user_alerts": st.get("user_alerts", {}), "liqw": lw, "end": time.time()}

def handoff_load():
    if not CTL: return {}
    if PREV_END:
        while time.time() < PREV_END + 2: time.sleep(0.5)
    for _ in range(4):
        try:
            r = S.get(f"https://ntfy.sh/{CTL}/json?poll=1&since=12h", timeout=10)
            msgs = [json.loads(l) for l in r.text.splitlines() if l.strip()]
            msgs = [m for m in msgs if m.get("event") == "message" and m.get("attachment")]
            if msgs:
                d = S.get(msgs[-1]["attachment"]["url"], timeout=10).json()
                if not PREV_END or d.get("end", 0) >= PREV_END - 5 or time.time() - d.get("end", 0) > 60: return d
        except Exception: pass
        time.sleep(2)
    return {}

def handoff_save(st):
    if not CTL: return
    body = json.dumps({**{k: st.get(k) or {} for k in KEEP}, "end": time.time()}, separators=(",", ":"))
    try: S.put(f"https://ntfy.sh/{CTL}", data=body.encode(), headers={"Filename": "state.json"}, timeout=15)
    except Exception: pass

_orig_push = positions.push
def urgent_push(text, title, log_, priority="urgent"):
    STATS["pushes"] += 1
    t0 = time.time(); topic = TOPIC
    low = text.lower()
    for key, c in CHARTS.items():
        if key and key in low and "Chart:" not in text: text += "\n" + chart.line(c); break
    if not topic: log.info("push (no NTFY_TOPIC): %s", title); return "not_configured"
    for a in range(3):
        try:
            r = S.post(f"https://ntfy.sh/{topic}", data=text.encode(), timeout=8,
                       headers={"Title": title, "Priority": "urgent", "Tags": "rotating_light"})
            if r.status_code < 300:
                log.info("PUSH %s %.2fs after tick start=%.2fs", title, time.time() - t0, time.time() - TICK_T[0]); return "ok"
        except requests.RequestException: pass
        time.sleep(1)
    return "fail"
positions.push = urgent_push
TICK_T = [time.time()]
CHARTS = {}                                    # pair address (lower) -> chart read (refreshed in the background, 1 coin / 15 s)
def chart_for(pair):
    k = (pair.get("pairAddress") or "").lower(); c = CHARTS.get(k)
    if c and time.time() - c.get("t", 0) < 600: return c
    try:
        STATS["gt_calls"] += 2
        c = chart.read(pair["pairAddress"], pair.get("chainId"), mcap=desk.f(pair.get("marketCap")) or desk.f(pair.get("fdv")), price=desk.f(pair.get("priceUsd")))
        if not c.get("error"): CHARTS[k] = c
    except Exception: STATS["errors"] += 1
    return CHARTS.get(k)

CH_AT = [0]
def chart_loop():
    """Background thread: refresh the stalest chart every 15 s (2 GT calls), so ticks never wait on GeckoTerminal."""
    import threading
    def run():
        while True:
            time.sleep(15)
            try:
                bests = [max(ps, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0)) for ps in list(TICK_CACHE.values()) if ps]
                bests.sort(key=lambda b: CHARTS.get((b.get("pairAddress") or "").lower(), {}).get("t", 0))
                if bests and time.time() - CHARTS.get((bests[0].get("pairAddress") or "").lower(), {}).get("t", 0) > 300: chart_for(bests[0])
            except Exception: STATS["errors"] += 1
    threading.Thread(target=run, daemon=True).start()

_POS_AT = [0.0]
def refresh_positions(every=20):
    """Pull positions.json from main every ~20s so new coins/levels apply without a restart. Never raises."""
    if time.time() - _POS_AT[0] < every: return
    _POS_AT[0] = time.time()
    try:
        r = S.get(f"https://raw.githubusercontent.com/comnsens/desk-cloud/main/positions.json?t={int(time.time())}", timeout=4)
        if r.ok:
            json.loads(r.text)
            tmp = positions.POS_FILE + ".tmp"; open(tmp, "w").write(r.text); os.replace(tmp, positions.POS_FILE)
    except Exception as e: log.warning("refresh_positions: %s", type(e).__name__)

def chart_cached(pair): return CHARTS.get((pair.get("pairAddress") or "").lower())

def main():
    st = {"flagged": {}, "flagged_ch": {}}
    global CHARTS
    prev = handoff_load()
    if not prev:                                                         # cold start: take fired/armed state from the watcher's saved state
        try:
            ws = S.get(FLAGS_URL.replace("flags.jsonl", "watcher_state.json"), timeout=10).json()
            prev = {k: ws.get(k) or {} for k in ("pos", "user_alerts", "liqw")}
        except Exception: prev = {}
    st.update({k: v for k, v in prev.items() if k in KEEP and v}); CHARTS = st.setdefault("chart", {})
    end = time.time() + RUN_S
    log.info("fastlane start; handoff state %s", "loaded" if prev else "none")
    flags_at = 0; chart_loop()
    while time.time() < end:
        t0 = time.time(); TICK_T[0] = t0
        if t0 - flags_at > 60: load_flags(st); flags_at = t0
        refresh_positions()
        TICK_CACHE.clear()
        by = {}
        for ch, tok in watch_set(st): by.setdefault(ch, []).append(tok)
        for ch, toks in by.items():
            ps = fetch(ch, toks)
            for t in toks: TICK_CACHE[(ch, t)] = []
            for p in ps:
                for side in ("baseToken", "quoteToken"):
                    a = (p.get(side) or {}).get("address") or ""
                    for k in ((ch, a), (ch, a.lower())):
                        if k in TICK_CACHE: TICK_CACHE[k].append(p)
        try:
            positions.check(st, log); positions.user_alerts(st, log); positions.ladder(st, log, chart_cached); positions.study_moves(st, log)
            st["liqw_next"] = 0; positions.liq_watch(st, log)
        except Exception as e:
            STATS["errors"] += 1; log.warning("alert eval: %s", f"{type(e).__name__}: {e}"[:200])
        STATS["ticks"] += 1; dt_ = time.time() - t0; STATS["max_tick_s"] = max(STATS["max_tick_s"], round(dt_, 2))
        time.sleep(max(0.2, TICK - dt_))
    handoff_save(st)
    log.info("fastlane end %s coins=%d", json.dumps(STATS), sum(len(v) for v in by.values()))

if __name__ == "__main__":
    main()
