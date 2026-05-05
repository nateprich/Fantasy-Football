"""Production-tier salary confidence bands.

STATUS: scaffolded, not implemented. See auction_prep/DESIGN.md.

Bins historical auctions by realized points (or FP projection at acquisition),
computes p25/p50/p75 salary per tier × year. Output replaces the eyeball-the-
top-30 step in the original JS exporter with proper percentile bands.
"""
from __future__ import annotations

import argparse


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--position", required=True, choices=["QB", "RB", "WR", "TE", "PK", "Def"])
    p.add_argument("--years", nargs="+", type=int, default=[2023, 2024, 2025],
                   help="Years to pool")
    p.add_argument("--bins", type=int, default=5, help="Number of production tiers")
    args = p.parse_args()
    raise NotImplementedError("auction_prep.tier_bands not yet implemented; see DESIGN.md")


if __name__ == "__main__":
    main()
