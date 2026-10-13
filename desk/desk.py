#!/usr/bin/env python3
"""Small-Pot Base Desk data toolkit. Read-only, free, no-key public APIs only.

Never signs, trades, connects a wallet, or creates accounts.
Commands: scan | audit <token> | wallets <token> | history <pair|token> --since ISO | quote <pair|token>
All output is compact JSON on stdout. See README.md.
"""
import argparse, hashlib, json, os, re, sys, time, datetime as dt
from zoneinfo import ZoneInfo
import requests

CHAIN = "base"; CHAIN_ID = 8453
PT = ZoneInfo("America/Los_Angeles")
UA = {"User-Agent": "small-pot-base-desk/1.0 (read-only research)", "Accept": "application/json"}
CACHE_DIR = os.environ.get("DESK_CACHE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
GT = "https://api.geckoterminal.com/api/v2"
DS = "https://api.dexscreener.com"
HP = "https://api.honeypot.is/v2/IsHoneypot"
GP = "https://api.gopluslabs.io/api/v1/token_security/8453"
BS = "https://base.blockscout.com/api/v2"           # main holder source when reachable (Cloudflare-blocked from the box as of 2026-10-11)
BE = "https://public-api.birdeye.so"                 # optional; needs env BIRDEYE_API_KEY (free tier: 1 req/s, ~30k CU/month)
BE_BUDGET = int(os.environ.get("DESK_BIRDEYE_BUDGET", "40"))  # max Birdeye calls per run
BE_STATE = os.environ.get("DESK_BE_STATE") or os.path.join(CACHE_DIR, "birdeye_state.json")
BE_HOURLY_CALLS = int(os.environ.get("DESK_BIRDEYE_HOURLY_CALLS", "40"))   # shared by CLI + watcher
BE_DAILY_CU = int(os.environ.get("DESK_BIRDEYE_DAILY_CU", "800"))         # 800 x 31 = 24.8k CU/month
# Birdeye compute-unit prices (docs.birdeye.so, Oct 2026); unknown endpoints assumed 30
BE_CU = {"/defi/price": 10, "/defi/token_overview": 20, "/defi/token_trending": 40, "/defi/v2/tokens/new_listing": 30,
         "/defi/v2/tokens/top_traders": 35, "/wallet/v2/pnl/summary": 30, "/defi/v3/token/txs": 20,
         "/defi/v3/token/trade-data/single": 15, "/defi/v3/token/market-data": 15}
CLANKER = "https://www.clanker.world/api/tokens"
ZORA = "https://api-sdk.zora.engineering/coin"
RPC_RECENT = "https://base-rpc.publicnode.com"      # getLogs: only ~last 10,000 blocks, big ranges OK
RPC_DEEP = "https://base.gateway.tenderly.co"       # getLogs: any depth, max 1,000-block ranges
RPC_DEPTH = 9500
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
V4_POOL_MANAGER = "0x498581ff718922c3f8e6a244956af099b2652b2b"
BURN = {"0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead",
        "0xdead000000000000000042069420694206942069"}
MAJORS = {"0x4200000000000000000000000000000000000006", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
          "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf", "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",
          "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2", "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22",
          "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca", "0x0000000000000000000000000000000000000000",
          "0x1111111111166b7fe7bd91427724b487980afc69", "0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42"}
RUNGS = [500e3, 1e6, 2.5e6, 5e6, 10e6, 20e6, 30e6, 50e6]
# min seconds between calls per host (free-tier politeness; GT free ~30/min, GoPlus no-key ~30/min, honeypot.is 50/window)
THROTTLE = {"api.geckoterminal.com": 2.1, "api.dexscreener.com": 0.3, "api.gopluslabs.io": 2.0,
            "api.honeypot.is": 1.0, "base-rpc.publicnode.com": 0.15, "base.gateway.tenderly.co": 0.15,
            "public-api.birdeye.so": 1.1, "base.blockscout.com": 0.5,
            "www.clanker.world": 1.0, "api-sdk.zora.engineering": 1.0,
            "api.rugcheck.xyz": 1.5, "api.jup.ag": 1.1, "lite-api.jup.ag": 1.1}
_last = {}
WARN = []
USE_CACHE = True
USE_BE = True
BE_RUN = {"calls": 0, "notes": set()}

def now_utc(): return dt.datetime.now(dt.timezone.utc)
def pt(ts):
    if ts is None: return None
    if isinstance(ts, (int, float)): ts = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    return ts.astimezone(PT).strftime("%Y-%m-%d %H:%M PT")
def f(x, d=None):
    try: return float(x) if x not in (None, "") else d
    except (TypeError, ValueError): return d
def r0(x): return None if x is None else round(x)
def sig(x, n=4): return None if x is None else float(f"{x:.{n}g}")
def low(a): return (a or "").lower()

def _throttle(host):
    gap = THROTTLE.get(host, 0.3); t = time.time() - _last.get(host, 0)
    if t < gap: time.sleep(gap - t)
    _last[host] = time.time()

def get(url, ttl=90, ok404=False):
    """GET JSON with per-host throttle, retries/backoff on 429/5xx, small disk cache."""
    key = os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".json")
    if USE_CACHE and ttl and os.path.exists(key) and time.time() - os.path.getmtime(key) < ttl:
        try: return json.load(open(key))
        except Exception: pass
    host = url.split("/")[2]; delay = 2
    for attempt in range(4):
        _throttle(host)
        try:
            r = requests.get(url, headers=UA, timeout=25)
        except requests.RequestException as e:
            err = str(e)[:120]; time.sleep(delay); delay *= 2; continue
        if r.status_code == 200:
            try: data = r.json()
            except ValueError:
                err = "non-JSON (blocked?)"; break
            if USE_CACHE and ttl:
                os.makedirs(CACHE_DIR, exist_ok=True); json.dump(data, open(key, "w"))
            return data
        if r.status_code == 404 and ok404:
            try: return {"_404": True, **(r.json() if r.headers.get("content-type", "").startswith("application/json") else {})}
            except ValueError: return {"_404": True}
        err = f"HTTP {r.status_code}"
        if r.status_code in (429, 500, 502, 503, 504):
            ra = f(r.headers.get("retry-after"), 0) or 0
            time.sleep(max(delay, min(ra, 60))); delay *= 2; continue
        break
    WARN.append(f"{host}: {err}")
    return None

def rpc(method, params, url=None):
    urls = [url] if url else [RPC_RECENT, RPC_DEEP]
    for u in urls:
        delay = 1
        for attempt in range(3):
            _throttle(u.split("/")[2])
            try:
                j = requests.post(u, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                  headers=UA, timeout=30).json()
            except Exception as e:
                j = {"error": {"message": str(e)[:100]}}
            if "result" in j: return j["result"]
            msg = str(j.get("error", {}).get("message", ""))
            if "limit" in msg.lower() and "range" not in msg.lower():
                time.sleep(delay); delay *= 2; continue
            break
    raise RuntimeError(f"rpc {method} failed: {msg[:120]}")

def call(to, data, block="latest"):
    try: return rpc("eth_call", [{"to": to, "data": data}, block])
    except RuntimeError: return None

def decimals(token):
    r = call(token, "0x313ce567")
    try: return int(r, 16)
    except (TypeError, ValueError): return None

def block_number(): return int(rpc("eth_blockNumber", []), 16)

def transfer_logs(token, from_block, to_block):
    """Transfer logs via free RPCs: publicnode for the recent ~10k blocks, tenderly (1k-block chunks) for older."""
    logs = []; recent_floor = to_block - RPC_DEPTH
    lo = max(from_block, recent_floor)
    if lo <= to_block:
        try:
            logs += rpc("eth_getLogs", [{"address": token, "topics": [TRANSFER], "fromBlock": hex(lo), "toBlock": hex(to_block)}], RPC_RECENT)
            to_block = lo - 1
        except RuntimeError as e:
            WARN.append(f"publicnode getLogs: {e}; falling back to tenderly")
    b = from_block
    while b <= to_block:
        e = min(b + 999, to_block)
        logs += rpc("eth_getLogs", [{"address": token, "topics": [TRANSFER], "fromBlock": hex(b), "toBlock": hex(e)}], RPC_DEEP)
        b = e + 1
    return logs

def onchain(token):
    """Bytecode facts straight from a public RPC: contract?, EIP-1167 clone, EIP-1967 upgradeable slot, owner()."""
    out = {}
    try:
        code = rpc("eth_getCode", [token, "latest"]); out["code_bytes"] = (len(code) - 2) // 2
        if code.startswith("0x363d3d373d3d3d363d73"): out["clone_of"] = "0x" + code[22:62]
        slot = rpc("eth_getStorageAt", [token, "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc", "latest"])
        out["eip1967_impl"] = ("0x" + slot[-40:]) if int(slot, 16) else None
    except RuntimeError as e:
        out["error"] = str(e)[:100]
    o = call(token, "0x8da5cb5b")
    out["owner_call"] = ("0x" + o[-40:]) if o and len(o) >= 66 else "no owner()"
    return out

def balance_of(token, holder):
    r = call(token, "0x70a08231" + "0" * 24 + holder[2:].lower())
    try: return int(r, 16)
    except (TypeError, ValueError): return None

