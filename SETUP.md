# desk-cloud setup (GitHub Actions, public repo, ntfy alerts)

Read-only paper watcher. No wallets, no keys besides the Birdeye read key, no trades.
Public repo = unlimited free Actions minutes. Code and the `state` branch are public; secrets are not.

## 1. Log in (once, by the owner)
    gh auth login            # GitHub.com, HTTPS, browser; needs scopes: repo, workflow
    gh auth setup-git        # lets plain git push use the gh login

## 2. Create the public repo and push
    cd /workspace/desk-cloud
    git init -b main && git add -A && git commit -m "desk watcher (cloud)"
    gh repo create desk-cloud --public --source . --remote origin --push

## 3. Secrets (values are read from local files/env, never typed into the shell history)
    gh secret set NTFY_TOPIC < /workspace/desk/.ntfy_topic
    printf %s "$BIRDEYE_API_KEY" | gh secret set BIRDEYE_API_KEY      # run in a login shell: bash -l

## 4. Optional: seed state from the box (keeps the dedupe list, 30-day long watchlist, Birdeye counters)
    rm -rf /tmp/seed && mkdir /tmp/seed && cd /tmp/seed
    cp /workspace/desk/tools/.cache/watcher_state.json /workspace/desk/tools/.cache/birdeye_state.json .
    touch flags.jsonl && git init -q -b state && git add -A && git commit -qm "seed state"
    git push -f "https://github.com/$(gh api user -q .login)/desk-cloud.git" state:state
    cd /workspace/desk-cloud

## 5. Enable and start
    gh workflow enable desk-watcher
    gh workflow run desk-watcher
    gh run list --workflow desk-watcher --limit 3
    gh run watch                                        # follow the first run

## 6. Phone alerts
Install the ntfy app, subscribe to the topic in /workspace/desk/.ntfy_topic (server ntfy.sh).
Each flag = 2 pushes: (1) only the contract address, (2) details with chain.
The topic name is the only protection: anyone who knows it can read/post. Never commit it.

## 7. Box pulls flags for paper logging
    curl -s https://raw.githubusercontent.com/<owner>/desk-cloud/state/flags.jsonl
    # or: git fetch https://github.com/<owner>/desk-cloud.git state && git show FETCH_HEAD:flags.jsonl
Heartbeat: .../state/watcher_heartbeat.json  (unix field = last cycle)

## Notes
- Cron */5; each run loops ~4.5 min (DESK_RUN_SECONDS=270), job timeout 8 min, one run at a time (concurrency group).
  GitHub may delay scheduled runs by several minutes at busy times, and disables schedules after 60 days with no repo
  activity (the state commits are on a non-default branch, so re-run `gh workflow enable desk-watcher` if that happens).
- State branch is one force-pushed commit (no history growth).
- Birdeye: the 800 CU/day cap is counted per copy. If the box watcher also keeps running, stop it or both spend.
- Account gates use desk/paper-book.md (template: pot $2000, 0 open). Update it in the repo if the paper book changes.
