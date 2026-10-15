mode: PAPER
updated: 2026-10-15T15:21ET
pot_usd: 2000
cash_usd: 1948.29
open_count: 0
open: []
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
last_cycle: 2026-10-15 15:21 ET — SEALS PAPER-20261014-01 STOP HIT; closed at live ~$142k mcap (stop was $196k); net -$51.71; cash $1948.29; open 0; cloud watcher healthy
notes: Fake money only. Never copy these numbers into book.md. Rules: paper-limits.md v2.
