"""Shadow decisions (LOG-ONLY). Every candidate the watcher fully evaluates gets an ENTER/SKIP decision with per-rule
reasons, even when the official paper book's account gates (2/day cap, streak cut, drawdown) block a real buy.
Forward outcomes (price/mcap at 1h/6h/24h/72h) are recorded for every decision. Never buys, never alerts.
Files (state dir): shadow.jsonl (decisions), shadow_outcomes.jsonl (horizon rows)."""
import json, os, time
import desk

STATE_DIR = os.environ.get("DESK_STATE_DIR", ".")
DEC_F = os.path.join(STATE_DIR, "shadow.jsonl")
OUT_F = os.path.join(STATE_DIR, "shadow_outcomes.jsonl")
HORIZONS = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "72h": 3 * 86400}
REDECIDE_S = 6 * 3600          # same token + same decision is logged at most every 6h
PENDING_MAX = 3000

def _ts(t): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

def record(st, tok, w, rec):
    """rec = flag record (official ENTER) or None. w['why'] holds the first failing rule."""
    t = time.time(); why = w.get("why") or ""
    if rec: dec, gate = "ENTER", "official"
    elif why.startswith("blocked:"): dec, gate = "ENTER", "shadow_only(" + why[8:] + ")"   # passed every coin rule; account caps blocked
    else: dec, gate = "SKIP", None
    sh = st.setdefault("shadow", {"last": {}, "pending": {}, "n": 0})
    last = sh["last"].get(tok)
    if last and last[1] == dec and t - last[0] < REDECIDE_S: return
    sh["last"][tok] = [t, dec]
    row = {"t": _ts(t), "token": tok, "chain": w.get("chain", "base"), "sym": w.get("sym"), "decision": dec, "gate": gate,
           "reason": None if dec == "ENTER" else why, "mcap": w.get("mcap"), "price": w.get("price"), "liq": w.get("liq"),
           "age_h": w.get("age_h"), "src": w.get("src"), "rule_checks": w.get("rule_checks"),
           "latency_s": round(t - w.get("first_seen", t))}
    with open(DEC_F, "a") as fh: fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
    sh["n"] += 1
    if row["price"]:
        key = f"{tok}@{int(t)}"
        sh["pending"][key] = {"tok": tok, "ch": row["chain"], "t0": t, "p0": row["price"], "dec": dec, "done": []}
        if len(sh["pending"]) > PENDING_MAX:
            for k in sorted(sh["pending"], key=lambda k: sh["pending"][k]["t0"])[:len(sh["pending"]) - PENDING_MAX]: sh["pending"].pop(k)

def outcomes(st, log, max_calls=4):
    """Batch DexScreener price lookups (30 tokens/call) for decisions that hit a horizon."""
    sh = st.get("shadow") or {}; P = sh.get("pending") or {}; t = time.time()
    due = [(k, h) for k, e in P.items() for h, s in HORIZONS.items() if h not in e["done"] and t - e["t0"] >= s]
    if not due: return
    by = {}
    for k, h in due: by.setdefault(P[k]["ch"], set()).add(P[k]["tok"])
    px, calls = {}, 0
    for ch, toks in by.items():
        toks = sorted(toks)
        for i in range(0, len(toks), 30):
            if calls >= max_calls: break
            calls += 1
            for p in desk.ds_token_pairs(toks[i:i + 30], chain=ch):
                for side in ("baseToken", "quoteToken"):
                    a = p[side]["address"]; a = a if ch == desk.SOL else desk.low(a)
                    if a in toks:
                        liq = desk.f((p.get("liquidity") or {}).get("usd"), 0)
                        if a not in px or liq > px[a][2]: px[a] = (desk.f(p.get("priceUsd")), desk.f(p.get("marketCap")) or desk.f(p.get("fdv")), liq)
    n = 0
    with open(OUT_F, "a") as fh:
        for k, h in due:
            e = P.get(k)
            if not e or e["tok"] not in px and calls >= max_calls: continue
            p, mc, liq = px.get(e["tok"], (None, None, 0))
            ret = round(p / e["p0"] - 1, 4) if p and e["p0"] else -1.0   # no pair left = treated as dead (-100%)
            fh.write(json.dumps({"t": _ts(t), "token": e["tok"], "chain": e["ch"], "decision": e["dec"], "decided": _ts(e["t0"]),
                                 "h": h, "ret": ret, "mcap": mc, "liq": round(liq), "dead": not p}, separators=(",", ":")) + "\n")
            e["done"].append(h); n += 1
            if len(e["done"]) == len(HORIZONS): P.pop(k, None)
    if n: log.info("shadow outcomes: %s horizon rows (%s DS calls)", n, calls)
    for tk in [tk for tk, v in sh.get("last", {}).items() if t - v[0] > 4 * 86400]: sh["last"].pop(tk)
