# Case study: VEIL (Solana) — HgTGdc29D5bDMrDvsCLnorxz899VyDj7ZN7FPa6vpump
Written 2026-10-13 ~6:30 PM PT from free data (GeckoTerminal candles, DexScreener, RugCheck) + 4 Birdeye calls. All times PT. Mcap = price x supply.

## Lifecycle
| When (PT) | Event | Mcap |
|---|---|---|
| Oct 12 ~12:00 | pump.fun bonding curve completes -> PumpSwap pool 4MWjp1 opens (GT pool created 12:01) | ~$50k |
| 12:00-13:00 | first leg, steady 15-min higher closes | $50k -> $400k |
| 13:00-14:00 | vertical leg (parabolic 15-min candles, $1.0M crossed ~13:30) | $400k -> $4.7M high at ~14:00 |
| 13:47 | Meteora DLMM pool 2LhZHy added (liquidity spread to Meteora) | ~$2M |
| 14:00-15:00 | first pullback: -69% from the high (low $1.44M) | $4.7M -> $1.5M |
| Oct 12 15:00 - Oct 13 06:00 | base/range ~15 h: closes $1.6M-$3.9M, lows held $1.5M-$2.0M | range |
| Oct 13 07:00-09:00 | second leg | $3.2M -> $9.4M |
| 09:00-15:00 | high base, lows $6.0M-$7.4M (-36% max pullback) | $6M-$9.7M |
| Oct 13 16:00 | third leg, biggest volume hour ($2.15M) | $9.0M -> ATH ~$18.9M |
| 17:00-18:30 | pullback -32% from ATH | ~$12.8M now (liq ~$344k PumpSwap + ~$362k Meteora) |

Pattern: three legs. Each base held well above the prior base. Pullbacks: -69%, -36%, -32% so far. Support zones now: $11.9M (17:00 low), then $9.0M (pre-breakout), then $6.0-7.4M (Oct 13 base). This fits the breakout study's "delayed run" shape: a launch run, then a base that holds, then a higher run.

## Who holds / bought
- 22,765 holders. RugCheck: mint and freeze off, PumpSwap LP 100% burned, creator 0%. Bundle filter: 1 insider cluster (10 wallets, 1.7% bought, 0.9% still held), 10 insider wallets -> passes the bundle filter.
- **The 20.94% "top wallet" is not a person.** Owner 8683DyCs… is labelled by RugCheck as a "Pump Fun" program account (type AMM). It is excluded from top-10 math. Top 10 real holders total ~12%; the largest real wallet holds 2.3%.
- Birdeye top traders (24h): **9 of 10 are tagged "bundler"**, 2 are tagged "dev". The largest net buyer, 6TnWWZ (1.43% holder, "fresh"+"bundler"), bought $119k and sold $16k: still accumulating. EjhX8 (0.81%) and 7JCe3G also net buyers. Two "dev"-tagged wallets are net sellers (5yJqXz sold $32.8k; 8UVHRj roughly flat).
- Proven-wallet check (our rule: >= 20 trades, win rate >= 50%, profit): **none qualify**. Win rates are 12-24%, though 6TnW (+$3.9k) and 8UVH (+$14.6k) are profitable overall. Smart-money signal: NONE_CLEAR.
- Last 100 swaps covered only 8 seconds, mostly small amounts: heavy bot activity.

## Could the desk have flagged it?
- The cloud watcher first saw VEIL on **Oct 13 12:03 PM PT** at ~$7M (graduation lists). It rejected it every pass as `mcap_over_1M` and moved it to the long watchlist (state `no_data`: too young for 5 daily candles). The box watcher (Base-only then, ~10% uptime) never saw it. Solana support only went live Oct 13.
- Earliest theoretical flag: below $1M it was only for ~1h 15m after graduation (12:00-13:15 Oct 12). That is inside the first-hour skip, so **our rules would have skipped it anyway**. After that it was always above our $1M entry cap.
- The 1.5-3.9M base (Oct 12 15:00 - Oct 13 06:00) is the setup a "delayed run" rule would catch. The current floor-watch needs a 70-92% drawdown and a 5-day range, so it would not catch this either. That gap is an input for the dataset/analysis work, not a rule change (per paper-limits: no rule changes before 30 closed trades).

## Close-watch (cloud, from 2026-10-13)
`positions.json` "study" entry. Snapshot every 15 min to the state branch `study/VEIL.jsonl`: mcap, liquidity, buys/sells, unique 1h buyers/sellers, holders, top 5 holders.
ntfy alerts on: new ATH (> $18.9M), +25% in ~1h, -30% from the high (already true at start, so the first run sends one push), liquidity -30%, the Pump Fun account moving 2+ points, or wallet 6TnW moving 0.5+ points. Study only, not desk signals.
