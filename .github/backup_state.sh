#!/usr/bin/env bash
# Copies state/state.json to the orphan 'state' branch: one commit, force-pushed, so the history never grows.
# Called after every run and every 10 minutes while the market-hours loop is open (a killed job then loses
# at most 10 minutes of memory instead of the whole chunk).
# Usage: bash .github/backup_state.sh <owner/repo> <token>
set -u
[ -f state/state.json ] || { echo "No state to back up"; exit 0; }
repo="$1"; token="$2"
tmp=$(mktemp -d)
cp state/state.json "$tmp/state.json"
cd "$tmp" || exit 0
git init -q -b state
git config user.name "pipeline-bot"
git config user.email "pipeline-bot@users.noreply.github.com"
git add state.json
git commit -qm "state backup $(date -u +%FT%TZ)"
if git push -qf "https://x-access-token:${token}@github.com/${repo}.git" state; then
  echo "State backed up at $(date -u +%H:%M) UTC"
else
  echo "Backup push failed (will retry next time)"
fi
