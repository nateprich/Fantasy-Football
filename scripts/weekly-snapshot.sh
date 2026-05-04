#!/bin/bash
# Weekly FantasyPros rankings snapshot. Commits + pushes to GitHub.
set -e
cd "$HOME/code/Fantasy-Football"
source .venv/bin/activate
python -m lib.snapshot_fp_rankings >> logs/snapshot.log 2>&1
TODAY=$(date +%F)
if [ -n "$(git status --porcelain data/)" ]; then
    git add data/fp_snapshots/
    git commit -m "FP snapshot $TODAY" >> logs/snapshot.log 2>&1
    git push >> logs/snapshot.log 2>&1
fi
