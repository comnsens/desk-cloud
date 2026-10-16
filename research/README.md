# Desk research (LOG-ONLY study feeds)

Zero-AI analysis over the collector dataset. Never places trades or sends phone alerts.

## Weekly study (Sunday)

```bash
python3 research/analyze.py --fetch --data data --provisional --out research/runner-signals.md
```

The collector also writes `weekly-YYYY-MM-DD.md` into the `data` release once each Sunday UTC (graduation + proven-wallet summary). Paper rules v2 stay frozen; this output only grades traits.

## Launchpad graduations (`desk/graduations.py`)

Logged by the collector every loop (no ntfy):

| Chain | Sources |
|-------|---------|
| Solana | pump.fun → PumpSwap, LetsBonk → Raydium, Meteora DBC → Meteora/DAMM (GeckoTerminal dex new pools + DexScreener) |
| Base | Clanker + Zora public APIs (launch = first full pool) |

At graduation: time, mcap, liq, holders, top10, buys/sells (1h). Follow-ups: **1h / 6h / 24h / 72h**. Assets: `grads-YYYY-MM.jsonl.gz`, `grads_registry.json.gz` on the `data` release.

## Proven-wallet scorecards (`desk/smart.py`)

Daily refresh from tracked big-buyer wallets in `smart_wallets.json`:

- **Scorecard fields:** trades, win_rate, pnl_usd, runners, months_active, label (holder/flipper/dumper)
- **proven:** ≥20 trades, win rate ≥50%, pnl > 0 (full realized history across coins we saw them in — not cherry-picked wins)
- **high_score (study tag):** proven OR (runners ≥ 2 and pnl > 0 and not a dumper)

When a high-score wallet buys a token, the watcher logs **`proven wallet in`** on flags and writes a `proven_buy` ds_rows row. Mode stays `log`; `alerts_mode: user_alerts_only` is unchanged (only SI user_alerts push the phone).

## Related

- `research/analyze.py` — runner signals + graduation trait bins + scorecard table
- `research/known_bad.py` — EVM deep-check validation set
- `collector/collect.py` — discovery, follow-ups, smart refresh, Sunday addendum
