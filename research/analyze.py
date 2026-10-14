#!/usr/bin/env python3
"""Which early features separate runners from duds and rugs? Zero AI, pure Python (no sklearn needed).
Usage: python3 research/analyze.py --data DIR [--provisional] [--out research/runner-signals.md] [--fetch]
  DIR holds labels-YYYY-MM.jsonl(.gz) from the 'data' release (and provisional.jsonl from the collector).
  --fetch downloads the labels assets from the GitHub release first (needs gh).
Population: tokens FIRST SEEN AT <= $100k mcap (incl. pre-graduation bonding-curve coins).
Primary label: later hit $10M+ (rare). Secondary labels: hit $5M+, hit $1M+. Rug = liquidity fell 80%+.
The model target is the primary label when it has >= 30 positives, else the strongest secondary label that does (stated in the report).
Labels are final after 30 days (or earlier when a coin is dead).
Validation is walk-forward by first-seen day: train on earlier days only, test on the next day (no lookahead).
"""
import argparse, glob, gzip, json, math, os, random, subprocess, time, datetime as dt

NUM = ["age_h", "mc", "liq", "v24", "v1", "v5", "b1", "s1", "b5", "s5", "c1", "c5", "soc", "web", "boost",
       "liq_mc", "bs1", "bs5", "v1_liq", "top10", "linked", "bundle", "ub1", "us1", "ub24", "fc_mult", "holders"]
CAT = ["ch", "lp", "dex", "why", "verdict", "wsig", "smart", "trending", "img"]
LOG = {"mc", "liq", "v24", "v1", "v5", "b1", "s1", "b5", "s5", "ub1", "us1", "ub24", "holders", "age_h"}

def load(d, prov):
    rows = []
    for p in sorted(glob.glob(os.path.join(d, "labels-*.jsonl*"))) + ([os.path.join(d, "provisional.jsonl")] if prov else []):
        if not os.path.exists(p): continue
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt") as fh:
            for line in fh:
                try: rows.append(json.loads(line))
                except Exception: pass
    seen = {}
    for r in rows: seen[r["tok"]] = r if (r.get("final") or r["tok"] not in seen) else seen[r["tok"]]
    return list(seen.values())

