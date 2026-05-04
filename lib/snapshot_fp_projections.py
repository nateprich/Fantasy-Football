"""Snapshot FantasyPros projections to disk.

In-season: pulls weekly projections for the current NFL week.
Off-season: pulls season-total projections (week=0).

Designed to run daily via launchd. Idempotent — same date runs are
overwritten; different dates accumulate.

The NFL season runs roughly:
  - Preseason kickoff: first Thursday after Labor Day (early September)
  - Last regular season game: ~early January
  - Playoffs: through mid-February
We treat September through January as in-season for projection purposes.

Run:
    python -m lib.snapshot_fp_projections                   # auto-detect
    python -m lib.snapshot_fp_projections --week 8          # force week
    python -m lib.snapshot_fp_projections --week 0          # force season-total
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from lib.fantasypros import DEFAULT_POSITIONS, fetch_projections

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "fp_projections"


def current_nfl_week(today: date | None = None) -> int:
    """Return the current NFL week (1..18) or 0 if it's offseason.

    Approximation: week 1 starts the first Thursday after Labor Day. We use a
    simple rule: Sept 4 ± a few days. Good enough for snapshot routing; the
    exact week number isn't critical for the dataset (weeks are stamped in
    the file path).
    """
    today = today or date.today()
    year = today.year
    # Roughly: first week of September → week 1
    season_start = date(year, 9, 4)
    season_end = date(year + 1 if today.month < 9 else year + 1, 1, 7)
    if today.month >= 2 and today.month < 9:
        return 0  # offseason
    if today < season_start:
        return 0
    if today > season_end:
        return 0
    days_in = (today - season_start).days
    week = days_in // 7 + 1
    return max(1, min(week, 18))


def snapshot(season: int, week: int, scoring_systems: list[str], positions=DEFAULT_POSITIONS) -> Path:
    """Pull projections for all (position × scoring) and save raw JSON to today's folder."""
    today = date.today().isoformat()
    out_dir = OUT_DIR / today / f"week_{week:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for pos in positions:
        try:
            data = fetch_projections(season, pos, week=week, use_cache=False)
        except Exception as e:  # noqa: BLE001
            print(f"  WARN {pos} {season} W{week}: {e}", file=sys.stderr)
            continue
        path = out_dir / f"{pos}.json"
        path.write_text(json.dumps(data))
        summary.append({"position": pos, "players": len(data.get("players", []))})

    # Append index entry
    index_path = OUT_DIR / "index.csv"
    is_new = not index_path.exists()
    with index_path.open("a") as f:
        if is_new:
            f.write("date,season,week,position,players\n")
        for s in summary:
            f.write(f"{today},{season},{week},{s['position']},{s['players']}\n")

    print(f"\nProjections snapshot {today} W{week:02d} → {out_dir}", file=sys.stderr)
    for s in summary:
        print(f"  {s['position']:<5} players={s['players']}", file=sys.stderr)
    return out_dir


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, default=date.today().year)
    p.add_argument("--week", type=int, default=None,
                   help="NFL week (1-18) or 0 for season-total. Default: auto-detect.")
    p.add_argument("--positions", nargs="+", default=list(DEFAULT_POSITIONS))
    args = p.parse_args()

    week = args.week if args.week is not None else current_nfl_week()
    if week == 0:
        print(f"Offseason detected (or --week 0): pulling season-total projections", file=sys.stderr)
    else:
        print(f"In-season: pulling week {week} projections", file=sys.stderr)

    snapshot(args.season, week, scoring_systems=["points_ppr"], positions=args.positions)


if __name__ == "__main__":
    main()
