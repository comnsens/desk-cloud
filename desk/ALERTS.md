# Desk alerts (read this, every desk bot)
Owner talks only to Grok Bot. Grok Bot runs the desk.
Watcher (PRIMARY, since 2026-10-13): cloud copy on GitHub Actions, public repo https://github.com/comnsens/desk-cloud, every 5 min, no AI usage, Base + Solana.
Flags: https://raw.githubusercontent.com/comnsens/desk-cloud/state/flags.jsonl (one JSON line per flag, with message_1/message_2). Heartbeat: .../state/watcher_heartbeat.json
Push alerts go to ntfy topic in /workspace/desk/.ntfy_topic (two pushes per flag).
Box watcher (tools/watcher.py) is DISABLED by tools/WATCHER_DISABLED; alerts/pending.jsonl is no longer written. Delete the flag file only if the cloud watcher is retired.
Each flag line carries ready-made `message_1` and `message_2` fields (also in the webhook JSON).
Rules: /workspace/desk/paper-limits.md (v2 + 2026-10-11 + 2026-10-13 additions). Mode: PAPER. No real trades, no signing, no wallet keys ever.
Alert format to the owner (always two messages):
1) ONLY the contract address. No words, labels, punctuation, or formatting.
2) Symbol, chain (BASE or SOLANA) and launchpad, contract address again, setup type, market cap, liquidity, buy amount, sell levels, evidence, DexScreener link, time in ET.
Setup types: new pair, trending, viral, launchpad, big-buyer cluster, strong trend, floor breakout (30-day floor-watch).
Health check / wake routine: bash -lc /workspace/desk/tools/check_watcher.sh (starts or repairs the watcher, prints one JSON status line).

## Liquidity-drop alerts (added 2026-10-13, live)
Title "LIQ DROP <SYM>". Sent when total liquidity on an open, re-entry, user-alert, study or recently flagged coin falls 30%+ below its 24h high. Same two-message format (CA first). Treat it as an exit-risk alert. It re-arms after liquidity recovers above -20%.

## Fastlane (added 2026-10-13, live)
Workflow desk-fastlane polls every 2.5 s: positions, liq_watch, user_alerts, re-entry and study coins, plus coins flagged in the last 48h. It sends one batched DexScreener call per chain (cache-busted) and falls back to GeckoTerminal.
It handles all level / stop / trail / user / liquidity-drop pushes at priority urgent. The watcher no longer sends these (DESK_FASTLANE=1). It still runs re-entry and study.
Each run lasts about 5.5 min and starts its successor 80 s before it ends. Alert state is handed over via the private ntfy topic <topic>-ctl.
If it stops: gh workflow run fastlane.yml -R comnsens/desk-cloud. The cron runs every 5 min as a backup.

## Added 2026-10-13: stop ladder, chart lines, study moves (fastlane)
- Held coin with a ladder: add to positions.json "positions": {"symbol","token","chain","entry_mcap", "tp":[{"x":2,"sell_pct":25},{"x":3,"sell_pct":25},{"x":5,"sell_pct":25}] (or "mcap" instead of "x"), "trail_pct":30 (25-35), optional "stop_mcap"}.
  Pushes (urgent): STOP SET (5% below the nearest chart support, or entry -25%), TPn HIT (sell %), STOP MOVED (TP1 -> breakeven, TP2 -> TP1 level, then trail from the high, re-pushed on each +5% raise), STOP HIT.
- Every fastlane push and every new-coin flag's message_2 now ends with a chart line: support/resistance, VWAP, trend, volume spike, breakout/breakdown, floor, dip zone and reclaim level, in mcap terms.
- Study ATH / +25% vs the 1h low / -30% from high now run in the fastlane on rolling windows. The watcher only keeps the 15-min snapshots and the WALLET alerts.
- Runner score (log-only) shows on flags once research/runner-model.json exists (written by analyze.py when 7-day labels exist).
