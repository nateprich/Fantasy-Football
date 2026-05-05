"""League cap-stress index for upcoming auction.

STATUS: scaffolded, not implemented. See auction_prep/DESIGN.md.

Projects each franchise's committed cap going into the upcoming auction:
  current contracts × 1.10 escalation
  − expiring contracts
  − tagged players (kept on books at tag salary)
  + 5th-year option exercises (added to books)

Aggregates into cap-flush / balanced / stressed buckets and outputs a market
signal (expected price direction).
"""
from __future__ import annotations

import argparse


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, help="Auction season (default: current+1)")
    args = p.parse_args()
    raise NotImplementedError("auction_prep.cap_stress not yet implemented; see DESIGN.md")


if __name__ == "__main__":
    main()
