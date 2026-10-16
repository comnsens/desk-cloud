mode: PAPER
updated: 2026-10-15T21:52ET
pot_usd: 1817.33
cash_usd: 1817.33
open_count: 0
open: []
closed:
  - id: PAPER-20261015-02
    symbol: Starman
    chain: solana
    token: H2pkmDKUio79yzzj5cRF6fqf6Xeq6AmpTsVVGG9wpump
    pair: AW1NPKi2JC6zw17hUv4GttiMDECoengd6eWG62WRusFy (PumpSwap)
    setup: strong_trend (cloud flag 2026-10-15 17:43 ET at $458k)
    opened: 2026-10-15 20:58 ET
    entry_mcap: 888089
    entry_liq: 82648
    size_usd: 95.65
    costs_buy_usd: 5.28 (5% slippage, liq < $100k, + $0.50 gas)
    position_value_at_entry_usd: 90.37
    closed: 2026-10-15 21:52 ET
    exit_reason: STOP HIT (hard -25% at $666067; also liquidity-exit — liq ~$2.3k << $49589; discovered at proactive pass with live mcap far below stop)
    exit_mcap: 2101
    exit_liq: 2274
    high_mcap: 888089
    remaining_pct_at_exit: 100
    costs_sell_usd: 0.51 (5% slippage on $0.21 + $0.50 gas; liq < $100k; net floored at $0 because sell costs exceed residual)
    net_proceeds_usd: 0.00
    pnl_usd: -95.65
    pnl_pct: -100.0
    note: Mark-to-market at discovery (paper stops enforced on scheduled/proactive checks, not continuous). Live PumpSwap ~$2.1k / Gecko FDV ~$2.1k vs stop $666k (~-99.8% from entry). Liq ~$2.3k (liquidity-exit ~$49.6k also triggered). High since entry stayed at entry (~$888k); never hit TP1 $1M. Study: first entry above $400k preferred zone and near-limit wash (4.97x) — dumped within ~1h of the night-check fill (same strong_trend dump pattern as Macca/SEALS-era fails).
  - id: PAPER-20261015-01
    symbol: Macca
    chain: solana
    token: AKotzk1dhcNangAJBtvNKtT9bhbSVZc2mMhRFgkFpump
    pair: 4FoNNNMMKMa66ppCiA9557cg63s2BNN9UoFcaKxXbmvg (PumpSwap)
    setup: strong_trend (cloud flag 2026-10-15 16:07 ET at $302k)
    opened: 2026-10-15 17:10 ET
    entry_mcap: 285865
    entry_liq: 51489
    size_usd: 97.41
    costs_buy_usd: 5.37 (5% slippage, liq < $100k, + $0.50 gas)
    position_value_at_entry_usd: 92.04
    closed: 2026-10-15 18:34 ET
    exit_reason: STOP HIT (hard -25% at $214399; discovered at proactive pass with live mcap below stop)
    exit_mcap: 204657
    exit_liq: 43365
    high_mcap: 285865
    remaining_pct_at_exit: 100
    costs_sell_usd: 3.79 (5% slippage on $65.89 + $0.50 gas; liq < $100k)
    net_proceeds_usd: 62.10
    pnl_usd: -35.31
    pnl_pct: -36.2
    note: Mark-to-market at discovery (paper stops enforced on scheduled/proactive checks, not continuous). Live PumpSwap ~$205k / Gecko FDV ~$213k vs stop $214k. Liq ~$43k (above liquidity-exit ~$30.9k). High since entry stayed at entry (~$286k); never hit TP1 $500k. Study: strong_trend PASS that failed to hold after entry (same pattern as many ~1h-age flags).
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
last_cycle: 2026-10-15 21:52 ET — proactive pass: Starman PAPER-20261015-02 STOP HIT (+ liquidity-exit); pot $1817.33, cash $1817.33, open 0/3, new today 2/2; loss streak 3 (next size 2.5% until a win); day pot chg -9.1% (circuit breaker still off, -10% trip); study-only unchanged
notes: Fake money only. Never copy these numbers into book.md. Rules: paper-limits.md v2.
