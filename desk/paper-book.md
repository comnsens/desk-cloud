mode: PAPER
updated: 2026-10-14T20:59ET
pot_usd: 2000
cash_usd: 1900
open_count: 1
open:
  - id: PAPER-20261014-01
    symbol: SEALS
    chain: solana
    token: 3sKaoPryg4KrXubLWtoqF7MznLT7QNqKRxMMkkxTpump
    pair: HvYfFr8nm6bn9V93TDDMgqWwS2D2LsMn1Y3rDCToqeAB (PumpSwap)
    setup: big_buyer_cluster (cloud flag 2026-10-14 20:49 ET at $272k)
    opened: 2026-10-14 20:59 ET (night check re-check)
    entry_mcap: 261248
    size_usd: 100.00
    costs_usd: 5.50 (5% slippage, liq < $100k, + $0.50 gas)
    position_value_at_entry_usd: 94.50
    remaining_pct: 100
    stop_mcap: 195936 (-25% from entry)
    take_profits: 25% at $500k; 25% at $1M; 25% at $2.5M; runner 25% (quarters at $5M/$10M/$20M, trail -35% from high, all out by $50M)
    after_tp1: stop to entry ($261k)
    time_stop: close 2026-10-16 20:59 ET if no TP1 and no big-wallet buying
    liquidity_exit: total liq down 40% from entry ($56.6k main pool) = exit all
    high_mcap: 261248
closed:
  - id: PAPER-20260903-01
    symbol: WFOT
    result: VOID (old $5 drill, no fill time or price recorded; closed when pot reset to $2000)
last_cycle: 2026-10-14 night check 20:59 ET — 1 new cloud flag (SEALS) re-checked PASS under v2, paper buy $100 at $261k mcap; cash $1900 (95%); ETH filter clear (-3.9%); circuit breaker off; cloud watcher healthy
notes: Fake money only. Never copy these numbers into book.md. Rules: paper-limits.md v2.
