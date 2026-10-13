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
