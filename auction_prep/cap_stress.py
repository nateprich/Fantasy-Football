"""League cap-stress index for upcoming auction.

Projects each franchise's committed cap going into the upcoming auction:
  current contracts × 1.10 escalation
  − expiring contracts (years_remaining == 1 currently → 0 next season)

Aggregates into cap-flush / balanced / stressed buckets and outputs a market
signal (expected price direction).

Run:
    python -m auction_prep.cap_stress
    python -m auction_prep.cap_stress --year 2027
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from lib import mfl
from lib.league import ANNUAL_ESCALATION, NUM_TEAMS, SALARY_CAP

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "auction_prep"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, default=date.today().year + 1,
                   help="Auction season (default: next year)")
    p.add_argument("--source-year", type=int, default=date.today().year,
                   help="Year to read current rosters from")
    args = p.parse_args()

    print(f"Pulling {args.source_year} rosters to project {args.year} auction cap...", file=sys.stderr)
    franchises = mfl.fetch_franchises(args.source_year)
    rosters = mfl.fetch_rosters(args.source_year, week=1)

    rows = []
    for fid, players in rosters.items():
        committed_next = 0.0
        kept_count = 0
        expiring = 0
        for pl in players:
            yrs = int(pl.get("contract_year") or 0)
            if yrs <= 1:
                expiring += 1
                continue  # contract expires before next auction
            committed_next += pl["salary"] * (1 + ANNUAL_ESCALATION)
            kept_count += 1
        rows.append({
            "franchise_id": fid,
            "franchise": franchises.get(fid, {}).get("name", fid),
            "current_contracts": len(players),
            "expiring": expiring,
            "kept_next_year": kept_count,
            "committed_next": int(committed_next),
            "room_next": int(SALARY_CAP - committed_next),
        })

    df = pd.DataFrame(rows).sort_values("room_next", ascending=False)

    # Buckets
    flush = (df["room_next"] >= 15_000_000).sum()
    balanced = ((df["room_next"] >= 5_000_000) & (df["room_next"] < 15_000_000)).sum()
    stressed = (df["room_next"] < 5_000_000).sum()

    if flush >= NUM_TEAMS // 2:
        signal = "INFLATIONARY (many cap-flush bidders, expect 10-20% premium on top tier)"
    elif stressed >= NUM_TEAMS // 2:
        signal = "DEFLATIONARY (most teams stressed, expect bargains in late period)"
    else:
        signal = "BALANCED (typical pricing expected)"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv = OUT_DIR / f"cap_stress_{args.year}.csv"
    df.to_csv(csv, index=False)

    print(f"\n=== {args.year} Pre-Auction Cap Forecast (post-Feb-15 escalation) ===\n")
    pretty = df.copy()
    pretty["committed_next"] = pretty["committed_next"].apply(lambda v: f"${int(v):,}")
    pretty["room_next"] = pretty["room_next"].apply(lambda v: f"${int(v):,}")
    print(pretty.to_string(index=False))

    print(f"\nBuckets: cap-flush ≥ $15M room: {flush} · balanced: {balanced} · stressed < $5M: {stressed}")
    print(f"\nMarket signal: {signal}")
    print(f"\nWrote {csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
