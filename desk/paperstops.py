"""Continuous paper stop / take-profit fills (runs inside fastlane, every ~2.5 s tick).
Reads open paper positions from desk/paper-book.md on main (the routine writes it), checks each tick's live market cap,
and when a level is crossed records a fill. Fill model (paper-limits.md "Paper fills"):
  - stop: fill at the stop mcap if the first observed price after the cross is within 3% of the stop; if price gapped
    further below (skipped the stop between ticks), fill at the FIRST observed mcap below it (gap fill).
  - take-profit rung: fill at the rung mcap (no upside gap credit).
  - then slippage 3% (5% if liquidity < $100k) + $0.50 gas on the sell, same as every paper sell.
Fills append to paper_fills.jsonl on the "paper-fills" branch (GitHub contents API). Never trades, never pushes ntfy.
The routine reads that file and syncs paper-book.md / journal/paper-log.md from it."""
import base64, json, logging, os, re, time, requests

log = logging.getLogger("paperstops")
REPO = os.environ.get("GITHUB_REPOSITORY", "comnsens/desk-cloud")
BRANCH, FILE = "paper-fills", "paper_fills.jsonl"
BOOK_URL = f"https://raw.githubusercontent.com/{REPO}/main/desk/paper-book.md"
RUNGS = [500e3, 1e6, 2.5e6, 5e6, 10e6, 20e6, 30e6]
STOP_PCT, GAP_TOL, GAS = 0.25, 0.03, 0.50
S = requests.Session(); S.headers["User-Agent"] = "desk-paperstops/1"
_BOOK = {"at": 0, "open": []}
DONE = set()          # (id, event) already recorded (loaded from branch at start)

def parse_open(md):
    """Minimal parser for the 'open:' list in paper-book.md (key: value lines, '- id:' starts an item)."""
    out, cur, inside = [], None, False
    for line in md.splitlines():
        if re.match(r"^open:\s*\[\]\s*$", line): return []
        if re.match(r"^open:\s*$", line): inside = True; continue
        if inside and re.match(r"^\S", line): break
        if not inside: continue
        m = re.match(r"^\s*(-\s+)?([A-Za-z0-9_]+):\s*(.*)$", line)
        if not m: continue
        if m.group(1): cur = {}; out.append(cur)
        if cur is not None: cur[m.group(2)] = m.group(3).strip()
    return out

def num(x):
    try: return float(re.match(r"[-0-9.]+", str(x).replace(",", "").replace("$", "")).group())
    except Exception: return None

def levels(p):
    e = num(p.get("entry_mcap")) or 0
    tp1_done = str(p.get("tp1_hit", "")).lower() in ("true", "yes", "1") or (num(p.get("remaining_pct")) or 100) < 100
    stop = num(p.get("stop_mcap")) or (e if tp1_done else e * (1 - STOP_PCT))   # breakeven after first TP
    rungs = [r for r in RUNGS if r > e][:3]
    return stop, rungs

def gh(method, path, **kw):
    tok = os.environ.get("GH_TOKEN")
    if not tok: return None
    return S.request(method, f"https://api.github.com/repos/{REPO}/{path}", timeout=10,
                     headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}, **kw)

def load_done():
    try:
        r = S.get(f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{FILE}?t={int(time.time())}", timeout=8)
        if r.ok:
            for l in r.text.splitlines():
                try: x = json.loads(l); DONE.add((x["id"], x["event"]))
                except Exception: pass
    except Exception as e: log.warning("paperstops load: %s", type(e).__name__)

def append_fill(rec):
    """Append one JSON line to paper_fills.jsonl on the paper-fills branch (creates branch/file if missing). 3 tries."""
    for _ in range(3):
        try:
            r = gh("GET", f"contents/{FILE}?ref={BRANCH}")
            if r is None: log.info("PAPER FILL (no GH_TOKEN): %s", rec); return False
            if r.status_code == 404:
                b = gh("GET", f"git/ref/heads/{BRANCH}")
                if b.status_code == 404:
                    sha = gh("GET", "git/ref/heads/main").json()["object"]["sha"]
                    gh("POST", "git/refs", json={"ref": f"refs/heads/{BRANCH}", "sha": sha})
                body, sha = "", None
            else:
                j = r.json(); body, sha = base64.b64decode(j["content"]).decode(), j["sha"]
            body += json.dumps(rec, separators=(",", ":")) + "\n"
            put = {"message": f"paper fill {rec['id']} {rec['event']}", "branch": BRANCH, "content": base64.b64encode(body.encode()).decode()}
            if sha: put["sha"] = sha
            if gh("PUT", f"contents/{FILE}", json=put).status_code in (200, 201): return True
        except Exception as e: log.warning("paperstops append: %s", type(e).__name__)
        time.sleep(1)
    return False

def refresh_book(every=30):
    if time.time() - _BOOK["at"] < every: return _BOOK["open"]
    _BOOK["at"] = time.time()
    try:
        r = S.get(f"{BOOK_URL}?t={int(time.time())}", timeout=5)
        if r.ok: _BOOK["open"] = parse_open(r.text)
    except Exception as e: log.warning("paperstops book: %s", type(e).__name__)
    return _BOOK["open"]

def watch_tokens():
    return [(p.get("chain", "solana"), p["token"]) for p in _BOOK["open"] if p.get("token")]

def fill(p, event, level, obs_mcap, liq, frac):
    gap = event == "STOP" and obs_mcap < level * (1 - GAP_TOL)
    px = obs_mcap if gap else level
    e = num(p.get("entry_mcap")); size = num(p.get("position_value_at_entry_usd")) or (num(p.get("size_usd")) or 0)
    slip = 0.05 if (liq or 0) < 100_000 else 0.03
    gross = size * (px / e) * frac if e else 0
    net = max(0.0, gross * (1 - slip) - GAS)
    rec = {"id": p.get("id"), "symbol": p.get("symbol"), "token": p["token"], "chain": p.get("chain"), "event": event,
           "level_mcap": round(level), "first_observed_mcap": round(obs_mcap), "fill_mcap": round(px), "gap_fill": gap,
           "fraction_sold": frac, "slippage": slip, "gas_usd": GAS, "gross_usd": round(gross, 2), "net_usd": round(net, 2),
           "liq": round(liq or 0), "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": "fastlane continuous"}
    log.info("PAPER %s %s level=%s observed=%s fill=%s gap=%s", event, p.get("symbol"), round(level), round(obs_mcap), round(px), gap)
    DONE.add((rec["id"], event))
    if not append_fill(rec): DONE.discard((rec["id"], event))   # retry next tick
    return rec

def check(pairs_for, f):
    """pairs_for(chain, token) -> DexScreener pairs from this tick. f = desk.f number parser."""
    for p in refresh_book():
        if not p.get("token") or not p.get("id"): continue
        ps = pairs_for(p.get("chain", "solana"), p["token"])
        if not ps: continue
        best = max(ps, key=lambda x: f((x.get("liquidity") or {}).get("usd"), 0))
        mc = f(best.get("marketCap")) or f(best.get("fdv")); liq = f((best.get("liquidity") or {}).get("usd"), 0)
        if not mc: continue
        stop, rungs = levels(p)
        rem = (num(p.get("remaining_pct")) or 100) / 100
        if mc <= stop and (p["id"], "STOP") not in DONE:
            fill(p, "STOP", stop, mc, liq, rem); continue
        for i, r in enumerate(rungs):
            ev = f"TP{i + 1}"
            if mc >= r and (p["id"], ev) not in DONE: fill(p, ev, r, mc, liq, 0.25)
