"""EVM deep checks for Base / BNB / Ethereum. Free public RPCs only, read-only (eth_call / logs), no keys.
- audit_basic(): GoPlus + honeypot.is (tax, owner powers, mint, pause, blacklist, hidden owner, honeypot)
- sell_test():  our own buy->sell simulation via eth_call + state override (V2 pairs and V3 pools; V4 not covered)
- launch_buyers(): buyers in the first N blocks after pool creation and the supply % they got
- funding_clusters(): first native funder of early buyers (bounded balance binary search), clustered with same-tx / same-sender / deployer
Results are cached on disk (desk.CACHE_DIR) so the watcher and collector reuse them."""
import json, os, time, hashlib, requests
import desk

RPCS = {"base": {"deep": ["https://base.gateway.tenderly.co"], "any": ["https://base.gateway.tenderly.co", "https://base-rpc.publicnode.com"]},
        "bsc": {"deep": ["https://1rpc.io/bnb"], "any": ["https://bsc-rpc.publicnode.com", "https://1rpc.io/bnb", "https://bsc.drpc.org"]},
        "ethereum": {"deep": ["https://gateway.tenderly.co/public/mainnet"], "any": ["https://ethereum-rpc.publicnode.com", "https://gateway.tenderly.co/public/mainnet"]}}
