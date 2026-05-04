"""Multi-year NPV surplus.

A player under contract is a multi-year asset. Each remaining year produces a surplus
(market_salary − actual_salary), but future surplus is worth less than current surplus
because (a) the future is uncertain (injury, bust, retirement) and (b) cap space today
has more strategic value than cap space later.

NPV formula:
    NPV = sum_{t=0..n-1}  surplus_t / (1 + r)^t
    where:
      n = years remaining on contract
      surplus_t = projected_market_salary_t − actual_salary_t
      actual_salary_t = current_salary * 1.10^t   (constitution mandates 10% raise/yr)
      r = discount rate (default 0.20 to reflect dynasty risk)

Projected points use a trailing 2-year average of realized fantasy points. The validation
suite showed single-year points are noisy (R² 0.06–0.16); pooling years stabilizes the
projection.

Cut floor: a player can always be waived. Per the constitution, waiver cost is
50% of current-year salary plus a years-remaining-dependent penalty in the next season.
If keep_npv < -cut_cost, the team would cut, so we floor the value at -cut_cost.

Run:
    python -m salary_efficiency.npv --year 2026 --discount 0.20
    python -m salary_efficiency.npv --year 2026 --by-team        # group by franchise
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from lib import mfl
from lib.league import (
    ANNUAL_ESCALATION,
    LEAGUE_MIN_SALARY,
    SCORING_POSITIONS,
)

from salary_efficiency.analyze import (
    apply_market,
    build_season_dataframe,
    fit_position_market,
    _money_md,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "salary_efficiency"

# Per-constitution waiver penalty: pct of current-year salary applied NEXT season,
# in addition to a flat 50% current-year hit. Indexed by years_remaining.
NEXT_YEAR_WAIVER_PENALTY_BY_YEARS_REMAINING = {
    1: 0.00,
    2: 0.15,
    3: 0.25,
    4: 0.35,
    5: 0.45,
}
CURRENT_YEAR_WAIVER_HIT = 0.50


def projected_points(history_pts: dict[int, dict[str, dict]], player_id: str, target_year: int,
                     fp_projections: dict[str, float] | None = None) -> tuple[float, int]:
    """Project a player's points for the target year.

    Source priority:
      1. FantasyPros season projection (if provided and player is found) — labeled years_used = -1
      2. Trailing 2-year average of realized points
      3. Trailing 1-year if only one year is available
      4. (0.0, 0) if no data

    Returns (projected_points, years_used). years_used = -1 indicates FP source.
    """
    if fp_projections:
        fp = fp_projections.get(player_id)
        if fp and fp > 0:
            return float(fp), -1

    pts_list = []
    for y in (target_year - 1, target_year - 2):
        season = history_pts.get(y, {}).get(player_id)
        if season and season.get("points", 0) > 0:
            pts_list.append(season["points"])
    if not pts_list:
        return 0.0, 0
    return float(sum(pts_list) / len(pts_list)), len(pts_list)


def predict_market(fits: dict, position: str, points: float) -> float:
    fit = fits.get(position)
    if not fit:
        return float(LEAGUE_MIN_SALARY)
    if points <= 0 or fit.get("k", 0) == 0.0:
        return max(LEAGUE_MIN_SALARY, fit.get("median", LEAGUE_MIN_SALARY))
    return max(LEAGUE_MIN_SALARY, fit["c"] * (points ** fit["k"]))


def cut_cost(current_salary: float, years_remaining: int) -> float:
    """Total cap cost (in PV dollars) to waive this player today.

    Current-year hit is 50% of current salary (today's dollars).
    Next-season hit is `pct * current_salary` (which has already escalated 10%, but
    constitution language is "% of salary" — interpret as current salary). We discount
    the next-year hit by 1/(1+r) when the caller wants PV.
    """
    pct_next = NEXT_YEAR_WAIVER_PENALTY_BY_YEARS_REMAINING.get(years_remaining, 0.0)
    return CURRENT_YEAR_WAIVER_HIT * current_salary + pct_next * current_salary


def player_npv(
    *,
    current_salary: float,
    years_remaining: int,
    projected_pts: float,
    position: str,
    fits: dict,
    discount_rate: float,
) -> dict:
    """Compute NPV components for one player.

    Returns dict with: years_priced, gross_npv, cut_cost, value (max of gross_npv vs -cut_cost).
    """
    if years_remaining <= 0:
        # Expiring/expired — only this year counts
        years_remaining = 1

    # No production history: we can't claim the player is a steal or an overpay.
    # Treat market = current salary so surplus defaults to zero. Avoids the bug where
    # rookies with zero trailing points get the position median market and then look
    # like top contracts in the league. Also fairly handles veterans coming off injury.
    if projected_pts <= 0:
        return {
            "years_priced": years_remaining,
            "gross_npv": 0,
            "cut_value": -round(CURRENT_YEAR_WAIVER_HIT * current_salary
                                + NEXT_YEAR_WAIVER_PENALTY_BY_YEARS_REMAINING.get(min(years_remaining, 5), 0.0)
                                * current_salary / (1 + discount_rate)),
            "value": 0,
            "would_cut": False,
            "yearly": [],
        }

    salary_t = current_salary
    market = predict_market(fits, position, projected_pts)
    yearly = []
    npv = 0.0
    for t in range(years_remaining):
        surplus_t = market - salary_t
        # The constitution applies escalation to salary; we hold market salary constant
        # at projected level (a simple "no further development" assumption).
        pv = surplus_t / ((1 + discount_rate) ** t)
        npv += pv
        yearly.append({"year_offset": t, "salary": round(salary_t), "market": round(market), "surplus": round(surplus_t), "pv": round(pv)})
        salary_t *= (1 + ANNUAL_ESCALATION)

    # Cut option: 50% of today's salary now (PV = full), plus next-year penalty discounted
    pct_next = NEXT_YEAR_WAIVER_PENALTY_BY_YEARS_REMAINING.get(min(years_remaining, 5), 0.0)
    cut_pv = -(CURRENT_YEAR_WAIVER_HIT * current_salary
               + pct_next * current_salary / (1 + discount_rate))

    # Floor: a rational owner cuts if keeping is worse than cutting
    value = max(npv, cut_pv)
    return {
        "years_priced": years_remaining,
        "gross_npv": round(npv),
        "cut_value": round(cut_pv),
        "value": round(value),
        "would_cut": cut_pv > npv,
        "yearly": yearly,
    }


def build_npv_dataframe(
    *,
    target_year: int,
    history: mfl.HistoricalBids,
    fits: dict,
    history_pts: dict,
    discount_rate: float,
    fp_projections: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Build NPV-per-player frame for the target year's roster snapshot."""
    print(f"[{target_year}] building NPV dataframe", file=sys.stderr)
    target_df = build_season_dataframe(target_year, history)
    rows = []
    for _, r in target_df.iterrows():
        pts_proj, yrs_used = projected_points(history_pts, r["player_id"], target_year, fp_projections)
        if pts_proj == 0 and r["points"] > 0:
            # First-year player or no prior data: use current-year points if available
            pts_proj = r["points"]
            yrs_used = 1
        out = player_npv(
            current_salary=r["salary"],
            years_remaining=int(r["contract_year"]),
            projected_pts=pts_proj,
            position=r["position"],
            fits=fits,
            discount_rate=discount_rate,
        )
        rows.append({
            "player_id": r["player_id"],
            "name": r["name"],
            "position": r["position"],
            "franchise": r["franchise"],
            "salary": r["salary"],
            "years_remaining": int(r["contract_year"]),
            "projected_pts": round(pts_proj, 1),
            "projection_source": "FP" if yrs_used == -1 else f"trailing-{yrs_used}y",
            "current_year_pts": round(r["points"], 1),
            "years_priced": out["years_priced"],
            "gross_npv": out["gross_npv"],
            "cut_value": out["cut_value"],
            "value": out["value"],
            "would_cut": out["would_cut"],
        })
    return pd.DataFrame(rows)


def write_report(year: int, df: pd.DataFrame, discount: float, top_n: int, by_team: bool) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = OUT_DIR / f"npv_{year}.md"
    csv = OUT_DIR / f"npv_{year}.csv"
    df.sort_values("value", ascending=False).to_csv(csv, index=False)

    skill = df[~df["position"].isin(["PK", "Def"])].copy()
    cols = ["name", "position", "franchise", "salary", "years_remaining", "projected_pts", "projection_source", "value", "gross_npv"]

    fp_count = (df["projection_source"] == "FP").sum()
    proj_label = (
        f"FantasyPros projection for {fp_count}/{len(df)} players, trailing-avg fallback for the rest"
        if fp_count > 0 else "trailing 2-yr avg points"
    )

    lines = [
        f"# Multi-Year NPV Surplus — {year}",
        f"Discount rate: **{discount:.0%}**  ·  Projection: {proj_label}  ·  PK/Def excluded",
        "",
        "Per-player asset value: NPV of remaining contract years (10%/yr salary escalation, "
        "discounted at the configured rate), floored at the cut option value. Higher = more "
        "valuable contract to own. Negative = team would cut if rational.",
        "",
    ]

    lines.append(f"## Top {top_n} most valuable contracts")
    top = skill.nlargest(top_n, "value")[cols].copy()
    lines.append(_npv_md(top))
    lines.append("")

    lines.append(f"## Top {top_n} worst contracts (lowest value, includes cut floor)")
    bot = skill.nsmallest(top_n, "value")[cols + ["would_cut"]].copy()
    lines.append(_npv_md(bot))
    lines.append("")

    lines.append("## Contracts the model says should be cut")
    cut = skill[skill["would_cut"]].sort_values("salary", ascending=False)[cols + ["cut_value"]]
    if cut.empty:
        lines.append("_(none — no rational team is currently holding a player whose keep value is below their cut cost)_")
    else:
        lines.append(_npv_md(cut))
    lines.append("")

    lines.append(f"## Best contract by position (top 5)")
    for pos in [p for p in SCORING_POSITIONS if p not in ("PK", "Def")]:
        sub = skill[skill["position"] == pos].nlargest(5, "value")[cols]
        if sub.empty:
            continue
        lines.append(f"### {pos}")
        lines.append(_npv_md(sub))
        lines.append("")

    if by_team:
        lines.append("## Team asset value (sum of contract NPV)")
        team = (
            skill.groupby("franchise")
            .agg(
                roster_value=("value", "sum"),
                committed_salary=("salary", "sum"),
                contracts=("name", "count"),
                avg_years_remaining=("years_remaining", "mean"),
            )
            .reset_index()
            .sort_values("roster_value", ascending=False)
        )
        team["roster_value"] = team["roster_value"].apply(lambda v: f"${int(v):,}")
        team["committed_salary"] = team["committed_salary"].apply(lambda v: f"${int(v):,}")
        team["avg_years_remaining"] = team["avg_years_remaining"].round(2)
        lines.append(team.to_markdown(index=False))
        lines.append("")

    md.write_text("\n".join(lines))
    print(f"Wrote {md} and {csv}", file=sys.stderr)
    return md


def _npv_md(df: pd.DataFrame) -> str:
    fmt = df.copy()
    for c in ("salary", "value", "gross_npv", "cut_value"):
        if c in fmt.columns:
            fmt[c] = fmt[c].apply(lambda v: f"${int(v):,}")
    return fmt.to_markdown(index=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, required=True, help="Target season (uses its rosters + contracts)")
    p.add_argument("--discount", type=float, default=0.20, help="Annual discount rate (0.20 = 20%)")
    p.add_argument("--years-back", type=int, default=3, help="Seasons to pool for the market fit")
    p.add_argument("--projection-years", type=int, default=2, help="Trailing seasons for points projection")
    p.add_argument("--history-start", type=int, default=2017, help="Earliest year for auction/BBID lookback")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--by-team", action="store_true", help="Include per-team asset value summary")
    p.add_argument("--no-fp", action="store_true",
                   help="Disable FantasyPros projections; use trailing-avg only")
    p.add_argument("--scoring", default="points_ppr",
                   choices=["points", "points_ppr", "points_half"],
                   help="FantasyPros scoring system to pull (default: full PPR)")
    args = p.parse_args()

    print(f"Loading historical bids {args.history_start}..{args.year}...", file=sys.stderr)
    history = mfl.HistoricalBids.load(args.history_start, args.year)

    fp_projections = None
    if not args.no_fp:
        try:
            from lib.fantasypros import projected_points_by_mflid
            print(f"Loading FantasyPros projections for {args.year} ({args.scoring})...", file=sys.stderr)
            fp_projections = projected_points_by_mflid(args.year, scoring=args.scoring)
            print(f"  loaded {len(fp_projections)} player projections", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"  WARN: FantasyPros load failed ({e}); falling back to trailing-avg", file=sys.stderr)

    # Build season dataframes for market fit AND for trailing projections
    market_frames = []
    history_pts: dict[int, dict[str, dict]] = {}
    earliest_needed = args.year - max(args.years_back, args.projection_years + 1)
    for y in range(args.year, earliest_needed, -1):
        df = build_season_dataframe(y, history)
        if y >= args.year - (args.years_back - 1):
            market_frames.append(df)
        # build pts lookup: player_id -> {points, weeks_with_score}
        history_pts[y] = {row["player_id"]: {"points": row["points"], "weeks_with_score": row["weeks_with_score"]}
                          for _, row in df.iterrows()}

    fits = fit_position_market(pd.concat(market_frames, ignore_index=True))

    npv_df = build_npv_dataframe(
        target_year=args.year,
        history=history,
        fits=fits,
        history_pts=history_pts,
        discount_rate=args.discount,
        fp_projections=fp_projections,
    )
    write_report(args.year, npv_df, args.discount, args.top, args.by_team)


if __name__ == "__main__":
    main()
