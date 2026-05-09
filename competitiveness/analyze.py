"""League competitiveness analyzer.

Given the current rosters and FP projections (already in the NPV report), compute:
  - Each team's projected best starting lineup points (1 QB + 6 RB/WR/TE flex + 1 PK + 1 Def)
  - League-wide projected standings ranking
  - Per-team strength of the bench (how much depth they have above replacement)
  - For the user's team: gap to playoff line, percentile, contender/middle/rebuilder verdict

The verdict drives the win-now vs. build-future strategic posture for the season.

Run:
    python -m competitiveness.analyze --year 2026 --my-team "Midwestside"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "competitiveness"

# Per the constitution, lineup is 1 QB + 1-4 RB + 1-4 WR + 1-4 TE + 1 PK + 1 Def.
# Total starters = 9. RB/WR/TE flex = 6 across the three positions.
STARTING_QB = 1
STARTING_FLEX = 6  # RB/WR/TE combined
STARTING_PK = 1
STARTING_DEF = 1


def best_lineup_pts(team_df: pd.DataFrame) -> dict:
    """Compute projected starting lineup points using top-N at each role."""
    qb = team_df[team_df["position"] == "QB"].nlargest(STARTING_QB, "projected_pts")
    flex = team_df[team_df["position"].isin(["RB", "WR", "TE"])].nlargest(STARTING_FLEX, "projected_pts")
    pk = team_df[team_df["position"] == "PK"].nlargest(STARTING_PK, "projected_pts")
    df_def = team_df[team_df["position"] == "Def"].nlargest(STARTING_DEF, "projected_pts")

    return {
        "qb_pts": qb["projected_pts"].sum(),
        "flex_pts": flex["projected_pts"].sum(),
        "pk_pts": pk["projected_pts"].sum(),
        "def_pts": df_def["projected_pts"].sum(),
        "starting_pts": (qb["projected_pts"].sum() + flex["projected_pts"].sum()
                         + pk["projected_pts"].sum() + df_def["projected_pts"].sum()),
        "starters_filled": (len(qb) == STARTING_QB and len(flex) == STARTING_FLEX
                            and len(pk) == STARTING_PK and len(df_def) == STARTING_DEF),
    }


def bench_strength(team_df: pd.DataFrame) -> dict:
    """Quantify bench depth: total points from non-starters, weighted by likelihood of starting."""
    qb_bench = team_df[team_df["position"] == "QB"].sort_values("projected_pts", ascending=False).iloc[STARTING_QB:]
    flex_bench = team_df[team_df["position"].isin(["RB", "WR", "TE"])].sort_values(
        "projected_pts", ascending=False).iloc[STARTING_FLEX:]

    # Bench skill players still produce when starters are injured/on bye
    bench_skill_pts = qb_bench["projected_pts"].sum() + flex_bench["projected_pts"].sum()
    bench_count = len(qb_bench) + len(flex_bench)

    return {
        "bench_skill_pts": bench_skill_pts,
        "bench_count": bench_count,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--my-team", help="Your franchise (substring match) for gap analysis")
    p.add_argument("--npv-csv", default=None, help="Path to NPV CSV (default: out/salary_efficiency/npv_<year>.csv)")
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
        starting = best_lineup_pts(sub)
        bench = bench_strength(sub)
        rows.append({
            "franchise": fid,
            "contracts": len(sub),
            "starting_pts": round(starting["starting_pts"], 1),
            "qb": round(starting["qb_pts"], 1),
            "flex": round(starting["flex_pts"], 1),
            "pk": round(starting["pk_pts"], 1),
            "def": round(starting["def_pts"], 1),
            "bench_pts": round(bench["bench_skill_pts"], 1),
            "bench_count": bench["bench_count"],
            "starters_filled": starting["starters_filled"],
        })
    teams = pd.DataFrame(rows).sort_values("starting_pts", ascending=False).reset_index(drop=True)
    teams["rank"] = teams.index + 1

    # League stats for buckets
    league_mean = teams["starting_pts"].mean()
    league_p25 = teams["starting_pts"].quantile(0.25)
    league_p75 = teams["starting_pts"].quantile(0.75)

    def bucket(pts: float) -> str:
        if pts >= league_p75:
            return "CONTENDER"
        if pts >= league_mean:
            return "PLAYOFF BUBBLE"
        if pts >= league_p25:
            return "BORDERLINE REBUILDER"
        return "REBUILDER"

    teams["bucket"] = teams["starting_pts"].apply(bucket)

    # Print league-wide table
    print(f"\n=== League competitiveness ({args.year}) ===")
    print(f"  League mean starting pts: {league_mean:.0f}")
    print(f"  p25 / p75: {league_p25:.0f} / {league_p75:.0f}")
    print()
    print(f"{'Rk':>3}  {'Bucket':<22}  {'Team':<24}  {'Start':>6}  {'QB':>6}  {'Flex':>6}  {'Bench':>6}")
    print("-" * 95)
    for _, t in teams.iterrows():
        print(f"  {t['rank']:>2}  {t['bucket']:<22}  {t['franchise']:<24}  "
              f"{t['starting_pts']:>6.0f}  {t['qb']:>6.0f}  {t['flex']:>6.0f}  {t['bench_pts']:>6.0f}")

    # Per-team detail for user
    if args.my_team:
        my_row = teams[teams["franchise"].str.contains(args.my_team, case=False, na=False)]
        if my_row.empty:
            print(f"\nWARN: '{args.my_team}' not found", file=sys.stderr)
        else:
            t = my_row.iloc[0]
            top_pts = teams.iloc[0]["starting_pts"]
            seventh_pts = teams.iloc[6]["starting_pts"] if len(teams) >= 7 else teams.iloc[-1]["starting_pts"]

            print(f"\n=== Your team: {t['franchise']} ===")
            print(f"  Bucket: {t['bucket']}")
            print(f"  Rank: {int(t['rank'])} of {len(teams)}")
            print(f"  Starting points: {t['starting_pts']:.0f}")
            print(f"  Gap to #1: {top_pts - t['starting_pts']:.0f} pts")
            print(f"  Gap to #7 (last playoff spot): {seventh_pts - t['starting_pts']:+.0f} pts")
            print(f"  Bench depth: {t['bench_pts']:.0f} pts in {int(t['bench_count'])} skill bench players")

    # Save CSV
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = OUT_DIR / f"competitiveness_{args.year}.csv"
    teams.to_csv(out_csv, index=False)
    print(f"\nWrote {out_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
