"""Compare two FantasyPros ranking snapshots.

Run:
    python -m lib.fp_rank_delta --type dynasty --top 30
    python -m lib.fp_rank_delta --type rookie_RB --from 2026-05-03 --to 2026-05-10
    python -m lib.fp_rank_delta --type dynasty                     # auto: oldest vs newest
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

SNAPS = Path(__file__).resolve().parent.parent / "data" / "fp_snapshots"


def list_dates() -> list[str]:
    return sorted(d.name for d in SNAPS.iterdir() if d.is_dir())


def load(date: str, kind: str) -> dict:
    f = SNAPS / date / f"{kind}.json"
    if not f.exists():
        raise FileNotFoundError(f)
    return json.loads(f.read_text())


def to_frame(data: dict) -> pd.DataFrame:
    rows = []
    for p in data.get("players", []):
        rows.append({
            "player_id": p.get("player_id"),
            "name": p.get("player_name"),
            "team": p.get("player_team_id"),
            "pos": p.get("player_position_id"),
            "rank": int(p.get("rank_ecr") or 0),
            "tier": p.get("tier"),
            "rank_min": p.get("rank_min"),
            "rank_max": p.get("rank_max"),
            "rank_std": p.get("rank_std"),
        })
    return pd.DataFrame(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--type", required=True, help="snapshot type (e.g. dynasty, rookie_WR)")
    p.add_argument("--from", dest="from_", default=None, help="oldest snapshot date (default: earliest available)")
    p.add_argument("--to", default=None, help="newest snapshot date (default: latest available)")
    p.add_argument("--top", type=int, default=30, help="show top-N biggest movers")
    args = p.parse_args()

    dates = list_dates()
    if not dates:
        print("No snapshots found.")
        return
    d_from = args.from_ or dates[0]
    d_to = args.to or dates[-1]
    if d_from == d_to:
        print(f"Only one snapshot available ({d_from}); need at least two for delta.")
        return

    a = to_frame(load(d_from, args.type)).rename(columns={"rank": "rank_a"})
    b = to_frame(load(d_to, args.type)).rename(columns={"rank": "rank_b"})
    m = a.merge(b[["player_id", "rank_b"]], on="player_id", how="outer", indicator=True)
    m["rank_a"] = m["rank_a"].fillna(999).astype(int)
    m["rank_b"] = m["rank_b"].fillna(999).astype(int)
    m["delta"] = m["rank_a"] - m["rank_b"]   # positive = moved up in ranking

    risers = m[m["_merge"] != "right_only"].nlargest(args.top, "delta")[["name", "team", "pos", "rank_a", "rank_b", "delta"]]
    fallers = m[m["_merge"] != "right_only"].nsmallest(args.top, "delta")[["name", "team", "pos", "rank_a", "rank_b", "delta"]]
    new = m[m["_merge"] == "right_only"].sort_values("rank_b").head(args.top)[["name", "team", "pos", "rank_b"]]
    dropped = m[m["_merge"] == "left_only"].sort_values("rank_a").head(args.top)[["name", "team", "pos", "rank_a"]]

    print(f"=== {args.type}  {d_from} → {d_to} ===\n")
    print(f"Top {args.top} risers (positive delta = moved up in rank):")
    print(risers.to_string(index=False))
    print(f"\nTop {args.top} fallers:")
    print(fallers.to_string(index=False))
    if not new.empty:
        print(f"\nNew entries (not present in {d_from}):")
        print(new.to_string(index=False))
    if not dropped.empty:
        print(f"\nDropped (not present in {d_to}):")
        print(dropped.to_string(index=False))


if __name__ == "__main__":
    main()
