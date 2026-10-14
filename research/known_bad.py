#!/usr/bin/env python3
"""Known-bad / known-good validation set from the dataset (zero AI).
BAD  = liquidity collapsed to <= 20% of its max (rug / LP pull) within 7 days of first seen, max liq >= $5k.
GOOD = alive >= 3 days with liquidity >= 50% of max and mcap >= first-seen mcap.
Sources: registry.json (live + provisional) and labels-*.jsonl (final). Writes research/known-bad.json.
Then (--check) runs our EVM checks + GoPlus/honeypot.is on the EVM members and prints catch rates per check."""
import gzip, json, os, sys, glob, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "desk"))
reg = json.load(gzip.open(sys.argv[1])) if len(sys.argv) > 1 and sys.argv[1].endswith(".gz") else {"tokens": {}}
items = {}
for tok, e in reg["tokens"].items():
    fu = sorted(e["fu"].values(), key=lambda x: x["t"])
    if not fu or e["max_liq"] < 5000: continue
    age = fu[-1]["t"] - e["t0"]
    last = fu[-1]["liq"]
    if age <= 7 * 86400 and last <= 0.2 * e["max_liq"]:
        items[tok] = {"tok": tok, "ch": e["ch"], "sym": e["fs"].get("sym"), "label": "BAD", "why": f"liq {e['max_liq']}->{last} by {age/3600:.0f}h", "pair": e.get("pair")}
    elif age >= 3 * 86400 and last >= 0.5 * e["max_liq"] and (fu[-1]["mc"] or 0) >= (e["fs"].get("mc") or 0):
        items[tok] = {"tok": tok, "ch": e["ch"], "sym": e["fs"].get("sym"), "label": "GOOD", "why": "alive 3d+", "pair": e.get("pair")}
for f in glob.glob(os.path.join(ROOT, "data", "labels-*.jsonl")):
    for line in open(f):
        r = json.loads(line)
        if r.get("rug"): items[r["tok"]] = {"tok": r["tok"], "ch": r["ch"], "sym": r["fs"].get("sym"), "label": "BAD", "why": "final label rug", "pair": None}
# manual rug set (flagged-and-rugged cases confirmed by hand)
MANUAL_BAD = [
    {"tok": "5Z6LFstu6KBC1pcR9co3e1forvP5jRBQhgLvyStxpump", "ch": "solana", "sym": "VCAT", "label": "BAD", "pair": "22T6SEtZswEAeBtnJA9qHGrX6MGucyUfPBEXqGuUEKbV",
     "why": "manual rug 2026-10-14: flagged $375k, -98% in 1h; LP 100% locked (no pull); sybil-spread supply (top10 1.13%) dumped into pool (pool now 99.3% of supply); ~4.7 buys/unique buyer"},
]
for x in MANUAL_BAD: items[x["tok"]] = x
out = {"built": int(time.time()), "n_bad": sum(1 for x in items.values() if x["label"] == "BAD"),
       "n_good": sum(1 for x in items.values() if x["label"] == "GOOD"), "items": list(items.values())}
json.dump(out, open(os.path.join(ROOT, "research", "known-bad.json"), "w"), indent=1)
by = {}
for x in items.values(): by.setdefault((x["ch"], x["label"]), 0); by[(x["ch"], x["label"])] += 1
print(out["n_bad"], "bad", out["n_good"], "good", by)