def deployer_linked(token, creator, extra=(), hours=48, max_wallets=25):
    """Deployer + wallets it sent tokens to directly (Transfer from=creator over `hours`) + extra addresses
    (owner, launchpad admin). Returns combined % of supply they hold now. Read-only RPC."""
    out = {"wallets": [], "pct_total": None, "window_h": hours, "error": None}
    try:
        dec = decimals(token); ts = call(token, "0x18160ddd")
        supply = int(ts, 16) if ts else 0
        if dec is None or not supply: raise RuntimeError("decimals/totalSupply call failed")
        linked = {low(a) for a in ([creator] + list(extra)) if a and is_addr(a) and low(a) not in BURN}
        if creator and is_addr(creator):
            bn = block_number(); start = bn - int(hours * 1800)
            topic_from = "0x" + "0" * 24 + creator[2:].lower()
            lo = max(start, bn - RPC_DEPTH); logs = []
            try:
                logs += rpc("eth_getLogs", [{"address": token, "topics": [TRANSFER, topic_from], "fromBlock": hex(lo), "toBlock": hex(bn)}], RPC_RECENT); top = lo - 1
            except RuntimeError: top = bn
            b = start
            while b <= top:
                e = min(b + 999, top)
                logs += rpc("eth_getLogs", [{"address": token, "topics": [TRANSFER, topic_from], "fromBlock": hex(b), "toBlock": hex(e)}], RPC_DEEP)
                b = e + 1
            for lg in logs:
                if len(lg["topics"]) >= 3: linked.add("0x" + lg["topics"][2][-40:])
        tot = 0.0
        for w in sorted(linked)[:max_wallets]:
            if w in BURN or w == V4_POOL_MANAGER: continue
            bal = balance_of(token, w)
            if bal is None: continue
            pct = bal / supply * 100; tot += pct
            if pct > 0.01: out["wallets"].append({"address": w, "pct": round(pct, 3), "deployer": w == low(creator)})
        out["pct_total"] = round(tot, 3); out["checked"] = min(len(linked), max_wallets)
        if len(linked) > max_wallets: out["truncated"] = len(linked)
    except RuntimeError as e:
        out["error"] = str(e)[:120]
    return out

def is_addr(s): return s.startswith("0x") and len(s) == 42
def is_pool_id(s): return s.startswith("0x") and len(s) == 66

# ---------------------------------------------------------------- Birdeye (optional)
def _be_state():
    try: return json.load(open(BE_STATE))
    except Exception: return {"unsupported": {}, "month": {}}

def be_get(path, params, ttl=120, chain=None):
    """Birdeye GET on Base. Returns `data` or None. Never raises; never logs the key.
    Skips when: no key, --no-birdeye, per-run budget spent, or endpoint known unsupported (re-tested after 7 days)."""
    key = os.environ.get("BIRDEYE_API_KEY")
    if not USE_BE: return None
    if not key: BE_RUN["notes"].add("birdeye skipped: BIRDEYE_API_KEY not set"); return None
    st = _be_state(); un = st.get("unsupported", {})
    if path in un and time.time() - un[path]["at"] < 7 * 86400:
        BE_RUN["notes"].add(f"birdeye {path}: skipped ({un[path]['why']})"); return None
    chain = chain or CHAIN
    ck = os.path.join(CACHE_DIR, "be_" + hashlib.sha1(((chain if chain != CHAIN else "") + path + json.dumps(params, sort_keys=True)).encode()).hexdigest() + ".json")
    if USE_CACHE and os.path.exists(ck) and time.time() - os.path.getmtime(ck) < ttl:
        try: return json.load(open(ck))
        except Exception: pass
    delay = 2
    for attempt in range(3):
        if BE_RUN["calls"] >= BE_BUDGET:
            BE_RUN["notes"].add(f"birdeye budget of {BE_BUDGET} calls reached; rest skipped"); return None
        st = _be_state(); n = now_utc(); hr = n.strftime("%Y-%m-%dT%H"); day = n.strftime("%Y-%m-%d")
        cu = BE_CU.get(path, 30)
        if st.get("hour", {}).get(hr, 0) >= BE_HOURLY_CALLS:
            BE_RUN["notes"].add(f"birdeye hourly cap {BE_HOURLY_CALLS} calls reached; skipped"); return None
        if st.get("cu_day", {}).get(day, 0) + cu > BE_DAILY_CU:
            BE_RUN["notes"].add(f"birdeye daily cap {BE_DAILY_CU} CU reached; skipped"); return None
        _throttle("public-api.birdeye.so"); BE_RUN["calls"] += 1
        mon = n.strftime("%Y-%m")
        for k in ("month", "hour", "cu_day", "cu_month"): st.setdefault(k, {})
        st["month"][mon] = st["month"].get(mon, 0) + 1
        st["hour"][hr] = st["hour"].get(hr, 0) + 1
        st["cu_day"][day] = st["cu_day"].get(day, 0) + cu
        st["cu_month"][mon] = st["cu_month"].get(mon, 0) + cu
        st["hour"] = {k: v for k, v in st["hour"].items() if k >= (n - dt.timedelta(hours=48)).strftime("%Y-%m-%dT%H")}
        st["cu_day"] = {k: v for k, v in st["cu_day"].items() if k >= (n - dt.timedelta(days=40)).strftime("%Y-%m-%d")}
        os.makedirs(CACHE_DIR, exist_ok=True); json.dump(st, open(BE_STATE, "w"))
        try:
            r = requests.get(BE + path, params=params, timeout=20,
                             headers={"X-API-KEY": key, "x-chain": chain, "accept": "application/json", "User-Agent": UA["User-Agent"]})
        except requests.RequestException as e:
            BE_RUN["notes"].add(f"birdeye {path}: network error {type(e).__name__}"); time.sleep(delay); delay *= 2; continue
        try: j = r.json()
        except ValueError: j = {}
        msg = str(j.get("message", ""))[:80]
        if r.status_code == 200 and j.get("success", True) and j.get("data") is not None:
            if USE_CACHE: json.dump(j["data"], open(ck, "w"))
            return j["data"]
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(delay); delay *= 2; continue
        if r.status_code in (400, 401, 403, 404) and ("not supported" in msg.lower() or "permission" in msg.lower() or r.status_code == 404):
            st["unsupported"][path] = {"why": f"HTTP {r.status_code} {msg}", "at": time.time()}
            json.dump(st, open(BE_STATE, "w"))
        BE_RUN["notes"].add(f"birdeye {path}: HTTP {r.status_code} {msg}")
        return None
    return None

def be_overview(tok):
    d = be_get("/defi/token_overview", {"address": tok})
    if not d: return None
    return {"price_usd": sig(f(d.get("price")), 6), "mcap_usd": r0(f(d.get("marketCap"))), "liq_usd_birdeye": r0(f(d.get("liquidity"))),
            "holders": d.get("holder"), "unique_wallets_1h": d.get("uniqueWallet1h"), "unique_wallets_24h": d.get("uniqueWallet24h"),
            "buy_usd_1h": r0(f(d.get("vBuy1hUSD"))), "sell_usd_1h": r0(f(d.get("vSell1hUSD"))),
            "buy_usd_24h": r0(f(d.get("vBuy24hUSD"))), "sell_usd_24h": r0(f(d.get("vSell24hUSD"))),
            "chg_1h": sig(f(d.get("priceChange1hPercent")), 3), "chg_24h": sig(f(d.get("priceChange24hPercent")), 3)}

def be_wallet_quality(w):
    d = be_get("/wallet/v2/pnl/summary", {"wallet": w}, ttl=6 * 3600)
    if not d or "summary" not in d: return None
    sm = d["summary"]; c = sm.get("counts", {}); pn = sm.get("pnl", {})
    trades = c.get("total_trade") or 0; wr = f(c.get("win_rate")); rp = f(pn.get("realized_profit_usd"))
    return {"address": low(w), "trades": trades, "win_rate": sig(wr, 2), "realized_pnl_usd": r0(rp), "tokens": sm.get("unique_tokens"),
            "proven": bool(trades >= 20 and (wr or 0) >= 0.5 and (rp or 0) > 0)}

# ---------------------------------------------------------------- launchpads (free, no key)
def launchpad(tok):
    """Is this a Clanker v4 or Zora coin? Both launchpads lock the LP by design. Returns dict or None."""
    c = get(f"{CLANKER}?q={tok}", ttl=6 * 3600)
    for x in (c or {}).get("data") or []:
        if low(x.get("contract_address")) == low(tok) and x.get("chain_id") == CHAIN_ID and x.get("locker_address"):
            return {"launchpad": x.get("type") or "clanker", "locker": low(x["locker_address"]), "admin": low(x.get("admin")),
                    "source": "clanker.world API"}
    z = get(f"{ZORA}?address={tok}&chain={CHAIN_ID}", ttl=6 * 3600)
    zt = (z or {}).get("zora20Token")
    if zt and low(zt.get("address")) == low(tok):
        return {"launchpad": "zora_" + low(zt.get("coinType") or "coin"), "creator": low(zt.get("creatorAddress")), "source": "Zora API"}
    return None

