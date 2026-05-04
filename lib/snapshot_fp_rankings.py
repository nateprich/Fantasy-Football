"""Snapshot FantasyPros rankings to disk for time-series analysis.

Pulls multiple ranking types and saves each as a dated JSON. Run weekly
(via cron / launchd) to build a longitudinal dataset. The intent is to
see how expert consensus drifts at key points (free agency period,
rookie draft, season). After enough snapshots accumulate, we can ask:
  - Which players moved most over the offseason?
  - Are dynasty ranks predictive at draft time vs. by week 8?
  - How fast does ECR react to news vs. lagging it?

Run:
    python -m lib.snapshot_fp_rankings              # standard pull
    python -m lib.snapshot_fp_rankings --types dynasty rookie  # subset
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import requests

from lib.fantasypros import _api_key

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "fp_snapshots"
BASE_URL = "https://api.fantasypros.com/public/v2/json/nfl"

# (label, endpoint path, query params)
# Note: "rookie" type requires per-position calls (position=ALL is rejected for that type),
# so we synthesize an ALL view by concatenating the position-specific responses.
RANKING_TYPES = {
    "dynasty":         ("consensus-rankings", {"type": "dynasty",  "position": "ALL"}),
    "redraft":         ("consensus-rankings", {"type": "draft",    "position": "ALL"}),
    "dynasty_QB":      ("consensus-rankings", {"type": "dynasty",  "position": "QB"}),
    "dynasty_RB":      ("consensus-rankings", {"type": "dynasty",  "position": "RB"}),
    "dynasty_WR":      ("consensus-rankings", {"type": "dynasty",  "position": "WR"}),
    "dynasty_TE":      ("consensus-rankings", {"type": "dynasty",  "position": "TE"}),
    "rookie_QB":       ("consensus-rankings", {"type": "rookie",   "position": "QB"}),
    "rookie_RB":       ("consensus-rankings", {"type": "rookie",   "position": "RB"}),
    "rookie_WR":       ("consensus-rankings", {"type": "rookie",   "position": "WR"}),
    "rookie_TE":       ("consensus-rankings", {"type": "rookie",   "position": "TE"}),
}


def fetch(season: int, label: str) -> dict | None:
    endpoint, params = RANKING_TYPES[label]
    url = f"{BASE_URL}/{season}/{endpoint}"
    headers = {"x-api-key": _api_key()}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR {label}: {e}", file=sys.stderr)
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, default=date.today().year)
    p.add_argument("--types", nargs="+", default=list(RANKING_TYPES.keys()),
                   help="Which ranking types to snapshot")
    args = p.parse_args()

    today = date.today().isoformat()
    out_dir = OUT_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for label in args.types:
        if label not in RANKING_TYPES:
            print(f"  SKIP unknown type: {label}", file=sys.stderr)
            continue
        print(f"  fetching {label}...", file=sys.stderr)
        data = fetch(args.season, label)
        if not data:
            continue
        path = out_dir / f"{label}.json"
        path.write_text(json.dumps(data, indent=None))
        n = len(data.get("players", []))
        experts = data.get("total_experts")
        summary.append({"type": label, "players": n, "experts": experts})

    # Append a CSV index entry so we can quickly see what's been collected
    index_path = OUT_DIR / "index.csv"
    is_new = not index_path.exists()
    with index_path.open("a") as f:
        if is_new:
            f.write("date,season,type,players,experts\n")
        for s in summary:
            f.write(f"{today},{args.season},{s['type']},{s['players']},{s['experts']}\n")

    print(f"\nSnapshot {today} written to {out_dir}", file=sys.stderr)
    for s in summary:
        print(f"  {s['type']:<14} players={s['players']:<4} experts={s['experts']}", file=sys.stderr)


if __name__ == "__main__":
    main()
