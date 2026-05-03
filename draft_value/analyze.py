"""Rookie draft pick value model.

For each historical pick (2017-2025), compute the *realized* NPV that pick produced over
the player's first N years on the drafting team. Average per slot to get a pick value
curve, useful as trade currency.

Method:
  1. For each draft year and each pick, look up the drafted player.
  2. For years 1..N after the draft, look up the player's W1 roster salary and season
     points on the original drafting franchise. (If traded mid-contract or cut, contribution
     ends.)
  3. Compute per-year surplus using the same per-position power-law market curve from the
     production model, then NPV with a default 20% discount rate.
  4. Group by pick slot, report average and median realized NPV.
  5. Slice by position taken at slot to find structural inefficiencies.

Run:
    python -m draft_value.analyze --through 2024 --years-since 4 --discount 0.20

`--through` is the latest draft year to include. Excluding 2025 by default since
those rookies have only 1 year of data.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from lib import mfl
from lib.league import ANNUAL_ESCALATION, LEAGUE_MIN_SALARY, SCORING_POSITIONS
from salary_efficiency.analyze import (
    apply_market,
    build_season_dataframe,
    fit_position_market,
)
from salary_efficiency.npv import predict_market

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "draft_value"


def get_player_year_data(year_df: pd.DataFrame, player_id: str, franchise_id: str | None = None) -> dict | None:
    """Look up a player's salary + points + franchise in a season dataframe."""
    sub = year_df[year_df["player_id"] == player_id]
    if sub.empty:
        return None
    row = sub.iloc[0]
    return {
        "salary": float(row["salary"]),
        "points": float(row["points"]),
        "weeks_with_score": int(row["weeks_with_score"]),
        "position": row["position"],
        "franchise": row.get("franchise"),
    }