def f(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def feats(r):
    fs = r.get("fs") or {}; ev = r.get("ev") or {}
    x = {k: f(fs.get(k)) for k in ("age_h", "mc", "liq", "v24", "v1", "v5", "b1", "s1", "b5", "s5", "c1", "c5", "soc", "web", "boost")}
    mc, liq = x["mc"], x["liq"]
    x["liq_mc"] = liq / mc if mc and liq is not None else None
    x["bs1"] = (x["b1"] or 0) / ((x["s1"] or 0) + 1) if x["b1"] is not None else None
    x["bs5"] = (x["b5"] or 0) / ((x["s5"] or 0) + 1) if x["b5"] is not None else None
    x["v1_liq"] = x["v1"] / liq if liq and x["v1"] is not None else None
    for k, src in (("top10", "top10"), ("linked", "linked"), ("bundle", "bundle"), ("ub1", "ub1"), ("us1", "us1"), ("ub24", "ub24"), ("holders", "holders")):
        x[k] = f(ev.get(src))
    x["fc_mult"] = (mc / f(ev.get("fc_mc"))) if mc and f(ev.get("fc_mc")) else None
    why = (ev.get("why") or fs.get("why") or "none")
    c = {"ch": r.get("ch"), "lp": fs.get("lp") or "none", "dex": fs.get("dex") or "?", "why": why.split(":")[0].split("(")[0].split("_")[0] if why else "none",
         "verdict": ev.get("verdict") or "not_audited", "wsig": ev.get("wsig") or "none", "smart": ev.get("smart") or "none",
         "trending": "yes" if any(("trend" in s or "boost" in s) for s in fs.get("src") or []) else "no", "img": str(bool(fs.get("img")))}
    return x, c

TARGETS = [("hit_10m", "first seen <= $100k, later $10M+ (PRIMARY)"), ("hit_5m", "first seen <= $100k, later $5M+"),
           ("hit_1m", "first seen <= $100k, later $1M+")]
TARGET = "hit_10m"

def label(r):
    if r.get(TARGET): return "runner"
    if r.get("rug"): return "rug"
    return "dud"

def early(r):
    mc0 = f((r.get("fs") or {}).get("mc")); return mc0 is not None and 0 < mc0 <= 100_000

def quantile_bins(vals, q=4):
    v = sorted(x for x in vals if x is not None)
    if len(v) < q * 3: return None
    return sorted(set(v[int(len(v) * i / q)] for i in range(1, q)))

def binname(x, edges):
    if x is None: return "missing"
    for i, e in enumerate(edges):
        if x < e: return f"Q{i + 1} (<{e:.3g})"
    return f"Q{len(edges) + 1} (>={edges[-1]:.3g})"

def tx(k, v):
    if v is None: return None
    if k in LOG: return math.log1p(max(v, 0))
    return max(min(v, 1e6), -1e6)

# ---- logistic regression (L2, gradient descent) ----
def fit_lr(X, y, l2=1.0, it=400, lr=0.1):
    n, m = len(X), len(X[0]); w = [0.0] * m; b = 0.0
    for _ in range(it):
        gw = [0.0] * m; gb = 0.0
        for xi, yi in zip(X, y):
            z = b + sum(a * c for a, c in zip(w, xi)); p = 1 / (1 + math.exp(-max(min(z, 30), -30)))
            d = p - yi; gb += d
            for j in range(m): gw[j] += d * xi[j]
        w = [wj - lr * (g / n + l2 * wj / n) for wj, g in zip(w, gw)]; b -= lr * gb / n
    return w, b

def pred_lr(model, X):
    w, b = model
    return [1 / (1 + math.exp(-max(min(b + sum(a * c for a, c in zip(w, x)), 30), -30))) for x in X]

# ---- small decision tree (gini, depth 3) ----
def gini(y):
    if not y: return 0
    p = sum(y) / len(y); return 2 * p * (1 - p)

def fit_tree(X, y, names, depth=3, min_leaf=20):
    if depth == 0 or len(y) < 2 * min_leaf or sum(y) in (0, len(y)): return {"p": sum(y) / max(len(y), 1), "n": len(y)}
    best = None
    for j in range(len(X[0])):
        vals = sorted(set(x[j] for x in X))
        cands = [vals[int(len(vals) * q / 10)] for q in range(1, 10)] if len(vals) > 10 else vals[1:]
        for t in set(cands):
            L = [yi for xi, yi in zip(X, y) if xi[j] < t]; R = [yi for xi, yi in zip(X, y) if xi[j] >= t]
            if len(L) < min_leaf or len(R) < min_leaf: continue
            g = (len(L) * gini(L) + len(R) * gini(R)) / len(y)
            if best is None or g < best[0]: best = (g, j, t)
    if not best or best[0] >= gini(y): return {"p": sum(y) / len(y), "n": len(y)}
    _, j, t = best
    li = [i for i, x in enumerate(X) if x[j] < t]; ri = [i for i, x in enumerate(X) if x[j] >= t]
    return {"f": names[j], "j": j, "t": t, "n": len(y), "p": sum(y) / len(y),
            "L": fit_tree([X[i] for i in li], [y[i] for i in li], names, depth - 1, min_leaf),
            "R": fit_tree([X[i] for i in ri], [y[i] for i in ri], names, depth - 1, min_leaf)}

def pred_tree(t, x):
    while "f" in t: t = t["L"] if x[t["j"]] < t["t"] else t["R"]
    return t["p"]

def tree_txt(t, ind=""):
    if "f" not in t: return f"{ind}-> runner rate {t['p']*100:.1f}% (n={t['n']})\n"
    return (f"{ind}if {t['f']} < {t['t']:.3g}:  (n={t['n']})\n" + tree_txt(t["L"], ind + "    ") +
            f"{ind}else:\n" + tree_txt(t["R"], ind + "    "))

def auc(y, p):
    pos = [s for s, t in zip(p, y) if t]; neg = [s for s, t in zip(p, y) if not t]
    if not pos or not neg: return None
    neg.sort(); import bisect
    return sum(bisect.bisect_left(neg, s) + 0.5 * (bisect.bisect_right(neg, s) - bisect.bisect_left(neg, s)) for s in pos) / (len(pos) * len(neg))

def matrix(rows, names, stats=None):
    raw = [[tx(k, r[0].get(k)) for k in names] for r in rows]
    if stats is None:
        stats = []
        for j in range(len(names)):
            col = [x[j] for x in raw if x[j] is not None]
            mu = sum(col) / len(col) if col else 0; sd = (sum((c - mu) ** 2 for c in col) / len(col)) ** 0.5 if col else 1
            stats.append((mu, sd or 1))
    X = [[0.0 if x[j] is None else (x[j] - stats[j][0]) / stats[j][1] for j in range(len(names))] +
         [1.0 if x[j] is None else 0.0 for j in range(len(names))] for x in raw]
    return X, stats

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data"); ap.add_argument("--provisional", action="store_true")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner-signals.md")); ap.add_argument("--fetch", action="store_true")
    a = ap.parse_args()
    if a.fetch:
        os.makedirs(a.data, exist_ok=True)
        subprocess.run(["gh", "release", "download", "data", "-R", os.environ.get("DESK_REPO", "comnsens/desk-cloud"), "-p", "labels-*", "-D", a.data, "--clobber"], check=False)
    allrows = load(a.data, a.provisional)
    rows = [r for r in allrows if early(r)]
    global TARGET
    counts = {k: sum(1 for r in rows if r.get(k)) for k, _ in TARGETS}
    TARGET = next((k for k, _ in TARGETS if counts[k] >= 30), "hit_1m")
    data = [(feats(r)[0], feats(r)[1], label(r), r.get("t0") or 0, r) for r in rows]
    n = len(data); L = [d[2] for d in data]
    cnt = {k: L.count(k) for k in ("runner", "dud", "rug")}
    out = [f"# Runner signals (generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} by research/analyze.py, zero AI)", "",
           f"All tokens in dataset: {len(allrows)}. Early population (first seen <= $100k): {len(rows)}.", "",
           "| label | positives | rate |", "|---|---|---|"] + \
          [f"| {desc} | {counts[k]} | {counts[k] / max(len(rows), 1) * 100:.2f}% |" for k, desc in TARGETS] + \
          ["", f"Model target used below: **{dict(TARGETS)[TARGET]}**" + ("" if TARGET == "hit_10m" else " (primary has < 30 positives so far)"), ""]
    final = sum(1 for d in data if d[4].get("final"))
    out += [f"Tokens: {n} ({final} final 7-day labels, {n - final} provisional{' INCLUDED' if a.provisional else ''}). "
            f"Runners {cnt['runner']} ({cnt['runner'] / max(n, 1) * 100:.1f}%), duds {cnt['dud']}, rugs {cnt['rug']}.", ""]
    if a.provisional and n - final:
        out += ["**Provisional labels are incomplete (tokens < 7 days old); treat everything below as a pipeline test, not findings.**", ""]
    by_ch = {}
    for d in data: by_ch.setdefault(d[1]["ch"], []).append(d[2])
    out += ["## By chain", "| chain | n | runner % | rug % |", "|---|---|---|---|"]
    out += [f"| {c} | {len(v)} | {v.count('runner') / len(v) * 100:.1f} | {v.count('rug') / len(v) * 100:.1f} |" for c, v in sorted(by_ch.items())]
    if cnt["runner"] < 15:
        out += ["", f"Only {cnt['runner']} runners so far: too few for lift or models. Re-run when the dataset has 7-day labels (at least ~50 runners)."]
        open(a.out, "w").write("\n".join(out) + "\n"); print("\n".join(out)); return
    base = cnt["runner"] / n
    # ---- per-feature lift ----
    lifts = []
    for k in NUM:
        edges = quantile_bins([d[0].get(k) for d in data])
        if not edges: continue
        g = {}
        for d in data: g.setdefault(binname(d[0].get(k), edges), []).append(d[2])
        for b, v in g.items():
            if len(v) >= 15: lifts.append((v.count("runner") / len(v) / base, k, b, len(v), v.count("rug") / len(v)))
    for k in CAT:
        g = {}
        for d in data: g.setdefault(str(d[1].get(k)), []).append(d[2])
        for b, v in g.items():
            if len(v) >= 15: lifts.append((v.count("runner") / len(v) / base, k, b, len(v), v.count("rug") / len(v)))
    lifts.sort(reverse=True)
    out += ["", f"## Feature lift (runner rate in the bin / overall {base * 100:.1f}%; bins with n >= 15)", "| lift | feature | bin | n | rug % |", "|---|---|---|---|---|"]
    out += [f"| {l:.2f} | {k} | {b} | {m} | {rg * 100:.0f} |" for l, k, b, m, rg in lifts[:20]]
    out += ["", "Lowest lift (dud/rug markers):", "| lift | feature | bin | n | rug % |", "|---|---|---|---|---|"]
    out += [f"| {l:.2f} | {k} | {b} | {m} | {rg * 100:.0f} |" for l, k, b, m, rg in lifts[-10:]]
    # ---- walk-forward models ----
    data.sort(key=lambda d: d[3])
    day = lambda d: dt.datetime.fromtimestamp(d[3], dt.timezone.utc).strftime("%Y-%m-%d")
    days = sorted(set(day(d) for d in data))
    folds = [(set(days[:i]), days[i]) for i in range(1, len(days)) if sum(1 for d in data if day(d) in days[:i]) >= 200]
    if not folds:
        cut = int(len(data) * 0.7); folds = [("idx", cut)]
    names = NUM; res = []; yall = []; plr = []; ptr = []; last_tree = None
    for tr, te in folds:
        train = [d for d in data if day(d) in tr] if tr != "idx" else data[:te]
        test = [d for d in data if day(d) == te] if tr != "idx" else data[te:]
        if not test or sum(1 for d in train if d[2] == "runner") < 3: continue
        if len(train) > 20000:     # keep runtime sane: all positives + the most recent negatives (no lookahead)
            pos = [d for d in train if d[2] == "runner"]; neg = [d for d in train if d[2] != "runner"][-(20000 - len(pos)):]
            train = sorted(pos + neg, key=lambda d: d[3])
        Xtr, sts = matrix(train, names); Xte, _ = matrix(test, names, sts)
        ytr = [1 if d[2] == "runner" else 0 for d in train]; yte = [1 if d[2] == "runner" else 0 for d in test]
        m = fit_lr(Xtr, ytr); t = fit_tree([[tx(k, d[0].get(k)) if d[0].get(k) is not None else -1e9 for k in names] for d in train], ytr, names)
        p1 = pred_lr(m, Xte); p2 = [pred_tree(t, [tx(k, d[0].get(k)) if d[0].get(k) is not None else -1e9 for k in names]) for d in test]
        yall += yte; plr += p1; ptr += p2; last_tree = t; last_lr = m
        res.append((te if tr != "idx" else "time-ordered 30% holdout", len(train), len(test), sum(yte), auc(yte, p1), auc(yte, p2)))
    out += ["", "## Walk-forward validation (train on earlier days only)", "| test day | train n | test n | test runners | LR AUC | tree AUC |", "|---|---|---|---|---|---|"]
    out += [f"| {a_} | {b_} | {c_} | {d_} | {e_ if e_ is None else round(e_, 3)} | {f_ if f_ is None else round(f_, 3)} |" for a_, b_, c_, d_, e_, f_ in res]
    if yall:
        k = max(1, len(yall) // 10)
        top = sorted(zip(plr, yall), reverse=True)[:k]
        out += ["", f"Pooled out-of-sample: LR AUC {auc(yall, plr)}, tree AUC {auc(yall, ptr)}; top-10% by LR score hit rate "
                f"{sum(y for _, y in top) / k * 100:.1f}% vs base {sum(yall) / len(yall) * 100:.1f}%."]
        w, _ = last_lr
        coef = sorted(((w[j], names[j]) for j in range(len(names))), key=lambda x: -abs(x[0]))[:10]
        out += ["", "Logistic weights (last fold, standardized; + = more runner-like):", ""] + [f"- {nm}: {c:+.2f}" for c, nm in coef]
        out += ["", "Decision tree (last fold, depth 3):", "```", tree_txt(last_tree).rstrip(), "```"]
    out += ["", "Notes: features are what the desk saw at first sight (+ its audit within 2h). Peaks are sampled at 1h/6h/24h/3d/7d and refined with 4h candles for movers, so short wicks between samples are missed."]
    open(a.out, "w").write("\n".join(out) + "\n"); print("\n".join(out))

if __name__ == "__main__":
    main()
