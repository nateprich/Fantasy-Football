"""Cutdown-day analyzer.

This tool answers a narrower question than competitiveness/posture analysis:
if a team must reduce contracts from current roster size to a target size, which
players create the least modeled damage if removed?

It uses the live MFL roster as the source of truth and joins NPV/projection data
when available. Missing NPV rows are flagged for scouting review, not treated as
automatic cuts.

Run:
    python -m cutdown.analyze --year 2026 --my-team "Midwestside"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from cap_health.analyze import build_roster_dataframe
from competitiveness.lineup import bench_skill_points, select_starting_lineup
from lib import mfl
from lib.league import ACTIVE_ROSTER_MAX, PRACTICE_SQUAD_MAX

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "cutdown"


def _money(value) -> str:
    if pd.isna(value):
        return ""
    return f"${int(value):,}"


def load_team_roster(year: int, week: int, my_team: str, history_start: int) -> pd.DataFrame:
    history = mfl.HistoricalBids.load(history_start, year)
    roster = build_roster_dataframe(year, week, history)
    matches = roster[roster["franchise"].str.contains(my_team, case=False, na=False)]
    if matches.empty:
        raise SystemExit(f"ERROR: no franchise matching '{my_team}'")
    franchise = matches["franchise"].iloc[0]
    return roster[roster["franchise"] == franchise].copy()


def overlay_npv(roster: pd.DataFrame, npv_csv: Path | None) -> pd.DataFrame:
    out = roster.copy()
    out["player_id"] = out["player_id"].astype(str)
    if npv_csv and npv_csv.exists():
        npv = pd.read_csv(npv_csv, dtype={"player_id": str})
        keep_cols = [
            "player_id", "projected_pts", "projection_source", "value", "gross_npv",
            "cut_value", "would_cut", "age", "aging_risk_flag", "aging_risk_score",
        ]
        npv = npv[[col for col in keep_cols if col in npv.columns]].copy()
        out = out.merge(npv, on="player_id", how="left", suffixes=("", "_npv"))
    else:
        out["projected_pts"] = None
        out["projection_source"] = None
        out["value"] = None
        out["gross_npv"] = None
        out["cut_value"] = None
        out["would_cut"] = None
        out["age"] = None
        out["aging_risk_flag"] = None
        out["aging_risk_score"] = None

    out["projected_pts"] = out["projected_pts"].fillna(0.0)
    out["projection_source"] = out["projection_source"].fillna("missing_npv")
    out["value"] = out["value"].fillna(0.0)
    out["cut_value"] = out["cut_value"].fillna(0.0)
    out["would_cut"] = out["would_cut"].fillna(False)
    out["model_status"] = out.apply(model_status, axis=1)
    return out


def model_status(row: pd.Series) -> str:
    if row["projection_source"] == "missing_npv":
        return "SCOUTING_REQUIRED_MISSING_NPV"
    if float(row.get("projected_pts") or 0) <= 0:
        return "SCOUTING_REQUIRED_NO_PROJECTION"
    return "MODELED"


def candidate_impacts(team: pd.DataFrame) -> pd.DataFrame:
    _, baseline = select_starting_lineup(team, points_col="projected_pts")
    baseline_bench = bench_skill_points(team, points_col="projected_pts")

    rows = []
    for idx, player in team.iterrows():
        after = team.drop(index=idx)
        _, lineup = select_starting_lineup(after, points_col="projected_pts")
        bench = bench_skill_points(after, points_col="projected_pts")
        rows.append({
            "name": player["name"],
            "position": player["position"],
            "salary": player["salary"],
            "years_remaining": player["years_remaining"],
            "projected_pts": player["projected_pts"],
            "projection_source": player["projection_source"],
            "value": player["value"],
            "cut_value": player["cut_value"],
            "would_cut": player["would_cut"],
            "aging_risk_flag": player.get("aging_risk_flag"),
            "model_status": player["model_status"],
            "lineup_loss": round(baseline["starting_pts"] - lineup["starting_pts"], 1),
            "bench_loss": round(baseline_bench["bench_skill_pts"] - bench["bench_skill_pts"], 1),
            "missing_slots_after_cut": lineup["missing_slots"],
        })
    out = pd.DataFrame(rows)
    out["modeled_rank_key"] = out["model_status"].map({"MODELED": 0}).fillna(1)
    return out.sort_values([
        "modeled_rank_key", "lineup_loss", "bench_loss", "value", "projected_pts", "salary"
    ], ascending=[True, True, True, True, True, False]).drop(columns=["modeled_rank_key"])


def position_counts(team: pd.DataFrame) -> pd.DataFrame:
    return (
        team.groupby("position")
        .agg(count=("name", "count"), salary=("salary", "sum"), projected_pts=("projected_pts", "sum"), value=("value", "sum"))
        .reset_index()
        .sort_values("position")
    )


def write_report(year: int, team: pd.DataFrame, candidates: pd.DataFrame, target_size: int) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    franchise = team["franchise"].iloc[0]
    slug = franchise.lower().replace(" ", "_").replace("/", "_")
    md = OUT_DIR / f"cutdown_{year}_{slug}.md"
    csv = OUT_DIR / f"cutdown_{year}_{slug}.csv"
    candidates.to_csv(csv, index=False)

    roster_size = len(team)
    required_cuts = max(0, roster_size - target_size)
    _, baseline = select_starting_lineup(team, points_col="projected_pts")
    bench = bench_skill_points(team, points_col="projected_pts")
    counts = position_counts(team)

    modeled = candidates[candidates["model_status"] == "MODELED"].head(max(10, required_cuts + 5)).copy()
    scouting = candidates[candidates["model_status"] != "MODELED"].copy()

    lines = [
        f"# Cutdown Analysis - {franchise} ({year})",
        "",
        f"Roster size: **{roster_size}**  Target size: **{target_size}**  Required cuts: **{required_cuts}**",
        "",
        "This is a cut-impact report, not an auto-drop list. It uses live roster slots first,",
        "then overlays NPV/projection data where available. Players missing projection data are",
        "flagged for scouting review instead of being ranked as easy cuts.",
        "",
        "## Baseline",
        f"Projected legal starting lineup: **{baseline['starting_pts']:.1f} pts**",
        f"Bench skill depth: **{bench['bench_skill_pts']:.1f} pts** across **{bench['bench_count']}** QB/RB/WR/TE bench players",
        f"Missing legal starter slots: **{baseline['missing_slots'] or 'none'}**",
        "",
        "## Position Counts",
        _format_money_table(counts, ["salary", "value"]),
        "",
        "## Modeled Low-Impact Cut Candidates",
        "Sorted by least starting-lineup loss, then least bench-depth loss, then lowest NPV.",
        _format_money_table(modeled[[
            "name", "position", "salary", "years_remaining", "projected_pts", "projection_source",
            "value", "cut_value", "lineup_loss", "bench_loss", "would_cut", "aging_risk_flag",
        ]], ["salary", "value", "cut_value"]),
        "",
    ]
    if not scouting.empty:
        lines.extend([
            "## Scouting-Required Players",
            "These rows are missing NPV/projection coverage. Do not treat zero modeled impact as zero real value.",
            _format_money_table(scouting[[
                "name", "position", "salary", "years_remaining", "model_status", "lineup_loss", "bench_loss",
            ]], ["salary"]),
            "",
        ])

    md.write_text("\n".join(lines))
    print(f"Wrote {md} and {csv}", file=sys.stderr)
    return md


def _format_money_table(df: pd.DataFrame, money_cols: list[str]) -> str:
    if df.empty:
        return "_(none)_"
    fmt = df.copy()
    for col in money_cols:
        if col in fmt.columns:
            fmt[col] = fmt[col].apply(_money)
    if "projected_pts" in fmt.columns:
        fmt["projected_pts"] = fmt["projected_pts"].apply(lambda value: f"{float(value):.1f}")
    if "lineup_loss" in fmt.columns:
        fmt["lineup_loss"] = fmt["lineup_loss"].apply(lambda value: f"{float(value):.1f}")
    if "bench_loss" in fmt.columns:
        fmt["bench_loss"] = fmt["bench_loss"].apply(lambda value: f"{float(value):.1f}")
    return fmt.to_markdown(index=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--week", type=int, default=None,
                        help="MFL roster week. Omit for current/offseason roster snapshot.")
    parser.add_argument("--my-team", required=True)
    parser.add_argument("--target-size", type=int, default=ACTIVE_ROSTER_MAX + PRACTICE_SQUAD_MAX)
    parser.add_argument("--history-start", type=int, default=2017)
    parser.add_argument("--npv-csv", default=None,
                        help="Path to NPV CSV. Default: out/salary_efficiency/npv_<year>.csv if present.")
    args = parser.parse_args()

    default_npv = Path(f"out/salary_efficiency/npv_{args.year}.csv")
    npv_csv = Path(args.npv_csv) if args.npv_csv else default_npv
    if not npv_csv.exists():
        print(f"WARN: {npv_csv} not found; running with live roster only", file=sys.stderr)
        npv_csv = None

    team = overlay_npv(load_team_roster(args.year, args.week, args.my_team, args.history_start), npv_csv)
    candidates = candidate_impacts(team)
    write_report(args.year, team, candidates, args.target_size)

    roster_size = len(team)
    required_cuts = max(0, roster_size - args.target_size)
    print(f"\n=== Cutdown analysis: {team['franchise'].iloc[0]} ===")
    print(f"Roster size {roster_size}; target {args.target_size}; required cuts {required_cuts}")
    print(candidates.head(max(10, required_cuts + 5))[[
        "name", "position", "salary", "years_remaining", "projected_pts",
        "value", "lineup_loss", "bench_loss", "model_status",
    ]].to_string(index=False))


if __name__ == "__main__":
    main()