# ---------------------------------------------------------------- holders
_BS_DOWN = []
def blockscout_holders(tok):
    """Top holders from Blockscout (main source). None if unreachable."""
    if _BS_DOWN: return None
    t = get(f"{BS}/tokens/{tok}", ttl=300)
    if not isinstance(t, dict) or not t.get("total_supply"): _BS_DOWN.append(1); return None
    h = get(f"{BS}/tokens/{tok}/holders", ttl=120)
    if not isinstance(h, dict) or "items" not in h: _BS_DOWN.append(1); return None
    sup = f(t["total_supply"], 0); dec = int(t.get("decimals") or 18)
    if not sup: return None
    hs = [{"address": low(i["address"]["hash"]), "percent": f(i.get("value"), 0) / sup, "is_contract": 1 if i["address"].get("is_contract") else 0,
           "tag": i["address"].get("name") or "", "is_locked": 0} for i in h["items"][:20]]
    hc = t.get("holders_count") or t.get("holders")
    return {"holders": hs, "holder_count": int(hc) if hc else None, "total_supply": sup / 10 ** dec, "source": "blockscout"}

def holders_for(tok, gp):
    """Blockscout first; GoPlus top-10 as fallback. GoPlus still supplies locked flags/tags when both exist."""
    b = blockscout_holders(tok)
    if b:
        gtags = {low(h["address"]): h for h in (gp or {}).get("holders") or []}
        for h in b["holders"]:
            g = gtags.get(h["address"])
            if g: h["is_locked"] = g.get("is_locked", 0); h["tag"] = h["tag"] or g.get("tag", "")
        return b
    if gp and gp.get("holders"):
        return {"holders": gp["holders"], "holder_count": int(gp["holder_count"]) if gp.get("holder_count") else None,
                "total_supply": f(gp.get("total_supply")), "source": "goplus(top-10; blockscout unreachable)"}
    return None

# ---------------------------------------------------------------- market data
def ds_token_pairs(tokens, chain=None):
    """DexScreener pairs for up to 30 tokens per call."""
    out = []; chain = chain or CHAIN
    for i in range(0, len(tokens), 30):
        d = get(f"{DS}/tokens/v1/{chain}/{','.join(tokens[i:i+30])}")
        if isinstance(d, list): out += [p for p in d if p.get("chainId") == chain]
    return out

def ds_pair(pair):
    d = get(f"{DS}/latest/dex/pairs/{CHAIN}/{pair}", ttl=30)
    ps = (d or {}).get("pairs") or ([d["pair"]] if (d or {}).get("pair") else [])
    return ps[0] if ps else None

def gt_pool(pool):
    d = get(f"{GT}/networks/{CHAIN}/pools/{pool}?include=base_token,quote_token", ttl=30, ok404=True)
    if not d or d.get("_404"): return None
    return d

def gt_token_top_pool(token):
    d = get(f"{GT}/networks/{CHAIN}/tokens/{token}/pools?page=1", ttl=120, ok404=True)
    if not d or d.get("_404") or not d.get("data"): return None
    return d["data"][0]["attributes"]["address"]

def meme_side(base_addr, quote_addr):
    b, q = low(base_addr), low(quote_addr)
    if b in MAJORS and q not in MAJORS: return q
    return b

def resolve(target):
    """Return dict(token, pair, ds, gt) for a pair address, v4 pool id, or token address."""
    t = target.strip()
    res = {"input": t, "token": None, "pair": None, "ds": None, "gt": None}
    p = ds_pair(t)
    if p:
        res.update(pair=low(p["pairAddress"]), ds=p,
                   token=meme_side(p["baseToken"]["address"], p["quoteToken"]["address"]))
    elif is_addr(t):
        ps = [x for x in ds_token_pairs([t]) if low(x["baseToken"]["address"]) == low(t) or low(x["quoteToken"]["address"]) == low(t)]
        if ps:
            best = max(ps, key=lambda x: f((x.get("liquidity") or {}).get("usd"), 0))
            res.update(pair=low(best["pairAddress"]), ds=best, token=low(t))
    if not res["pair"]:
        g = gt_pool(t)
        if g:
            res["pair"] = low(t)
        elif is_addr(t):
            res["token"] = low(t); res["pair"] = gt_token_top_pool(t)
    if res["pair"] and not res["gt"]:
        g = gt_pool(res["pair"])
        if g:
            res["gt"] = g
            if not res["token"]:
                rel = g["data"]["relationships"]
                res["token"] = meme_side(rel["base_token"]["data"]["id"].split("_", 1)[1], rel["quote_token"]["data"]["id"].split("_", 1)[1])
    return res

def snapshot(res):
    """Normalized live numbers from DexScreener (preferred) or GeckoTerminal."""
    p, g = res.get("ds"), res.get("gt")
    if p:
        price = f(p.get("priceUsd")); mc = f(p.get("marketCap")); fdv = f(p.get("fdv"))
        tx = (p.get("txns") or {}).get("h24") or {}
        return {"symbol": p["baseToken"]["symbol"] if low(p["baseToken"]["address"]) == res["token"] else p["quoteToken"]["symbol"],
                "price_usd": sig(price, 6), "mcap_usd": r0(mc or fdv), "fdv_usd": r0(fdv), "mcap_is_fdv": mc is None,
                "liq_usd": r0(f((p.get("liquidity") or {}).get("usd"))), "vol24_usd": r0(f((p.get("volume") or {}).get("h24"))),
                "buys24": tx.get("buys"), "sells24": tx.get("sells"),
                "chg_h1": (p.get("priceChange") or {}).get("h1"), "chg_h24": (p.get("priceChange") or {}).get("h24"),
                "dex": p.get("dexId") + ("-" + ",".join(p.get("labels") or []) if p.get("labels") else ""),
                "pair_created": pt(p["pairCreatedAt"] / 1000) if p.get("pairCreatedAt") else None,
                "source": "dexscreener"}
    if g:
        a = g["data"]["attributes"]; tx = a["transactions"]["h24"]
        inc = {x["id"]: x["attributes"] for x in g.get("included", [])}
        tokattr = inc.get(f"{CHAIN}_{res['token']}", {})
        is_base = g["data"]["relationships"]["base_token"]["data"]["id"].endswith(res["token"] or "~")
        price = f(a["base_token_price_usd"] if is_base else a["quote_token_price_usd"])
        mc = f(a.get("market_cap_usd")); fdv = f(a.get("fdv_usd"))
        return {"symbol": tokattr.get("symbol"), "price_usd": sig(price, 6), "mcap_usd": r0(mc or fdv), "fdv_usd": r0(fdv),
                "mcap_is_fdv": mc is None, "liq_usd": r0(f(a.get("reserve_in_usd"))), "vol24_usd": r0(f(a["volume_usd"]["h24"])),
                "buys24": tx.get("buys"), "sells24": tx.get("sells"), "buyers24": tx.get("buyers"), "sellers24": tx.get("sellers"),
                "chg_h1": f(a["price_change_percentage"].get("h1")), "chg_h24": f(a["price_change_percentage"].get("h24")),
                "dex": g["data"]["relationships"]["dex"]["data"]["id"], "pair_created": pt(dt.datetime.fromisoformat(a["pool_created_at"].replace("Z", "+00:00"))) if a.get("pool_created_at") else None,
                "source": "geckoterminal"}
    return None

# ---------------------------------------------------------------- commands
def cmd_quote(a):
    res = resolve(a.target)
    s = snapshot(res)
    if not s:
        be = be_overview(res["token"] or a.target) if is_addr(a.target) else None
        if be: return {"cmd": "quote", "token": low(a.target), **be, "source": "birdeye", "at": pt(now_utc())}
        return {"cmd": "quote", "input": a.target, "status": "NOT_FOUND", "note": "no live pair on DexScreener, GeckoTerminal or Birdeye"}
    out = {"cmd": "quote", "token": res["token"], "pair": res["pair"], **s, "at": pt(now_utc())}
    be = be_overview(res["token"]) if res["token"] else None
    if be: out["birdeye"] = be
    return out

