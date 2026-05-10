"""League projected-lineup snapshot.

Given the current rosters and FP projections (already in the NPV report), compute
each team's projected legal starting lineup points, bench skill depth, and gap to
the projected playoff line.

This is an input to win-now vs. build-future decisions, not a verdict. The output
does not account for schedule, weekly variance, injuries, rookie uncertainty, trade
liquidity, cap flexibility, or contract asset value.

Run:
    python -m competitiveness.analyze --year 2026 --my-team "Midwestside"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from competitiveness.lineup import bench_skill_points, select_starting_lineup

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "competitiveness"
PLAYOFF_SPOTS = 7


def experimental_band(pts: float, *, league_mean: float, league_p25: float, league_p75: float) -> str:
    """Optional quartile band, kept deliberately neutral until validated."""
    if pts >= league_p75:
        return "top_quartile"
    if pts >= league_mean:
        return "above_average"
    if pts >= league_p25:
        return "below_average"
    return "bottom_quartile"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--my-team", help="Your franchise (substring match) for gap analysis")
    p.add_argument("--npv-csv", default=None, help="Path to NPV CSV (default: out/salary_efficiency/npv_<year>.csv)")
    p.add_argument("--include-experimental-bands", action="store_true",
                   help="Add neutral quartile bands to the CSV/table. Not validated as posture labels.")
    args = p.parse_args()

    csv_path = args.npv_csv or f"out/salary_efficiency/npv_{args.year}.csv"
    csv = Path(csv_path)
    if not csv.exists():
        print(f"ERROR: {csv} not found. Run `python -m salary_efficiency.npv --year {args.year}` first.",
              file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(csv)
    df["franchise"] = df["franchise"].fillna("?").str.strip()

    # Per-team metrics
    rows = []
    for fid, sub in df.groupby("franchise"):
        _, starting = select_starting_lineup(sub, points_col="projected_pts")
        bench = bench_skill_points(sub, points_col="projected_pts")
        rows.append({
            "franchise": fid,
            "contracts": len(sub),
            "starting_pts": round(starting["starting_pts"], 1),
            "qb": round(starting["qb_pts"], 1),
            "rb": round(starting["rb_pts"], 1),
            "wr": round(starting["wr_pts"], 1),
            "te": round(starting["te_pts"], 1),
            "skill": round(starting["skill_pts"], 1),
            "pk": round(starting["pk_pts"], 1),
            "def": round(starting["def_pts"], 1),
            "bench_pts": round(bench["bench_skill_pts"], 1),
            "bench_count": bench["bench_count"],
            "starters_filled": starting["starters_filled"],
            "missing_slots": starting["missing_slots"],
        })
    teams = pd.DataFrame(rows).sort_values("starting_pts", ascending=False).reset_index(drop=True)
    teams["rank"] = teams.index + 1

    # League stats for context. These are descriptive, not posture verdicts.
    league_mean = teams["starting_pts"].mean()
    league_p25 = teams["starting_pts"].quantile(0.25)
    league_p75 = teams["starting_pts"].quantile(0.75)
    playoff_line = teams.iloc[min(PLAYOFF_SPOTS, len(teams)) - 1]["starting_pts"]
    teams["projection_percentile"] = (teams["starting_pts"].rank(pct=True) * 100).round(0).astype(int)
    teams["gap_to_playoff_line"] = (teams["starting_pts"] - playoff_line).round(1)

    if args.include_experimental_bands:
        teams["projection_band_experimental"] = teams["starting_pts"].apply(
            lambda pts: experimental_band(
                pts,
                league_mean=league_mean,
                league_p25=league_p25,
                league_p75=league_p75,
            )
        )

    # Print league-wide table
    print(f"\n=== Projected lineup snapshot ({args.year}) ===")
    print(f"  League mean starting pts: {league_mean:.0f}")
    print(f"  p25 / p75: {league_p25:.0f} / {league_p75:.0f}")
    print(f"  Projected playoff line (#{PLAYOFF_SPOTS}): {playoff_line:.0f}")
    print("  Note: snapshot only; not a win-now/rebuild verdict.")
    if args.include_experimental_bands:
        print("  Experimental bands are descriptive quartiles only, not posture labels.")
    print()
    if args.include_experimental_bands:
        header = (f"{'Rk':>3}  {'Band*':<15}  {'Team':<24}  {'Start':>6}  {'Pctl':>5}  "
                  f"{'Gap#7':>6}  {'QB':>6}  {'Skill':>6}  {'Bench':>6}")
    else:
        header = (f"{'Rk':>3}  {'Team':<24}  {'Start':>6}  {'Pctl':>5}  "
                  f"{'Gap#7':>6}  {'QB':>6}  {'Skill':>6}  {'Bench':>6}")
    print(header)
    print("-" * (98 if args.include_experimental_bands else 82))
    for _, t in teams.iterrows():
        band = f"  {t['projection_band_experimental']:<15}" if args.include_experimental_bands else ""
        print(f"  {t['rank']:>2}{band}  {t['franchise']:<24}  "
              f"{t['starting_pts']:>6.0f}  {t['projection_percentile']:>4.0f}%  "
              f"{t['gap_to_playoff_line']:>+6.0f}  {t['qb']:>6.0f}  "
              f"{t['skill']:>6.0f}  {t['bench_pts']:>6.0f}")

    # Per-team detail for user
    if args.my_team:
        my_row = teams[teams["franchise"].str.contains(args.my_team, case=False, na=False)]
        if my_row.empty:
            print(f"\nWARN: '{args.my_team}' not found", file=sys.stderr)
        else:
            t = my_row.iloc[0]
            top_pts = teams.iloc[0]["starting_pts"]

            print(f"\n=== Your team: {t['franchise']} ===")
            print(f"  Rank: {int(t['rank'])} of {len(teams)}")
            print(f"  Projection percentile: {int(t['projection_percentile'])}%")
            print(f"  Starting points: {t['starting_pts']:.0f}")
            print(f"  Gap to #1: {top_pts - t['starting_pts']:.0f} pts")
            print(f"  Gap to #{PLAYOFF_SPOTS} projected playoff line: {t['gap_to_playoff_line']:+.0f} pts")
            print(f"  Bench depth: {t['bench_pts']:.0f} pts in {int(t['bench_count'])} skill bench players")
            if t["missing_slots"]:
                print(f"  Missing legal starter slots: {t['missing_slots']}")

    # Save CSV
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = OUT_DIR / f"lineup_snapshot_{args.year}.csv"
    teams.to_csv(out_csv, index=False)
    print(f"\nWrote {out_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