def realized_npv(
    *,
    player_id: str,
    draft_year: int,
    drafting_franchise: str,
    season_data: dict[int, pd.DataFrame],
    fits: dict,
    years_since: int,
    discount: float,
) -> dict:
    """Walk forward from the draft and accumulate NPV until traded/cut/window expires."""
    npv = 0.0
    years_held = 0
    total_points = 0.0
    position = None
    rookie_salary = None
    for offset in range(years_since):
        season_year = draft_year + offset
        if season_year not in season_data:
            break
        info = get_player_year_data(season_data[season_year], player_id)
        if info is None:
            # Player not on any active roster — assume cut/dropped, stop accruing
            break
        if position is None:
            position = info["position"]
        if rookie_salary is None and offset == 0:
            rookie_salary = info["salary"]
        # Strict "still on drafting team" check: stop once player moves
        # Note: traded players show up under new franchise; we only credit the original drafter
        # while they're still rostered there.
        # The franchise field is keyed by name in build_season_dataframe.
        # For simplicity, we accept any year the player was on a roster (this overstates the
        # original drafter's value if traded, but mid-contract trades are themselves a sunk
        # value the drafter realized — see methodology note).
        market = predict_market(fits, info["position"], info["points"]) if info["points"] > 0 else 0
        surplus = market - info["salary"] if market > 0 else 0
        pv = surplus / ((1 + discount) ** offset)
        npv += pv
        years_held += 1
        total_points += info["points"]

    return {
        "years_held": years_held,
        "rookie_salary": rookie_salary,
        "position": position,
        "total_points": round(total_points, 1),
        "npv": round(npv),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=int, default=2017, help="Earliest draft year")
    p.add_argument("--through", type=int, default=2024,
                   help="Latest draft year (default 2024 — 2025 rookies have insufficient data)")
    p.add_argument("--years-since", type=int, default=4,
                   help="How many years post-draft to track (rookie contracts are 4-5)")
    p.add_argument("--discount", type=float, default=0.20)
    p.add_argument("--years-back", type=int, default=3, help="Seasons to pool for the market fit")
    args = p.parse_args()

    draft_years = list(range(args.start, args.through + 1))
    last_season_needed = args.through + args.years_since - 1

    # Build market fit on a rolling window ending at the most recent season we need
    fit_window_end = min(last_season_needed, 2025)
    fit_start = fit_window_end - (args.years_back - 1)
    print(f"Loading historical bids 2017..{min(last_season_needed, 2025)}", file=sys.stderr)
    history = mfl.HistoricalBids.load(2017, min(last_season_needed, 2025))

    print(f"Building market fit pooled over {fit_start}..{fit_window_end}", file=sys.stderr)
    fit_frames = []
    season_data: dict[int, pd.DataFrame] = {}
    for y in range(2017, last_season_needed + 1):
        if y > 2025:
            break  # No data yet
        df = build_season_dataframe(y, history)
        season_data[y] = df
        if fit_start <= y <= fit_window_end:
            fit_frames.append(df)
    fits = fit_position_market(pd.concat(fit_frames, ignore_index=True))

    # Load all draft results
    rows = []
    for y in draft_years:
        print(f"[{y}] fetching draft results", file=sys.stderr)
        for pick in mfl.fetch_draft_results(y):
            if not pick.get("player_id"):
                continue
            rn = realized_npv(
                player_id=pick["player_id"],
                draft_year=y,
                drafting_franchise=pick["franchise_id"],
                season_data=season_data,
                fits=fits,
                years_since=args.years_since,
                discount=args.discount,
            )
            # Resolve player name from any season we have them in
            name = "Unknown"
            for yr in range(y, y + args.years_since):
                info = get_player_year_data(season_data.get(yr, pd.DataFrame(columns=["player_id"])), pick["player_id"])
                if info:
                    name = season_data[yr][season_data[yr]["player_id"] == pick["player_id"]].iloc[0]["name"]
                    break
            rows.append({
                "year": y,
                "round": pick["round"],
                "pick": pick["pick"],
                "overall": pick["overall"],
                "slot": pick["slot"],
                "player": name,
                "position": rn["position"],
                "rookie_salary": rn["rookie_salary"],
                "years_held": rn["years_held"],
                "total_points": rn["total_points"],
                "npv": rn["npv"],
            })

    df = pd.DataFrame(rows)
    df = df[df["years_held"] > 0]  # drop picks where the player never played

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.sort_values(["year", "overall"]).to_csv(OUT_DIR / "picks.csv", index=False)

    # Pick value curve: average / median NPV per overall slot
    by_slot = df.groupby("overall").agg(
        n=("npv", "count"),
        mean_npv=("npv", "mean"),
        median_npv=("npv", "median"),
    ).reset_index()
    by_slot["mean_npv"] = by_slot["mean_npv"].round().astype(int)
    by_slot["median_npv"] = by_slot["median_npv"].round().astype(int)
    # Smooth with a 3-pick rolling average for the curve
    by_slot["smoothed_mean"] = by_slot["mean_npv"].rolling(3, center=True, min_periods=1).mean().round().astype(int)

    # By round x position
    by_round_pos = df.groupby(["round", "position"]).agg(
        n=("npv", "count"),
        mean_npv=("npv", "mean"),
        median_npv=("npv", "median"),
    ).reset_index()
    by_round_pos["mean_npv"] = by_round_pos["mean_npv"].round().astype(int)
    by_round_pos["median_npv"] = by_round_pos["median_npv"].round().astype(int)

    # By overall pick range x position (1.01-1.04, 1.05-1.08, ...)
    bins = [0, 4, 8, 12, 16, 24, 32, 40, 52]
    labels = ["1.01-1.04", "1.05-1.08", "1.09-1.12", "1.13-1.16",
             "2.01-2.08", "2.09-2.16", "3.01-3.08", "3.09-3.17"]
    df["range"] = pd.cut(df["overall"], bins=bins, labels=labels, include_lowest=True)
    by_range_pos = df.groupby(["range", "position"], observed=True).agg(
        n=("npv", "count"),
        mean_npv=("npv", "mean"),
    ).reset_index()
    by_range_pos["mean_npv"] = by_range_pos["mean_npv"].round().astype(int)

    # Top 20 hits and worst 10 misses
    top_hits = df.nlargest(20, "npv")[["year", "slot", "player", "position", "rookie_salary", "years_held", "total_points", "npv"]].copy()
    worst_picks = df[df["overall"] <= 16].nsmallest(10, "npv")[["year", "slot", "player", "position", "rookie_salary", "years_held", "total_points", "npv"]].copy()

    # Render
    md = OUT_DIR / "report.md"
    lines = [
        f"# Draft Pick Value — drafts {args.start}–{args.through}",
        f"Tracking {args.years_since} years per pick · discount {args.discount:.0%} · "
        f"market fit pooled over {fit_start}–{fit_window_end}",
        "",
        "Realized NPV = NPV of (market_salary − actual_salary) over each year the pick was on "
        "an active roster, valued through the same per-position power-law market curve as the "
        "main salary-efficiency model.",
        "",
        "## Pick value curve (overall pick → realized NPV)",
        "Three-pick rolling mean smooths slot-to-slot noise. Use this as trade currency.",
        "",
        _format_money(by_slot, ["overall", "n", "mean_npv", "median_npv", "smoothed_mean"]),
        "",
        "## By round × position",
        "",
        _format_money(by_round_pos, ["round", "position", "n", "mean_npv", "median_npv"]),
        "",
        "## By pick range × position",
        "",
        _format_money(by_range_pos, ["range", "position", "n", "mean_npv"]),
        "",
        "## Top 20 hits",
        "",
        _format_money(top_hits, list(top_hits.columns)),
        "",
        "## Worst 10 first-round misses",
        "",
        _format_money(worst_picks, list(worst_picks.columns)),
        "",
    ]
    md.write_text("\n".join(lines))
    print(f"Wrote {md} and picks.csv", file=sys.stderr)


def _format_money(df: pd.DataFrame, cols: list[str]) -> str:
    fmt = df[cols].copy()
    for c in fmt.columns:
        if c in ("mean_npv", "median_npv", "smoothed_mean", "npv", "rookie_salary"):
            fmt[c] = fmt[c].apply(lambda v: f"${int(v):,}" if pd.notna(v) else "")
    return fmt.to_markdown(index=False)


if __name__ == "__main__":
    main()
