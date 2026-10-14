# Tool accuracy (EVM)

Ground truth: 7-day outcome (BAD = rug or dead within 7d; GOOD = alive at 7d at/above first-seen mcap). RugCheck is Solana-only and is scored in the Solana study.

| check | chain | n | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|---|---|

Rows with deep checks: 0. A check becomes a live reject only with precision >= 0.9 on >= 30 BAD cases and no worse than GoPlus/honeypot.is.
