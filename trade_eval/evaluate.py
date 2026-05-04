"""Trade fairness evaluator.

Combines player NPV (from salary_efficiency.npv) with the draft pick value curve (from
draft_value.analyze) to value any trade in dollars.

Usage (each --side argument is a list of assets, separated):

    python -m trade_eval.evaluate --year 2026 \
        --side-a "Puka Nacua" "Kyren Williams" "2027 1.05" \
        --side-b "Drake London" "2026 2.07"

Asset syntax:
  - Player: any unambiguous substring of the player's name (case-insensitive).
  - Pick:   "<year> <round>.<pick>"  e.g.  "2026 1.05"  (16-team league: 1.01–1.16)
            or "<year> <round>" for round-only when slot is unknown
            (uses round average as the value).

The tool prints both sides' total NPV, cap impact, the diff, and a fairness verdict.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from lib import mfl
from lib.league import SALARY_CAP
from salary_efficiency.analyze import build_season_dataframe, fit_position_market
from salary_efficiency.npv import (
    NEXT_YEAR_WAIVER_PENALTY_BY_YEARS_REMAINING,
    CURRENT_YEAR_WAIVER_HIT,
    player_npv,
    projected_points,
)

PICK_VALUE_CSV = Path(__file__).resolve().parent.parent / "out" / "draft_value" / "picks.csv"

PICK_RE = re.compile(r"^\s*(\d{4})\s+(\d+)(?:\.(\d{1,2}))?\s*$")


def parse_asset(s: str) -> dict:
    m = PICK_RE.match(s)
    if m:
        year, round_, pick = m.group(1), int(m.group(2)), m.group(3)
        return {"kind": "pick", "year": int(year), "round": round_,
                "pick": int(pick) if pick else None,
                "slot": f"{round_}.{int(pick):02d}" if pick else f"R{round_}",
                "raw": s.strip()}
    return {"kind": "player", "query": s.strip()}


def resolve_player(query: str, current_df: pd.DataFrame) -> pd.Series | None:
    q = query.lower()
    matches = current_df[current_df["name"].str.lower().str.contains(q, na=False)]
    if matches.empty:
        return None
    if len(matches) == 1:
        return matches.iloc[0]
    # Prefer exact, then highest-salary (most likely the intended marquee player)
    exact = matches[matches["name"].str.lower() == q]
    if not exact.empty:
        return exact.iloc[0]
    return matches.sort_values("salary", ascending=False).iloc[0]


def value_pick(pick: dict, picks_df: pd.DataFrame, discount: float) -> dict:
    """Return {value, basis, n} for a pick.

    Uses the historical realized-NPV curve. Discounts future-year picks back to current dollars.
    """
    target_year = pick["year"]
    current_year = pd.Timestamp.now().year
    discount_factor = 1.0 / ((1 + discount) ** max(target_year - current_year, 0))

    if pick["pick"] is not None:
        overall = (pick["round"] - 1) * 16 + pick["pick"]
        sub = picks_df[picks_df["overall"] == overall]
        if not sub.empty:
            mean = sub["npv"].mean()
            median = sub["npv"].median()
            return {
                "value": round(median * discount_factor),
                "basis": f"slot {pick['slot']} median (n={len(sub)})",
                "mean_undiscounted": round(mean),
                "median_undiscounted": round(median),
                "discount_factor": round(discount_factor, 3),
                "n": len(sub),
            }
    # Fall back to round-level
    sub = picks_df[picks_df["round"] == pick["round"]]
    if sub.empty:
        return {"value": 0, "basis": f"unknown round {pick['round']}", "n": 0}
    median = sub["npv"].median()
    return {
        "value": round(median * discount_factor),
        "basis": f"round {pick['round']} median (n={len(sub)})",
        "mean_undiscounted": round(sub['npv'].mean()),
        "median_undiscounted": round(median),
        "discount_factor": round(discount_factor, 3),
        "n": len(sub),
    }


def value_player(player_row: pd.Series, history_pts: dict, fits: dict, target_year: int,
                 discount: float, fp_projections: dict[str, float] | None = None) -> dict:
    pts_proj, yrs_used = projected_points(history_pts, player_row["player_id"], target_year, fp_projections)
    if pts_proj == 0 and player_row["points"] > 0:
        pts_proj = player_row["points"]
    out = player_npv(
        current_salary=player_row["salary"],
        years_remaining=int(player_row["contract_year"]),
        projected_pts=pts_proj,
        position=player_row["position"],
        fits=fits,
        discount_rate=discount,
    )
    return {
        "name": player_row["name"],
        "position": player_row["position"],
        "salary": player_row["salary"],
        "years_remaining": int(player_row["contract_year"]),
        "projected_pts": round(pts_proj, 1),
        "projection_source": "FP" if yrs_used == -1 else f"trailing-{yrs_used}y",
        "npv": out["value"],
        "gross_npv": out["gross_npv"],
        "would_cut": out["would_cut"],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, required=True, help="Current season (uses its rosters)")
    p.add_argument("--discount", type=float, default=0.20)
    p.add_argument("--side-a", nargs="+", required=True, help="Assets going to Side A (your team) — players and picks")
    p.add_argument("--side-b", nargs="+", required=True, help="Assets going to Side B (their team)")
    p.add_argument("--years-back", type=int, default=3, help="Seasons to pool for market fit")
    p.add_argument("--history-start", type=int, default=2017)
    p.add_argument("--no-fp", action="store_true", help="Disable FantasyPros projections")
    p.add_argument("--scoring", default="points_ppr",
                   choices=["points", "points_ppr", "points_half"])
    args = p.parse_args()

    if not PICK_VALUE_CSV.exists():
        print(f"ERROR: {PICK_VALUE_CSV} not found.", file=sys.stderr)
        print("Run: python -m draft_value.analyze --start 2017 --through 2024 --years-since 4", file=sys.stderr)
        sys.exit(1)

    picks_df = pd.read_csv(PICK_VALUE_CSV)

    # Load market fit and trailing-points history
    print(f"Loading historical bids {args.history_start}..{args.year}...", file=sys.stderr)
    history = mfl.HistoricalBids.load(args.history_start, args.year)
    market_frames = []
    history_pts: dict[int, dict] = {}
    earliest = args.year - max(args.years_back, 3)
    for y in range(args.year, earliest, -1):
        df = build_season_dataframe(y, history)
        if y >= args.year - (args.years_back - 1):
            market_frames.append(df)
        history_pts[y] = {row["player_id"]: {"points": row["points"], "weeks_with_score": row["weeks_with_score"]}
                          for _, row in df.iterrows()}
    fits = fit_position_market(pd.concat(market_frames, ignore_index=True))
    current_df = market_frames[0]  # target year's roster snapshot

    fp_projections = None
    if not args.no_fp:
        try:
            from lib.fantasypros import projected_points_by_mflid
            fp_projections = projected_points_by_mflid(args.year, scoring=args.scoring)
            print(f"FantasyPros: loaded {len(fp_projections)} projections for {args.year}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"WARN: FantasyPros load failed ({e}); using trailing-avg", file=sys.stderr)

    sides = {"A": args.side_a, "B": args.side_b}
    valued = {"A": [], "B": []}
    cap_swing = {"A": 0.0, "B": 0.0}

    for label, raw_assets in sides.items():
        for raw in raw_assets:
            asset = parse_asset(raw)
            if asset["kind"] == "pick":
                v = value_pick(asset, picks_df, args.discount)
                valued[label].append({"type": "PICK", "label": f"{asset['year']} {asset['slot']}", **v})
            else:
                row = resolve_player(asset["query"], current_df)
                if row is None:
                    print(f"WARN: could not resolve player '{asset['query']}'", file=sys.stderr)
                    valued[label].append({"type": "PLAYER", "label": asset["query"], "value": 0, "basis": "UNKNOWN"})
                    continue
                pv = value_player(row, history_pts, fits, args.year, args.discount, fp_projections)
                valued[label].append({
                    "type": "PLAYER",
                    "label": pv["name"],
                    "value": pv["npv"],
                    "basis": f"{pv['position']} ${int(pv['salary']):,} × {pv['years_remaining']}y, {pv['projected_pts']} pts ({pv['projection_source']})",
                    **pv,
                })
                cap_swing[label] += float(pv["salary"])

    # Render
    def fmt_money(v):
        v = int(v)
        return f"-${abs(v):,}" if v < 0 else f"${v:,}"

    print("\n" + "=" * 70)
    print("TRADE EVALUATION")
    print(f"Year: {args.year}  ·  Discount: {args.discount:.0%}")
    print("=" * 70)

    totals = {}
    for label in ("A", "B"):
        print(f"\n  Side {label} receives:")
        total = 0
        for item in valued[label]:
            print(f"    {item['type']:<6}  {item['label']:<32}  {fmt_money(item['value']):>14}   ({item['basis']})")
            total += item["value"]
        totals[label] = total
        print(f"    {'TOTAL':<40}  {fmt_money(total):>14}")
        print(f"    {'Cap added (current-year salary)':<40}  {fmt_money(cap_swing[label]):>14}")

    print("\n" + "-" * 70)
    diff = totals["A"] - totals["B"]
    cap_net_a = cap_swing["A"] - cap_swing["B"]  # what A's cap absorbs (positive = more salary on A)
    print(f"  Net value swing to Side A:          {fmt_money(diff)}")
    print(f"  Net cap absorbed by Side A:         {fmt_money(cap_net_a)}")

    abs_diff = abs(diff)
    if abs_diff < 500_000:
        verdict = "FAIR — within $500K"
    elif abs_diff < 2_000_000:
        winner = "A" if diff > 0 else "B"
        verdict = f"SLIGHT EDGE → Side {winner}"
    elif abs_diff < 5_000_000:
        winner = "A" if diff > 0 else "B"
        verdict = f"CLEAR WIN → Side {winner}"
    else:
        winner = "A" if diff > 0 else "B"
        verdict = f"LOPSIDED → Side {winner} (don't accept the other side)"
    print(f"  Verdict:                            {verdict}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
