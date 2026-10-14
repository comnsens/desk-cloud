#!/usr/bin/env python3
"""Zero-AI dataset collector. Learns from ALL tokens the desk sees, not samples.
- ingests watcher rows (Base/Solana first-seen + evaluation features) from the state branch
- discovers BNB + Ethereum new pools itself (GeckoTerminal) and snapshots them via DexScreener (30/call)
- follow-ups at ~1h, 6h, 24h, 3d, 7d: mcap, liquidity, holders (capped), then a final label
- persists to GitHub release 'data' assets (no repo growth). Birdeye is never used here.
"""
import gzip, json, os, subprocess, sys, time, datetime as dt
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "desk"))
os.environ.setdefault("DESK_CACHE_DIR", os.path.join(ROOT, ".http-cache"))
import desk  # noqa: E402
desk.USE_BE = False

WORK = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
REPO = os.environ.get("GITHUB_REPOSITORY", "comnsens/desk-cloud")
RUN_S = float(os.environ.get("COLLECT_RUN_SECONDS", "270"))
OFFLINE = os.environ.get("COLLECT_OFFLINE") == "1"           # local test: no release download/upload
ROWS_FILE = os.environ.get("COLLECT_ROWS_FILE")              # local test: read watcher rows from a file
FU = [("1h", 3600), ("6h", 6 * 3600), ("24h", 86400), ("3d", 3 * 86400), ("7d", 7 * 86400)]
DS_CHAIN = {"base": "base", "solana": "solana", "bsc": "bsc", "ethereum": "ethereum"}
GT_NET = {"bsc": "bsc", "ethereum": "eth"}
GP_ID = {"base": 8453, "bsc": 56, "ethereum": 1}
EVM_MAJORS = {"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "0x55d398326f99059ff775485246999027b3197955", "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
              "0xe9e7cea3dedca5984780bafc599bd69add087d56", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
              "0xdac17f958d2ee523a2206206994597c13d831ec7", "0x6b175474e89094c44da98b954eedeac495271d0f", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"} | desk.MAJORS
HOLDER_CAP = {"solana": 20, "evm": 15}                        # holder lookups per loop (RugCheck / GoPlus free limits)
RAW_KEEP_DAYS = 90

def today(): return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
def gh(*a, check=True): return subprocess.run(["gh", *a], capture_output=True, text=True, check=check)

def restore():
    os.makedirs(WORK, exist_ok=True)
    if OFFLINE: return
    r = gh("release", "view", "data", "-R", REPO, "--json", "assets", "-q", ".assets[].name", check=False)
    if r.returncode != 0:
        gh("release", "create", "data", "-R", REPO, "-t", "dataset", "-n", "Collector dataset (raw rows 90 days, labels kept). Machine-written.", check=False)
        return
    names = set(r.stdout.split())
    want = ["registry.json.gz", f"raw-{today()}.jsonl.gz", f"labels-{today()[:7]}.jsonl.gz"]
    for n in want:
        if n in names and not os.path.exists(os.path.join(WORK, n.replace(".gz", ""))):
            gh("release", "download", "data", "-R", REPO, "-p", n, "-D", WORK, "--clobber", check=False)
            p = os.path.join(WORK, n)
            if os.path.exists(p):
                with gzip.open(p) as fi, open(p[:-3], "wb") as fo: fo.write(fi.read())
                os.remove(p)

def upload(reg_changed=True):
    if OFFLINE: return
    files = []
    for n in ("registry.json", f"raw-{today()}.jsonl", f"labels-{today()[:7]}.jsonl"):
        p = os.path.join(WORK, n)
        if os.path.exists(p):
            with open(p, "rb") as fi, gzip.open(p + ".gz", "wb", compresslevel=6) as fo: fo.write(fi.read())
            files.append(p + ".gz")
    # previous day's raw file (finalised at rollover)
    for fn in os.listdir(WORK):
        if fn.startswith("raw-") and fn.endswith(".jsonl") and not fn.startswith(f"raw-{today()}"):
            p = os.path.join(WORK, fn)
            with open(p, "rb") as fi, gzip.open(p + ".gz", "wb") as fo: fo.write(fi.read())
            files.append(p + ".gz"); os.remove(p)
    if files: gh("release", "upload", "data", "-R", REPO, "--clobber", *files, check=False)
    # prune raw assets older than 90 days (labels are kept)
    r = gh("release", "view", "data", "-R", REPO, "--json", "assets", "-q", ".assets[].name", check=False)
    cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RAW_KEEP_DAYS)).strftime("%Y-%m-%d")
    for n in r.stdout.split():
        if n.startswith("raw-") and n[4:14] < cut: gh("release", "delete-asset", "data", n, "-R", REPO, "-y", check=False)

