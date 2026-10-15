mode: PAPER
updated: 2026-10-15T17:10ET
pot_usd: 1948.29
cash_usd: 1850.88
open_count: 1
open:
  - id: PAPER-20261015-01
    symbol: Macca
    chain: solana
    token: AKotzk1dhcNangAJBtvNKtT9bhbSVZc2mMhRFgkFpump
    pair: 4FoNNNMMKMa66ppCiA9557cg63s2BNN9UoFcaKxXbmvg (PumpSwap)
    setup: strong_trend (cloud flag 2026-10-15 16:07 ET at $302k)
    opened: 2026-10-15 17:10 ET
    entry_mcap: 285865
    entry_liq: 51489
    size_usd: 97.41 (5% of pot $1948.29; max 1.5% of liq = $772)
    costs_buy_usd: 5.37 (5% slippage, liq < $100k, + $0.50 gas)
    position_value_at_entry_usd: 92.04
    remaining_pct: 100
    stop_mcap: 214399 (hard -25%)
    take_profits: 25% at $500k / 25% at $1M / 25% at $2.5M / runner 25% (quarters at $5M/$10M/$20M, trail -35% from high; top exit rules from $30M)
    time_stop: 2026-10-17 17:10 ET (close if no TP1 and no big-wallet buying)
    liquidity_exit: total liq down 40% from entry (below ~$30.9k) = exit all
    high_mcap: 285865
    note: Audit PASS live at 17:10 ET (RugCheck no risks, mint/freeze off, no transfer fee, pump AMM LP 100% locked, top10 non-pool ~26%, bundle 4.8% in 3 clusters (max 2.53%), graphInsiders 28; Jupiter $200 buy impact 3.2%, sell impact 3.1%). Smart money not checked (Birdeye 790/800 CU today).
closed:
  - id: PAPER-20261014-01
    symbol: SEALS
    chain: solana
    token: 3sKaoPryg4KrXubLWtoqF7MznLT7QNqKRxMMkkxTpump
    pair: HvYfFr8nm6bn9V93TDDMgqWwS2D2LsMn1Y3rDCToqeAB (PumpSwap)
    setup: big_buyer_cluster (cloud flag 2026-10-14 20:49 ET at $272k)
    opened: 2026-10-14 20:59 ET
    entry_mcap: 261248
    size_usd: 100.00
    costs_buy_usd: 5.50
    position_value_at_entry_usd: 94.50
    closed: 2026-10-15 15:21 ET
    exit_reason: STOP HIT (hard -25% at $195936; discovered at check with live mcap below stop)
    exit_mcap: 141978
    exit_liq: 42505
    high_mcap: 465600
    remaining_pct_at_exit: 100
    costs_sell_usd: 3.07 (5% slippage on $51.36 + $0.50 gas; liq < $100k)
    net_proceeds_usd: 48.29
    pnl_usd: -51.71
    pnl_pct: -51.7
    note: Mark-to-market at discovery (paper stops enforced on scheduled checks, not continuous). Live PumpSwap ~$142k / Gecko ~$141.5k vs stop $196k (~-45.6% from entry). Study: big_buyer_cluster PASS that failed to hold; overnight high $466k never tagged TP1 $500k.
  - id: PAPER-20260903-01
    symbol: WFOT
    result: VOID (old $5 drill, no fill time or price recorded; closed when pot reset to $2000)
last_cycle: 2026-10-15 17:10 ET — mid-day check: 4 new flags (FOMO, DADOGE, MIM REJECT; Macca PASS). PAPER BUY PAPER-20261015-01 Macca $97.41 at ~$286k mcap; pot $1948.29 (after SEALS loss), cash $1850.88; open 1; cloud watcher healthy
notes: Fake money only. Never copy these numbers into book.md. Rules: paper-limits.md v2.
