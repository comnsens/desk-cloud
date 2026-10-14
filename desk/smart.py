"""Best-wallet tracker (item 5). Zero AI, free sources only.
build: early buyers of coins that later hit $1M+ (Solana: oldest txs via public RPC; EVM: launch sniffer), minus
       snipers in the launch slot/block, bots (>1000 txs/day or contracts), devs, and bundle/cluster-linked wallets.
detect: each watcher cycle checks a staggered slice of tracked wallets for new buys; 2+ tracked wallets buying the same
        new coin within 6h = hit. mode "log" only records; mode "push" sends ntfy (CA first, then details) after safety PASS."""
import json, os, time, requests
import desk

SOL_RPCS = ["https://api.mainnet-beta.solana.com", "https://solana-rpc.publicnode.com"]
for u in SOL_RPCS: desk.THROTTLE.setdefault(u.split("/")[2], 0.3)
FILE = os.environ.get("DESK_SMART_FILE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "smart_wallets.json")
SKIP_MINTS = {"So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
CALLS = {"n": 0}

def srpc(method, params):
    last = None
    for u in SOL_RPCS:
        desk._throttle(u.split("/")[2]); CALLS["n"] += 1
        try: j = requests.post(u, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=25).json()
        except Exception as e: last = type(e).__name__; continue
        if "result" in j: return j["result"]
        last = (j.get("error") or {}).get("message")
    raise RuntimeError(f"sol rpc {method}: {str(last)[:100]}")

def _deltas(tx, mint):
    m = tx.get("meta") or {}
    pre = {(b["accountIndex"]): float(b["uiTokenAmount"]["uiAmount"] or 0) for b in m.get("preTokenBalances") or [] if b["mint"] == mint}
    out = {}
    for b in m.get("postTokenBalances") or []:
        if b["mint"] != mint: continue
        d = float(b["uiTokenAmount"]["uiAmount"] or 0) - pre.get(b["accountIndex"], 0)
        out[b.get("owner")] = out.get(b.get("owner"), 0) + d
    return out

def sol_early_buyers(mint, max_pages=40, first_n=80):
    before, sigs = None, []
    for _ in range(max_pages):
        p = {"limit": 1000, **({"before": before} if before else {})}
        r = srpc("getSignaturesForAddress", [mint, p])
        sigs += r
        if len(r) < 1000: break
        before = r[-1]["signature"]
    else: return {"error": f"more than {max_pages}k txs; oldest not reached"}
    old = [s for s in reversed(sigs) if not s.get("err")][:first_n]
    if not old: return {"error": "no txs"}
    slot0 = old[0]["slot"]; buyers = {}; creator = None
    for i, s in enumerate(old):
        try: tx = srpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        except RuntimeError: continue
        if not tx: continue
        signer = tx["transaction"]["message"]["accountKeys"][0]["pubkey"]
        if i == 0: creator = signer
        for owner, d in _deltas(tx, mint).items():
            if d > 0 and owner == signer and owner not in buyers:
                buyers[owner] = {"slot_offset": s["slot"] - slot0, "rank": len(buyers) + 1}
    return {"creator": creator, "buyers": buyers, "n_txs_total": len(sigs)}

def sol_is_bot(w):
    r = srpc("getSignaturesForAddress", [w, {"limit": 1000}])
    if len(r) >= 1000 and r[-1].get("blockTime") and time.time() - r[-1]["blockTime"] < 86400: return True
    return False

def load():
    try: return json.load(open(FILE))
    except FileNotFoundError: return {"mode": "log", "wallets": {}, "sources": [], "updated": None}

def save(d): json.dump(d, open(FILE + ".tmp", "w"), indent=1); os.replace(FILE + ".tmp", FILE)

def add_coin(d, ch, tok, sym, peak, log=print):
    """Add early buyers of one runner. Returns count added."""
    if any(s["tok"] == tok for s in d["sources"]): return 0
    excl, cand = set(), {}
    if ch == "solana":
        eb = sol_early_buyers(tok)
        if eb.get("error"): d["sources"].append({"tok": tok, "sym": sym, "ch": ch, "skipped": eb["error"]}); return 0
        excl.add(eb["creator"])
        rc = desk.rugcheck(tok) or {}
        excl.add(rc.get("creator"))
        for net in rc.get("insiderNetworks") or []:
            for a in net.get("activeAccounts") or net.get("accounts") or []: excl.add(a if isinstance(a, str) else a.get("address"))
        excl |= set((rc.get("knownAccounts") or {}).keys())
        for w, v in eb["buyers"].items():
            if v["slot_offset"] == 0: continue              # bought in the launch slot = bundle/sniper
            if w in excl: continue
            cand[w] = v
        cand = dict(list(cand.items())[:25])
        for w in list(cand):
            try:
                if sol_is_bot(w): cand.pop(w)
            except RuntimeError: cand.pop(w)
    else:
        import evm
        pairs = desk.ds_token_pairs([tok], chain=ch) or []
        if not pairs: return 0
        p = max(pairs, key=lambda x: desk.f((x.get("liquidity") or {}).get("usd"), 0))
        dpairs = [x["pairAddress"] for x in pairs]
        try:
            lb = evm.launch_buyers(tok, ch, p["pairAddress"], p.get("pairCreatedAt") or 0, dpairs, n_blocks=30)
        except Exception as e: lb = {"error": str(e)[:80]}
        if lb.get("error") or not lb.get("buyers"):
            d["sources"].append({"tok": tok, "sym": sym, "ch": ch, "skipped": lb.get("error") or "no buyers"}); return 0
        try:
            basic = evm.audit_basic(tok, ch); excl.add(basic.get("creator"))
            fc = evm.funding_clusters(tok, ch, lb, basic.get("creator"))
            for c in fc.get("clusters") or []:
                if c["size"] >= 2 or c.get("deployer"): excl |= set(c["members"])
        except Exception: pass
        for b in lb["buyers"]:
            w = b["address"]
            if b["block_offset"] == 0 or w in excl: continue
            try:
                if evm.rpc(ch, "eth_getCode", [w, "latest"]) not in ("0x", "0x0"): continue
                if int(evm.rpc(ch, "eth_getTransactionCount", [w, "latest"]), 16) > 3000: continue
            except Exception: continue
            cand[w.lower()] = {"block_offset": b["block_offset"], "pct": b["pct"]}
    n = 0
    for w, v in cand.items():
        e = d["wallets"].setdefault(w, {"chain": ch, "coins": [], "added": time.strftime("%Y-%m-%d")})
        if sym not in e["coins"]: e["coins"].append(sym); n += 1
        e["score"] = len(e["coins"])
    d["sources"].append({"tok": tok, "sym": sym, "ch": ch, "peak": peak, "added_wallets": len(cand), "excluded": len([x for x in excl if x])})
    d["updated"] = int(time.time())
    log(f"smart: {sym} {ch} +{len(cand)} wallets")
    return n

# ---------------------------------------------------------------- detection (watcher, each cycle)
PER_CYCLE = int(os.environ.get("SMART_PER_CYCLE", "8"))
def detect(st, log, push_fn=None):
    d = load(); W = d.get("wallets") or {}
    S = st.setdefault("smart", {"cursor": 0, "last": {}, "buys": {}, "hits": [], "evm_block": {}})
    if not W: S["note"] = "no tracked wallets"; return
    now = time.time()
    sol = sorted(w for w, v in W.items() if v["chain"] == "solana")
    for w in [sol[(S["cursor"] + i) % len(sol)] for i in range(min(PER_CYCLE, len(sol)))] if sol else []:
        try:
            p = {"limit": 15, **({"until": S["last"][w]} if S["last"].get(w) else {})}
            sigs = srpc("getSignaturesForAddress", [w, p])
        except RuntimeError: continue
        if not sigs: continue
        first_time = w not in S["last"]; S["last"][w] = sigs[0]["signature"]
        if first_time: continue                                  # baseline only
        for s in sigs[:5]:
            if s.get("err"): continue
            try: tx = srpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            except RuntimeError: continue
            for b in ((tx or {}).get("meta") or {}).get("postTokenBalances") or []:
                if b.get("owner") == w and b["mint"] not in SKIP_MINTS:
                    if _deltas(tx, b["mint"]).get(w, 0) > 0:
                        S["buys"].setdefault(b["mint"], {})[w] = int(s.get("blockTime") or now)
    if sol: S["cursor"] = (S["cursor"] + PER_CYCLE) % len(sol)
    # EVM: one log query per chain over new blocks, tracked wallets as Transfer recipients
    try:
        import evm
        for ch in ("base", "bsc", "ethereum"):
            ws = [w for w, v in W.items() if v["chain"] == ch]
            if not ws: continue
            head = int(evm.rpc(ch, "eth_blockNumber", []), 16)
            fb = max(S["evm_block"].get(ch, head - 20), head - (45 if ch == "bsc" else 100))
            if fb >= head: continue
            topics = [evm.TRANSFER, None, ["0x" + "0" * 24 + w[2:].lower() for w in ws[:100]]]
            logs = evm.rpc(ch, "eth_getLogs", [{"fromBlock": hex(fb + 1), "toBlock": hex(head), "topics": topics}], deep=True) or []
            S["evm_block"][ch] = head
            for lg in logs:
                tok = lg["address"].lower()
                if tok in evm.WNATIVE.values(): continue
                S["buys"].setdefault(tok, {})["0x" + lg["topics"][2][-40:]] = int(now)
    except Exception as e: S["evm_err"] = str(e)[:100]
    # hits
    for tok, b in list(S["buys"].items()):
        b = {w: t for w, t in b.items() if now - t < 6 * 3600}
        if not b: S["buys"].pop(tok); continue
        S["buys"][tok] = b
        if len(b) >= 2 and tok not in {h["tok"] for h in S["hits"]}:
            ch = W[next(iter(b))]["chain"]
            hit = {"t": int(now), "tok": tok, "ch": ch, "wallets": sorted(b)[:6], "mode": d.get("mode", "log")}
            if d.get("mode") == "push" and len(W) >= 20 and push_fn:
                hit["push"] = push_fn(tok, ch, hit)
            S["hits"] = (S["hits"] + [hit])[-50:]
            log.info("SMART_HIT %s %s wallets=%d mode=%s", ch, tok, len(b), hit["mode"])
    S["tracked"] = len(W); S["calls"] = CALLS["n"]