def cmd_scan(a):
    now = now_utc(); cands = {}  # token -> gt pool attrs (for unique buyer counts)
    def add_gt(data):
        for p in data or []:
            rel = p["relationships"]
            tok = meme_side(rel["base_token"]["data"]["id"].split("_", 1)[1], rel["quote_token"]["data"]["id"].split("_", 1)[1])
            if tok in MAJORS: continue
            at = p["attributes"]; ca = at.get("pool_created_at")
            if ca:
                age = (now - dt.datetime.fromisoformat(ca.replace("Z", "+00:00"))).total_seconds() / 3600
                if age > a.max_age + 24: continue  # pool clearly too old; token may still have a newer pool elsewhere
            cands.setdefault(tok, at)
    for pg in range(1, a.pages + 1):
        add_gt((get(f"{GT}/networks/{CHAIN}/pools?sort=h24_volume_usd_desc&page={pg}", ttl=300) or {}).get("data"))
    for pg in range(1, 3):
        add_gt((get(f"{GT}/networks/{CHAIN}/pools?sort=h24_tx_count_desc&page={pg}", ttl=300) or {}).get("data"))
    for pg in (8, 9, 10):  # oldest end of the new-pools feed (~2h old)
        add_gt((get(f"{GT}/networks/{CHAIN}/new_pools?page={pg}", ttl=300) or {}).get("data"))
    for d in ("6h", "24h"):
        add_gt((get(f"{GT}/networks/{CHAIN}/trending_pools?duration={d}&page=1", ttl=300) or {}).get("data"))
    for u in ("/token-boosts/latest/v1", "/token-boosts/top/v1", "/token-profiles/latest/v1"):
        for x in get(DS + u, ttl=300) or []:
            if x.get("chainId") == CHAIN and is_addr(x.get("tokenAddress", "")): cands.setdefault(low(x["tokenAddress"]), {})
    pairs = ds_token_pairs(list(cands))
    by_tok = {}
    for p in pairs:
        tok = meme_side(p["baseToken"]["address"], p["quoteToken"]["address"])
        if tok in cands: by_tok.setdefault(tok, []).append(p)
    out, skipped = [], {"age": 0, "liquidity": 0, "volume": 0, "txns": 0, "no_pair": len(cands) - len(by_tok)}
    for tok, ps in by_tok.items():
        created = [p["pairCreatedAt"] for p in ps if p.get("pairCreatedAt")]
        if not created: skipped["no_pair"] += 1; continue
        age = (now.timestamp() * 1000 - min(created)) / 3.6e6
        best = max(ps, key=lambda x: f((x.get("liquidity") or {}).get("usd"), 0))
        liq = f((best.get("liquidity") or {}).get("usd"), 0)
        vol = sum(f((p.get("volume") or {}).get("h24"), 0) for p in ps)
        tx = best.get("txns", {}).get("h24", {}); ntx = (tx.get("buys") or 0) + (tx.get("sells") or 0)
        if not (a.min_age <= age <= a.max_age): skipped["age"] += 1; continue
        if liq < a.min_liq: skipped["liquidity"] += 1; continue
        if vol < a.min_vol: skipped["volume"] += 1; continue
        if ntx < a.min_txns: skipped["txns"] += 1; continue
        g = cands.get(tok) or {}
        gtx = (g.get("transactions") or {}).get("h24") or {}
        mc = f(best.get("marketCap")) or f(best.get("fdv"))
        zone = "preferred" if mc and mc < 400e3 else ("ok" if mc and mc <= 1e6 else ("skip_over_1M" if mc else "unknown"))
        out.append({"symbol": best["baseToken"]["symbol"] if low(best["baseToken"]["address"]) == tok else best["quoteToken"]["symbol"],
                    "token": tok, "pair": low(best["pairAddress"]), "dex": best["dexId"] + ("-" + best["labels"][0] if best.get("labels") else ""),
                    "age_h": round(age, 1), "mcap": r0(mc), "liq": r0(liq), "vol24": r0(vol), "buys24": tx.get("buys"), "sells24": tx.get("sells"),
                    "uniq_buyers24": gtx.get("buyers"), "uniq_sellers24": gtx.get("sellers"),
                    "chg_h1": (best.get("priceChange") or {}).get("h1"), "chg_h24": (best.get("priceChange") or {}).get("h24"),
                    "vol_liq": round(vol / liq, 1) if liq else None, "entry_zone": zone,
                    "flags": [x for x in ["mcap_below_liquidity(odd pricing)" if mc and mc < liq else None,
                                          "vol_liq>30(possible wash volume)" if liq and vol / liq > 30 else None] if x]})
    order = {"preferred": 0, "ok": 1, "unknown": 2, "skip_over_1M": 3}
    out.sort(key=lambda x: (order[x["entry_zone"]], -x["vol24"]))
    for r in [x for x in out if x["entry_zone"] != "skip_over_1M"][:a.birdeye_top]:
        be = be_overview(r["token"])
        if be: r["birdeye"] = {k: be[k] for k in ("holders", "unique_wallets_24h", "buy_usd_24h", "sell_usd_24h", "buy_usd_1h", "sell_usd_1h")}
    return {"cmd": "scan", "at": pt(now), "filters": {"age_h": [a.min_age, a.max_age], "min_liq": a.min_liq, "min_vol24": a.min_vol, "min_txns24": a.min_txns},
            "candidates_checked": len(cands), "skipped": skipped, "count": len(out), "results": out}

def goplus(token):
    d = get(f"{GP}?contract_addresses={token}", ttl=300)
    if not d or d.get("code") != 1: return None
    return (d.get("result") or {}).get(low(token))

def honeypot(token):
    d = get(f"{HP}?address={token}&chainID={CHAIN_ID}", ttl=300, ok404=True)
    return d

def excluded_holder(h, pools, gp):
    ad = low(h.get("address"))
    if ad in BURN: return "burn"
    if ad in pools: return "lp_pool"
    if ad == V4_POOL_MANAGER: return "v4_pool_manager(lp)"
    if h.get("is_locked") in (1, "1"): return "locked"
    tag = low(h.get("tag"))
    if any(k in tag for k in ("lock", "uniswap", "aerodrome", "pool", "pair", "burn")): return f"tag:{h.get('tag')}"
    return None

