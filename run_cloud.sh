#!/bin/bash
# Cloud entrypoint: one ~4.5 min run of watcher cycles. State lives in $STATE (checked out from the "state" branch).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd); STATE=${STATE:-$ROOT/state}
mkdir -p "$STATE" "$ROOT/.http-cache"
export DESK_DIR="$ROOT/desk" DESK_STATE_DIR="$STATE" DESK_BE_STATE="$STATE/birdeye_state.json" \
       DESK_ALERTS_FILE="$STATE/flags.jsonl" DESK_LOG_FILE="$STATE/watcher.log" DESK_CACHE_DIR="$ROOT/.http-cache" \
       DESK_PAPER_BOOK="$ROOT/desk/paper-book.md" DESK_RUN_SECONDS="${DESK_RUN_SECONDS:-270}"
touch "$DESK_ALERTS_FILE"
timeout $(( ${DESK_RUN_SECONDS%.*} + 60 )) python3 -u "$ROOT/desk/watcher.py"
# keep state small: drop the rotating log backups from the branch
rm -f "$STATE"/watcher.log.[0-9]
