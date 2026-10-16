#!/usr/bin/env python3
"""Always-on Base + Solana desk watcher. Plain Python, no AI calls. Read-only: no wallets, no signing, no trades.

Every ~60 s: discover Base + Solana tokens -> cheap pre-filter -> full audit -> pre-entry rules -> wallets -> flag.
Flags go to /workspace/desk/alerts/pending.jsonl (and an optional webhook). See README.md ("watcher").
"""
import json, os, re, sys, time, signal, traceback, logging, datetime as dt
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desk  # noqa: E402
import dsrows  # noqa: E402

DESK = os.environ.get("DESK_DIR", "/workspace/desk")
TOOLS = os.path.join(DESK, "tools")
CACHE = desk.CACHE_DIR
STATE_DIR = os.environ.get("DESK_STATE_DIR", CACHE)
STATE_F = os.path.join(STATE_DIR, "watcher_state.json")
HEART_F = os.path.join(STATE_DIR, "watcher_heartbeat.json")
ALERTS = os.environ.get("DESK_ALERTS_FILE") or os.path.join(DESK, "alerts", "pending.jsonl")
LOG_F = os.environ.get("DESK_LOG_FILE") or os.path.join(TOOLS, "watcher.log")
PAPER_BOOK = os.environ.get("DESK_PAPER_BOOK") or os.path.join(DESK, "paper-book.md")
RUN_SECONDS = float(os.environ.get("DESK_RUN_SECONDS", "0"))   # 0 = forever (box); cloud runs set ~270
WEBHOOK_FILE = "/home/box/.desk_webhook"
ET = ZoneInfo("America/New_York"); PT = ZoneInfo("America/Los_Angeles")
WETH = "0x4200000000000000000000000000000000000006"

CYCLE_S = 60
# ---- rules (paper-limits.md v2 + "Added 2026-10-11") ----
MIN_LIQ = 50_000; MAX_MCAP = 1_000_000; PREF_MCAP = 400_000  # MAX_MCAP = warn threshold (not hard reject; user 2026-10-15)
MIN_VOL24 = 25_000; MIN_TXNS24 = 150; MAX_VOL_LIQ = 30; MAX_VOL_LIQ_SOL = 80  # pump.fun graduates trade 30-60x liq; unique-trader wash screen still applies
MIN_AGE_H = 1.0; MAX_AGE_H = 48.0
DEPLOYER_LINKED_MAX = 5.0; TOP10_MAX = 40.0
SIZE_PCT = 0.05; SIZE_PCT_LOSING = 0.025; MAX_BUY_LIQ = 0.015; MIN_CASH = 0.70
MAX_OPEN = 3; MAX_NEW_PER_DAY = 2
ETH_HALF = -7.0; ETH_STOP = -12.0
CB_DAY = -10.0; CB_WEEK = -20.0
# strong-trend definition (documented in README)
TREND_H1_CHG = 15.0; TREND_H1_BUYSELL = 1.3; TREND_H1_VOL = 25_000
# ---- schedule ----
EVERY = {"boosts": 300, "launchpads": 300, "eth": 600, "be_trending": 8 * 3600, "be_new": 12 * 3600,
         "prune": 3600, "bs_retry": 3600}
ROTATE = ([f"pools?sort=h24_volume_usd_desc&page={i}" for i in range(1, 9)] +
          [f"pools?sort=h24_tx_count_desc&page={i}" for i in (1, 2)] +
          [f"new_pools?page={i}" for i in range(2, 11)] +
          ["trending_pools?duration=1h&page=1", "trending_pools?duration=6h&page=1", "trending_pools?duration=24h&page=1"])
DS_BATCHES_PER_CYCLE = 4
WATCH_MAX = 4000
# ---- 2026-10-13 additions ----
SOL = desk.SOL
SOL_ROTATE = (["dexes/pumpswap/pools?sort=h24_tx_count_desc&page=1", "dexes/pumpswap/pools?sort=h24_tx_count_desc&page=2",
               "dexes/pumpswap/pools?sort=h24_volume_usd_desc&page=1", "dexes/raydium/pools?sort=h24_tx_count_desc&page=1",
               "dexes/meteora-damm-v2/pools?sort=h24_tx_count_desc&page=1", "dexes/meteora/pools?sort=h24_tx_count_desc&page=1",
               "trending_pools?duration=1h&page=1", "trending_pools?duration=6h&page=1", "pools?sort=h24_tx_count_desc&page=1",
               "pools?sort=h24_volume_usd_desc&page=1", "pools?sort=h24_volume_usd_desc&page=2"])
GRAD_DEX = {"pumpswap": "pump.fun", "raydium": "letsbonk/raydium", "meteora": "meteora", "meteora-damm-v2": "meteora"}
GAP_CATCHUP_S = 300              # wall-clock gap (box asleep / restart) that triggers a deeper catch-up scan
LONG_DAYS = 30; LONG_MAX = 400; LONG_MIN_MCAP = 300_000
FLOOR_DD_MIN, FLOOR_DD_MAX = 0.70, 0.92; FLOOR_DAYS = 5; FLOOR_RANGE_MAX = 1.6
BRK_ABOVE = 1.30; BRK_VOL_X = 3.0; BRK_SELLERS = 50; BRK_HOLDERS_UP = 0.10

log = logging.getLogger("watcher")

def setup_log():
    os.makedirs(CACHE, exist_ok=True)
    h = RotatingFileHandler(LOG_F, maxBytes=512 * 1024, backupCount=2)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(h); log.setLevel(logging.INFO)

# ---- call accounting (wraps desk's per-host throttle, which runs once per HTTP attempt) ----
CALLS = []  # (ts, host)
_orig_throttle = desk._throttle
def _counting_throttle(host):
    CALLS.append((time.time(), host)); _orig_throttle(host)
desk._throttle = _counting_throttle

def calls_last_hour():
    cut = time.time() - 3600
    while CALLS and CALLS[0][0] < cut: CALLS.pop(0)
    out = {}
    for _, h in CALLS: out[h] = out.get(h, 0) + 1
    return out

# ---- state ----
def load_state():
    try: return json.load(open(STATE_F))
    except Exception: return {"watch": {}, "flagged": {}, "last": {}, "rot": 0, "pot_snaps": [], "stats": {}}

def save_state(st):
    tmp = STATE_F + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE_F)

def due(st, key, every):
    if time.time() - st["last"].get(key, 0) >= every:
        st["last"][key] = time.time(); return True
    return False

def now(): return dt.datetime.now(dt.timezone.utc)
def stamp(t=None):
    t = t or now()
    return {"time_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "time_et": t.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
            "time_pt": t.astimezone(PT).strftime("%Y-%m-%d %H:%M PT")}

# ---- discovery ----
def is_sol(s): return bool(s) and 32 <= len(s) <= 44 and not s.startswith("0x") and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]+", s) is not None

def add(st, tok, src, pair=None, chain="base"):
    if chain == SOL:
        if not is_sol(tok) or tok in desk.SOL_MAJORS: return
    else:
        tok = desk.low(tok)
        if not desk.is_addr(tok) or tok in desk.MAJORS: return
    w = st["watch"].get(tok)
    if not w:
        if len(st["watch"]) >= WATCH_MAX: return
        st["watch"][tok] = {"first_seen": time.time(), "src": [src], "next": 0, "fails": 0, "chain": chain}
    elif src not in w["src"]:
        w["src"] = (w["src"] + [src])[-6:]