def load_reg():
    try: return json.load(open(os.path.join(WORK, "registry.json")))
    except Exception: return {"tokens": {}, "seen_rows": [], "gt_rot": 0}

def save_reg(reg):
    p = os.path.join(WORK, "registry.json"); json.dump(reg, open(p + ".tmp", "w"), separators=(",", ":")); os.replace(p + ".tmp", p)

def raw(row):
    with open(os.path.join(WORK, f"raw-{today()}.jsonl"), "a") as fh: fh.write(json.dumps(row, separators=(",", ":")) + "\n")

def label_out(row):
    with open(os.path.join(WORK, f"labels-{today()[:7]}.jsonl"), "a") as fh: fh.write(json.dumps(row, separators=(",", ":")) + "\n")

def new_token(reg, tok, ch, fs):
    T = reg["tokens"]
    if tok in T: return False
    T[tok] = {"ch": ch, "t0": fs["t"], "fs": fs, "ev": None, "fu": {}, "max_mc": fs.get("mc") or 0, "max_t": fs["t"],
              "max_liq": fs.get("liq") or 0, "pair": fs.get("pair"), "holders0": None}
    return True

# ---- watcher rows ----
def ingest(reg):
    if ROWS_FILE:
        lines = open(ROWS_FILE).read().splitlines() if os.path.exists(ROWS_FILE) else []
    else:
        r = gh("api", f"repos/{REPO}/contents/ds_rows.jsonl?ref=state", "-H", "Accept: application/vnd.github.raw", check=False)
        lines = r.stdout.splitlines() if r.returncode == 0 else []
    seen = set(reg["seen_rows"]); n = 0
    for line in lines:
        try: row = json.loads(line)
        except Exception: continue
        rid = f"{row['k']}:{row['tok']}:{row['t']}"
        if rid in seen: continue
        seen.add(rid); n += 1
        raw(row)
        if row["k"] == "fs": new_token(reg, row["tok"], row["ch"], row)
        elif row["k"] == "ev":
            e = reg["tokens"].get(row["tok"])
            if e and row["t"] - e["t0"] <= 2 * 3600: e["ev"] = row       # only early evaluations (no lookahead)
    cut = time.time() - 4 * 3600
    reg["seen_rows"] = [x for x in seen if int(x.rsplit(":", 1)[1]) >= cut]
    return n

# ---- BNB / Ethereum discovery (GeckoTerminal new + trending pools) ----
def discover_evm(reg):
    rot = reg.get("gt_rot", 0); reg["gt_rot"] = rot + 1
    paths = ["new_pools?page=1", "new_pools?page=2", "trending_pools?duration=1h&page=1", "new_pools?page=3"]
    found = {}
    for ch, net in GT_NET.items():
        d = desk.get(f"{desk.GT}/networks/{net}/{paths[rot % len(paths)]}", ttl=45) or {}
        for p in d.get("data") or []:
            rel = p["relationships"]
            b = rel["base_token"]["data"]["id"].split("_", 1)[1].lower(); q = rel["quote_token"]["data"]["id"].split("_", 1)[1].lower()
            tok = q if (b in EVM_MAJORS and q not in EVM_MAJORS) else b
            if tok not in reg["tokens"] and tok not in EVM_MAJORS: found.setdefault(ch, set()).add(tok)
    n = 0
    for ch, toks in found.items():
        by = best_pairs(list(toks), ch)
        for tok, (best, src) in by.items():
            fs = snapshot_fs(tok, ch, best)
            if new_token(reg, tok, ch, fs): raw(fs); n += 1
    return n

def best_pairs(toks, ch):
    out = {}
    for i in range(0, len(toks), 30):
        for p in desk.ds_token_pairs(toks[i:i + 30], chain=DS_CHAIN[ch]):
            for side in ("baseToken", "quoteToken"):
                a = p[side]["address"]; key = a if ch == "solana" else a.lower()
                if key in toks:
                    cur = out.get(key)
                    if not cur or desk.f((p.get("liquidity") or {}).get("usd"), 0) > desk.f((cur[0].get("liquidity") or {}).get("usd"), 0):
                        out[key] = (p, side)
    return out

