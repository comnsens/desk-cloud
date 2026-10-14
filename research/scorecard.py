#!/usr/bin/env python3
"""Tool-accuracy scorecard (zero AI). Ground truth = 7-day outcome from final labels: BAD if rug (liquidity fell to <=20% of max)
or dead_early within 7 days; GOOD if alive at 7d with mc >= first-seen mc. Precision/recall of each check's REJECT for BAD, per chain.
Usage: python3 research/scorecard.py labels-*.jsonl > research/tool-accuracy.md"""
import json, sys, glob, collections
rows = []
for f in sys.argv[1:] or glob.glob("data/labels-*.jsonl"):
    for line in open(f):
        try: r = json.loads(line)
        except Exception: continue
        if r.get("deep") and r.get("ch") != "solana": rows.append(r)
def truth(r):
    fu7 = (r.get("fu") or {}).get("7d") or {}
    if r.get("rug") or (r.get("dead_early") and r.get("t_peak_h", 999) <= 168): return 1
    if fu7 and fu7.get("mc", 0) >= (r["fs"].get("mc") or 0) > 0: return 0
    return None
checks = {
    "goplus+honeypot.is (basic)": lambda d: d["basic"]["verdict"] == "REJECT",
    "our sell sim": lambda d: d.get("sell") in ("REJECT", "REVERT"),
    "bundle >10% / cluster >5%": lambda d: any(h.startswith("bundle") for h in d["risk"]["hard"]),
    "combined hard": lambda d: bool(d["risk"]["hard"]),
    "combined soft >= 30": lambda d: d["risk"]["soft"] >= 30,
}
print("# Tool accuracy (EVM)\n\nGround truth: 7-day outcome (BAD = rug or dead within 7d; GOOD = alive at 7d at/above first-seen mcap).",
      "RugCheck is Solana-only and is scored in the Solana study.\n")
print("| check | chain | n | TP | FP | FN | precision | recall |\n|---|---|---|---|---|---|---|---|")
for name, fn in checks.items():
    agg = collections.defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        y = truth(r)
        if y is None: continue
        try: p = fn(r["deep"])
        except Exception: continue
        for ch in (r["ch"], "all"):
            a = agg[ch]; a[0] += 1
            if p and y: a[1] += 1
            elif p and not y: a[2] += 1
            elif y: a[3] += 1
    for ch, (n, tp, fp, fn_) in sorted(agg.items()):
        pr = f"{tp/(tp+fp):.2f}" if tp + fp else "-"; rc = f"{tp/(tp+fn_):.2f}" if tp + fn_ else "-"
        print(f"| {name} | {ch} | {n} | {tp} | {fp} | {fn_} | {pr} | {rc} |")
print(f"\nRows with deep checks: {len(rows)}. A check becomes a live reject only with precision >= 0.9 on >= 30 BAD cases and no worse than GoPlus/honeypot.is.")