def gt_list(st, path, src):
    d = desk.get(f"{desk.GT}/networks/base/{path}", ttl=45) or {}
    for p in d.get("data") or []:
        rel = p["relationships"]
        tok = desk.meme_side(rel["base_token"]["data"]["id"].split("_", 1)[1], rel["quote_token"]["data"]["id"].split("_", 1)[1])
        add(st, tok, src)

def gt_sol_list(st, path):
    d = desk.get(f"{desk.GT}/networks/solana/{path}", ttl=45) or {}
    dexsrc = path.split("/")[1] if path.startswith("dexes/") else None
    for p in d.get("data") or []:
        rel = p["relationships"]
        b = rel["base_token"]["data"]["id"].split("_", 1)[1]; q = rel["quote_token"]["data"]["id"].split("_", 1)[1]
        tok = q if (b in desk.SOL_MAJORS and q not in desk.SOL_MAJORS) else b
        dex = (rel.get("dex") or {}).get("data", {}).get("id") or dexsrc
        add(st, tok, ("grad_" + GRAD_DEX[dex].split("/")[0].replace(".", "")) if dex in GRAD_DEX else "gt_sol", chain=SOL)

def ds_grad_search(st):
    """Graduated launchpad pools via DexScreener search (pump.fun -> PumpSwap, LetsBonk -> Raydium, Meteora DBC -> DAMM)."""
    for q, src in (("pumpswap", "grad_pumpfun"), ("bonk", "grad_letsbonk"), ("meteora", "grad_meteora")):
        d = desk.get(f"{desk.DS}/latest/dex/search?q={q}", ttl=120) or {}
        for p in d.get("pairs") or []:
            if p.get("chainId") != SOL: continue
            b, qq = p["baseToken"]["address"], p["quoteToken"]["address"]
            tok = qq if (b in desk.SOL_MAJORS and qq not in desk.SOL_MAJORS) else b
            if src == "grad_letsbonk" and not tok.endswith("bonk"): continue
            add(st, tok, src, chain=SOL)

