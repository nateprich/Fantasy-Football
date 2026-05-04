#!/bin/bash
# Daily FantasyPros snapshot. Smart behavior:
#  - In-season (Sep-Jan): pulls weekly projections every day
#  - Off-season (Feb-Aug): pulls season-total projections weekly on Sundays only
#  - Sundays year-round: also pulls dynasty/redraft/rookie rankings
# Auto-commits and pushes the snapshot to git.
set -e
cd "$HOME/code/Fantasy-Football"
source .venv/bin/activate
mkdir -p logs

DOW=$(date +%u)   # 1=Mon..7=Sun
MONTH=$(date +%m)
TODAY=$(date +%F)

# Detect in-season (Sep-Jan)
case "$MONTH" in
  09|10|11|12|01) IN_SEASON=1 ;;
  *)              IN_SEASON=0 ;;
esac

{
  echo "=== $TODAY (DOW=$DOW, in_season=$IN_SEASON) ==="

  if [ "$IN_SEASON" = "1" ]; then
    python -m lib.snapshot_fp_projections
  elif [ "$DOW" = "7" ]; then
    # Off-season Sundays: weekly season-total snapshot
    python -m lib.snapshot_fp_projections --week 0
  fi

  # Rankings: every Sunday, year-round
  if [ "$DOW" = "7" ]; then
    python -m lib.snapshot_fp_rankings
  fi
} >> logs/snapshot.log 2>&1

# Commit and push if there are new files in data/
if [ -n "$(git status --porcelain data/)" ]; then
    git add data/
    git commit -m "FP snapshot $TODAY" >> logs/snapshot.log 2>&1
    git push >> logs/snapshot.log 2>&1
fi
