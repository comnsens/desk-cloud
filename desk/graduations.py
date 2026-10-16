"""Launchpad graduation study feed (LOG-ONLY). Free public data only; never pushes phone alerts.

Tracks tokens graduating from launchpads to full pools:
  Solana: pump.fun -> PumpSwap, LetsBonk -> Raydium, Meteora DBC -> Meteora/DAMM
  Base: Clanker + Zora (launch = first full Uniswap pool; LP locked by design)

At graduation records: time, mcap, liq, holders, top10 share, buys/sells (1h).
Follow-ups at 1h / 6h / 24h / 72h (mcap + liq) so the weekly study can grade which
graduation traits lead to runners. Storage: collector data release assets.
"""
import json, os, time, datetime as dt
import desk

FU = [("1h", 3600), ("6h", 6 * 3600), ("24h", 86400), ("72h", 72 * 3600)]
GRAD_DEX = {"pumpswap": "pumpfun", "raydium": "letsbonk", "meteora": "meteora", "meteora-damm-v2": "meteora"}
GP_ID = {"base": 8453}
MAX_NEW = int(os.environ.get("GRAD_MAX_NEW", "8"))          # new graduations per collector loop
HOLDER_CAP = int(os.environ.get("GRAD_HOLDER_CAP", "4"))    # RugCheck/GoPlus per loop
MAX_PAIR_AGE_H = 6                                         # only pairs created in last N hours

def _work():
    return os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

def _reg_path(): return os.path.join(_work(), "grads_registry.json")
def _log_path():
    mon = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    return os.path.join(_work(), f"grads-{mon}.jsonl")

def load_reg():
    try: return json.load(open(_reg_path()))
    except Exception: return {"tokens": {}, "seen": []}

def save_reg(reg):
    os.makedirs(_work(), exist_ok=True)
    p = _reg_path(); json.dump(reg, open(p + ".tmp", "w"), separators=(",", ":")); os.replace(p + ".tmp", p)

def _append(row):
    os.makedirs(_work(), exist_ok=True)
    with open(_log_path(), "a") as fh: fh.write(json.dumps(row, separators=(",", ":")) + "\n")

def _pair_age_h(p):
    c = p.get("pairCreatedAt")
    return (time.time() * 1000 - c) / 3.6e6 if c else None

def _snap_from_pair(tok, ch, p, launchpad, src):
    tx = p.get("txns") or {}
    return {
        "k": "grad", "t": int(time.time()), "tok": tok, "ch": ch, "lp": launchpad, "src": src,
        "dex": p.get("dexId"), "pair": p.get("pairAddress"),
        "sym": (p.get("baseToken") or {}).get("symbol") or (p.get("quoteToken") or {}).get("symbol"),
        "mc": round(desk.f(p.get("marketCap")) or desk.f(p.get("fdv")) or 0),
        "liq": round(desk.f((p.get("liquidity") or {}).get("usd"), 0)),
        "v1": round(desk.f((p.get("volume") or {}).get("h1"), 0)),
        "b1": (tx.get("h1") or {}).get("buys"), "s1": (tx.get("h1") or {}).get("sells"),
        "age_h": round(_pair_age_h(p), 2) if _pair_age_h(p) is not None else None,
        "holders": None, "top10": None,
    }