def cmd_audit(a):
    tok = low(a.token); checks = []
    def chk(name, status, detail): checks.append({"check": name, "status": status, "detail": detail})
    gp = goplus(tok); hp = honeypot(tok)
    hp_ok = isinstance(hp, dict) and not hp.get("_404") and "honeypotResult" in hp
    pairs = ds_token_pairs([tok])
    pools = {low(p["pairAddress"]) for p in pairs} | {low(x.get("pair")) for x in ((gp or {}).get("dex") or [])}
    liq = sum(f((p.get("liquidity") or {}).get("usd"), 0) for p in pairs) if pairs else None
    sym = (gp or {}).get("token_symbol") or ((hp or {}).get("token") or {}).get("symbol") if (gp or hp_ok) else None
    # 1 sellable
    if hp_ok and hp["honeypotResult"].get("isHoneypot"):
        chk("sellable", "REJECT", "honeypot.is: isHoneypot=true")
    elif hp_ok and hp.get("simulationSuccess"):
        chk("sellable", "PASS", "honeypot.is sell simulation succeeded")
    elif gp and gp.get("is_honeypot") == "1":
        chk("sellable", "REJECT", "GoPlus is_honeypot=1")
    elif gp and gp.get("cannot_sell_all") == "1":
        chk("sellable", "REJECT", "GoPlus cannot_sell_all=1")
    elif gp and gp.get("is_honeypot") == "0" and gp.get("sell_tax", "") != "":
        chk("sellable", "PASS", "GoPlus is_honeypot=0 with sell tax measured (honeypot.is: " + ("no pair/v4 unsupported" if (hp or {}).get("_404") else "unavailable") + ")")
    else:
        chk("sellable", "UNKNOWN", "no sell simulation: honeypot.is " + ("404 pair not found" if (hp or {}).get("_404") else "unavailable") + "; GoPlus tax/honeypot fields empty")
    # 2 taxes
    bt = st = None
    if hp_ok and hp.get("simulationResult"):
        bt, st = f(hp["simulationResult"].get("buyTax")), f(hp["simulationResult"].get("sellTax")); src = "honeypot.is"
    elif gp:
        bt, st = f(gp.get("buy_tax")), f(gp.get("sell_tax")); src = "GoPlus"
        if bt is not None: bt *= 100
        if st is not None: st *= 100
    if bt is None or st is None: chk("tax", "UNKNOWN", "buy/sell tax not measured")
    else:
        mx = max(bt, st)
        chk("tax", "REJECT" if mx > 10 else ("CAUTION" if mx > 5 else "PASS"), f"buy {bt:.1f}% / sell {st:.1f}% ({src})")
    if not gp:
        chk("goplus", "UNKNOWN", "GoPlus unavailable: owner powers, holders, LP unknown")
    else:
        # 3 verified source
        os_ = gp.get("is_open_source")
        chk("verified_source", {"1": "PASS", "0": "REJECT"}.get(os_, "UNKNOWN"), f"is_open_source={os_!r}")
        # 4 owner powers
        oc = onchain(tok)
        owner = low(gp.get("owner_address")) if "owner_address" in gp else None
        if not owner and is_addr(oc.get("owner_call", "")): owner = oc["owner_call"]
        renounced = owner is not None and (owner in BURN or owner == "")
        powers = {k: gp.get(k) for k in ("is_mintable", "is_blacklisted", "transfer_pausable", "slippage_modifiable",
                                           "personal_slippage_modifiable", "hidden_owner", "can_take_back_ownership",
                                           "owner_change_balance", "selfdestruct", "external_call", "trading_cooldown", "is_proxy")}
        live = [k for k, v in powers.items() if v == "1" and k != "is_proxy"]
        miss = [k for k, v in powers.items() if v in (None, "")]
        hard = {"hidden_owner", "can_take_back_ownership", "owner_change_balance", "selfdestruct"}
        if any(k in hard for k in live): st_ = "REJECT"
        elif live and not renounced: st_ = "REJECT"
        elif owner is None and len(miss) > 3: st_ = "UNKNOWN"
        elif live: st_ = "CAUTION"
        elif len(miss) > 3: st_ = "UNKNOWN"
        else: st_ = "PASS"
        chk("owner_powers", st_, {"owner": "unknown" if owner is None else (owner or "none found"), "powers_on": live, "missing_fields": miss})
        if oc.get("code_bytes") == 0: chk("bytecode", "REJECT", "no contract code at this address")
        if oc.get("eip1967_impl"): pst, pdt = "CAUTION", f"upgradeable proxy (EIP-1967), impl {oc['eip1967_impl']}: logic can change"
        elif powers["is_proxy"] == "1": pst, pdt = "CAUTION", "GoPlus is_proxy=1: logic may change"
        elif oc.get("clone_of"): pst, pdt = "PASS", f"EIP-1167 clone of {oc['clone_of']} (not upgradeable; audit covers the template)"
        elif powers["is_proxy"] == "0": pst, pdt = "PASS", "not a proxy (GoPlus + no EIP-1967 slot)"
        else: pst, pdt = "UNKNOWN", "proxy status unknown"
        chk("proxy", pst, pdt)
        # 5 holders
        hd = holders_for(tok, gp); hs = (hd or {}).get("holders") or []
        creator = low(gp.get("creator_address"))
        if not hs: chk("holder_concentration", "UNKNOWN", "no holder list")
        else:
            kept = [(low(h["address"]), f(h.get("percent"), 0) * 100, h.get("is_contract")) for h in hs if not excluded_holder(h, pools, gp)][:10]
            top = max((p for _, p, _ in kept), default=0); tot = sum(p for _, p, _ in kept)
            stt = "REJECT" if top >= 15 or tot >= 50 else ("CAUTION" if top >= 8 or tot >= 30 else "PASS")
            chk("holder_concentration", stt, {"largest_pct": round(top, 2), "top10_ex_lp_burn_pct": round(tot, 2),
                                              "creator": creator, "creator_pct": sig(f(gp.get("creator_percent"), 0) * 100, 3),
                                              "holder_count": hd.get("holder_count"), "source": hd["source"]})
        # 6 LP
        lph = gp.get("lp_holders") or []
        dexes = [x.get("liquidity_type") or x.get("name") for x in (gp.get("dex") or [])]
        lpd = launchpad(tok) if (not lph or all(h.get("is_contract") in (1, "1") for h in lph)) else None
        if lpd and (not lph or all(low(h["address"]) in (lpd.get("locker"), V4_POOL_MANAGER) or h.get("is_locked") in (1, "1") for h in lph)):
            chk("lp_lock", "PASS", {"dex": dexes, "detail": f"{lpd['launchpad']} launch: LP is locked by the launchpad by design ({lpd['source']})",
                                    **({"locker": lpd["locker"]} if lpd.get("locker") else {})})
        elif not lph:
            chk("lp_lock", "UNKNOWN", {"dex": dexes, "detail": "no LP-holder data (typical for Uniswap v4 hook/Zora pools); can't prove liquidity can't be pulled"})
        else:
            locked = sum(f(h.get("percent"), 0) for h in lph if h.get("is_locked") in (1, "1") or low(h["address"]) in BURN) * 100
            eoa = sum(f(h.get("percent"), 0) for h in lph if not (h.get("is_locked") in (1, "1") or low(h["address"]) in BURN) and h.get("is_contract") in (0, "0")) * 100
            other = max(0.0, 100 - locked - eoa)
            stt = "REJECT" if eoa >= 50 else ("CAUTION" if eoa >= 10 or other >= 50 else "PASS")
            top_lp = max(lph, key=lambda h: f(h.get("percent"), 0))
            chk("lp_lock", stt, {"dex": dexes, "locked_or_burned_pct": round(locked, 1), "pullable_by_wallet_pct": round(eoa, 1),
                                 "unlabeled_contract_pct": round(other, 1), "top_lp_holder": low(top_lp["address"]),
                                 "top_lp_is_creator": low(top_lp["address"]) == creator})
    if hp_ok:
        ha = hp.get("holderAnalysis") or {}
        if f(ha.get("siphoned"), 0) > 0 or f(ha.get("highTaxWallets"), 0) > 0:
            chk("holder_sell_sim", "REJECT", f"honeypot.is holder sim: siphoned={ha.get('siphoned')} highTax={ha.get('highTaxWallets')}")
        elif ha.get("holders") and f(ha.get("successful"), 0) < 0.9 * f(ha.get("holders"), 1):
            chk("holder_sell_sim", "REJECT", f"only {ha.get('successful')}/{ha.get('holders')} sampled holders could sell")
        elif ha.get("holders"):
            chk("holder_sell_sim", "PASS", f"{ha.get('successful')}/{ha.get('holders')} holders could sell, max tax {ha.get('highestTax')}%")
    chk("liquidity", "UNKNOWN" if liq is None else ("PASS" if liq >= 50e3 else "CAUTION"),
        "no DexScreener pair" if liq is None else f"${r0(liq):,} across {len(pairs)} pair(s); desk min $50k")
    sts = [c["status"] for c in checks]
    verdict = "REJECT" if "REJECT" in sts else "UNKNOWN" if "UNKNOWN" in sts else "CAUTION" if "CAUTION" in sts else "PASS"
    reasons = [c["check"] for c in checks if c["status"] == verdict] if verdict != "PASS" else []
    return {"cmd": "audit", "token": tok, "symbol": sym, "verdict": verdict, "because": reasons, "at": pt(now_utc()), "checks": checks}

