"""LOG-ONLY signal tags from first-seen rows (zero AI, no network). Picked by research/signal-survey.md backtest
(7,195 early tokens, 80 runners = 5x and >= $300k). Tags never gate alerts or paper trades; the weekly study grades them."""
import time

def tags(fs, e=None):
    t = []
    mc = fs.get("mc") or 0
    if not mc: return t
    b, s = fs.get("b1") or 0, fs.get("s1") or 0
    if b + s >= 20:
        br = b / (b + s)
        if br >= 0.6: t.append("SIG: buy pressure 60%+")
        elif 0.45 <= br < 0.55: t.append("SIG: flat buy/sell (weak)")
    vm = (fs.get("v1") or 0) / mc
    if vm >= 4: t.append("SIG: vol/mcap 4x+")
    if fs.get("curve") is False or fs.get("dex") == "pumpswap": t.append("SIG: graduated pool")
    elif fs.get("curve") is True: t.append("SIG: still on curve")
    h = (time.gmtime(fs.get("t") or time.time()).tm_hour - 4) % 24      # ET (EDT)
    if 12 <= h < 20: t.append("SIG: ET afternoon launch (weak)")
    if fs.get("img") and not fs.get("soc"): t.append("SIG: image no socials")
    if (fs.get("age_h") or 0) >= 12: t.append("SIG: survived 12h+")
    if e and (e.get("holders0") or 0) >= 1000: t.append("SIG: 1000+ holders early")
    return t

def lift_table(reg_tokens, now=None, early_mc=100_000):
    """Markdown lines: per-tag runner/rug lift on tokens first seen <= $100k and >= 24h old."""
    now = now or time.time(); rows = []
    for e in reg_tokens.values():
        fs = e.get("fs") or {}; mc = fs.get("mc") or 0
        if not mc or mc > early_mc or now - e.get("t0", now) < 86400: continue
        y = (e.get("max_mc") or 0) >= max(5 * mc, 300_000)
        rug = ((e.get("fu") or {}).get("24h") or {}).get("mc", 1) < 0.2 * mc
        rows.append((tags(fs, e), y, rug))
    if not rows: return ["_no labelled rows yet_"]
    n = len(rows); base = sum(r[1] for r in rows) / n; rb = sum(r[2] for r in rows) / n
    out = [f"Base: n={n}, runner (5x & >= $300k) {base*100:.2f}%, rug24 {rb*100:.0f}%", "",
           "| tag | n | runner % | lift | rug24 % |", "|---|---|---|---|---|"]
    names = sorted({x for r in rows for x in r[0]})
    for nm in names:
        g = [r for r in rows if nm in r[0]]
        if len(g) < 20: continue
        p = sum(r[1] for r in g) / len(g); rg = sum(r[2] for r in g) / len(g)
        out.append(f"| {nm} | {len(g)} | {p*100:.2f} | {p/base if base else 0:.2f} | {rg*100:.0f} |")
    return out