def _enrich_holders(snap, cap):
    """Fill holders + top10 from free sources (capped)."""
    if cap["n"] <= 0: return
    tok, ch = snap["tok"], snap["ch"]
    try:
        if ch == "solana":
            cap["n"] -= 1
            r = desk.rugcheck(tok) or {}
            snap["holders"] = r.get("totalHolders")
            # top10 non-pool share if present
            t10 = r.get("topHolders") or r.get("markets")
            if isinstance(r.get("totalMarketLiquidity"), (int, float)):
                pass
            # RugCheck often exposes top10 via risks / tokenMeta; prefer explicit field
            for k in ("top10HoldersPercent", "top10"):
                if r.get(k) is not None:
                    snap["top10"] = desk.f(r.get(k)); break
            if snap["top10"] is None and isinstance(r.get("topHolders"), list):
                s = sum(desk.f(h.get("pct") or h.get("percentage"), 0) for h in r["topHolders"][:10])
                if s: snap["top10"] = round(s, 2)
        elif ch == "base":
            cap["n"] -= 1
            d = desk.get(f"https://api.gopluslabs.io/api/v1/token_security/{GP_ID['base']}?contract_addresses={tok}", ttl=1800)
            info = list((d.get("result") or {}).values())[0] if d and d.get("result") else {}
            hc = info.get("holder_count")
            snap["holders"] = int(hc) if hc else None
            snap["top10"] = desk.f(info.get("top_10_holder_rate"))
            if snap["top10"] is not None and snap["top10"] <= 1: snap["top10"] = round(snap["top10"] * 100, 2)
    except Exception:
        pass

def _add(reg, snap, cap):
    tok = snap["tok"]
    if tok in reg["tokens"]: return False
    if tok in set(reg.get("seen") or []): return False
    _enrich_holders(snap, cap)
    reg["tokens"][tok] = {"t0": snap["t"], "ch": snap["ch"], "lp": snap["lp"], "grad": snap, "fu": {}, "max_mc": snap["mc"], "max_liq": snap["liq"]}
    seen = reg.setdefault("seen", []); seen.append(tok)
    if len(seen) > 5000: reg["seen"] = seen[-4000:]
    _append(snap)
    return True

def discover_solana(reg, cap, budget):
    """New PumpSwap / Raydium / Meteora pools = Solana launchpad graduations."""
    n, found = 0, []
    # GeckoTerminal dex new pools
    # GT has no per-dex new_pools endpoint (404); use network new_pools and filter by dex id.
    GT_LP = {"pumpswap": "pumpfun", "raydium": "letsbonk", "raydium-launchlab": "letsbonk",
             "meteora": "meteora", "meteora-damm-v2": "meteora"}
    for page in (1, 2):
        if time.time() > budget or n >= MAX_NEW: break
        d = desk.get(f"{desk.GT}/networks/solana/new_pools?page={page}", ttl=60) or {}
        for p in d.get("data") or []:
            rel = p.get("relationships") or {}
            dex = (((rel.get("dex") or {}).get("data") or {}).get("id") or "")
            lp = GT_LP.get(dex)
            if not lp: continue
            try:
                b = rel["base_token"]["data"]["id"].split("_", 1)[1]
                q = rel["quote_token"]["data"]["id"].split("_", 1)[1]
            except Exception: continue
            tok = q if (b in desk.SOL_MAJORS and q not in desk.SOL_MAJORS) else b
            if tok in desk.SOL_MAJORS or tok in reg["tokens"]: continue
            # prefer pump suffix for pumpfun; skip non-bonk on letsbonk path lightly
            if lp == "letsbonk" and not tok.endswith("bonk") and dex == "raydium":
                # Raydium has many non-graduation pools; only keep *bonk or young low-liq later via DS
                attrs = (p.get("attributes") or {})
                created = attrs.get("pool_created_at")
                if not created: continue
            found.append((tok, lp, f"gt_{dex}"))
    # DexScreener search for fresh pumpswap pairs
    if time.time() < budget and n < MAX_NEW:
        d = desk.get(f"{desk.DS}/latest/dex/search?q=pumpswap", ttl=90) or {}
        for p in d.get("pairs") or []:
            if p.get("chainId") != "solana": continue
            age = _pair_age_h(p)
            if age is None or age > MAX_PAIR_AGE_H: continue
            b, qq = p["baseToken"]["address"], p["quoteToken"]["address"]
            tok = qq if (b in desk.SOL_MAJORS and qq not in desk.SOL_MAJORS) else b
            if tok in desk.SOL_MAJORS or tok in reg["tokens"]: continue
            found.append((tok, "pumpfun", "ds_pumpswap"))
    # Dedupe preserving order
    seen, uniq = set(), []
    for t, lp, src in found:
        if t not in seen: seen.add(t); uniq.append((t, lp, src))
    # Batch DexScreener for snaps
    need = [t for t, _, _ in uniq if t not in reg["tokens"]][:MAX_NEW]
    if not need: return 0
    by = {}
    for i in range(0, len(need), 30):
        for p in desk.ds_token_pairs(need[i:i + 30], chain="solana") or []:
            for side in ("baseToken", "quoteToken"):
                a = p[side]["address"]
                if a in need:
                    cur = by.get(a)
                    if not cur or desk.f((p.get("liquidity") or {}).get("usd"), 0) > desk.f((cur.get("liquidity") or {}).get("usd"), 0):
                        by[a] = p
    lp_by = {t: (lp, src) for t, lp, src in uniq}
    for tok in need:
        if time.time() > budget or n >= MAX_NEW: break
        p = by.get(tok)
        if not p: continue
        age = _pair_age_h(p)
        if age is not None and age > MAX_PAIR_AGE_H * 2: continue
        # Prefer graduated DEX ids
        dex = (p.get("dexId") or "").lower()
        lp, src = lp_by.get(tok, ("pumpfun", "ds"))
        if dex in GRAD_DEX: lp = GRAD_DEX[dex]
        elif tok.endswith("pump"): lp = "pumpfun"
        elif tok.endswith("bonk"): lp = "letsbonk"
        snap = _snap_from_pair(tok, "solana", p, lp, src)
        if snap["mc"] <= 0 and snap["liq"] <= 0: continue
        if _add(reg, snap, cap): n += 1
    return n

