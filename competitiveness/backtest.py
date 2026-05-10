"""Retrospective lineup-strength sanity check.

This is not a true preseason projection backtest unless you feed it archived
preseason projection CSVs. The default mode uses actual season player points on
the roster-week snapshot, so it mainly tests whether the lineup aggregation and
roster-strength ranking line up with actual team scoring.

Run:
    python -m competitiveness.backtest --years 2021 2022 2023 2024 2025
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from competitiveness.lineup import bench_skill_points, select_starting_lineup
from lib import mfl

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "competitiveness"
PLAYOFF_SPOTS = 7


def _ensure_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def fetch_team_points(year: int) -> dict[str, float]:
    """Return actual team fantasy points by franchise id, summed across weeks."""
    totals: dict[str, float] = {}
    for week in range(1, 18):
        try:
            data = mfl.fetch(year, "weeklyResults", W=week)
        except RuntimeError as exc:
            msg = str(exc)
            if "Invalid week" in msg or "503" in msg or "404" in msg:
                break
            raise
        for matchup in _ensure_list(data.get("weeklyResults", {}).get("matchup")):
            for franchise in _ensure_list(matchup.get("franchise")):
                fid = franchise.get("id")
                score = franchise.get("score")
                if not fid or score in (None, ""):
                    continue
                totals[fid] = totals.get(fid, 0.0) + float(score)
    return totals


def retrospective_lineup_snapshot(year: int, roster_week: int) -> pd.DataFrame:
    """Build a lineup-strength snapshot using actual full-season player points."""
    players = mfl.fetch_player_metadata(year)
    franchises = mfl.fetch_franchises(year)
    rosters = mfl.fetch_rosters(year, week=roster_week)
    season_points = mfl.fetch_season_points(year)
    team_points = fetch_team_points(year)

    rows = []
    for fid, roster in rosters.items():
        player_rows = []
        for player in roster:
            player_id = player["player_id"]
            meta = players.get(player_id)
            if not meta or not meta.get("position"):
                continue
            player_rows.append({
                "player_id": player_id,
                "name": meta["name"],
                "position": meta["position"],
                "actual_pts": season_points.get(player_id, {}).get("points", 0.0),
            })
        team_df = pd.DataFrame(player_rows)
        if team_df.empty:
            continue
        _, starting = select_starting_lineup(team_df, points_col="actual_pts")
        bench = bench_skill_points(team_df, points_col="actual_pts")
        rows.append({
            "year": year,
            "franchise_id": fid,
            "franchise": franchises.get(fid, {}).get("name", fid),
            "contracts": len(team_df),
            "lineup_strength_pts": round(starting["starting_pts"], 1),
            "bench_pts": round(bench["bench_skill_pts"], 1),
            "actual_team_pts": round(team_points.get(fid, 0.0), 1),
            "starters_filled": starting["starters_filled"],
            "missing_slots": starting["missing_slots"],
        })
    out = pd.DataFrame(rows)
    out["lineup_rank"] = out["lineup_strength_pts"].rank(ascending=False, method="min").astype(int)
    out["actual_rank"] = out["actual_team_pts"].rank(ascending=False, method="min").astype(int)
    out["rank_error"] = (out["lineup_rank"] - out["actual_rank"]).abs()
    playoff_cut = set(out.nsmallest(PLAYOFF_SPOTS, "actual_rank")["franchise_id"])
    projected_cut = set(out.nsmallest(PLAYOFF_SPOTS, "lineup_rank")["franchise_id"])
    out["actual_top7"] = out["franchise_id"].isin(playoff_cut)
    out["lineup_top7"] = out["franchise_id"].isin(projected_cut)
    return out.sort_values(["year", "lineup_rank"])


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year, sub in results.groupby("year"):
        rows.append({
            "year": year,
            "pearson": round(sub["lineup_strength_pts"].corr(sub["actual_team_pts"], method="pearson"), 3),
            "spearman": round(sub["lineup_strength_pts"].corr(sub["actual_team_pts"], method="spearman"), 3),
            "mean_abs_rank_error": round(sub["rank_error"].mean(), 2),
            "top7_overlap": int((sub["actual_top7"] & sub["lineup_top7"]).sum()),
        })
    all_rows = {
        "year": "all",
        "pearson": round(results["lineup_strength_pts"].corr(results["actual_team_pts"], method="pearson"), 3),
        "spearman": round(results["lineup_strength_pts"].corr(results["actual_team_pts"], method="spearman"), 3),
        "mean_abs_rank_error": round(results["rank_error"].mean(), 2),
        "top7_overlap": "",
    }
    return pd.concat([pd.DataFrame(rows), pd.DataFrame([all_rows])], ignore_index=True)


def write_report(results: pd.DataFrame, summary: pd.DataFrame, years: list[int], roster_week: int) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    start, end = min(years), max(years)
    csv = OUT_DIR / f"backtest_lineup_strength_{start}_{end}.csv"
    md = OUT_DIR / f"backtest_lineup_strength_{start}_{end}.md"
    results.to_csv(csv, index=False)

    lines = [
        f"# Lineup Strength Retrospective Check ({start}-{end})",
        "",
        f"Roster snapshot week: **{roster_week}**",
        "",
        "This is a sanity check for the lineup aggregation, not a true forecast backtest.",
        "Default mode uses actual full-season player points on each roster-week snapshot,",
        "so it has hindsight baked in. A decision-grade forecast test requires archived",
        "preseason projections saved before the season starts.",
        "",
        "## Summary",
        summary.to_markdown(index=False),
        "",
        "## Per-team rows",
        results[[
            "year", "franchise", "lineup_rank", "actual_rank", "rank_error",
            "lineup_strength_pts", "actual_team_pts", "bench_pts", "starters_filled",
        ]].to_markdown(index=False),
        "",
    ]
    md.write_text("\n".join(lines))
    print(f"Wrote {md} and {csv}")
    return md


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="+", type=int, required=True)
    parser.add_argument("--roster-week", type=int, default=1)
    args = parser.parse_args()

    frames = [retrospective_lineup_snapshot(year, args.roster_week) for year in args.years]
    results = pd.concat(frames, ignore_index=True)
    summary = summarize(results)
    write_report(results, summary, args.years, args.roster_week)
    print("\n=== Retrospective lineup-strength check ===")
    print(summary.to_string(index=False))
    print("\nCaution: this is hindsight-based unless run against archived preseason projections.")


if __name__ == "__main__":
    main()