def cmd_wallets(a):
    tok = low(a.token)
    gp = goplus(tok)
    if not gp: return {"cmd": "wallets", "token": tok, "signal": "UNKNOWN", "evidence": ["GoPlus holder data unavailable"]}
    pairs = ds_token_pairs([tok])
    pools = {low(p["pairAddress"]) for p in pairs if is_addr(p["pairAddress"])} | \
            {low(x.get("pair")) for x in (gp.get("dex") or []) if is_addr(x.get("pair") or "")}
    v4 = any(x.get("liquidity_type") == "UniV4" for x in (gp.get("dex") or [])) or any("v4" in (p.get("labels") or []) for p in pairs)
    if v4: pools.add(V4_POOL_MANAGER)
    creator = low(gp.get("creator_address"))
    dec = decimals(tok)
    hd = holders_for(tok, gp) or {"holders": [], "holder_count": None, "total_supply": f(gp.get("total_supply")), "source": "none"}
    supply = hd.get("total_supply") or f(gp.get("total_supply"))
    holders = []
    for h in hd["holders"]:
        why = excluded_holder(h, pools, gp)
        holders.append({"address": low(h["address"]), "pct": round(f(h.get("percent"), 0) * 100, 3), "contract": h.get("is_contract") in (1, "1"),
                        "excluded": why, "creator": low(h["address"]) == creator})
    big = [h for h in holders if not h["excluded"]][:10]
    # recent transfers
    flows = {}; pool_buy = pool_sell = 0.0; buyers = set(); sellers = set(); err = None; nlogs = 0
    try:
        if dec is None: raise RuntimeError("decimals() call failed")
        bn = block_number(); start = bn - int(a.hours * 1800)
        logs = transfer_logs(tok, start, bn); nlogs = len(logs)
        bytx = {}
        for lg in logs:
            if len(lg["topics"]) < 3: continue
            fr = "0x" + lg["topics"][1][-40:]; to = "0x" + lg["topics"][2][-40:]
            amt = int(lg["data"], 16) / 10 ** dec if lg["data"] not in ("0x", "") else 0
            d = bytx.setdefault(lg["transactionHash"], {})
            d[fr] = d.get(fr, 0.0) - amt; d[to] = d.get(to, 0.0) + amt
        # net per address per tx, so routers/aggregators that pass tokens through net to ~0
        for d in bytx.values():
            pool_net = sum(v for k, v in d.items() if k in pools)
            for ad, v in d.items():
                if ad in pools or abs(v) < 1e-12: continue
                fl = flows.setdefault(ad, [0.0, 0.0])
                if v > 0: fl[0] += v
                else: fl[1] -= v
                if pool_net < 0 and v > 0: pool_buy += v; buyers.add(ad)
                elif pool_net > 0 and v < 0: pool_sell -= v; sellers.add(ad)
    except RuntimeError as e:
        err = str(e); WARN.append(err)
    pct = lambda x: round(x / supply * 100, 3) if supply else None
    acc = dist = hold = 0; ev = []
    for h in big:
        inn, out = flows.get(h["address"], [0.0, 0.0]); net = inn - out
        h["net_pct_window"] = pct(net); h["in_pct"] = pct(inn); h["out_pct"] = pct(out)
        bal = h["pct"]; n = pct(net) or 0
        if err: h["trend"] = "unknown"
        elif n > max(0.05, 0.05 * bal): h["trend"] = "accumulating"; acc += 1
        elif n < -max(0.05, 0.05 * bal): h["trend"] = "distributing"; dist += 1
        else: h["trend"] = "holding"; hold += 1
    # biggest fresh buyers in window (net >= 0.3% supply), outside top-10
    fresh = []
    if not err and supply:
        topset = {h["address"] for h in holders}
        for ad, (inn, out) in flows.items():
            if ad in pools or ad in BURN or ad in topset: continue
            net = inn - out
            if net / supply * 100 >= 0.3: fresh.append({"address": ad, "net_pct_window": pct(net), "creator": ad == creator})
        fresh.sort(key=lambda x: -x["net_pct_window"]); fresh = fresh[:8]
    flip = sum(1 for ad in buyers & sellers)
    if err or not big or nlogs == 0: signal = "UNKNOWN"
    elif acc + len(fresh) >= 3 and acc + len(fresh) >= 2 * max(dist, 1) and pool_buy >= pool_sell: signal = "ACCUMULATING"
    elif dist >= 3 and dist >= 2 * max(acc, 1) or (dist >= 2 and pool_sell > 1.5 * pool_buy): signal = "DISTRIBUTING"
    else: signal = "MIXED"
    if not err and nlogs == 0: ev.append("no transfers in window: inactive token, nothing to read")
    if not err:
        ev.append(f"top holders ex-LP/burn: {acc} accumulating, {dist} distributing, {hold} flat over {a.hours}h")
        ev.append(f"{len(fresh)} new wallets bought >=0.3% of supply in window")
        ev.append(f"pool flow: buys {pct(pool_buy)}% vs sells {pct(pool_sell)}% of supply; {len(buyers)} buyers, {len(sellers)} sellers, {flip} both")
    else: ev.append(f"transfer history unavailable: {err}")
    created = [p["pairCreatedAt"] for p in pairs if p.get("pairCreatedAt")]
    if created and (time.time() * 1000 - min(created)) / 3.6e6 < a.hours:
        ev.append("window covers the token's whole life, so 'accumulating' top holders may just be first buyers")
    cr = next((h for h in holders if h["creator"]), None)
    cflow = flows.get(creator, [0.0, 0.0])
    be = None
    if USE_BE and os.environ.get("BIRDEYE_API_KEY"):
        be = {}
        tt = be_get("/defi/v2/tokens/top_traders", {"address": tok, "time_frame": "24h", "sort_by": "volume", "sort_type": "desc", "limit": 10})
        if tt and tt.get("items"):
            be["top_traders_24h"] = [{"address": low(x["owner"]), "buy_usd": r0(f(x.get("volumeBuyUSD"))), "sell_usd": r0(f(x.get("volumeSellUSD"))),
                                      "net_usd": r0(f(x.get("volumeBuyUSD"), 0) - f(x.get("volumeSellUSD"), 0)), "trades": x.get("trade"),
                                      "pnl_usd": r0(f(x.get("totalPnl")))} for x in tt["items"]]
        tx = be_get("/defi/v3/token/txs", {"address": tok, "limit": 100, "tx_type": "swap", "sort_type": "desc"}, ttl=60)
        net = {}
        if tx and tx.get("items"):
            it = tx["items"]; bu = su = 0.0; big_b = big_s = 0
            for x in it:
                v = f(x.get("volume_usd"), 0); o = low(x.get("owner"))
                if x.get("side") == "buy" or x.get("tx_type") == "buy": bu += v; net[o] = net.get(o, 0) + v; big_b += v >= a.big_usd
                else: su += v; net[o] = net.get(o, 0) - v; big_s += v >= a.big_usd
            span = (it[0]["block_unix_time"] - it[-1]["block_unix_time"]) / 60 if len(it) > 1 else 0
            be["last_swaps"] = {"n": len(it), "span_min": round(span), "buy_usd": r0(bu), "sell_usd": r0(su),
                                f"buys_over_{int(a.big_usd)}usd": big_b, f"sells_over_{int(a.big_usd)}usd": big_s}
        # wallet track records for the biggest holders/buyers (budgeted)
        cand = [h["address"] for h in big if not h["contract"]][:3]
        cand += [w for w, v in sorted(net.items(), key=lambda kv: -kv[1]) if v >= a.big_usd and w not in cand][:3]
        cand += [x["address"] for x in be.get("top_traders_24h", []) if x["net_usd"] and x["net_usd"] > 0 and x["address"] not in cand][:2]
        q = [r for r in (be_wallet_quality(w) for w in cand[:a.pnl_checks]) if r]
        if q:
            netmap = {**{x["address"]: x["net_usd"] for x in be.get("top_traders_24h", [])}, **{k: r0(v) for k, v in net.items()}}
            for r in q:
                r["net_usd_recent"] = netmap.get(r["address"])
                bh = next((h for h in big if h["address"] == r["address"]), None)
                if bh: r["holder_trend"] = bh.get("trend")
            be["wallet_track_records"] = q
            pb = sum(1 for r in q if r["proven"] and ((r.get("net_usd_recent") or 0) > 0 or r.get("holder_trend") == "accumulating"))
            ps = sum(1 for r in q if r["proven"] and ((r.get("net_usd_recent") or 0) < 0 or r.get("holder_trend") == "distributing"))
            ph = sum(1 for r in q if r["proven"] and r.get("holder_trend") == "holding")
            be["smart_money"] = ("BUYING" if pb >= 2 and pb > ps else "SELLING" if ps >= 2 and ps > pb
                                 else "HOLDING" if ph >= 2 and ps == 0 else "NONE_CLEAR")
            ev.append(f"birdeye: {sum(r['proven'] for r in q)}/{len(q)} checked wallets have a profitable record (>=20 trades, win rate >=50%); {pb} buying, {ph} holding, {ps} selling")
        if not be: be = {"status": "unavailable", "notes": sorted(BE_RUN["notes"])}
    return {"cmd": "wallets", "token": tok, "symbol": gp.get("token_symbol"), "signal": signal, "evidence": ev,
            "window_h": a.hours, "transfers_seen": nlogs, "holder_count": hd.get("holder_count") or (int(gp["holder_count"]) if gp.get("holder_count") else None),
            "top10_ex_lp_pct": round(sum(h["pct"] for h in big), 2),
            "deployer": {"address": creator, "pct_now": cr["pct"] if cr else sig(f(gp.get("creator_percent"), 0) * 100, 3),
                         "net_pct_window": None if err else pct(cflow[0] - cflow[1])},
            "big_wallets": [{k: h[k] for k in ("address", "pct", "contract", "creator", "trend", "net_pct_window") if k in h} for h in big],
            "excluded": [{"address": h["address"], "pct": h["pct"], "why": h["excluded"]} for h in holders if h["excluded"]],
            "fresh_big_buyers": fresh, "holder_source": hd["source"], **({"birdeye": be} if be is not None else {}), "at": pt(now_utc())}

def parse_since(s):
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None: t = t.replace(tzinfo=PT)
    return t