def snapshot_fs(tok, ch, best):
    tx = best.get("txns") or {}; info = best.get("info") or {}
    created = best.get("pairCreatedAt")
    return {"k": "fs", "t": int(time.time()), "tok": tok, "ch": ch, "dex": best.get("dexId"), "pair": best.get("pairAddress"),
            "lp": "fourmeme" if ch == "bsc" and tok.endswith("4444") else None, "sym": (best.get("baseToken") or {}).get("symbol"),
            "age_h": round((time.time() * 1000 - created) / 3.6e6, 2) if created else None,
            "mc": round(desk.f(best.get("marketCap")) or desk.f(best.get("fdv")) or 0), "liq": round(desk.f((best.get("liquidity") or {}).get("usd"), 0)),
            "v24": round(desk.f((best.get("volume") or {}).get("h24"), 0)), "v1": round(desk.f((best.get("volume") or {}).get("h1"), 0)),
            "v5": round(desk.f((best.get("volume") or {}).get("m5"), 0)),
            "b1": (tx.get("h1") or {}).get("buys"), "s1": (tx.get("h1") or {}).get("sells"), "b5": (tx.get("m5") or {}).get("buys"), "s5": (tx.get("m5") or {}).get("sells"),
            "c1": (best.get("priceChange") or {}).get("h1"), "c5": (best.get("priceChange") or {}).get("m5"),
            "soc": len(info.get("socials") or []), "web": len(info.get("websites") or []), "img": bool(info.get("imageUrl")),
            "boost": (best.get("boosts") or {}).get("active") or 0, "src": ["collector_gt"], "why": "not_evaluated (collector-only chain)"}

# ---- holders (free, capped) ----
def holders(tok, ch, cap):
    if ch == "solana":
        if cap["solana"] <= 0: return None
        cap["solana"] -= 1; r = desk.rugcheck(tok); return (r or {}).get("totalHolders")
    if cap["evm"] <= 0: return None
    cap["evm"] -= 1
    d = desk.get(f"https://api.gopluslabs.io/api/v1/token_security/{GP_ID[ch]}?contract_addresses={tok}", ttl=1800)
    try:
        hc = list((d.get("result") or {}).values())[0].get("holder_count"); return int(hc) if hc else None
    except Exception: return None

# ---- follow-ups + labels ----
def followups(reg, budget_s):
    t0 = time.time(); now = time.time(); T = reg["tokens"]
    due = {}
    for tok, e in T.items():
        if e.get("done"): continue
        for name, off in FU:
            if name not in e["fu"] and now >= e["t0"] + off:
                due.setdefault(e["ch"], []).append((tok, name)); break
    cap = dict(HOLDER_CAP); n = 0
    for ch, items in due.items():
        items.sort(key=lambda x: T[x[0]]["t0"])
        for i in range(0, len(items), 30):
            if time.time() - t0 > budget_s: return n
            chunk = items[i:i + 30]; toks = [x[0] for x in chunk]
            by = best_pairs(toks, ch)
            for tok, name in chunk:
                e = T[tok]; p = (by.get(tok) or (None,))[0]
                mc = round(desk.f(p.get("marketCap")) or desk.f(p.get("fdv")) or 0) if p else 0
                liq = round(desk.f((p.get("liquidity") or {}).get("usd"), 0)) if p else 0
                hc = holders(tok, ch, cap) if (p and liq >= 10_000 and name in ("1h", "24h", "7d")) else None
                if name == "1h" and hc: e["holders0"] = hc
                row = {"k": "fu", "t": int(time.time()), "tok": tok, "ch": ch, "fu": name, "mc": mc, "liq": liq, "holders": hc,
                       "v24": round(desk.f((p.get("volume") or {}).get("h24"), 0)) if p else 0, "alive": bool(p)}
                e["fu"][name] = {k: row[k] for k in ("t", "mc", "liq", "holders")}
                if mc > e["max_mc"]: e["max_mc"], e["max_t"] = mc, row["t"]
                e["max_liq"] = max(e["max_liq"], liq)
                if not e.get("pair") and p: e["pair"] = p.get("pairAddress")
                raw(row); n += 1
                if name == "7d": finalize(tok, e)
    return n