def discover_base(reg, cap, budget):
    """Clanker + Zora new tokens on Base (launch = full pool)."""
    n, toks = 0, []
    if time.time() < budget:
        c = desk.get(f"{desk.CLANKER}?sort=desc&page=1", ttl=120) or {}
        for x in (c.get("data") or [])[:20]:
            if x.get("chain_id") == 8453:
                a = (x.get("contract_address") or "").lower()
                if a and a not in reg["tokens"]: toks.append((a, "clanker", "clanker_api"))
    if time.time() < budget:
        for lt in ("NEW", "TOP_VOLUME_24H"):
            z = desk.get(f"https://api-sdk.zora.engineering/explore?listType={lt}&count=15", ttl=120) or {}
            for e in ((z.get("exploreList") or {}).get("edges") or []):
                node = e.get("node") or {}
                if str(node.get("chainId")) == "8453":
                    a = (node.get("address") or "").lower()
                    if a and a not in reg["tokens"]: toks.append((a, "zora", "zora_" + lt.lower()))
    seen, uniq = set(), []
    for t, lp, src in toks:
        if t not in seen: seen.add(t); uniq.append((t, lp, src))
    need = [t for t, _, _ in uniq][:MAX_NEW]
    if not need: return 0
    by = {}
    for i in range(0, len(need), 30):
        for p in desk.ds_token_pairs(need[i:i + 30], chain="base") or []:
            for side in ("baseToken", "quoteToken"):
                a = p[side]["address"].lower()
                if a in need:
                    cur = by.get(a)
                    if not cur or desk.f((p.get("liquidity") or {}).get("usd"), 0) > desk.f((cur.get("liquidity") or {}).get("usd"), 0):
                        by[a] = p
    lp_by = {t: (lp, src) for t, lp, src in uniq}
    for tok in need:
        if time.time() > budget or n >= MAX_NEW: break
        p = by.get(tok)
        if not p: continue
        age = _pair_age_h(p)
        if age is not None and age > 48: continue   # Base launches: allow older than Solana grads
        lp, src = lp_by[tok]
        snap = _snap_from_pair(tok, "base", p, lp, src)
        if snap["liq"] < 1000 and snap["mc"] < 5000: continue
        if _add(reg, snap, cap): n += 1
    return n

