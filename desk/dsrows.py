"""Dataset rows for the learning pipeline (zero AI). The watcher appends compact JSON rows to $DESK_STATE_DIR/ds_rows.jsonl;
the collector workflow ingests them. Rows older than 3h are trimmed (the collector polls every minute)."""
import json, os, time

def _f(): return os.path.join(os.environ.get("DESK_STATE_DIR") or os.path.dirname(os.path.abspath(__file__)), "ds_rows.jsonl")

def launchpad_guess(tok, w):
    for s in w.get("src", []):
        if s.startswith("grad_"): return s[5:]
        if s.startswith(("clanker", "zora")): return s.split("_")[0]
    if tok.endswith("pump"): return "pumpfun"
    if tok.endswith("bonk"): return "letsbonk"
    return None

def first_seen(tok, w, best):
    info = best.get("info") or {}
    tx = best.get("txns") or {}
    row = {"k": "fs", "t": int(time.time()), "tok": tok, "ch": w.get("chain", "base"), "dex": best.get("dexId"),
           "lp": launchpad_guess(tok, w), "sym": w.get("sym"), "age_h": w.get("age_h"), "mc": w.get("mcap"), "liq": w.get("liq"),
           "v24": w.get("vol24"), "v1": w.get("vol_h1"), "v5": round(((best.get("volume") or {}).get("m5")) or 0),
           "b1": (tx.get("h1") or {}).get("buys"), "s1": (tx.get("h1") or {}).get("sells"),
           "b5": (tx.get("m5") or {}).get("buys"), "s5": (tx.get("m5") or {}).get("sells"),
           "c1": (best.get("priceChange") or {}).get("h1"), "c5": (best.get("priceChange") or {}).get("m5"),
           "soc": len(info.get("socials") or []), "web": len(info.get("websites") or []), "img": bool(info.get("imageUrl")),
           "boost": (best.get("boosts") or {}).get("active") or 0, "src": w.get("src", [])[:6], "why": w.get("why")}
    _append(row)

def evaluated(tok, w):
    ac = w.get("audit") or {}; wa = w.get("wash") or {}; fk = w.get("fake") or {}; wl = w.get("wallets") or {}
    row = {"k": "ev", "t": int(time.time()), "tok": tok, "ch": w.get("chain", "base"), "mc": w.get("mcap"), "liq": w.get("liq"),
           "why": w.get("why"), "warn": w.get("warn"), "verdict": ac.get("verdict"), "because": ac.get("because"), "checks": ac.get("checks"),
           "top10": ac.get("top10"), "linked": (w.get("linked") or {}).get("pct", ac.get("linked_pct")), "bundle": ac.get("bundle_pct"),
           "ub1": wa.get("h1_unique_buyers"), "us1": wa.get("h1_unique_sellers"), "fc_mc": fk.get("first"), "ub24": fk.get("buyers24"),
           "wsig": wl.get("signal"), "smart": wl.get("smart"), "holders": ac.get("holders")}
    _append(row)

def _append(row):
    try:
        with open(_f(), "a") as fh: fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception: pass

def trim(max_age=3 * 3600):
    p = _f()
    if not os.path.exists(p): return 0
    cut = time.time() - max_age; keep = []
    for line in open(p):
        try:
            if json.loads(line)["t"] >= cut: keep.append(line)
        except Exception: pass
    tmp = p + ".tmp"; open(tmp, "w").writelines(keep); os.replace(tmp, p)
    return len(keep)