def finalize(tok, e):
    """True peak from GeckoTerminal 4h candles for tokens that moved (sampled max >= 2x or >= $500k); else sampled max."""
    mc0 = e["fs"].get("mc") or 0; peak, peak_t, src = e["max_mc"], e["max_t"], "sampled"
    if e.get("pair") and mc0 and (e["max_mc"] >= 2 * mc0 or e["max_mc"] >= 500_000 or (e["fu"].get("1h") or {}).get("mc", 0) >= 2 * mc0):
        c = desk.gt_ohlcv(e["pair"], e["ch"] if e["ch"] in ("base", "solana") else {"bsc": "bsc", "ethereum": "eth"}[e["ch"]], tf="hour", agg=4, limit=60, ttl=3600)
        last = (e["fu"].get("7d") or {}).get("mc") or 0
        if c and last:
            px_last = float(c[-1][4]); ratio = last / px_last if px_last else 0
            hi = max(c, key=lambda x: float(x[2]))
            if ratio and float(hi[2]) * ratio > peak: peak, peak_t, src = round(float(hi[2]) * ratio), int(hi[0]), "gt_4h"
    liqs = [f["liq"] for f in e["fu"].values()]
    rug = bool(e["max_liq"] and min(liqs[-2:] or [0]) <= 0.2 * e["max_liq"])
    lab = {"tok": tok, "ch": e["ch"], "t0": e["t0"], "fs": e["fs"], "ev": e["ev"], "fu": e["fu"],
           "max_mc": peak, "peak_src": src, "max_mult": round(peak / mc0, 2) if mc0 else None,
           "hit_1m": peak >= 1e6, "hit_5m": peak >= 5e6, "hit_10m": peak >= 10e6,
           "t_peak_h": round((peak_t - e["t0"]) / 3600, 1), "rug": rug,
           "holders_growth": round(((e["fu"].get("24h") or {}).get("holders") or 0) / e["holders0"], 2) if e.get("holders0") else None,
           "final": True}
    label_out(lab); e["done"] = True

def provisional(reg):
    """Partial labels for tokens not yet 7 days old (for quick tests); written to data/provisional.jsonl, never uploaded as final."""
    with open(os.path.join(WORK, "provisional.jsonl"), "w") as fh:
        for tok, e in reg["tokens"].items():
            if e.get("done") or not e["fu"]: continue
            mc0 = e["fs"].get("mc") or 0; liqs = [f["liq"] for f in e["fu"].values()]
            fh.write(json.dumps({"tok": tok, "ch": e["ch"], "t0": e["t0"], "fs": e["fs"], "ev": e["ev"], "fu": e["fu"], "max_mc": e["max_mc"],
                                 "max_mult": round(e["max_mc"] / mc0, 2) if mc0 else None, "hit_1m": e["max_mc"] >= 1e6, "hit_5m": e["max_mc"] >= 5e6,
                                 "hit_10m": e["max_mc"] >= 1e7, "t_peak_h": round((e["max_t"] - e["t0"]) / 3600, 1),
                                 "rug": bool(e["max_liq"] and liqs[-1] <= 0.2 * e["max_liq"]), "final": False}, separators=(",", ":")) + "\n")

def prune(reg):
    cut = time.time() - 8 * 86400
    for k in [k for k, e in reg["tokens"].items() if e.get("done") or e["t0"] < cut]: reg["tokens"].pop(k)

def main():
    start = time.time(); restore(); reg = load_reg(); stats = {"rows_in": 0, "evm_new": 0, "followups": 0}
    while time.time() - start < RUN_S - 30:
        t = time.time()
        stats["rows_in"] += ingest(reg)
        stats["evm_new"] += discover_evm(reg)
        stats["followups"] += followups(reg, budget_s=max(5, 45 - (time.time() - t)))
        save_reg(reg)
        if os.environ.get("COLLECT_ONCE") == "1": break
        time.sleep(max(3, 60 - (time.time() - t)))
    prune(reg); save_reg(reg); provisional(reg)
    by = {}
    for e in reg["tokens"].values(): by[e["ch"]] = by.get(e["ch"], 0) + 1
    print(json.dumps({**stats, "tracked": len(reg["tokens"]), "by_chain": by, "warnings": sorted(set(desk.WARN))[:5]}))
    upload()

if __name__ == "__main__":
    main()