def followups(reg, budget_s=40):
    """Record mcap/liq at 1h/6h/24h/72h after graduation; finalize after 72h."""
    t0 = time.time(); now = time.time(); n = 0
    due = []
    for tok, e in reg["tokens"].items():
        if e.get("done"): continue
        for name, off in FU:
            if name not in e["fu"] and now >= e["t0"] + off:
                due.append((tok, name, e["ch"])); break
    by_ch = {}
    for tok, name, ch in due: by_ch.setdefault(ch, []).append((tok, name))
    for ch, items in by_ch.items():
        for i in range(0, len(items), 30):
            if time.time() - t0 > budget_s: return n
            chunk = items[i:i + 30]; toks = [x[0] for x in chunk]
            pairs = {}
            for j in range(0, len(toks), 30):
                for p in desk.ds_token_pairs(toks[j:j + 30], chain=ch) or []:
                    for side in ("baseToken", "quoteToken"):
                        a = p[side]["address"] if ch == "solana" else p[side]["address"].lower()
                        if a in toks:
                            cur = pairs.get(a)
                            if not cur or desk.f((p.get("liquidity") or {}).get("usd"), 0) > desk.f((cur.get("liquidity") or {}).get("usd"), 0):
                                pairs[a] = p
            for tok, name in chunk:
                e = reg["tokens"][tok]; p = pairs.get(tok)
                mc = round(desk.f(p.get("marketCap")) or desk.f(p.get("fdv")) or 0) if p else 0
                liq = round(desk.f((p.get("liquidity") or {}).get("usd"), 0)) if p else 0
                row = {"k": "grad_fu", "t": int(time.time()), "tok": tok, "ch": ch, "fu": name, "mc": mc, "liq": liq, "alive": bool(p)}
                e["fu"][name] = {"t": row["t"], "mc": mc, "liq": liq}
                e["max_mc"] = max(e.get("max_mc") or 0, mc)
                e["max_liq"] = max(e.get("max_liq") or 0, liq)
                _append(row); n += 1
                if name == "72h" or (not p and name in ("24h", "72h")):
                    g = e["grad"]; mc0 = g.get("mc") or 0
                    lab = {"k": "grad_label", "t": int(time.time()), "tok": tok, "ch": ch, "lp": e.get("lp"),
                           "grad": g, "fu": e["fu"], "max_mc": e["max_mc"], "max_liq": e.get("max_liq"),
                           "mult": round(e["max_mc"] / mc0, 2) if mc0 else None,
                           "hit_1m": e["max_mc"] >= 1e6, "hit_5m": e["max_mc"] >= 5e6, "hit_10m": e["max_mc"] >= 1e7,
                           "dead": not p, "final": True}
                    _append(lab); e["done"] = True
    # prune finished older than 10 days from registry (labels stay in jsonl)
    cut = time.time() - 10 * 86400
    for k in [k for k, e in reg["tokens"].items() if e.get("done") and e["t0"] < cut]:
        reg["tokens"].pop(k)
    return n

def scan(budget_s=50):
    """Discover new graduations + run due follow-ups. Returns stats dict."""
    start = time.time(); reg = load_reg(); cap = {"n": HOLDER_CAP}
    n_sol = discover_solana(reg, cap, start + budget_s * 0.55)
    n_base = discover_base(reg, cap, start + budget_s * 0.75)
    n_fu = followups(reg, budget_s=max(5, budget_s - (time.time() - start)))
    save_reg(reg)
    return {"grad_new": n_sol + n_base, "grad_sol": n_sol, "grad_base": n_base, "grad_fu": n_fu,
            "grad_tracked": len(reg["tokens"]), "grad_holder_left": cap["n"]}

def load_labels(data_dir=None):
    """Load finalized graduation labels from grads-*.jsonl for analyze.py."""
    d = data_dir or _work(); out = {}
    if not os.path.isdir(d): return []
    for fn in sorted(os.listdir(d)):
        if not (fn.startswith("grads-") and fn.endswith(".jsonl")): continue
        for line in open(os.path.join(d, fn)):
            try: r = json.loads(line)
            except Exception: continue
            if r.get("k") == "grad_label" and r.get("final"): out[r["tok"]] = r
    return list(out.values())
