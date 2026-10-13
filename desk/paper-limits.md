# Paper limits (v2, same rules as risk-limits.md, fake money, updated 2026-10-11 by user request)
chain: Base
leverage: off
mode: PAPER until the user says REAL ON. real_execution: off

## Sizing (never all in)
pot_start_usd: 2000
size_per_trade: 5% of current pot (about $100 at start)
max_names_open: 3
min_cash: 70% of pot at all times
never_add_to_loser: true
max_new_trades_per_day: 2

## Entry
entry_market_cap: under $400k preferred; skip if above $1M
must pass Auditor (sellable, low tax, no mint/blacklist/pause/tax powers, no outsized wallet)
liquidity: at least $50k
note big-wallet early buying as a plus

## Exits: scale out on the rise (by market cap)
- sell 25% at $500k
- sell 25% at $1M
- sell 25% at $2.5M
- last 25% is the RUNNER: hold only while several big wallets are accumulating and holding.
  Sell a quarter of it at $5M, a quarter at $10M, a quarter at $20M.
  Runner trailing stop: out if it falls 35% from its high.
- TOP EXIT: at $30M market cap or higher, be fully out by $50M. Above $30M, exit the rest on any 20% drop from the high or when big wallets start selling. Never ride a $30M+ top back down.
- if entry market cap is already past a rung, use the next rungs.

## Losses and time
hard_stop: -25% from entry on whatever is still held
after first take-profit: stop moves to entry (breakeven)
liquidity down 40%, unsellable, or owner action: exit everything now
no first take-profit within 48h and no big-wallet buying: close
runner max hold on the desk: 14 days. Past that, flag the user to move it to the cold wallet by hand.

## Buy-back (re-entry) after a big run
- after a full exit on a name that ran, put it on /workspace/desk/rebuy-watch.md
- re-enter only if ALL: price is down 50-90% from the peak; it has held a floor (no new low) for at least 3 days; several big wallets are still holding; liquidity steady; still passes Auditor
- size: normal 5% of pot. Stop: 15% below the floor.
- targets: sell 25% at 2x, 25% at 3x, rest trails (out on 35% drop from high), all out near the old peak
- drop from the watch list after 30 days or if it makes a new low

## Withdrawals and milestones
milestones: $5k, $10k, $25k, $50k, $100k, then every doubling
at each milestone, or when the pot doubles since the last withdrawal: tell the user and suggest withdrawing about 25% of profits. The user decides and does it by hand.

## Added 2026-10-11 (user approved all)
- scorecard leads with expectancy: win% x avg win - loss% x avg loss, per trade, after costs
- no rule changes until 30+ closed trades unless a rule is clearly broken; log each proposed change in journal/rule-changes.md BEFORE testing it
- circuit breaker: pot -10% in a day or -20% in a week = no new entries until the user reviews
- size scales with current pot (5%); after 3 losses in a row use 2.5% until the next win
- costs: every paper buy and sell pays 3% slippage (5% if liquidity under $100k) plus $0.50 gas each side
- max buy = 1.5% of pool liquidity
- skip if deployer or linked wallets hold over 5%, or top 10 non-LP holders over 40%
- no entries in a pair's first hour
- market filter: ETH down over 7% in 24h = half size; over 12% = no new entries
- wallets: hot (trading only), cold (long holds), profit (withdrawals). Never unlimited approvals; revoke old approvals monthly
- real trades: log to journal/tax-log.csv (date, token, contract, side, amount, USD value, fee, tx hash)
- every losing trade: one-line "why the rules let it in" in the paper log

## Added 2026-10-13 (user approved; enforced by tools/watcher.py)
- chains: Base and Solana (Fomo app). Every alert names the chain.
- copycat skip: tickers of established coins (BTC, ETH, SOL, USDC, BRETT, TOSHI, ...), hidden/bidi or lookalike letters, or the same ticker as an earlier bigger token.
- fake market cap skip: already >= $500k market cap on the first 15-min candle; or liquidity >= 40% of market cap with under 100 buyers in 24h.
- wash / only-goes-up skip (any one): unique sellers < 10% of unique buyers in the last 1h; under 20 unique sellers in the last 1h; buys >= 5x sells in the last 1h.
- sell test per chain: Base = honeypot.is simulation (unchanged); Solana = Jupiter sell quote for ~$200 must route with under 10% price impact.
- Solana audit (RugCheck): reject if mint or freeze authority is live, any transfer fee or risky Token-2022 extension, LP under 90% locked/burned, RugCheck danger risk or rugged flag. Holder rules same as Base: creator + insider-flagged wallets over 5%, or top 10 non-pool holders over 40% = skip.
- Solana graduations tracked: pump.fun -> PumpSwap, LetsBonk -> Raydium, Meteora DBC -> Meteora/DAMM (GeckoTerminal lists + DexScreener search).
- 30-day long watchlist: every audit PASS, and any token seen at $300k+ market cap (not copycat/fake). Floor-watch arms when price is 70-92% below the peak and closes have held one range (high/low within 1.6x) for 5+ days. Floor breakout alert needs ALL: a completed 1h close 30%+ above the range top, 24h volume >= 3x the range daily average, 50+ unique sellers in 24h, and either 2+ proven wallets buying (Birdeye) or holders up 10%+ vs 4+ days earlier; then the full audit, wash screen, liquidity >= $50k, sizing and account gates still apply.
- unchanged: pool-lock, holder, sell-test rules, first-hour skip, $50k liquidity, 1.5%-of-liquidity max buy, ETH filter, circuit breaker.

## Added 2026-10-13: bundle filter
- Solana: RugCheck insiderNetworks (transfer-linked wallet clusters). Bundle % = sum over clusters of max(tokenAmount, currentHolding) / supply.
  REJECT if clusters total > 10% of supply, any one cluster > 5%, or graphInsidersDetected >= 50. bundle_pct is shown in alert evidence and message 2.
- Backtest: FLOYD REJECT (26.2% in 5 clusters, biggest 16.9%, 251 insider wallets); Bumo REJECT (37.1%); recent passes: SEALS still PASS (2.7%), FARM REJECT (10.1%), YILONGMA REJECT (95.8%).
- Base/EVM gap: no free cluster data (GoPlus gives top-10 holders only). Existing deployer-linked check (deployer + wallets it funded/transferred to, >5% = skip) and top-10 <= 40% remain the EVM proxy.