def cmd_history(a):
    res = resolve(a.target)
    if not res["pair"] or not res["token"]: return {"cmd": "history", "input": a.target, "status": "NOT_FOUND"}
    since = parse_since(a.since); s0 = int(since.timestamp()); now = int(time.time())
    span_h = (now - s0) / 3600
    tf, agg, step = ("minute", 15, 900) if span_h <= 48 else (("hour", 1, 3600) if span_h <= 24 * 40 else ("day", 1, 86400))
    candles, before = [], now + step
    while True:
        d = get(f"{GT}/networks/{CHAIN}/pools/{res['pair']}/ohlcv/{tf}?aggregate={agg}&limit=1000&currency=usd&token={res['token']}&before_timestamp={before}", ttl=60)
        lst = ((d or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
        candles += [c for c in lst if c[0] + step > s0]
        if not lst or lst[-1][0] <= s0 or len(lst) < 1000: break
        before = lst[-1][0]
    candles = sorted({c[0]: c for c in candles}.values())
    snap = snapshot(res)
    price_now = (snap or {}).get("price_usd"); mc_now = (snap or {}).get("mcap_usd")
    supply = mc_now / price_now if mc_now and price_now else None
    if not candles: return {"cmd": "history", "pair": res["pair"], "token": res["token"], "status": "NO_CANDLES", "live": snap}
    mc = lambda p: r0(p * supply) if supply else None
    hi = max(candles, key=lambda c: c[2]); lo = min(candles, key=lambda c: c[3])
    first = candles[0]; last = candles[-1]
    rungs = {}
    for r in RUNGS:
        hit = next((c for c in candles if supply and c[2] * supply >= r), None)
        rungs[f"${r/1e6:g}M"] = pt(hit[0]) if hit else None
    after_hi = [c for c in candles if c[0] > hi[0]]
    low_after_hi = min(after_hi, key=lambda c: c[3]) if after_hi else [0, 0, 0, hi[2], 0]
    out = {"cmd": "history", "symbol": (snap or {}).get("symbol"), "token": res["token"], "pair": res["pair"], "since": pt(since),
           "candle": f"{agg}{tf[0]}", "n": len(candles),
           "start": {"t": pt(first[0]), "price": sig(first[1]), "mcap": mc(first[1])},
           "high": {"t": pt(hi[0]), "price": sig(hi[2]), "mcap": mc(hi[2]), "x_from_start": round(hi[2] / first[1], 2) if first[1] else None},
           "low": {"t": pt(lo[0]), "price": sig(lo[3]), "mcap": mc(lo[3]), "pct_from_start": round((lo[3] / first[1] - 1) * 100, 1) if first[1] else None},
           "last": {"t": pt(last[0]), "price": sig(last[4]), "mcap": mc(last[4]), "pct_from_start": round((last[4] / first[1] - 1) * 100, 1) if first[1] else None,
                    "pct_off_high": round((last[4] / hi[2] - 1) * 100, 1)},
           "max_drawdown_after_high_pct": round((low_after_hi[3] / hi[2] - 1) * 100, 1),
           "rungs_first_crossed": rungs,
           "mcap_basis": "live mcap/price ratio (assumes fixed supply)" if supply else "unavailable (price only)"}
    if a.candles:
        out["candles_tohlc_mcap"] = [[c[0], mc(c[1]), mc(c[2]), mc(c[3]), mc(c[4])] if supply else c[:5] for c in candles]
    return out

# ================================================================ 2026-10-13 additions: multi-chain screens (free sources)
SOL = "solana"
WSOL = "So11111111111111111111111111111111111111112"
SOL_MAJORS = {WSOL, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
RUGCHECK = "https://api.rugcheck.xyz/v1/tokens"
JUP_QUOTE = ["https://api.jup.ag/swap/v1/quote", "https://lite-api.jup.ag/swap/v1/quote"]
GT_NET = {"base": "base", "solana": "solana"}
# tickers of large/established coins: a new pair using one of these is treated as a copycat
COPYCAT_TICKERS = {"BTC", "WBTC", "CBBTC", "ETH", "WETH", "CBETH", "SOL", "WSOL", "USDT", "USDC", "USD", "DAI", "USDE", "PYUSD", "FDUSD",
    "BNB", "XRP", "DOGE", "ADA", "TRX", "TON", "AVAX", "DOT", "LINK", "LTC", "BCH", "XMR", "XLM", "HBAR", "SUI", "APT", "NEAR", "ICP",
    "ARB", "OP", "MATIC", "POL", "UNI", "AAVE", "PEPE", "SHIB", "WIF", "BONK", "FLOKI", "TRUMP", "MELANIA", "PENGU", "FARTCOIN", "POPCAT",
    "BRETT", "TOSHI", "DEGEN", "AERO", "VIRTUAL", "ZORA", "CLANKER", "MORPHO", "JUP", "RAY", "PUMP", "HYPE", "WLD", "ENA", "ONDO", "TAO",
    "MOG", "SPX", "GIGA", "MEW", "BOME", "PNUT", "GOAT", "AI16Z", "MOODENG", "CHILLGUY"}
_BIDI = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\u034f]")

def gt_pool_info(pool, chain=None):
    """GeckoTerminal pool attributes incl. unique buyers/sellers per window. None on failure."""
    d = get(f"{GT}/networks/{GT_NET.get(chain or CHAIN, chain)}/pools/{pool}", ttl=120, ok404=True)
    if not d or d.get("_404"): return None
    return d["data"]["attributes"]

def gt_ohlcv(pool, chain=None, tf="minute", agg=15, limit=1000, ttl=600):
    d = get(f"{GT}/networks/{GT_NET.get(chain or CHAIN, chain)}/pools/{pool}/ohlcv/{tf}?aggregate={agg}&limit={limit}&currency=usd", ttl=ttl, ok404=True)
    try: return sorted(d["data"]["attributes"]["ohlcv_list"], key=lambda c: c[0])   # [ts, o, h, l, c, v]
    except Exception: return None

def copycat_check(sym, name=""):
    """Returns a reason string if the ticker looks like a copycat, else None."""
    s = sym or ""
    if _BIDI.search(s) or _BIDI.search(name or ""): return "hidden/bidi characters in ticker"
    if re.search(r"[A-Za-z]", s) and re.search("[\u0370-\u03ff\u0400-\u04ff\uff01-\uff5e]", s): return "lookalike (Cyrillic/Greek/fullwidth) letters in ticker"
    core = re.sub(r"[^A-Z0-9]", "", s.upper())
    if core in COPYCAT_TICKERS: return f"uses established ticker {core}"
    return None

def fake_mcap_check(pool, chain, mcap, price, liq, pool_attr=None):
    """Prefab / fake market cap screen. Returns (reason or None, detail)."""
    det = {}
    c = gt_ohlcv(pool, chain)
    if c and price and mcap:
        first = c[0]; fm = mcap * (float(first[4]) / price) if price else None
        det["first_candle_mcap"] = r0(fm); det["first_candle_utc"] = dt.datetime.fromtimestamp(first[0], dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        if fm and fm >= 500_000: return f"already ${fm/1e3:.0f}k mcap on first candle", det
    else:
        det["first_candle_mcap"] = None
    a = pool_attr if pool_attr is not None else gt_pool_info(pool, chain)
    if a:
        b24 = ((a.get("transactions") or {}).get("h24") or {}).get("buyers")
        det["buyers_24h"] = b24
        if mcap and liq and liq >= 0.40 * mcap and (b24 is None or b24 < 100):
            return f"liquidity {liq/mcap*100:.0f}% of mcap with only {b24} buyers in 24h", det
    return None, det

def wash_screen(pool, chain, ds_h1_buys, ds_h1_sells, pool_attr=None):
    """'Only goes up' / wash screen. Any one trigger = skip. Returns (reason or None, detail)."""
    a = pool_attr if pool_attr is not None else gt_pool_info(pool, chain)
    if not a: return "unique buyer/seller data unavailable", {}
    h1 = (a.get("transactions") or {}).get("h1") or {}
    by, se = h1.get("buyers"), h1.get("sellers")
    b, s = ds_h1_buys if ds_h1_buys is not None else h1.get("buys"), ds_h1_sells if ds_h1_sells is not None else h1.get("sells")
    det = {"h1_unique_buyers": by, "h1_unique_sellers": se, "h1_buys": b, "h1_sells": s}
    if by is None or se is None: return "unique buyer/seller data unavailable", det
    if by and se < 0.10 * by: return f"sellers {se} < 10% of buyers {by} (1h)", det
    if se < 20: return f"only {se} unique sellers in last 1h (<20)", det
    if (b or 0) >= 5 * max(s or 0, 1): return f"buys {b} >= 5x sells {s} (1h)", det
    return None, det

def jup_sell(mint, decimals_, price_usd, usd=200.0):
    """Jupiter quote for selling ~$usd of the token into SOL. Returns dict(status PASS/REJECT/UNKNOWN, detail)."""
    if not price_usd or decimals_ is None: return {"status": "UNKNOWN", "detail": "no price/decimals for quote"}
    amt = int(usd / price_usd * 10 ** decimals_)
    if amt <= 0: return {"status": "UNKNOWN", "detail": "amount rounds to 0"}
    for base in JUP_QUOTE:
        q = get(f"{base}?inputMint={mint}&outputMint={WSOL}&amount={amt}&slippageBps=1500&restrictIntermediateTokens=true", ttl=120, ok404=True)
        if q is None: continue
        if q.get("_404") or q.get("error") or q.get("errorCode") or not q.get("routePlan"):
            return {"status": "REJECT", "detail": f"no Jupiter sell route ({q.get('errorCode') or q.get('error') or 'none'})"}
        imp = f(q.get("priceImpactPct"), 0) * 100
        route = [r["swapInfo"].get("label") for r in q["routePlan"]]
        if imp > 10: return {"status": "REJECT", "detail": f"Jupiter sell of ${usd:.0f}: price impact {imp:.1f}%", "route": route}
        return {"status": "PASS", "detail": f"Jupiter sell route for ${usd:.0f} via {','.join(route)}; impact {imp:.2f}%", "route": route}
    return {"status": "UNKNOWN", "detail": "Jupiter quote API unreachable"}

def rugcheck(mint):
    d = get(f"{RUGCHECK}/{mint}/report", ttl=900, ok404=True)
    if not d or d.get("_404"): return None
    return d

def sol_audit(mint, price_usd=None):
    """Solana audit: RugCheck (mint/freeze authority, transfer fee, LP lock/burn, holders, insiders) + Jupiter sell route.
    Same verdict shape as cmd_audit: PASS / REJECT / UNKNOWN with checks[]."""
    checks = []; chk = lambda c, s, d: checks.append({"check": c, "status": s, "detail": d})
    r = rugcheck(mint)
    if not r:
        return {"cmd": "audit", "chain": SOL, "token": mint, "verdict": "UNKNOWN", "because": ["rugcheck_unavailable"], "checks": []}
    tok = r.get("token") or {}
    chk("mint_authority", "REJECT" if (r.get("mintAuthority") or tok.get("mintAuthority")) else "PASS", r.get("mintAuthority") or "none (renounced)")
    chk("freeze_authority", "REJECT" if (r.get("freezeAuthority") or tok.get("freezeAuthority")) else "PASS", r.get("freezeAuthority") or "none")
    tf = r.get("transferFee") or {}
    chk("transfer_fee", "REJECT" if f(tf.get("pct"), 0) > 0 else "PASS", f"{f(tf.get('pct'), 0)}%")
    ext = r.get("token_extensions") or {}
    ext = ext if isinstance(ext, dict) else {}
    bad_ext = [e for e in ("transferHook", "permanentDelegate", "nonTransferable", "defaultAccountState", "transferFeeConfig")
               if ext.get(e) and not (isinstance(ext.get(e), dict) and not any(v for k, v in ext[e].items() if k not in ("withheldAmount", "newerTransferFee", "olderTransferFee")))]
    tfc = ext.get("transferFeeConfig") or {}
    for k in ("newerTransferFee", "olderTransferFee"):
        if f((tfc.get(k) or {}).get("transferFeeBasisPoints"), 0) > 0 and "transfer_fee_bps" not in bad_ext: bad_ext.append("transfer_fee_bps")
    chk("token2022_extensions", "REJECT" if bad_ext else "PASS", bad_ext or "none risky")
    if r.get("rugged"): chk("rugcheck_rugged", "REJECT", "RugCheck marks token as rugged")
    danger = [x.get("name") for x in (r.get("risks") or []) if x.get("level") == "danger"]
    chk("rugcheck_danger_risks", "REJECT" if danger else "PASS", danger or [x.get("name") for x in (r.get("risks") or [])])
    mk = sorted(r.get("markets") or [], key=lambda m: -f((m.get("lp") or {}).get("lpLockedUSD"), 0) - f((m.get("lp") or {}).get("quoteUSD"), 0))
    if not mk: chk("lp_locked_or_burned", "UNKNOWN", "no markets in RugCheck report")
    else:
        lp = mk[0].get("lp") or {}; pct = f(lp.get("lpLockedPct"))
        st = "PASS" if pct is not None and pct >= 90 else ("UNKNOWN" if pct is None else "REJECT")
        chk("lp_locked_or_burned", st, {"market": mk[0].get("marketType"), "lp_locked_pct": sig(pct, 3)})
    known = r.get("knownAccounts") or {}
    pools = {m.get("pubkey") for m in r.get("markets") or []} | {k for k, v in known.items() if (v or {}).get("type") in ("AMM", "LOCKER")}
    for m in r.get("markets") or []:
        for k in ("liquidityAAccount", "liquidityBAccount"):
            if isinstance(m.get(k), dict): pools.add(m[k].get("owner"))
    hs = [h for h in r.get("topHolders") or [] if h.get("owner") not in pools and h.get("address") not in pools]
    top10 = round(sum(f(h.get("pct"), 0) for h in hs[:10]), 2)
    sup = f(tok.get("supply")); creator_pct = round(f(r.get("creatorBalance"), 0) / sup * 100, 3) if sup else None
    insider = round(sum(f(h.get("pct"), 0) for h in hs if h.get("insider") and h.get("owner") != r.get("creator")), 3)
    linked = round((creator_pct or 0) + insider, 3) if creator_pct is not None else None
    chk("holder_concentration", "REJECT" if top10 > 40 else "PASS",
        {"top10_ex_lp_burn_pct": top10, "creator": r.get("creator"), "creator_pct": creator_pct, "insider_pct": insider,
         "insider_networks": r.get("graphInsidersDetected"), "holders": r.get("totalHolders")})
    chk("deployer_linked", "REJECT" if (linked or 0) > 5 else ("PASS" if linked is not None else "UNKNOWN"), {"pct": linked})
    dec = tok.get("decimals"); price = price_usd or f(r.get("price"))
    js = jup_sell(mint, dec, price)
    chk("sellable", js["status"], js["detail"])
    rej = [c["check"] for c in checks if c["status"] == "REJECT"]; unk = [c["check"] for c in checks if c["status"] == "UNKNOWN"]
    verdict = "REJECT" if rej else "UNKNOWN" if unk else "PASS"
    lp_name = (r.get("launchpad") or {}).get("platform") or r.get("deployPlatform")
    return {"cmd": "audit", "chain": SOL, "token": mint, "verdict": verdict, "because": rej or unk, "checks": checks,
            "top10": top10, "linked_pct": linked, "creator": r.get("creator"), "holders": r.get("totalHolders"), "launchpad": lp_name}

def sol_wallets(mint, big_usd=500, pnl_checks=3):
    """Birdeye-only big-wallet read for Solana (no free RPC log scan). smart_money BUYING/HOLDING/SELLING/NONE_CLEAR."""
    if not (USE_BE and os.environ.get("BIRDEYE_API_KEY")):
        return {"signal": "UNKNOWN", "birdeye": {"status": "no key"}, "evidence": ["birdeye unavailable"]}
    be = {}; ev = []
    tt = be_get("/defi/v2/tokens/top_traders", {"address": mint, "time_frame": "24h", "sort_by": "volume", "sort_type": "desc", "limit": 10}, chain=SOL)
    tops = [{"address": x["owner"], "net_usd": r0(f(x.get("volumeBuyUSD"), 0) - f(x.get("volumeSellUSD"), 0))} for x in (tt or {}).get("items") or []]
    tx = be_get("/defi/v3/token/txs", {"address": mint, "limit": 100, "tx_type": "swap", "sort_type": "desc"}, ttl=60, chain=SOL)
    net = {}
    for x in (tx or {}).get("items") or []:
        v = f(x.get("volume_usd"), 0); o = x.get("owner")
        net[o] = net.get(o, 0) + (v if (x.get("side") == "buy" or x.get("tx_type") == "buy") else -v)
    cand = [w for w, v in sorted(net.items(), key=lambda kv: -kv[1]) if v >= big_usd][:3]
    cand += [x["address"] for x in tops if (x["net_usd"] or 0) > 0 and x["address"] not in cand][:3]
    q = []
    for w in cand[:pnl_checks]:
        d = be_get("/wallet/v2/pnl/summary", {"wallet": w}, ttl=6 * 3600, chain=SOL)
        if d and "summary" in d:
            sm = d["summary"]; c = sm.get("counts", {}); pn = sm.get("pnl", {})
            tr = c.get("total_trade") or 0; wr = f(c.get("win_rate")); rp = f(pn.get("realized_profit_usd"))
            nn = net.get(w) if w in net else next((x["net_usd"] for x in tops if x["address"] == w), None)
            q.append({"address": w, "trades": tr, "win_rate": sig(wr, 2), "realized_pnl_usd": r0(rp), "net_usd_recent": r0(nn),
                      "proven": bool(tr >= 20 and (wr or 0) >= 0.5 and (rp or 0) > 0)})
    pb = sum(1 for r in q if r["proven"] and (r["net_usd_recent"] or 0) > 0)
    ps = sum(1 for r in q if r["proven"] and (r["net_usd_recent"] or 0) < 0)
    smart = "BUYING" if pb >= 2 and pb > ps else "SELLING" if ps >= 2 and ps > pb else "NONE_CLEAR" if q else None
    big_buyers = sum(1 for v in net.values() if v >= big_usd)
    ev.append(f"birdeye: {big_buyers} wallets net-bought >= ${big_usd} in last 100 swaps; {sum(r['proven'] for r in q)}/{len(q)} checked wallets proven; {pb} buying, {ps} selling")
    signal = "ACCUMULATING" if big_buyers >= 3 and pb >= ps else "UNKNOWN" if not tx else "MIXED"
    be["wallet_track_records"] = q; be["smart_money"] = smart
    return {"signal": signal, "birdeye": be, "evidence": ev}

def holder_count(tok, chain):
    """Free holder count: RugCheck (Solana) or GoPlus (Base)."""
    if chain == SOL:
        r = rugcheck(tok); return (r or {}).get("totalHolders")
    gp = goplus(tok); hc = (gp or {}).get("holder_count")
    return int(hc) if hc else None


def main():
    global USE_CACHE, USE_BE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-cache", action="store_true", help="skip the 30-300s response cache")
    common.add_argument("--pretty", action="store_true", help="indented JSON")
    common.add_argument("--no-birdeye", action="store_true", help="don't call Birdeye even if BIRDEYE_API_KEY is set")
    sp = ap.add_subparsers(dest="cmd", required=True)
    _add = sp.add_parser
    sp.add_parser = lambda name, **kw: _add(name, parents=[common], **kw)
    s = sp.add_parser("scan"); s.add_argument("--min-age", type=float, default=2); s.add_argument("--max-age", type=float, default=48)
    s.add_argument("--min-liq", type=float, default=50e3); s.add_argument("--min-vol", type=float, default=25e3)
    s.add_argument("--min-txns", type=int, default=150); s.add_argument("--pages", type=int, default=8)
    s.add_argument("--birdeye-top", type=int, default=3, help="enrich the top N in-zone results with Birdeye overview (1 call each)")
    s = sp.add_parser("audit"); s.add_argument("token")
    s = sp.add_parser("wallets"); s.add_argument("token"); s.add_argument("--hours", type=float, default=6)
    s.add_argument("--pnl-checks", type=int, default=5, help="Birdeye wallet track-record lookups (1 call each)")
    s.add_argument("--big-usd", type=float, default=500, help="USD size that counts as a big swap")
    s = sp.add_parser("history"); s.add_argument("target"); s.add_argument("--since", required=True); s.add_argument("--candles", action="store_true")
    s = sp.add_parser("quote"); s.add_argument("target")
    a = ap.parse_args(); USE_CACHE = not a.no_cache; USE_BE = not a.no_birdeye
    try:
        out = {"scan": cmd_scan, "audit": cmd_audit, "wallets": cmd_wallets, "history": cmd_history, "quote": cmd_quote}[a.cmd](a)
    except Exception as e:
        out = {"cmd": a.cmd, "status": "ERROR", "error": f"{type(e).__name__}: {e}"}
    if WARN: out["warnings"] = sorted(set(WARN))
    if BE_RUN["calls"] or BE_RUN["notes"]:
        out["birdeye_usage"] = {"calls_this_run": BE_RUN["calls"], "budget": BE_BUDGET,
                                "calls_this_month": _be_state().get("month", {}).get(now_utc().strftime("%Y-%m"), 0),
                                **({"notes": sorted(BE_RUN["notes"])} if BE_RUN["notes"] else {})}
    print(json.dumps(out, indent=1 if a.pretty else None, separators=None if a.pretty else (",", ":")))

if __name__ == "__main__":
    main()
