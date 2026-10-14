#!/usr/bin/env python3
"""Validate our EVM deep checks against known-clean coins and honeypot.is/GoPlus-flagged coins. Log-only tooling.
Usage: python3 research/validate_evm.py [--sample N]  -> research/evm-validation.json + summary to stdout"""
import json, os, sys, time, argparse
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, "desk"))
import desk, evm
CLEAN = [("base", "0x532f27101965dd16442e59d40670faf5ebb142e4", "BRETT"), ("base", "0xac1bd2486aaf3b5c0fc3fd868558b082a531b2b4", "TOSHI"),
         ("base", "0x4ed4e862860bed51a9570b96d89af5e1b0efefed", "DEGEN"), ("ethereum", "0x6982508145454ce325ddbe47a25d4ec3d2311933", "PEPE"),
         ("ethereum", "0xaaee1a9723aadb7afa2810263653a34ba2c21c7a", "MOG"), ("bsc", "0xfb5b838b6cfeedc2873ab27866079ac55363d37e", "FLOKI")]
def best_pair(ch, tok):
    ps = [p for p in desk.ds_token_pairs([tok], chain=ch) if evm.WNATIVE[ch] in (p["quoteToken"]["address"].lower(), p["baseToken"]["address"].lower())]
    return max(ps, key=lambda p: desk.f((p.get("liquidity") or {}).get("usd"), 0)) if ps else None
def sample_new(ch, n):
    net = {"base": "base", "bsc": "bsc", "ethereum": "eth"}[ch]; out = []
    for pg in (1, 2, 3, 4, 5):
        d = desk.get(f"{desk.GT}/networks/{net}/new_pools?page={pg}", ttl=600) or {}
        for p in d.get("data") or []:
            rel = p["relationships"]; b = rel["base_token"]["data"]["id"].split("_", 1)[1].lower(); q = rel["quote_token"]["data"]["id"].split("_", 1)[1].lower()
            if evm.WNATIVE[ch] in (b, q) and float(p["attributes"]["reserve_in_usd"] or 0) > 3000:
                out.append((ch, q if b == evm.WNATIVE[ch] else b, p["attributes"]["name"]))
        if len(out) >= n: break
    return out[:n]
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sample", type=int, default=8); a = ap.parse_args()
    toks = [(c, t, s, "clean") for c, t, s in CLEAN] + [(c, t, s, "new") for ch in ("base", "bsc", "ethereum") for c, t, s in sample_new(ch, a.sample)]
    res = []
    for ch, tok, sym, grp in toks:
        p = best_pair(ch, tok)
        if not p: continue
        r = {"ch": ch, "tok": tok, "sym": sym, "grp": grp, "dex": p.get("dexId"), "labels": p.get("labels"), "liq": desk.f((p.get("liquidity") or {}).get("usd"))}
        r["basic"] = evm.audit_basic(tok, ch)
        pn = desk.f(p.get("priceNative")); pu = desk.f(p.get("priceUsd")); kind = "v4" if "v4" in (p.get("labels") or []) else "v3" if "v3" in (p.get("labels") or []) else "v2"
        t0 = evm.CALLS["n"]
        r["sell"] = evm.sell_test(tok, ch, p["pairAddress"], kind, r["liq"] or 0, pu / pn if pn else 0, p.get("dexId") or "") if pn else None
        r["sell_calls"] = evm.CALLS["n"] - t0
        if grp == "new":
            t0 = evm.CALLS["n"]
            try:
                lb = evm.launch_buyers(tok, ch, p["pairAddress"], p.get("pairCreatedAt") or 0)
                r["launch"] = {k: lb.get(k) for k in ("n_buyers", "pct_total", "multi_recipient_txs", "error")}
                if lb.get("buyers"):
                    fc = evm.funding_clusters(tok, ch, lb, r["basic"].get("creator"), max_buyers=5)
                    r["bundle"] = {k: fc[k] for k in ("bundle_pct", "biggest_cluster_pct")}; r["bundle"]["n_clusters"] = len(fc["clusters"])
            except Exception as e: r["launch_err"] = str(e)[:120]
            r["deep_calls"] = evm.CALLS["n"] - t0
        res.append(r)
        hp = "HONEYPOT" if "honeypot" in r["basic"]["because"] else ""
        print(f"{grp:5} {ch:8} {sym[:14]:14} {r['dex']:12} {str(r['labels']):10} basic={r['basic']['verdict']:6} {','.join(r['basic']['because'])[:40]:40} "
              f"ours={ (r['sell'] or {}).get('status')} tax={(r['sell'] or {}).get('sell_tax', (r['sell'] or {}).get('sell_transfer_tax'))} rt={(r['sell'] or {}).get('round_trip_loss')} "
              f"maxtx={(r['sell'] or {}).get('max_tx_ok')} launch={r.get('launch')} bundle={r.get('bundle')} {r.get('launch_err','')} calls={r['sell_calls']}+{r.get('deep_calls')}", flush=True)
    json.dump(res, open(os.path.join(ROOT, "research", "evm-validation.json"), "w"), indent=1)
if __name__ == "__main__": main()
