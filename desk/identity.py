"""Wallet identities + behaviour (zero AI, LOG-ONLY). Sources, all public and login-free:
- pump.fun public frontend API: /users/<wallet> -> username, followers, x_username (cached 7 days)
- pump.fun swap-api trades per coin (cursor-paged, 100/page) and per wallet in a coin (userAddress filter)
Skipped: Fomo (no public API found; app-only), kolscan (unreachable), GMGN (Cloudflare-gated; automated access not allowed).
Behaviour per (wallet, coin): entry mcap, bought/sold USD, and whether they dumped early (sold >= 70% of the bag within 2h
of their first buy) -> labels 'holder' / 'flipper' / 'dumper (sells into followers)' when followers are large."""
import json, os, time, requests
import desk

PF_USERS = "https://frontend-api-v3.pump.fun/users/"
PF_TRADES = "https://swap-api.pump.fun/v2/coins/{mint}/trades"
desk.THROTTLE.setdefault("frontend-api-v3.pump.fun", 0.4); desk.THROTTLE.setdefault("swap-api.pump.fun", 0.3)
CALLS = {"n": 0}
SUPPLY = 1e9                                         # pump.fun coins: fixed 1B supply -> mcap = price x 1e9

def _get(url, params=None):
    desk._throttle(url.split("/")[2]); CALLS["n"] += 1
    try:
        r = requests.get(url, params=params, timeout=12, headers={"User-Agent": "desk-research/1"})
        return r.json() if r.status_code == 200 else None
    except Exception: return None

def name(w):
    p = os.path.join(desk.CACHE_DIR, f"pfuser_{w}.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 7 * 86400:
        try: return json.load(open(p))
        except Exception: pass
    d = _get(PF_USERS + w); d = d[0] if isinstance(d, list) and d else d
    out = {"username": d.get("username"), "followers": d.get("followers"), "x": d.get("x_username"), "kind": d.get("kind")} if isinstance(d, dict) and d.get("address") == w else {}
    os.makedirs(desk.CACHE_DIR, exist_ok=True); json.dump(out, open(p, "w")); return out

def trades(mint, max_pages=30, user=None):
    out, cur = [], None
    for _ in range(max_pages):
        q = {"limit": 100, **({"cursor": cur} if cur else {}), **({"userAddress": user} if user else {})}
        d = _get(PF_TRADES.format(mint=mint), q)
        if not d or not d.get("trades"): break
        out += d["trades"]; pg = d.get("pagination") or {}
        if not pg.get("hasMore"): return out, True
        cur = pg.get("nextCursor")
    return out, False

def early_buyers(mint, max_mc=100_000, max_pages=30):
    """Wallets whose FIRST buy was at mcap <= max_mc. Only complete when the coin is young (all trades reachable)."""
    tr, complete = trades(mint, max_pages)
    if not complete: return {"error": f"more than {max_pages * 100} trades; oldest not reached", "n": len(tr)}
    tr.sort(key=lambda t: t["slotIndexId"]); first_slot = tr[0]["slotIndexId"][:10] if tr else None
    W = {}
    for t in tr:
        w = t["userAddress"]; usd = desk.f(t.get("amountUsd"), 0); mc = desk.f(t.get("priceUsd"), 0) * SUPPLY
        e = W.setdefault(w, {"first_mc": None, "buy_usd": 0.0, "sell_usd": 0.0, "first_t": t["timestamp"], "launch_slot": t["slotIndexId"][:10] == first_slot})
        if t["type"] == "buy":
            if e["first_mc"] is None: e["first_mc"] = round(mc)
            e["buy_usd"] += usd
        else: e["sell_usd"] += usd
    eb = {w: e for w, e in W.items() if e["first_mc"] and e["first_mc"] <= max_mc and not e["launch_slot"]}
    return {"buyers": eb, "n_trades": len(tr)}

def behaviour(mint, w):
    """Did this wallet hold or dump? Uses its own trades in the coin (1-3 calls)."""
    tr, _ = trades(mint, 3, user=w)
    if not tr: return None
    tr.sort(key=lambda t: t["slotIndexId"])
    t0 = time.mktime(time.strptime(tr[0]["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
    bought = sum(desk.f(t.get("baseAmount"), 0) for t in tr if t["type"] == "buy") or 1
    sold2h = sum(desk.f(t.get("baseAmount"), 0) for t in tr if t["type"] == "sell" and time.mktime(time.strptime(t["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")) - t0 <= 7200)
    sold = sum(desk.f(t.get("baseAmount"), 0) for t in tr if t["type"] == "sell")
    bu = sum(desk.f(t.get("amountUsd"), 0) for t in tr if t["type"] == "buy"); su = sum(desk.f(t.get("amountUsd"), 0) for t in tr if t["type"] == "sell")
    return {"sold_2h_pct": round(sold2h / bought * 100), "sold_pct": round(sold / bought * 100), "pnl_usd": round(su - bu), "n": len(tr)}

def label(stats, followers):
    """stats: list of behaviour dicts across coins."""
    st = [s for s in stats if s]
    if not st: return "unknown"
    early_dump = sum(1 for s in st if s["sold_2h_pct"] >= 70) / len(st)
    if early_dump >= 0.6 and (followers or 0) >= 1000: return "dumper (sells into followers)"
    if early_dump >= 0.6: return "flipper"
    return "holder"
