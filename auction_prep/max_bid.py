"""Per-player max-bid calculator.

STATUS: scaffolded, not implemented. See auction_prep/DESIGN.md.

Computes the bid above which a contract goes NPV-negative at the configured
discount rate, for each contract length 1–5 years. Reuses the existing NPV
machinery; the only new logic is the inversion (given a desired NPV, solve
for the salary).
"""
from __future__ import annotations

import argparse


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--player", help="Player name (substring match) or MFL id")
    p.add_argument("--position", help="Position to batch-rank (QB/RB/WR/TE)")
    p.add_argument("--top", type=int, default=30, help="Top N players in batch mode")
    p.add_argument("--discount", type=float, default=0.20)
    p.add_argument("--margin", type=float, default=0.0, help="Surplus margin (0.10 = 10% under NPV-zero)")
    p.add_argument("--year", type=int, help="Season for projections (default: current)")
    args = p.parse_args()
    raise NotImplementedError("auction_prep.max_bid not yet implemented; see DESIGN.md")


if __name__ == "__main__":
    main()