WNATIVE = {"base": "0x4200000000000000000000000000000000000006", "bsc": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
           "ethereum": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"}
CHAIN_ID = {"base": 8453, "bsc": 56, "ethereum": 1}
BLOCK_S = {"base": 2.0, "bsc": 0.75, "ethereum": 12.0}
V4_PM = {"base": "0x498581ff718922c3f8e6a244956af099b2652b2b", "ethereum": "0x000000000004444c5dc75cb358380d2e3de08a90",
         "bsc": "0x28e2ea090877bf75740558f6bfb36a5ffee9e9df"}
TRANSFER = desk.TRANSFER
HELPER = "0x5e11000000000000000000000000000000005e11"
_ST = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contracts", "SellTest.json")))
for u in sum([v["any"] + v["deep"] for v in RPCS.values()], []): desk.THROTTLE.setdefault(u.split("/")[2], 0.2)
CALLS = {"n": 0}

def rpc(ch, method, params, deep=False):
    last = None
    for u in (RPCS[ch]["deep"] if deep else RPCS[ch]["any"]) + (RPCS[ch]["deep"] if not deep else []):
        desk._throttle(u.split("/")[2]); CALLS["n"] += 1
        try:
            j = requests.post(u, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20).json()
        except Exception as e:
            last = type(e).__name__; continue
        if "result" in j: return j["result"]
        last = (j.get("error") or {}).get("message", "error")
        if "revert" in str(last).lower() or "execution" in str(last).lower(): raise RuntimeError("revert: " + str(last)[:160])
    raise RuntimeError(f"rpc {method} failed: {str(last)[:120]}")

def _cache(key, fn, ttl):
    d = os.environ.get("EVM_CACHE_DIR") or desk.CACHE_DIR
    p = os.path.join(d, "evm_" + hashlib.sha1(key.encode()).hexdigest() + ".json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < ttl:
        try: return json.load(open(p))
        except Exception: pass
    v = fn(); os.makedirs(d, exist_ok=True); json.dump(v, open(p, "w")); return v

def h32(x): return hex(x)[2:].rjust(64, "0")
def a32(a): return a.lower().replace("0x", "").rjust(64, "0")
def call(ch, to, data, block="latest", override=None, value=None, frm=None):
    tx = {"to": to, "data": data}
    if value is not None: tx["value"] = hex(value)
    if frm: tx["from"] = frm
    return rpc(ch, "eth_call", [tx, block] + ([override] if override else []))

def total_supply(ch, tok): return int(call(ch, tok, "0x18160ddd"), 16)

# ------------------------------------------------------------------ 0. GoPlus + honeypot.is
def audit_basic(tok, ch):
    def run():
        gp = desk.get(f"https://api.gopluslabs.io/api/v1/token_security/{CHAIN_ID[ch]}?contract_addresses={tok}", ttl=1800) or {}
        g = (gp.get("result") or {}).get(tok.lower()) or {}
        hp = desk.get(f"{desk.HP}?address={tok}&chainID={CHAIN_ID[ch]}", ttl=900, ok404=True) or {}
        rej, unk = [], []
        def flag(cond, name): (rej.append(name) if cond else None)
        hr = hp.get("honeypotResult") or {}; sim = hp.get("simulationResult") or {}
        if not g and not hr: unk.append("no_goplus_or_honeypot_data")
        flag(hr.get("isHoneypot") or g.get("is_honeypot") == "1", "honeypot")
        flag(g.get("cannot_sell_all") == "1", "cannot_sell_all")
        bt = desk.f(sim.get("buyTax"), None); st = desk.f(sim.get("sellTax"), None)
        if bt is None and g.get("buy_tax") not in (None, ""): bt = desk.f(g.get("buy_tax"), 0) * 100
        if st is None and g.get("sell_tax") not in (None, ""): st = desk.f(g.get("sell_tax"), 0) * 100
        flag((bt or 0) > 10, "buy_tax>10%"); flag((st or 0) > 10, "sell_tax>10%")
        owner_live = g.get("owner_address") not in (None, "", "0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead")
        for k, n in (("is_mintable", "mint"), ("transfer_pausable", "pause"), ("is_blacklisted", "blacklist"), ("hidden_owner", "hidden_owner"),
                     ("can_take_back_ownership", "take_back_owner"), ("owner_change_balance", "owner_change_balance"),
                     ("slippage_modifiable", "tax_modifiable"), ("selfdestruct", "selfdestruct"), ("external_call", "external_call")):
            if g.get(k) == "1" and (owner_live or k in ("hidden_owner", "can_take_back_ownership", "selfdestruct")): rej.append(n)
        if g.get("is_open_source") == "0": rej.append("not_open_source")
        lp = g.get("lp_holders") or []
        lp_locked = round(sum(desk.f(h.get("percent"), 0) for h in lp if h.get("is_locked") == 1 or (h.get("address") or "").lower() in desk.BURN) * 100, 1) if lp else None
        return {"verdict": "REJECT" if rej else ("UNKNOWN" if unk else "PASS"), "because": rej or unk, "buy_tax": bt, "sell_tax": st,
                "owner_live": owner_live, "holders": int(g["holder_count"]) if g.get("holder_count") else None, "lp_locked_pct": lp_locked,
                "creator": (g.get("creator_address") or "").lower() or None, "top10": round(sum(desk.f(h.get("percent"), 0) for h in (g.get("holders") or [])
                if not h.get("is_locked") and (h.get("address") or "").lower() not in desk.BURN and (h.get("address") or "").lower() not in {x.get("address", "").lower() for x in lp}) * 100, 2) if g.get("holders") else None}
    return _cache(f"basic:{ch}:{tok}", run, 1800)

# ------------------------------------------------------------------ 3. own sell test (eth_call + state override)
def _selector(name):
    return "0x" + next(v for k, v in _ST["hashes"].items() if k.startswith(name + "("))

def v4_key(ch, pool_id, created_ms):
    def run():
        pm = V4_PM.get(ch)
        if not pm or not created_ms: return None
        b0 = block_at(ch, int(created_ms / 1000) - 5); W = 45 if ch == "bsc" else 100
        for start in range(b0 - W, b0 + 6 * W, W):
            for lg in rpc(ch, "eth_getLogs", [{"address": pm, "fromBlock": hex(start), "toBlock": hex(start + W - 1), "topics": [None, pool_id]}], deep=True) or []:
                if len(lg["topics"]) == 4:
                    w = [lg["data"][2 + 64 * i: 66 + 64 * i] for i in range(3)]
                    return {"c0": "0x" + lg["topics"][2][-40:], "c1": "0x" + lg["topics"][3][-40:], "fee": int(w[0], 16), "ts": w[1], "hooks": "0x" + w[2][-40:]}
        return None
    return _cache(f"v4key:{ch}:{pool_id}", run, 30 * 86400)

def sell_test(tok, ch, pair, kind, liq_usd, native_usd, dex="", created_ms=None):
    """kind: 'v2' or 'v3'. Buys ~0.5% of liquidity (min $20) then sells it all; second buy at 1.5% of liquidity checks max-tx limits."""
    if kind == "v2" and "aerodrome" in dex: kind = "aero"
    if kind not in ("v2", "v3", "aero", "v4"):
        return {"status": "UNSUPPORTED", "detail": f"{kind}/{dex} pools not simulated (honeypot.is / GoPlus cover these)"}
    key = None
    if kind == "v4":
        key = v4_key(ch, pair, created_ms)
        if not key: return {"status": "UNKNOWN", "detail": "v4 pool key not found (Initialize log)"}
    w = WNATIVE[ch]; fee = 25 if "pancake" in dex else 30
    def one(usd):
        amt = int(max(usd, 20) / native_usd * 1e18)
        ov = {HELPER: {"code": _ST["runtime"], "balance": hex(amt * 2)}}
        if kind == "v4":
            data = _selector("v4") + a32(V4_PM[ch]) + a32(key["c0"]) + a32(key["c1"]) + h32(key["fee"]) + key["ts"] + a32(key["hooks"]) + a32(tok) + h32(amt)
        elif kind == "aero":
            data = _selector("aero") + a32(pair) + a32(tok) + a32(w) + h32(amt)
        elif kind == "v2":
            data = _selector("v2") + a32(pair) + a32(tok) + a32(w) + h32(amt) + h32(fee)
        else:
            data = _selector("v3") + a32(pair) + a32(tok) + a32(w) + h32(amt)
        r = call(ch, HELPER, data, override=ov, frm="0x1111111111111111111111111111111111111111")
        v = [int(r[2 + 64 * i: 66 + 64 * i], 16) for i in range((len(r) - 2) // 64)]
        if kind == "v4":
            gb, gs = v
            return {"round_trip_loss": round((1 - gs / amt) * 100, 2), "note": "v4: tax shows up as round-trip loss (includes hook fees)"}
        if kind in ("v2", "aero"):
            eb, gb, tp, es, gs = v
            return {"buy_tax": round((1 - gb / eb) * 100, 2) if eb else None, "sell_tax": round((1 - tp / gb) * 100, 2) if gb else None,
                    "round_trip_loss": round((1 - gs / amt) * 100, 2)}
        gb, tp, gs, sa = v
        return {"sell_transfer_tax": round((1 - tp / sa) * 100, 2) if sa else None, "round_trip_loss": round((1 - gs / amt) * 100, 2)}
    def run():
        try: small = one(liq_usd * 0.005)
        except RuntimeError as e:
            msg = str(e)
            return {"status": "REVERT" if "revert" in msg else "UNKNOWN", "detail": "buy/sell simulation " + msg[:140]}
        out = {"status": "PASS", **small}
        tax = max(small.get("sell_tax") or 0, small.get("sell_transfer_tax") or 0, small.get("buy_tax") or 0)
        if tax > 10: out.update(status="REJECT", detail=f"tax {tax:.1f}% > 10%")
        elif small["round_trip_loss"] > 20: out.update(status="REJECT", detail=f"round-trip loss {small['round_trip_loss']:.1f}% (hidden tax or sell block)")
        try: one(liq_usd * 0.015); out["max_tx_ok"] = True
        except RuntimeError as e: out["max_tx_ok"] = False; out["max_tx_note"] = "1.5%-of-liquidity buy/sell reverted (max-tx/wallet limit?) " + str(e)[:80]
        return out
    return _cache(f"sell:{ch}:{tok}:{pair}", run, 1800)

# ------------------------------------------------------------------ 1. launch-block buyers
def block_at(ch, ts):
    hi = int(rpc(ch, "eth_blockNumber", []), 16)
    t_hi = int(rpc(ch, "eth_getBlockByNumber", [hex(hi), False])["timestamp"], 16)
    lo = max(1, hi - int((t_hi - ts) / BLOCK_S[ch] * 1.3) - 100)
    for _ in range(40):
        if hi - lo <= 1: break
        mid = (lo + hi) // 2
        t = int(rpc(ch, "eth_getBlockByNumber", [hex(mid), False], deep=True)["timestamp"], 16)
        if t < ts: lo = mid
        else: hi = mid
    return hi

def launch_buyers(tok, ch, pair, created_ms, pools=(), n_blocks=10):
    def run():
        tsup = total_supply(ch, tok)
        b0 = block_at(ch, int(created_ms / 1000) - 2)
        # first block with any transfer of the token at/after pool creation
        pset = {x.lower() for x in pools} | {pair.lower(), V4_PM.get(ch, "")}
        head = int(rpc(ch, "eth_blockNumber", []), 16)
        W = 45 if ch == "bsc" else 100; logs = []; start = b0
        def get(a, b): return rpc(ch, "eth_getLogs", [{"address": tok, "fromBlock": hex(a), "toBlock": hex(b), "topics": [TRANSFER]}], deep=True) or []
        while start <= min(b0 + 1200, head):
            end = min(start + W - 1, head)
            chunk = get(start, end)
            hit = [lg for lg in chunk if len(lg["topics"]) >= 3 and ("0x" + lg["topics"][1][-40:]) in pset]
            if hit:
                fb = int(hit[0]["blockNumber"], 16)
                logs = [lg for lg in chunk if int(lg["blockNumber"], 16) >= fb]
                e2 = min(fb + n_blocks, head)
                while end < e2:
                    nxt = min(end + W, e2); logs += get(end + 1, nxt); end = nxt
                break
            start = end + 1
        buys = {}; txs = {}
        first_pool_out = None
        for lg in logs:
            bn = int(lg["blockNumber"], 16)
            if len(lg["topics"]) < 3: continue
            fr = "0x" + lg["topics"][1][-40:]; to = "0x" + lg["topics"][2][-40:]
            amt = int(lg["data"], 16) if lg["data"] not in ("0x", "") else 0
            if fr in pset and to not in pset:
                if first_pool_out is None: first_pool_out = bn
                if bn > first_pool_out + n_blocks: break
                b = buys.setdefault(to, {"amt": 0, "txs": set(), "block": bn}); b["amt"] += amt; b["txs"].add(lg["transactionHash"])
                txs.setdefault(lg["transactionHash"], set()).add(to)
        if first_pool_out is None: return {"error": "no pool buys within 1200 blocks of pool creation", "block0": b0}
        top = sorted(buys.items(), key=lambda kv: -kv[1]["amt"])[:25]
        out = [{"address": a, "pct": round(v["amt"] / tsup * 100, 3) if tsup else None, "block_offset": v["block"] - first_pool_out,
                "txs": sorted(v["txs"])[:3]} for a, v in top]
        return {"first_buy_block": first_pool_out, "n_blocks": n_blocks, "buyers": out, "n_buyers": len(buys),
                "pct_total": round(sum(v["amt"] for v in buys.values()) / tsup * 100, 2) if tsup else None,
                "multi_recipient_txs": sum(1 for v in txs.values() if len(v) > 1)}
    return _cache(f"launch:{ch}:{tok}", run, 7 * 86400)

# ------------------------------------------------------------------ 2. funding clusters
def first_funder(ch, addr, before_block, window_blocks):
    lo = max(1, before_block - window_blocks)
    if int(rpc(ch, "eth_getBalance", [addr, hex(lo)], deep=True), 16) > 0 or int(rpc(ch, "eth_getTransactionCount", [addr, hex(lo)], deep=True), 16) > 0:
        return {"funder": None, "note": "wallet older than window"}
    hi = before_block
    for _ in range(32):
        if hi - lo <= 1: break
        mid = (lo + hi) // 2
        if int(rpc(ch, "eth_getBalance", [addr, hex(mid)], deep=True), 16) > 0: hi = mid
        else: lo = mid
    blk = rpc(ch, "eth_getBlockByNumber", [hex(hi), True], deep=True)
    for t in blk.get("transactions") or []:
        if (t.get("to") or "").lower() == addr and int(t.get("value", "0x0"), 16) > 0:
            return {"funder": t["from"].lower(), "block": hi}
    return {"funder": f"internal@{hi}", "block": hi, "note": "funded by contract/internal transfer"}

def funding_clusters(tok, ch, lb, deployer=None, max_buyers=8, window_days=7):
    def run():
        buyers = lb.get("buyers") or []
        parent = {}
        def find(x):
            while parent.setdefault(x, x) != x: x = parent[x]
            return x
        def union(a, b): parent[find(a)] = find(b)
        actors = {}
        for b in buyers[:15]:
            for h in b["txs"][:1]:
                tx = rpc(ch, "eth_getTransactionByHash", [h])
                actors[b["address"]] = (tx.get("from") or "").lower()
                union("b:" + b["address"], "a:" + actors[b["address"]])
                union("b:" + b["address"], "t:" + h)
        fund = {}
        win = int(window_days * 86400 / BLOCK_S[ch])
        for b in buyers[:max_buyers]:
            act = actors.get(b["address"]) or b["address"]
            try: fr = first_funder(ch, act, lb["first_buy_block"], win)
            except RuntimeError as e: fr = {"funder": None, "note": str(e)[:60]}
            fund[b["address"]] = fr
            if fr.get("funder") and not fr["funder"].startswith("internal"): union("b:" + b["address"], "f:" + fr["funder"])
        if deployer:
            for b in buyers:
                a = actors.get(b["address"])
                if a == deployer or (fund.get(b["address"]) or {}).get("funder") == deployer or b["address"] == deployer:
                    union("b:" + b["address"], "DEPLOYER")
        groups = {}
        for b in buyers: groups.setdefault(find("b:" + b["address"]), []).append(b)
        clusters = [g for k, g in groups.items() if len(g) >= 2 or k == find("DEPLOYER") and deployer]
        cl = [{"size": len(g), "pct": round(sum(x["pct"] or 0 for x in g), 3), "deployer": any(find("b:" + x["address"]) == find("DEPLOYER") for x in g) if deployer else False,
               "members": [x["address"] for x in g][:8]} for g in clusters]
        cl.sort(key=lambda c: -c["pct"])
        return {"bundle_pct": round(sum(c["pct"] for c in cl), 2), "biggest_cluster_pct": cl[0]["pct"] if cl else 0.0, "clusters": cl[:6],
                "funders": {k[:10]: (v.get("funder") or v.get("note")) for k, v in fund.items()}}
    return _cache(f"fund:{ch}:{tok}", run, 7 * 86400)

def deep_check(tok, ch, pair_info, deployer=None, pools=()):
    """All deep checks for one coin. pair_info = DexScreener pair dict. Never raises."""
    out = {"at": int(time.time()), "rpc_calls_before": CALLS["n"]}
    try:
        labels = pair_info.get("labels") or []; dex = pair_info.get("dexId") or ""
        kind = "v4" if "v4" in labels else "v3" if ("v3" in labels or any("CL" in l for l in labels) or "slipstream" in dex) else "v2"
        q = (pair_info.get("quoteToken") or {}).get("address", "").lower(); b = (pair_info.get("baseToken") or {}).get("address", "").lower()
        if WNATIVE[ch] not in (q, b): kind_note = "quote is not wrapped native"; kind = "other"
        pn = desk.f(pair_info.get("priceNative")); pu = desk.f(pair_info.get("priceUsd"))
        native_usd = pu / pn if pn and pu else None
        liq = desk.f((pair_info.get("liquidity") or {}).get("usd"), 0)
        out["sell"] = sell_test(tok, ch, pair_info["pairAddress"], kind, liq, native_usd, dex, pair_info.get("pairCreatedAt")) if native_usd else {"status": "UNKNOWN", "detail": "no native price"}
    except Exception as e: out["sell"] = {"status": "UNKNOWN", "detail": f"{type(e).__name__}: {e}"[:160]}
    try:
        lb = launch_buyers(tok, ch, pair_info["pairAddress"], pair_info.get("pairCreatedAt") or 0, pools)
        out["launch"] = {k: lb.get(k) for k in ("first_buy_block", "n_buyers", "pct_total", "multi_recipient_txs", "error")}
        out["launch"]["top"] = (lb.get("buyers") or [])[:5]
        if lb.get("buyers"):
            fc = funding_clusters(tok, ch, lb, deployer)
            out["bundle"] = fc
    except Exception as e: out["launch_err"] = f"{type(e).__name__}: {e}"[:160]
    out["rpc_calls"] = CALLS["n"] - out.pop("rpc_calls_before")
    return out

def risk(basic, deep):
    """LOG-ONLY combined view. hard = what WOULD be a hard reject once validated; soft = 0-100 score (higher = riskier)."""
    hard, soft = [], 0
    b = (basic or {}); d = deep or {}
    sell = d.get("sell") or {}
    if sell.get("status") in ("REJECT", "REVERT"): hard.append("ours_unsellable:" + str(sell.get("detail", ""))[:40])
    if sell.get("max_tx_ok") is False: soft += 15
    for r in b.get("because") or []:
        if r in ("honeypot", "cannot_sell_all", "buy_tax>10%", "sell_tax>10%", "mint", "owner_change_balance", "hidden_owner", "take_back_owner", "selfdestruct", "pause", "blacklist"): hard.append("basic:" + r)
        else: soft += 10
    bun = d.get("bundle") or {}
    if (bun.get("bundle_pct") or 0) > 10 or (bun.get("biggest_cluster_pct") or 0) > 5: hard.append(f"bundle:{bun.get('bundle_pct')}%")
    la = d.get("launch") or {}
    if (la.get("pct_total") or 0) > 30: soft += 20
    if (la.get("multi_recipient_txs") or 0) > 0: soft += 15
    return {"hard": hard, "soft": min(100, soft)}
