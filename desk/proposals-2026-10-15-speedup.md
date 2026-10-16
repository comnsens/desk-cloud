# Proposals awaiting Craig's approval (2026-10-15) — NOT live
Current paper-book rules are unchanged (paper-limits.md). The cloud now logs shadow ENTER/SKIP decisions with
per-rule reasons and 1h/6h/24h/72h outcomes for every evaluated candidate (state/shadow*.jsonl); these never buy.

1. Cloud places official paper buys 24/7 (instead of waiting for a routine pass).
   + No missed entries overnight; fills at the flag price, stops already watched every few seconds by fastlane.
   - Removes the human/routine look before each practice buy; bad rule = more bad trades faster.
2. Raise the daily new-trade cap 2 -> 6 (keep 5% sizing, 3-loss size cut, -10%/day and -20%/week breakers, max 3 open).
   + Reaches the 30 closed trades needed for rule reviews in ~1 week instead of ~2-3.
   - Larger daily drawdown possible; trades within a day are correlated (same market mood).
3. Count shadow decisions toward the study (not toward the 30-trade freeze).
   + Dozens of graded samples/day for Sunday's study instead of 2.
   - Shadow fills are idealized (no slippage, no size limits), so they overstate results; label them separately.