def catch_up(st, gap):
    """After a sleep/restart gap, scan deeper so launches during the gap are not missed."""
    pages = min(10, 2 + int(gap // 600))
    for i in range(1, pages + 1): gt_list(st, f"new_pools?page={i}", "gt_new")
    for path in SOL_ROTATE[:6]: gt_sol_list(st, path)
    ds_grad_search(st)
    log.info("catch-up after %.0f min gap: scanned %s Base new-pool pages + Solana graduation lists", gap / 60, pages)

def discover(st):
    gt_list(st, "new_pools?page=1", "gt_new")
    sp = SOL_ROTATE[st.get("srot", 0) % len(SOL_ROTATE)]; st["srot"] = st.get("srot", 0) + 1
    gt_sol_list(st, sp)
    if due(st, "ds_grad", 300): ds_grad_search(st)
    path = ROTATE[st["rot"] % len(ROTATE)]; st["rot"] += 1
    gt_list(st, path, "gt_trending" if "trending" in path else "gt_list")
    if due(st, "boosts", EVERY["boosts"]):
        for u, src in (("/token-boosts/latest/v1", "ds_boost"), ("/token-boosts/top/v1", "ds_boost_top"), ("/token-profiles/latest/v1", "ds_profile")):
            for x in desk.get(desk.DS + u, ttl=120) or []:
                if x.get("chainId") == "base": add(st, x.get("tokenAddress", ""), src)
                elif x.get("chainId") == SOL: add(st, x.get("tokenAddress", ""), src, chain=SOL)
    if due(st, "launchpads", EVERY["launchpads"]):
        c = desk.get(f"{desk.CLANKER}?sort=desc&page=1", ttl=120) or {}
        for x in c.get("data") or []:
            if x.get("chain_id") == 8453: add(st, x.get("contract_address", ""), "clanker")
        for lt in ("NEW", "TOP_VOLUME_24H", "TOP_GAINERS"):
            z = desk.get(f"https://api-sdk.zora.engineering/explore?listType={lt}&count=20", ttl=120) or {}
            for e in ((z.get("exploreList") or {}).get("edges") or []):
                n = e.get("node") or {}
                if str(n.get("chainId")) == "8453": add(st, n.get("address", ""), "zora_" + lt.lower())
    if due(st, "be_trending", EVERY["be_trending"]):
        d = desk.be_get("/defi/token_trending", {"sort_by": "rank", "sort_type": "asc", "offset": 0, "limit": 20}, ttl=600)
        for x in (d or {}).get("tokens") or []: add(st, x.get("address", ""), "be_trending")
    if due(st, "be_new", EVERY["be_new"]):
        d = desk.be_get("/defi/v2/tokens/new_listing", {"limit": 20}, ttl=600)
        for x in (d or {}).get("items") or []: add(st, x.get("address", ""), "be_new")

# ---- cheap pre-filter (DexScreener batch, 30 tokens/call) ----
def prefilter(st):
    t = time.time()
    dueq = sorted([k for k, w in st["watch"].items() if w["next"] <= t], key=lambda k: st["watch"][k]["next"])
    dueq = dueq[:30 * DS_BATCHES_PER_CYCLE]
    survivors = []
    if not dueq: return survivors
    by = {}
    for ch in ("base", SOL):
        q = [k for k in dueq if st["watch"][k].get("chain", "base") == ch]
        if not q: continue
        for p in desk.ds_token_pairs(q, chain=ch):
            if ch == SOL:
                b, qq = p["baseToken"]["address"], p["quoteToken"]["address"]
                tok = qq if (b in desk.SOL_MAJORS and qq not in desk.SOL_MAJORS) else b
            else:
                tok = desk.meme_side(p["baseToken"]["address"], p["quoteToken"]["address"])
            by.setdefault(tok, []).append(p)
    for tok in dueq:
        w = st["watch"][tok]; ps = by.get(tok, [])
        age_seen = (t - w["first_seen"]) / 3600
        if not ps:
            w["fails"] += 1; w["why"] = "no_pair"
            w["next"] = t + (300 if age_seen < 2 else 1800)
            if age_seen > 6: w["drop"] = True
            continue
        created = [p["pairCreatedAt"] for p in ps if p.get("pairCreatedAt")]
        age = (t * 1000 - min(created)) / 3.6e6 if created else None
        best = max(ps, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0))
        liq = desk.f((best.get("liquidity") or {}).get("usd"), 0)
        vol = sum(desk.f((p.get("volume") or {}).get("h24"), 0) for p in ps)
        tx = (best.get("txns") or {}).get("h24") or {}; ntx = (tx.get("buys") or 0) + (tx.get("sells") or 0)
        mc = desk.f(best.get("marketCap")) or desk.f(best.get("fdv"))
        h1 = (best.get("txns") or {}).get("h1") or {}
        w.update(age_h=round(age, 2) if age is not None else None, liq=round(liq), mcap=round(mc) if mc else None, vol24=round(vol),
                 pair=best["pairAddress"] if w.get("chain") == SOL else desk.low(best["pairAddress"]),
                 sym=best["baseToken"]["symbol"] if desk.low(best["baseToken"]["address"]) == desk.low(tok) else best["quoteToken"]["symbol"],
                 name=best["baseToken"].get("name") if desk.low(best["baseToken"]["address"]) == desk.low(tok) else best["quoteToken"].get("name"),
                 price=desk.f(best.get("priceUsd")), dex=best.get("dexId"),
                 url=best.get("url"), chg_h1=(best.get("priceChange") or {}).get("h1"), h1_buys=h1.get("buys"), h1_sells=h1.get("sells"),
                 vol_h1=round(desk.f((best.get("volume") or {}).get("h1"), 0)), txns24=ntx)
        if mc and mc >= LONG_MIN_MCAP and liq >= 20_000: long_add(st, tok, w)
        why = None
        cc = desk.copycat_check(w.get("sym"), w.get("name") or "")
        if not cc:
            twin = next((o for k2, o in st["watch"].items() if k2 != tok and o.get("sym") and desk.low(o.get("sym")) == desk.low(w.get("sym"))
                         and (o.get("mcap") or 0) > (mc or 0) and o.get("first_seen", 0) < w["first_seen"]), None)
            if twin: cc = f"same ticker as an earlier, bigger token ({twin.get('sym')})"
        if cc: why = "copycat:" + cc
        elif age is None: why = "no_age"
        elif age > MAX_AGE_H: why = "too_old"
        elif age < MIN_AGE_H: why = "first_hour"
        elif liq < MIN_LIQ: why = "liquidity"
        elif not mc: why = "no_mcap"
        elif vol < MIN_VOL24: why = "volume"
        elif ntx < MIN_TXNS24: why = "txns"
        elif liq and vol / liq > (MAX_VOL_LIQ_SOL if w.get("chain") == "solana" else MAX_VOL_LIQ): why = "wash_volume"
        w["why"] = why
        if not w.get("ds0"):
            w["ds0"] = 1
            try: dsrows.first_seen(tok, w, best)
            except Exception: pass
        if why == "too_old" or (why or "").startswith("copycat"): w["drop"] = True; continue
        if why == "first_hour":
            w["next"] = t + 300; continue                       # track brand-new pairs until 1h old
        if why:
            w["fails"] += 1
            w["next"] = t + (600 if liq >= 10_000 else 1800)
            if liq < 2_000 and age and age > 6: w["drop"] = True
            continue
        survivors.append(tok)
        w["next"] = t + 900                                     # survivors re-checked every 15 min
    for k in [k for k, w in st["watch"].items() if w.get("drop")]:
        st["watch"].pop(k, None)
    return survivors

# ---- account-level gates from paper-book.md ----
def read_book():
    txt = open(PAPER_BOOK).read()
    def num(k):
        m = re.search(rf"^{k}:\s*([-\d.]+)", txt, re.M); return float(m.group(1)) if m else None
    book = {"pot": num("pot_usd"), "cash": num("cash_usd"), "open": int(num("open_count") or 0)}
    # closed trades: optional fields result/pnl_usd/closed (YYYY-MM-DD)
    results = re.findall(r"pnl_usd:\s*([-\d.]+)", txt)
    book["pnls"] = [float(x) for x in results]
    book["opened_today"] = len(re.findall(rf"opened:\s*{dt.datetime.now(PT).strftime('%Y-%m-%d')}", txt))
    book["breaker_flag"] = bool(re.search(r"circuit_breaker:\s*(on|tripped|true)", txt, re.I))
    return book

def account_gates(st):
    g = {"ok": True, "reasons": [], "size_mult": 1.0}
    try:
        b = read_book()
    except Exception as e:
        return {"ok": False, "reasons": [f"paper-book unreadable: {e}"], "size_mult": 0}
    g["book"] = {k: b[k] for k in ("pot", "cash", "open")}
    pot = b["pot"] or 0
    snaps = st["pot_snaps"]; t = time.time()
    if not snaps or t - snaps[-1][0] > 3600: snaps.append([t, pot])
    st["pot_snaps"] = [s for s in snaps if t - s[0] < 8 * 86400]
    def chg(sec):
        base = [s[1] for s in st["pot_snaps"] if t - s[0] <= sec]
        return (pot / base[0] - 1) * 100 if base and base[0] else 0.0
    d, wk = chg(86400), chg(7 * 86400)
    g["pot_chg_day"], g["pot_chg_week"] = round(d, 2), round(wk, 2)
    if b["breaker_flag"]: g["ok"] = False; g["reasons"].append("circuit breaker set in paper-book")
    if d <= CB_DAY: g["ok"] = False; g["reasons"].append(f"circuit breaker: pot {d:.1f}% in a day")
    if wk <= CB_WEEK: g["ok"] = False; g["reasons"].append(f"circuit breaker: pot {wk:.1f}% in a week")
    if b["open"] >= MAX_OPEN: g["ok"] = False; g["reasons"].append(f"{b['open']} names open (max {MAX_OPEN})")
    if b["opened_today"] >= MAX_NEW_PER_DAY: g["ok"] = False; g["reasons"].append("2 new trades already today")
    p = b["pnls"]; streak = 0
    for x in reversed(p):
        if x < 0: streak += 1
        else: break
    g["size_pct"] = SIZE_PCT_LOSING if streak >= 3 else SIZE_PCT
    g["loss_streak"] = streak
    eth = st.get("eth_chg")
    g["eth_24h"] = eth
    if eth is None: g["ok"] = False; g["reasons"].append("ETH 24h change unknown")
    elif eth <= ETH_STOP: g["ok"] = False; g["reasons"].append(f"ETH {eth:.1f}% in 24h (no new entries)")
    elif eth <= ETH_HALF: g["size_mult"] = 0.5; g["reasons"].append(f"ETH {eth:.1f}% in 24h: half size")
    size = pot * g["size_pct"] * g["size_mult"]
    if b["cash"] is not None and b["cash"] - size < MIN_CASH * pot:
        g["ok"] = False; g["reasons"].append("cash would fall under 70% of pot")
    g["size_usd"] = round(size, 2)
    return g

def eth_change(st):
    d = desk.get(f"{desk.GT}/simple/networks/base/token_price/{WETH}?include_24hr_price_change=true", ttl=300)
    try:
        st["eth_chg"] = float(d["data"]["attributes"]["h24_price_change_percentage"][WETH]); st["eth_src"] = "geckoterminal"; return
    except Exception: pass
    p = desk.ds_pair("0xd0b53d9277642d899df5c87a3966a349a798f224")  # WETH/USDC v3
    try: st["eth_chg"] = float(p["priceChange"]["h24"]); st["eth_src"] = "dexscreener"
    except Exception: st["eth_chg"] = None

# ---- webhook (file re-read every send, so no restart needed) ----
def webhook_cfg():
    cfg = {}
    try:
        if os.path.exists(WEBHOOK_FILE):
            mode = os.stat(WEBHOOK_FILE).st_mode & 0o777
            if mode & 0o077: log.warning("webhook file permissions are %o, expected 600 (still used)", mode)
            for line in open(WEBHOOK_FILE):
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1); cfg[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    except Exception as e:
        log.warning("webhook file unreadable: %s", type(e).__name__)
    url = cfg.get("DESK_WEBHOOK_URL") or os.environ.get("DESK_WEBHOOK_URL")
    auth = cfg.get("DESK_WEBHOOK_AUTH") or os.environ.get("DESK_WEBHOOK_AUTH")
    return url, auth

def post_webhook(summary):
    url, auth = webhook_cfg()
    if not url: return "not_configured"
    hdr = {"Content-Type": "application/json", "User-Agent": "small-pot-base-desk-watcher/1.0"}
    if auth: hdr["Authorization"] = auth
    delay = 2
    for _ in range(3):
        try:
            r = requests.post(url, json=summary, headers=hdr, timeout=15)
            if r.status_code < 300: return f"sent {r.status_code}"
            if r.status_code in (429, 500, 502, 503, 504): time.sleep(delay); delay *= 2; continue
            return f"failed HTTP {r.status_code}"
        except requests.RequestException as e:
            err = type(e).__name__; time.sleep(delay); delay *= 2
    return "failed after retries"   # never log url/auth

# ---- evaluation of one pre-filter survivor ----
def screens(w, tok):
    """Fake-mcap/prefab + wash/only-goes-up screens (GeckoTerminal, free). Returns reason or None."""
    ch = w.get("chain", "base"); t = time.time()
    pa = desk.gt_pool_info(w["pair"], ch)
    fk = w.get("fake") or {}
    if fk.get("first") is None and fk.get("tries", 0) < 3:      # first candle never changes: fetch once (3 tries)
        c = desk.gt_ohlcv(w["pair"], ch)
        if c and w.get("price") and w.get("mcap"):
            fk["first"] = round(w["mcap"] * float(c[0][4]) / w["price"])
        fk["tries"] = fk.get("tries", 0) + 1
    fk["at"] = t; w["fake"] = fk
    if fk.get("first") and fk["first"] >= 500_000:
        return f"fake_mcap:already ${fk['first']/1e3:.0f}k mcap on first candle"
    b24 = ((pa or {}).get("transactions") or {}).get("h24", {}).get("buyers") if pa else None
    fk["buyers24"] = b24
    if pa and w.get("mcap") and w.get("liq") and w["liq"] >= 0.40 * w["mcap"] and (b24 or 0) < 100:
        return f"fake_mcap:liquidity {w['liq']/w['mcap']*100:.0f}% of mcap with only {b24} buyers in 24h"
    ww, wdet = desk.wash_screen(w["pair"], ch, w.get("h1_buys"), w.get("h1_sells"), pa)
    w["wash"] = {"at": t, "why": ww, **wdet}
    if ww: return "wash:" + ww
    return None

def run_audit(w, tok):
    if w.get("chain") == SOL:
        a = desk.sol_audit(tok, w.get("price"))
        hc = next((c["detail"] for c in a.get("checks", []) if c["check"] == "holder_concentration"), {})
        return {"verdict": a["verdict"], "because": a.get("because", []), "at": time.time(),
                "checks": {c["check"]: c["status"] for c in a.get("checks", [])}, "creator": a.get("creator"), "owner": None,
                "top10": a.get("top10"), "linked_pct": a.get("linked_pct"), "bundle_pct": a.get("bundle_pct"), "launchpad": a.get("launchpad"), "holders": a.get("holders"),
                "insider_networks": hc.get("insider_networks") if isinstance(hc, dict) else None}
    a = desk.cmd_audit(SimpleNamespace(token=tok))
    return {"verdict": a["verdict"], "because": a.get("because", []), "at": time.time(),
            "checks": {c["check"]: c["status"] for c in a.get("checks", [])},
            "creator": next((c["detail"].get("creator") for c in a.get("checks", []) if c["check"] == "holder_concentration" and isinstance(c["detail"], dict)), None),
            "owner": next((c["detail"].get("owner") for c in a.get("checks", []) if c["check"] == "owner_powers" and isinstance(c["detail"], dict)), None),
            "top10": next((c["detail"].get("top10_ex_lp_burn_pct") for c in a.get("checks", []) if c["check"] == "holder_concentration" and isinstance(c["detail"], dict)), None)}

def evaluate(st, tok, gates, setup_override=None):
    w = st["watch"][tok]; t = time.time(); ch = w.get("chain", "base")
    rc = w["rule_checks"] = {"at": stamp()["time_utc"]}     # per-rule PASS/FAIL/WARN/INFO log (audits verify every rule)
    rc["mcap_over_1M"] = "WARN" if (w.get("mcap") or 0) > MAX_MCAP else "PASS"   # tag only, never a skip (user 2026-10-15)
    rc["bonding_curve"] = "WARN" if w.get("on_curve") or any("curve" in str(x) for x in (w.get("sig") or [])) else "PASS(warn-only rule)"  # warn-only
    sc = screens(w, tok)
    rc["fake_wash_screens"] = ("FAIL:" + sc) if sc else "PASS"
    if sc:
        w["why"] = sc; w["next"] = t + (12 * 3600 if sc.startswith("fake_mcap:already") else 1800); return None
    ac = w.get("audit")
    if not ac or t - ac["at"] > (12 * 3600 if ac["verdict"] == "REJECT" else 2 * 3600):
        ac = run_audit(w, tok)
        w["audit"] = ac
        st["stats"]["audits"] = st["stats"].get("audits", 0) + 1
    rc["audit"] = "PASS" if ac["verdict"] == "PASS" else f"FAIL:{ac['verdict']}"
    if ac["verdict"] != "PASS":
        w["why"] = f"audit_{ac['verdict']}:{','.join(ac['because'])}"
        if ac["verdict"] == "REJECT": w["next"] = t + 12 * 3600
        return None
    long_add(st, tok, w)                                 # every audit PASS goes on the 30-day long watchlist
    rc["top10_le_40"] = "PASS" if ac["top10"] is not None and ac["top10"] <= TOP10_MAX else f"FAIL:{ac['top10']}"
    if ac["top10"] is None or ac["top10"] > TOP10_MAX:
        w["why"] = f"top10_{ac['top10']}%>40%"; w["next"] = t + 3600; return None
    # deployer + linked wallets (Base: on-chain transfer scan; Solana: RugCheck creator + insider-flagged holders)
    if ch == SOL:
        w["linked"] = {"at": t, "pct": ac.get("linked_pct"), "err": None if ac.get("linked_pct") is not None else "rugcheck", "n": None}
    elif not w.get("linked") or t - w["linked"]["at"] > 3600:
        lpd = desk.launchpad(tok) or {}
        extra = [x for x in (ac.get("owner"), lpd.get("admin"), lpd.get("creator")) if x and desk.is_addr(x)]
        lk = desk.deployer_linked(tok, ac.get("creator") or "", extra, hours=min((w.get("age_h") or 48) + 0.5, 48))
        w["linked"] = {"at": t, "pct": lk["pct_total"], "err": lk["error"], "n": len(lk["wallets"])}
    rc["deployer_linked_le_5"] = "PASS" if w["linked"]["pct"] is not None and w["linked"]["pct"] <= DEPLOYER_LINKED_MAX else f"FAIL:{w['linked']['pct']}"
    if w["linked"]["pct"] is None:
        w["why"] = f"linked_unknown:{w['linked']['err']}"; w["next"] = t + 1800; return None
    if w["linked"]["pct"] > DEPLOYER_LINKED_MAX:
        w["why"] = f"deployer_linked_{w['linked']['pct']}%>5%"; w["next"] = t + 3600; return None
    # 2026-10-15 user-approved (signal-survey): skip flat buy/sell (45-55% buys, >=20 trades last 1h).
    # Basis: unique buyer/seller wallets (GT 1h) when available, else txns. Both are logged.
    hb, hs = w.get("h1_buys") or 0, w.get("h1_sells") or 0
    br_tx = hb / (hb + hs) if hb + hs >= 20 else None
    wa = w.get("wash") or {}; ub, us = wa.get("h1_unique_buyers"), wa.get("h1_unique_sellers")
    br_u = ub / (ub + us) if ub is not None and us is not None and ub + us >= 20 else None
    br, basis = (br_u, "unique_wallets") if br_u is not None else (br_tx, "txns")
    rc["buy_share"] = {"basis": basis, "unique_wallets": round(br_u, 3) if br_u is not None else None, "txns": round(br_tx, 3) if br_tx is not None else None}
    rc["near_even_45_55_skip"] = ("FAIL:" + f"{br:.0%}") if br is not None and 0.45 <= br <= 0.55 else ("PASS" if br is not None else "INFO:<20 trades")
    if br is not None and 0.45 <= br <= 0.55:
        w["why"] = f"flat_buysell:{br:.0%}({basis})"; w["next"] = t + 1800; return None
    vm = (w.get("vol_h1") or 0) / (w.get("mcap") or 1e18)
    support = [x for x, ok in (("vol_mcap_4x", vm >= 4), ("buys_60pct", br is not None and br >= 0.6)) if ok]
    rc["support_vol4x_buys60"] = {"vol_h1_over_mcap": round(vm, 2), "vol_mcap_4x": vm >= 4, "buys_60pct": br is not None and br >= 0.6, "both": len(support) == 2}
    # setups
    strong = ((desk.f(w.get("chg_h1"), 0) >= TREND_H1_CHG) and (w.get("h1_buys") or 0) >= TREND_H1_BUYSELL * max(w.get("h1_sells") or 0, 1)
              and (w.get("vol_h1") or 0) >= TREND_H1_VOL)
    listed = any(s in w["src"] for s in ("gt_trending", "ds_boost_top", "be_trending", "zora_top_volume_24h", "zora_top_gainers"))
    wl = None
    if not w.get("wallets") or t - w["wallets"]["at"] > 3600:
        r = (desk.sol_wallets(tok) if ch == SOL else desk.cmd_wallets(SimpleNamespace(token=tok, hours=6, pnl_checks=3, big_usd=500)))
        be = r.get("birdeye") or {}
        w["wallets"] = {"at": t, "signal": r.get("signal"), "smart": be.get("smart_money"), "evidence": r.get("evidence", [])[:5]}
    wl = w["wallets"]
    cluster = wl["smart"] in ("BUYING", "HOLDING") or wl["signal"] == "ACCUMULATING"
    setups = [s for s, ok in (("big_buyer_cluster", cluster), ("strong_trend", strong)) if ok]
    # support may pass a borderline trend (half the 1h-change bar, vol bar met) only when BOTH supports hold; safety already passed above
    if not setups and len(support) == 2 and desk.f(w.get("chg_h1"), 0) >= TREND_H1_CHG / 2 and (w.get("vol_h1") or 0) >= TREND_H1_VOL:
        setups = ["supported_trend"]
    if setup_override: setups = setup_override + [s for s in setups if s not in setup_override]
    rc["setup"] = setups or "FAIL:none"
    if not setups:
        w["why"] = f"no_setup(wallets={wl['signal']}/{wl['smart']},trend={'listed' if listed else 'no'})"; return None
    rc["account_gates"] = "PASS" if gates["ok"] else "FAIL:" + ";".join(gates["reasons"])
    if not gates["ok"]:
        w["why"] = "blocked:" + ";".join(gates["reasons"]); return None
    liq = w["liq"]; size = min(gates["size_usd"], MAX_BUY_LIQ * liq)
    slip = 0.05 if liq < 100_000 else 0.03
    return {"token": tok, "chain": ch, "symbol": w.get("sym"), "pair": w.get("pair"), "link": w.get("url") or f"https://dexscreener.com/{ch}/{w.get('pair')}",
            "launchpad": ac.get("launchpad") or next((s.replace("grad_", "") for s in w["src"] if s.startswith(("grad_", "clanker", "zora"))), None),
            "mcap": w.get("mcap"), "liquidity": liq, "vol24": w.get("vol24"), "age_h": w.get("age_h"),
            "entry_zone": "preferred" if (w.get("mcap") or 0) < PREF_MCAP else "ok",
            "setup": setups, "trend_listed": listed, "support": support, "rule_checks": rc,
            "evidence": {"audit": "PASS", "wallets_signal": wl["signal"], "smart_money": wl["smart"], "wallets": wl["evidence"],
                         "chg_h1": w.get("chg_h1"), "h1_buys": w.get("h1_buys"), "h1_sells": w.get("h1_sells"), "vol_h1": w.get("vol_h1"),
                         "top10_pct": ac["top10"], "deployer_linked_pct": w["linked"]["pct"], "bundle_pct": ac.get("bundle_pct", "n/a (EVM: no free cluster data)"), "sources": w["src"],
                         "first_candle_mcap": (w.get("fake") or {}).get("first"),
                         "wash": {k: (w.get("wash") or {}).get(k) for k in ("h1_unique_buyers", "h1_unique_sellers", "h1_buys", "h1_sells")},
                         "sell_test": "Jupiter quote" if ch == SOL else "honeypot.is", **({"floor": w["floor_ev"]} if w.get("floor_ev") else {})},
            "sizing": {"paper_size_usd": round(size, 2), "max_buy_1_5pct_liq": round(MAX_BUY_LIQ * liq, 2), "size_pct": gates["size_pct"],
                       "eth_24h": gates.get("eth_24h"), "size_mult": gates["size_mult"],
                       "est_cost_usd_round_trip": round(size * slip * 2 + 1.0, 2), "slippage_assumed": slip},
            "rules": "paper-limits.md v2", "status": "pending_review", **stamp()}

def fmt_usd(x):
    if x is None: return "?"
    return f"${x/1e6:.2f}M" if x >= 1e6 else f"${x/1e3:.0f}k"

def sell_levels(mcap):
    rungs = [r for r in desk.RUNGS if r > (mcap or 0)]
    pl = ["25%", "25%", "25%", "runner"]
    return [f"{p} at {fmt_usd(r)}" for p, r in zip(pl, rungs)]

def messages(rec):
    """Owner alert = two messages: (1) ONLY the contract address, (2) details incl. chain (see ALERTS.md)."""
    ev = rec["evidence"]
    lines = [f"{rec['symbol']} | {rec['chain'].upper()}" + (f" | {rec['launchpad']}" if rec.get("launchpad") else ""),
             rec["token"],
             f"Setup: {', '.join(rec['setup'])}",
             f"Market cap: {fmt_usd(rec['mcap'])} | Liquidity: {fmt_usd(rec['liquidity'])} | Age: {rec.get('age_h')}h",
             f"Buy (paper): ${rec['sizing']['paper_size_usd']:.0f} (max 1.5% of liq = ${rec['sizing']['max_buy_1_5pct_liq']:.0f})",
             "Sell: " + "; ".join(sell_levels(rec["mcap"])) + "; stop -25%",
             f"Evidence: audit PASS ({ev['sell_test']} sell test), top10 {ev['top10_pct']}%, deployer-linked {ev['deployer_linked_pct']}%, bundle {ev.get('bundle_pct')}%, "
             f"wallets {ev['wallets_signal']}/{ev['smart_money']}, 1h unique buyers/sellers {ev['wash'].get('h1_unique_buyers')}/{ev['wash'].get('h1_unique_sellers')}"
             + (f", floor {ev['floor']}" if ev.get("floor") else ""),
             rec["link"], rec["time_et"]]
    fl = ev.get("floor")
    if fl and fl.get("range_top"):
        top = fl["range_top"]; px = fl.get("price") or top; m = (rec["mcap"] or 0) / px if px else 0
        lo = top / 1.6
        lines.insert(-2, f"Entry zone: up to {fmt_usd(rec['mcap'] * 1.10) if rec['mcap'] else '?'} mcap (breakout retest of {fmt_usd(top * m * 1.3)})")
        lines.insert(-2, f"Stop: 15% below the floor (~{fmt_usd(lo * m * 0.85)} mcap); targets 2x/3x of entry, trail -35%, out near old peak ({fmt_usd(fl.get('peak_usd', 0) * m)})")
    return rec["token"], "\n".join(lines)

LATE_H1_CHG, LATE_FC_MULT = 150, 5
def late_chase(rec):
    """Return reject reason or None. BREAKDOWN signal, or 1h>+150% AND mcap>=5x first candle unless inside the dip zone."""
    ch = rec.get("chart") or {}
    if (ch.get("signal") or "") == "breakdown": return "late_chase:chart_BREAKDOWN"
    ev = rec.get("evidence") or {}; h1 = desk.f(ev.get("chg_h1"), 0); fc = ev.get("first_candle_mcap"); mc = rec.get("mcap") or 0
    if h1 > LATE_H1_CHG and fc and mc >= LATE_FC_MULT * fc:
        dz = (ch.get("entry_zones") or {}).get("dip")
        if dz and dz["from"] <= mc <= dz["to"]: return None
        return f"late_chase:h1+{h1:.0f}%_mcap{mc/fc:.1f}x_first_candle"
    return None

def sybil_spread(rec):
    """LOG-ONLY tag (VCAT 2026-10-14): supply spread thin (top10<3%) + >4 buys per unique 1h buyer = bot/wallet farm."""
    ev = rec.get("evidence") or {}; t10 = ev.get("top10_pct"); wa = ev.get("wash") or {}
    ub, b = wa.get("h1_unique_buyers"), wa.get("h1_buys")
    if t10 is not None and t10 < 3 and ub and b and b / ub > 4: return f"sybil_spread:top10_{t10}%_{b/ub:.1f}buys_per_buyer"
    return None

def flag(st, rec):
    try:
        import chart; rec["chart"] = chart.read(rec["pair"], rec["chain"], mcap=rec.get("mcap"), quick=True)
    except Exception as e: rec["chart"] = {"error": f"{type(e).__name__}"}
    why = late_chase(rec)                                # WARN tag only (user: never block on these); logged as dataset feature
    rec["warn"] = [why] if why else []
    sy = sybil_spread(rec)
    if sy: rec["warn"].append(sy); st["stats"]["sybil_warns"] = st["stats"].get("sybil_warns", 0) + 1
    if (rec.get("mcap") or 0) > MAX_MCAP:
        rec["warn"].append(f"entry_over_1M:{rec['mcap']}")
        st["stats"]["over_1m_warns"] = st["stats"].get("over_1m_warns", 0) + 1
    # LOG-ONLY study tag: high-score / proven tracked wallet bought this token (no new phone alert)
    pb = (st.get("smart") or {}).get("proven_buys") or {}
    if rec["token"] in pb or any(h.get("tok") == rec["token"] and h.get("proven") for h in ((st.get("smart") or {}).get("hits") or [])):
        rec.setdefault("warn", []).append("proven_wallet_in")
        st["stats"]["proven_wallet_tags"] = st["stats"].get("proven_wallet_tags", 0) + 1
    w = st["watch"].get(rec["token"])
    if w is not None: w["warn"] = rec["warn"]
    if why: st["stats"]["late_chase_warns"] = st["stats"].get("late_chase_warns", 0) + 1
    try:
        import runner; rec["runner_score"] = runner.score({"mc": rec.get("mcap"), "liq": rec.get("liquidity"), "v24": rec.get("vol24"), "age_h": rec.get("age_h")}, ch=rec["chain"])
    except Exception as e: rec["runner_score"] = {"error": f"{type(e).__name__}"}
    rec["message_1"], rec["message_2"] = messages(rec)
    try:
        import chart
        rec["message_2"] = rec["message_2"] + "\n" + chart.line(rec["chart"]) + (f"\nRunner score (log-only): {rec['runner_score']['score']}" if (rec.get("runner_score") or {}).get("score") is not None else "")
    except Exception: pass
    for x in rec.get("warn") or []:
        if x.startswith("sybil_spread"): rec["message_2"] = "WARN: sybil-spread (" + x.split(":")[1].replace("_", " ") + ")\n" + rec["message_2"]; continue
        if x.startswith("proven_wallet_in"): rec["message_2"] = "proven wallet in\n" + rec["message_2"]; continue
        if x.startswith("entry_over_1M"): rec["message_2"] = "WARN: entry over $1M\n" + rec["message_2"]; continue
        rec["message_2"] = ("WARN: chart breakdown" if "BREAKDOWN" in x else "WARN: chasing " + x.split("mcap")[1].split("x")[0] + "x pump (1h " + x.split("h1")[1].split("_")[0] + ")") + "\n" + rec["message_2"]
    with open(ALERTS, "a") as fh: fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    st["flagged"][rec["token"]] = time.time()
    st.setdefault("flagged_ch", {})[rec["token"]] = rec["chain"]
    summ = {k: rec[k] for k in ("chain", "symbol", "token", "link", "mcap", "liquidity", "setup", "time_utc", "time_et", "message_1", "message_2")}
    summ["size_usd"] = rec["sizing"]["paper_size_usd"]; summ["note"] = "PAPER candidate; read-only watcher"
    res = post_webhook(summ); res += " ntfy=" + post_ntfy(rec)
    log.info("FLAG %s %s setup=%s mcap=%s liq=%s webhook=%s", rec["symbol"], rec["token"], rec["setup"], rec["mcap"], rec["liquidity"], res)
    st["stats"]["flags"] = st["stats"].get("flags", 0) + 1
    return True

# ---- 30-day long watchlist + floor-watch ----
def long_add(st, tok, w):
    L = st.setdefault("long", {})
    if tok in L or len(L) >= LONG_MAX or not w.get("pair"): return
    if (w.get("why") or "").startswith(("copycat", "fake_mcap")) or (w.get("fake") or {}).get("first", 0) and w["fake"]["first"] >= 500_000: return
    L[tok] = {"chain": w.get("chain", "base"), "pair": w["pair"], "sym": w.get("sym"), "name": w.get("name"), "url": w.get("url"),
              "added": time.time(), "next_daily": time.time() + 60, "next_check": 0, "holders": [], "state": "tracking"}

def floor_daily(st, tok, L):
    """Once a day: daily candles -> peak, drawdown, floor range. Holder snapshot for armed names."""
    c = desk.gt_ohlcv(L["pair"], L["chain"], tf="day", agg=1, limit=60, ttl=6 * 3600)
    L["next_daily"] = time.time() + 86400
    if not c or len(c) < 3: L["state"] = "no_data"; return
    done = c[:-1]                                    # last candle is today's (incomplete)
    peak = max(float(x[2]) for x in c); last = float(c[-1][4])
    rng = []
    for x in reversed(done):                          # trailing days that stay in one range
        cand = rng + [x]
        hi = max(float(y[4]) for y in cand); lo = min(float(y[4]) for y in cand)
        if lo > 0 and hi / lo <= FLOOR_RANGE_MAX: rng = cand
        else: break
    dd = 1 - (max(float(y[4]) for y in rng) if rng else last) / peak if peak else 0
    L.update(peak=peak, last=last, dd=round(dd, 3), range_days=len(rng),
             range_top=max(float(y[4]) for y in rng) if rng else None, range_low=min(float(y[3]) for y in rng) if rng else None,
             range_vol=(sum(float(y[5]) for y in rng) / len(rng)) if rng else None)
    armed = FLOOR_DD_MIN <= dd <= FLOOR_DD_MAX and len(rng) >= FLOOR_DAYS
    L["state"] = "armed" if armed else "tracking"
    if armed or len(rng) >= FLOOR_DAYS - 2:
        hc = desk.holder_count(tok, L["chain"])
        if hc: L["holders"] = (L["holders"] + [[int(time.time()), hc]])[-12:]

def floor_watch(st, gates, budget_s=40):
    L = st.setdefault("long", {}); t = time.time(); t0 = t; flags = []
    for k in [k for k, v in L.items() if t - v["added"] > LONG_DAYS * 86400 and not v.get("pinned")]: L.pop(k)
    for tok, e in sorted(L.items(), key=lambda kv: kv[1]["next_daily"]):
        if time.time() - t0 > budget_s: break
        if e["next_daily"] <= t: floor_daily(st, tok, e)
    armed = [k for k, e in L.items() if e.get("state") == "armed" and e.get("next_check", 0) <= t]
    if not armed: return flags
    by = {}
    for ch in ("base", SOL):
        q = [k for k in armed if L[k]["chain"] == ch][:30]
        for p in desk.ds_token_pairs(q, chain=ch) if q else []:
            for k in q:
                if desk.low(p["baseToken"]["address"]) == desk.low(k) or desk.low(p["quoteToken"]["address"]) == desk.low(k):
                    if desk.f((p.get("liquidity") or {}).get("usd"), 0) >= desk.f((by.get(k, {}).get("liquidity") or {}).get("usd"), 0): by[k] = p
    for k in armed:
        e = L[k]; e["next_check"] = t + 3600; p = by.get(k)
        if not p: continue
        price = desk.f(p.get("priceUsd")); vol24 = desk.f((p.get("volume") or {}).get("h24"), 0)
        if not price or not e.get("range_top") or price < BRK_ABOVE * e["range_top"]: continue
        if not e.get("range_vol") or vol24 < BRK_VOL_X * e["range_vol"]: continue
        # confirm on a completed 1h close (not just a wick)
        h = desk.gt_ohlcv(e["pair"], e["chain"], tf="hour", agg=1, limit=3, ttl=300)
        if not h or len(h) < 2 or float(h[-2][4]) < BRK_ABOVE * e["range_top"]: continue
        pa = desk.gt_pool_info(e["pair"], e["chain"])
        sellers24 = (((pa or {}).get("transactions") or {}).get("h24") or {}).get("sellers")
        if (sellers24 or 0) < BRK_SELLERS: e["why"] = f"breakout but only {sellers24} unique sellers"; continue
        hc_now = desk.holder_count(k, e["chain"]); old = [n for ts, n in e["holders"] if t - ts >= 4 * 86400]
        hold_up = bool(hc_now and old and hc_now >= (1 + BRK_HOLDERS_UP) * old[0])
        smart = None
        if not hold_up:
            r = desk.sol_wallets(k) if e["chain"] == SOL else desk.cmd_wallets(SimpleNamespace(token=k, hours=6, pnl_checks=3, big_usd=500))
            smart = (r.get("birdeye") or {}).get("smart_money")
        if not (hold_up or smart == "BUYING"):
            e["why"] = f"breakout without holder growth ({hc_now} vs {old[:1]}) or 2+ proven wallets buying ({smart})"; continue
        ev = {"peak_usd": desk.sig(e["peak"], 4), "drawdown_pct": round(e["dd"] * 100), "range_days": e["range_days"],
              "range_top": desk.sig(e["range_top"], 4), "price": desk.sig(price, 4), "vol24_vs_range": round(vol24 / e["range_vol"], 1),
              "sellers_24h": sellers24, "holders_now": hc_now, "holders_before": old[:1], "proven_wallets": smart}
        flags.append((k, e, p, ev))
    out = []
    for k, e, p, ev in flags:
        if k in st["flagged"]: continue
        w = st["watch"].get(k) or {"first_seen": time.time() - 86400 * 3, "src": ["floor_watch"], "next": time.time() + 3600, "fails": 0, "chain": e["chain"]}
        mc = desk.f(p.get("marketCap")) or desk.f(p.get("fdv")); liq = desk.f((p.get("liquidity") or {}).get("usd"), 0)
        h1 = (p.get("txns") or {}).get("h1") or {}
        created = p.get("pairCreatedAt")
        w.update(pair=e["pair"], sym=e["sym"], name=e.get("name"), url=p.get("url"), mcap=round(mc) if mc else None, liq=round(liq),
                 price=desk.f(p.get("priceUsd")), chg_h1=(p.get("priceChange") or {}).get("h1"), h1_buys=h1.get("buys"), h1_sells=h1.get("sells"),
                 vol_h1=round(desk.f((p.get("volume") or {}).get("h1"), 0)), vol24=round(desk.f((p.get("volume") or {}).get("h24"), 0)),
                 age_h=round((time.time() * 1000 - created) / 3.6e6, 1) if created else None, floor_ev=ev, chain=e["chain"])
        if liq < MIN_LIQ: e["why"] = "breakout but liquidity under $50k"; continue
        st["watch"][k] = w
        rec = evaluate(st, k, gates, setup_override=["floor_breakout"])
        if rec: out.append(rec)
        else: e["why"] = "breakout blocked: " + (w.get("why") or "?")
    return out

def post_ntfy(rec):
    """Two pushes to https://ntfy.sh/$NTFY_TOPIC: (1) ONLY the address, (2) details. Topic never logged."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic: return "not_configured"
    import positions as _p
    if _p.quiet(): return "quiet"
    out = []
    for i, body in enumerate((rec["message_1"], rec["message_2"])):
        hdr = {"Title": f"{rec['symbol']} {rec['chain'].upper()} paper flag"} if i else {}
        for attempt in range(3):
            try:
                r = requests.post(f"https://ntfy.sh/{topic}", data=body.encode(), headers=hdr, timeout=15)
                if r.status_code < 300: out.append("ok"); break
            except requests.RequestException: pass
            time.sleep(2 * (attempt + 1))
        else: out.append("fail")
        time.sleep(1)   # keep message order
    return "/".join(out)

def prune_cache():
    cut = time.time() - 86400
    for fn in os.listdir(CACHE):
        p = os.path.join(CACHE, fn)
        if fn.endswith(".json") and len(fn) > 40 and os.path.getmtime(p) < cut:
            try: os.remove(p)
            except OSError: pass

def heartbeat(st, cyc_s, gates, survivors, err):
    try: st["stats"]["ds_rows_buffer"] = dsrows.trim()
    except Exception: pass
    calls = calls_last_hour()
    bst = desk._be_state(); day = desk.now_utc().strftime("%Y-%m-%d"); mon = desk.now_utc().strftime("%Y-%m")
    hb = {"pid": os.getpid(), "status": "ok" if not err else "error", **stamp(), "unix": int(time.time()),
          "cycle_s": round(cyc_s, 1), "cycles": st["stats"].get("cycles", 0), "watchlist": len(st["watch"]),
          "survivors_last_cycle": survivors, "audits_total": st["stats"].get("audits", 0), "flags_total": st["stats"].get("flags", 0),
          "api_calls_last_hour": sum(calls.values()), "api_calls_by_host": calls,
          "birdeye": {"cu_today": bst.get("cu_day", {}).get(day, 0), "cu_cap_day": desk.BE_DAILY_CU, "cu_month": bst.get("cu_month", {}).get(mon, 0),
                      "calls_month": bst.get("month", {}).get(mon, 0), "key_present": bool(os.environ.get("BIRDEYE_API_KEY"))},
          "gates": {k: gates.get(k) for k in ("ok", "reasons", "size_usd", "eth_24h", "pot_chg_day", "pot_chg_week", "loss_streak")},
          "webhook_configured": bool(webhook_cfg()[0]), "last_error": err}
    why = {}
    for w in st["watch"].values():
        k = (w.get("why") or "pending").split(":")[0].split("(")[0]
        why[k] = why.get(k, 0) + 1
    L = st.get("long", {})
    hb["long_watch"] = {"n": len(L), "armed": sum(1 for e in L.values() if e.get("state") == "armed"),
                        "by_chain": {c: sum(1 for e in L.values() if e["chain"] == c) for c in ("base", SOL)}}
    hb["watch_by_chain"] = {c: sum(1 for w in st["watch"].values() if w.get("chain", "base") == c) for c in ("base", SOL)}
    hb["positions"] = {"loaded": st.get("pos_loaded", []), **{k[:6]: {x: v.get(x) for x in ("mcap", "liq", "high", "fired")} for k, v in st.get("pos", {}).items()}}
    hb["reentry"] = {"loaded": st.get("reentry_loaded", []), **{k[:6]: {x: v.get(x) for x in ("peak_mcap", "floor_mcap", "floor_days", "dd", "mcap", "why")} for k, v in st.get("reentry", {}).items()}}
    hb["user_alerts"] = {"loaded": st.get("user_alerts_loaded", []), **{k[:6] + k.split(":")[-1]: v for k, v in st.get("user_alerts", {}).items()}}
    hb["study"] = {"loaded": st.get("study_loaded", []), **{k[:6]: {"high": v.get("high"), "fired": v.get("fired"), "last": v.get("last")} for k, v in st.get("study", {}).items()}}
    sm = st.get("smart", {}); hb["smart_wallets"] = {"tracked": sm.get("tracked"), "hits": sm.get("hits", [])[-5:], "pending": len(sm.get("buys", {})), "evm_err": sm.get("evm_err"), "proven_n": sm.get("proven_n"), "high_score_n": sm.get("high_score_n"), "proven_buys": len(sm.get("proven_buys") or {})}
    hb["watch_reasons"] = dict(sorted(why.items(), key=lambda kv: -kv[1])[:12])
    tmp = HEART_F + ".tmp"; json.dump(hb, open(tmp, "w"), indent=1); os.replace(tmp, HEART_F)

def smart_push(tok, ch, hit):
    """Only used when smart_wallets.json mode == "push". Safety must PASS first; then CA-only message, then details."""
    import positions
    if ch == SOL:
        v = desk.sol_audit(tok).get("verdict")
    else:
        import evm; v = evm.audit_basic(tok, ch).get("verdict")
    if v != "PASS": return f"blocked: safety {v}"
    pairs = desk.ds_token_pairs([tok], chain=ch) or []
    p = max(pairs, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0)) if pairs else {}
    text = (f"{(p.get('baseToken') or {}).get('symbol', '?')} ({ch}): {len(hit['wallets'])} tracked wallets bought in the last 6h\n"
            f"Who: {'; '.join(hit.get('names') or [])}\n"
            f"mcap {desk.f(p.get('marketCap')) or '?'} | liq {desk.f((p.get('liquidity') or {}).get('usd'))} | safety PASS\n{p.get('url') or ''}")
    return positions.push_pair(tok, text, "SMART WALLETS", log)

def main():
    setup_log()
    desk.BE_BUDGET = 10 ** 9   # per-run cap replaced by desk's hourly-call and daily-CU caps
    st = load_state(); st.setdefault("stats", {})
    log.info("watcher start pid=%s (birdeye key %s)", os.getpid(), "present" if os.environ.get("BIRDEYE_API_KEY") else "absent")
    stop = []
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
    t_start = time.time()
    while not stop and not (RUN_SECONDS and time.time() - t_start > RUN_SECONDS - CYCLE_S):
        t0 = time.time(); err = None; survivors = 0; gates = {}
        desk.WARN.clear(); desk.BE_RUN["calls"] = 0; desk.BE_RUN["notes"] = set()
        try:
            if due(st, "bs_retry", EVERY["bs_retry"]): desk._BS_DOWN.clear()
            if due(st, "eth", EVERY["eth"]) or "eth_chg" not in st: eth_change(st)
            gap = time.time() - st.get("last_cycle_end", time.time())
            if gap > GAP_CATCHUP_S: catch_up(st, gap)
            discover(st)
            sv = prefilter(st); survivors = len(sv)
            gates = account_gates(st)
            for k in [k for k, ts in st["flagged"].items() if time.time() - ts > 86400]: st["flagged"].pop(k)
            for tok in sv:
                if tok in st["flagged"]: continue          # 24h dedupe
                if time.time() - t0 > 240: break             # keep cycles bounded
                try:
                    rec = evaluate(st, tok, gates)
                    if rec: flag(st, rec)
                    try: dsrows.evaluated(tok, st["watch"][tok])
                    except Exception: pass
                except Exception as e:
                    log.warning("eval %s: %s", tok, f"{type(e).__name__}: {e}"[:200])
            if time.time() - t0 < 200:
                for rec in floor_watch(st, gates, budget_s=min(40, 230 - (time.time() - t0))):
                    flag(st, rec)
            if due(st, "prune", EVERY["prune"]): prune_cache()
            if desk.WARN: log.info("warnings: %s", "; ".join(sorted(set(desk.WARN)))[:400])
            if desk.BE_RUN["notes"]: log.info("birdeye: %s", "; ".join(sorted(desk.BE_RUN["notes"]))[:300])
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:300]
            log.error("cycle error: %s\n%s", err, traceback.format_exc()[-1200:])
        st["stats"]["cycles"] = st["stats"].get("cycles", 0) + 1; st["last_cycle_end"] = time.time()
        cyc = time.time() - t0
        try:
            try:
                import positions
                if os.environ.get("DESK_FASTLANE") == "1":       # levels / stops / user alerts / liquidity drops run in the fastlane workflow
                    positions.sync_loaded(st); positions.reentry_check(st, log, None); positions.study(st, log)
                else:
                    positions.check(st, log); positions.reentry_check(st, log, None); positions.user_alerts(st, log); positions.study(st, log); positions.liq_watch(st, log)
            except Exception as e:
                log.warning("position watch: %s", f"{type(e).__name__}: {e}"[:200])
            try:
                import smart; smart.detect(st, log, push_fn=smart_push)
            except Exception as e:
                log.warning("smart wallets: %s", f"{type(e).__name__}: {e}"[:200])
            try:
                import copybook; copybook.step(st, log)          # LOG-ONLY shadow copy-top-traders book, never pushes
            except Exception as e:
                log.warning("copybook: %s", f"{type(e).__name__}: {e}"[:200])
            save_state(st); heartbeat(st, cyc, gates, survivors, err)
        except Exception as e:
            log.error("state/heartbeat write failed: %s", e)
        if st["stats"]["cycles"] % 10 == 1:
            log.info("cycle %s %.1fs watch=%s survivors=%s flags=%s calls/h=%s", st["stats"]["cycles"], cyc, len(st["watch"]),
                     survivors, st["stats"].get("flags", 0), sum(calls_last_hour().values()))
        sl = max(5, CYCLE_S - cyc)
        while sl > 0 and not stop:
            time.sleep(min(5, sl)); sl -= 5
    log.info("watcher stop (SIGTERM)")

if __name__ == "__main__":
    main()
