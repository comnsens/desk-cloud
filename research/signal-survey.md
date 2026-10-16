# Signal survey (2026-10-15, log-only)

Backtest: collector registry, 7,195 tokens first seen <= $100k and >= 24h old (Oct 2026, mostly Solana/BSC/Base).
Target "runner" = later max mcap >= 5x entry and >= $300k (only 80 positives; zero $10M runners yet, so this is a proxy).
Rug24 = mcap at 24h < 20% of entry. Single-week sample: treat lifts as hints, not proof.

## Added (LOG-ONLY tags: desk/signals.py -> registry `sig`, weekly study lift table)
| tag | n | runner % | lift | rug24 % | evidence |
|---|---|---|---|---|---|
| 1000+ holders early | 203 | 8.9 | 8.0 | 16 | strong but confounded with older/graduated coins |
| survived 12h+ | 137 | 3.7 | 3.3 | 7 | moderate, small n |
| graduated pool (off bonding curve) | 2443 | 2.9 | 2.6 | 49 | strong n; high rug too |
| vol/mcap 4x+ (1h) | 304 | 2.6 | 2.4 | 12 | moderate |
| buy pressure 60%+ (1h) | 2581 | 1.8 | 1.6 | 21 | strong n, modest lift |
| still on curve | 4007 | 0.1 | 0.1 | 27 | strong negative |
| flat buy/sell 45-55% | 1493 | 0.07 | 0.06 | 56 | strong negative / rug tell |
| ET afternoon launch 12-8pm | 2370 | 0.55 | 0.5 | 35 | weak (one week) |
| image but no socials | - | - | ~0.3 | - | weak negative |

## Rejected / deferred
- Socials/website count: no lift (0-2.2x on n<150). Dropped as predictor.
- DexScreener boosts/paid profiles: 0 boosted tokens in the early set; can't test. Paid boosts are usually dev marketing; keep the field logged only.
- X/KOL social velocity: x connector capped ~1000 reads/day, no history to backtest; costly. Deferred.
- Telegram call channels: no free public API; scraping fragile/ToS. Rejected.
- CEX listing signals: irrelevant at <= $100k; listings come long after the move. Rejected.
- Dev-wallet serial-launcher history, fresh-wallet ratio: need per-token on-chain lookups (RPC/Birdeye budget); existing deployer_linked + bundle + sybil-spread checks partly cover it. Deferred until free source fits budget.
- Bundled/sniper supply, LP lock/burn, top10: already in safety checks (only on evaluated coins; not enough overlap to grade here).
- Narrative/meta clustering: needs name/theme labelling; deferred (could do zero-AI keyword clusters later).

## Proposed rule changes (NOT applied; need user approval, rules frozen until 30 closed trades)
1. Skip practice buys on coins with flat 45-55% buy/sell split in the last hour (0.07% runner rate, 56% rug).
2. Prefer graduated-pool coins; skip still-on-curve entries (0.1% runner rate).
3. Add "vol/mcap 4x+" and "buy pressure 60%+" as positive setup evidence.
