"""Chart engine (zero AI). GeckoTerminal candles -> support/resistance, trend, volume spike, breakout/breakdown, floor, VWAP,
and two entry zones (dip-to-support, breakout-reclaim). All levels are returned in MARKET CAP terms (price x supply ratio).
Candles: 1m (last ~5h) + 5m (last ~3.5 days); 15m and 1h are built from the 5m candles, so a full read costs 2 GT calls."""
import time
import desk

def _agg(c, n):
    out = []
    for i in range(0, len(c) - len(c) % n, n):
        g = c[i:i + n]; out.append([g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4], sum(x[5] for x in g)])
    return out

def _ema(v, n):
    k = 2 / (n + 1); e = v[0]
    for x in v[1:]: e = x * k + e * (1 - k)
    return e

def _pivots(c, w=3):
    hi, lo = [], []
    for i in range(w, len(c) - w):
        if c[i][2] == max(x[2] for x in c[i - w:i + w + 1]): hi.append(c[i][2])
        if c[i][3] == min(x[3] for x in c[i - w:i + w + 1]): lo.append(c[i][3])
    return hi, lo

def _cluster(levels, tol=0.04):
    """Merge nearby pivots; strength = number of touches."""
    out = []
    for p in sorted(levels):
        if out and p <= out[-1]["px"] * (1 + tol):
            o = out[-1]; o["px"] = (o["px"] * o["n"] + p) / (o["n"] + 1); o["n"] += 1
        else: out.append({"px": p, "n": 1})
    return out

def read(pool, chain, mcap=None, price=None, quick=False):
    """quick=True: one call (5m) for instant flag messages; else 1m + 5m."""
    c5 = desk.gt_ohlcv(pool, chain, tf="minute", agg=5, limit=1000, ttl=120) or []
    c1 = [] if quick else (desk.gt_ohlcv(pool, chain, tf="minute", agg=1, limit=300, ttl=60) or [])
    if len(c5) < 12: return {"error": "not enough candles", "n5": len(c5)}
    c5 = [[x[0]] + [float(v) for v in x[1:]] for x in c5]; c1 = [[x[0]] + [float(v) for v in x[1:]] for x in c1]
    c15, c60 = _agg(c5, 3), _agg(c5, 12)
    px = price or c5[-1][4]; m = (mcap / px) if (mcap and px) else None
    M = (lambda p: round(p * m)) if m else (lambda p: p)
    hi, lo = [], []
    for c, w in ((c5, 3), (c15, 3), (c60, 2)):
        h, l = _pivots(c, w); hi += h; lo += l
    lv = _cluster(hi + lo)
    sup = sorted([x for x in lv if x["px"] < px * 0.995], key=lambda x: -x["px"])[:3]
    res = sorted([x for x in lv if x["px"] > px * 1.005], key=lambda x: x["px"])[:3]
    closes = [x[4] for x in c15]
    trend = "up" if len(closes) >= 50 and _ema(closes[-60:], 20) > _ema(closes[-60:], 50) * 1.01 and closes[-1] > _ema(closes[-60:], 20) else \
            "down" if len(closes) >= 50 and _ema(closes[-60:], 20) < _ema(closes[-60:], 50) * 0.99 else "flat"
    vols = [x[5] for x in (c1 or c5)[-40:]]; med = sorted(vols[:-3])[len(vols[:-3]) // 2] if len(vols) > 10 else 0
    spike = round(max(vols[-3:]) / med, 1) if med else None
    day = c5[-288:]; vw = sum((x[2] + x[3] + x[4]) / 3 * x[5] for x in day) / max(1e-9, sum(x[5] for x in day))
    prev_hi = max(x[2] for x in c60[-24:-1]) if len(c60) > 3 else None
    r1 = res[0]["px"] if res else None; s1 = sup[0]["px"] if sup else None
    brk = "breakout" if prev_hi and px > prev_hi and (spike or 0) >= 2 else "breakdown" if s1 and c15[-1][4] < s1 * 0.97 else None
    # floor: tightest 12h+ range in the last 3 days (lows held within 25%)
    floor = None
    lows = [x[3] for x in c60[-72:]]
    if len(lows) >= 12:
        fl = min(lows[-36:]); touches = sum(1 for x in lows[-36:] if x <= fl * 1.08)
        if touches >= 3: floor = {"mcap": M(fl), "touches": touches}
    z = {"dip": {"from": M(s1), "to": M(s1 * 1.05)} if s1 else None,
         "reclaim": {"above": M(r1), "to": M(r1 * 1.03)} if r1 else None}
    return {"t": int(time.time()), "trend": trend, "vol_spike_x": spike, "signal": brk, "vwap_24h": M(vw), "floor": floor,
            "support": [{"mcap": M(x["px"]), "touches": x["n"]} for x in sup], "resistance": [{"mcap": M(x["px"]), "touches": x["n"]} for x in res],
            "entry_zones": z, "n_candles": {"1m": len(c1), "5m": len(c5)}}

def fmt(x): return "?" if x is None else (f"${x/1e6:.2f}M" if x >= 1e6 else f"${x/1e3:.0f}k")

def line(ch):
    """One line for message_2."""
    if not ch or ch.get("error"): return "Chart: n/a"
    s = "/".join(fmt(x["mcap"]) for x in ch["support"][:2]) or "-"; r = "/".join(fmt(x["mcap"]) for x in ch["resistance"][:2]) or "-"
    z = ch["entry_zones"]
    parts = [f"Chart: S {s} | R {r} | VWAP {fmt(ch['vwap_24h'])} | trend {ch['trend']}"]
    if ch.get("vol_spike_x"): parts.append(f"vol {ch['vol_spike_x']}x")
    if ch.get("signal"): parts.append(ch["signal"].upper())
    if ch.get("floor"): parts.append(f"floor {fmt(ch['floor']['mcap'])} ({ch['floor']['touches']} touches)")
    if z.get("dip"): parts.append(f"dip zone {fmt(z['dip']['from'])}-{fmt(z['dip']['to'])}")
    if z.get("reclaim"): parts.append(f"reclaim >{fmt(z['reclaim']['above'])}")
    return " | ".join(parts)
