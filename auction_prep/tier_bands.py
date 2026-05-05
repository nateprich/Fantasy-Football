"""Production-tier salary confidence bands.

Bins historical auction acquisitions by realized fantasy points in the
acquisition season, computes p25/p50/p75 salary per tier per year. Output
replaces the eyeball-the-top-30 step with proper percentile bands.

Run:
    python -m auction_prep.tier_bands --position RB
    python -m auction_prep.tier_bands --position QB --years 2023 2024 2025
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from lib import mfl
from lib.league import LEAGUE_MIN_SALARY, SCORING_POSITIONS
from salary_efficiency.analyze import build_season_dataframe

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "auction_prep"


# Per-position tier definitions (point thresholds reflect typical fantasy starter tiers)
TIER_THRESHOLDS = {
    "QB":  [(350, "T1"), (290, "T2"), (220, "T3"), (150, "T4")],
    "RB":  [(280, "T1"), (220, "T2"), (160, "T3"), (100, "T4")],
    "WR":  [(260, "T1"), (200, "T2"), (140, "T3"), (90,  "T4")],
    "TE":  [(180, "T1"), (130, "T2"), (90,  "T3"), (50,  "T4")],
    "PK":  [(160, "T1"), (130, "T2"), (100, "T3")],
    "Def": [(160, "T1"), (130, "T2"), (100, "T3")],
}


def assign_tier(points: float, position: str) -> str:
    for threshold, label in TIER_THRESHOLDS.get(position, []):
        if points >= threshold:
            return label
    return "T5"  # below all thresholds


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--position", required=True, choices=SCORING_POSITIONS)
    p.add_argument("--years", nargs="+", type=int, default=[2023, 2024, 2025])
    args = p.parse_args()

    # Build per-season frames (auction year + realized points + position)
    print(f"Loading historical bids 2017..{max(args.years)}...", file=sys.stderr)
    history = mfl.HistoricalBids.load(2017, max(args.years))

    rows = []
    for y in args.years:
        df = build_season_dataframe(y, history)
        sub = df[df["position"] == args.position].copy()
        # Use the auction price as the salary signal (HistoricalBids.by_year is
        # original-bid only). For tier banding we want what teams paid AT acquisition,
        # which is exactly what we get if we filter to year-1 contracts.
        sub["tier"] = sub["points"].apply(lambda p: assign_tier(p, args.position))
        sub["year"] = y
        rows.append(sub[["year", "name", "tier", "points", "salary"]])
    pooled = pd.concat(rows, ignore_index=True)
    pooled = pooled[pooled["salary"] >= LEAGUE_MIN_SALARY]

    # Per-tier × year stats
    summary = (
        pooled.groupby(["tier", "year"])
        .agg(n=("salary", "count"),
             p25=("salary", lambda s: int(np.percentile(s, 25))),
             p50=("salary", lambda s: int(np.percentile(s, 50))),
             p75=("salary", lambda s: int(np.percentile(s, 75))))
        .reset_index()
    )

    # Per-tier across all pooled years
    pooled_summary = (
        pooled.groupby("tier")
        .agg(n=("salary", "count"),
             p25=("salary", lambda s: int(np.percentile(s, 25))),
             p50=("salary", lambda s: int(np.percentile(s, 50))),
             p75=("salary", lambda s: int(np.percentile(s, 75))),
             mean=("salary", lambda s: int(s.mean())))
        .reset_index()
        .sort_values("tier")
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv = OUT_DIR / f"tier_bands_{args.position}.csv"
    summary.to_csv(csv, index=False)

    print(f"\n=== {args.position} salary by tier (pooled {min(args.years)}-{max(args.years)}, "
          f"n={len(pooled)}) ===\n")
    fmt = pooled_summary.copy()
    for c in ("p25", "p50", "p75", "mean"):
        fmt[c] = fmt[c].apply(lambda v: f"${int(v):,}")
    print(fmt.to_string(index=False))

    # Tier thresholds for clarity
    print(f"\nTier thresholds (FP-style points):")
    for threshold, label in TIER_THRESHOLDS.get(args.position, []):
        print(f"  {label}: ≥ {threshold} pts")
    print(f"  T5: below all thresholds (depth)")

    print(f"\nWrote {csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
