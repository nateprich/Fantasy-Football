"""Cap health analysis.

Per-team breakdown of:
  - Committed cap, remaining cap, top-3 player concentration.
  - Roster size and contract-year distribution (years remaining).
  - Expirations next season (years_remaining == 1).
  - Risk flags (cap-stressed, top-heavy, thin roster).

Note on MFL contractYear: per the JS exporter and constitution, this field
represents YEARS REMAINING on the contract (counts down annually on Feb 15).

Run:
    python -m cap_health.analyze --year 2026
    python -m cap_health.analyze --year 2026 --week 14
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from lib import mfl
from lib.league import (
    ACTIVE_ROSTER_MAX,
    NUM_TEAMS,
    SALARY_CAP,
    SCORING_POSITIONS,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "cap_health"


def build_roster_dataframe(year: int, week: int | None, history: mfl.HistoricalBids) -> pd.DataFrame:
    players = mfl.fetch_player_metadata(year)
    franchises = mfl.fetch_franchises(year)
    rosters = mfl.fetch_rosters(year, week=week)

    rows = []
    for fid, plist in rosters.items():
        franchise_name = franchises.get(fid, {}).get("name", fid)
        for p in plist:
            meta = players.get(p["player_id"], {})
            pos = meta.get("position")
            escalated = history.escalated_salary(p["player_id"], year)
            salary = escalated if escalated > 0 else p["salary"]
            rows.append({
                "franchise_id": fid,
                "franchise": franchise_name,
                "player_id": p["player_id"],
                "name": meta.get("name", "?"),
                "position": pos,
                "salary": salary,
                "years_remaining": p["contract_year"],
                "status": p.get("status"),
            })
    return pd.DataFrame(rows)


def team_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for fid, sub in df.groupby("franchise_id"):
        committed = float(sub["salary"].sum())
        top3 = float(sub.nlargest(3, "salary")["salary"].sum())
        scoring_only = sub[sub["position"].isin(SCORING_POSITIONS)]
        years = sub["years_remaining"]
        rows.append({
            "franchise_id": fid,
            "franchise": sub["franchise"].iloc[0],
            "roster_size": len(sub),
            "scoring_players": len(scoring_only),
            "committed": round(committed),
            "remaining": round(SALARY_CAP - committed),
            "remaining_pct": round((SALARY_CAP - committed) / SALARY_CAP * 100, 1),
            "top3_pct_of_cap": round(top3 / SALARY_CAP * 100, 1),
            "expiring_next": int((years == 1).sum()),
            "y2_remaining": int((years == 2).sum()),
            "y3plus_remaining": int((years >= 3).sum()),
            "avg_years_remaining": round(years[years > 0].mean() if (years > 0).any() else 0, 2),
        })
    out = pd.DataFrame(rows).sort_values("remaining", ascending=False)
    return out


def league_position_market(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for pos in SCORING_POSITIONS:
        sub = df[df["position"] == pos]
        if sub.empty:
            continue
        rows.append({
            "position": pos,
            "rostered": len(sub),
            "total_committed": int(sub["salary"].sum()),
            "median_salary": int(sub["salary"].median()),
            "top10_avg": int(sub.nlargest(10, "salary")["salary"].mean()) if len(sub) >= 10 else int(sub["salary"].mean()),
            "top30_avg": int(sub.nlargest(30, "salary")["salary"].mean()) if len(sub) >= 30 else int(sub["salary"].mean()),
        })
    return pd.DataFrame(rows)


def flag_risks(team_df: pd.DataFrame) -> pd.DataFrame:
    flags = []
    for _, t in team_df.iterrows():
        f = []
        if t["remaining"] < 0:
            f.append("OVER_CAP")
        elif t["remaining"] < 1_000_000:
            f.append("CAP_STRESS")
        if t["top3_pct_of_cap"] >= 50:
            f.append("TOP_HEAVY")
        if t["roster_size"] < 20:
            f.append("THIN_ROSTER")
        if t["expiring_next"] >= 8:
            f.append("EXPIRATION_CLIFF")
        flags.append(" ".join(f) or "—")
    out = team_df.copy()
    out["flags"] = flags
    return out


def write_report(year: int, week: int | None, roster_df: pd.DataFrame) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = OUT_DIR / f"{year}.md"
    csv = OUT_DIR / f"{year}_rosters.csv"
    roster_df.to_csv(csv, index=False)

    teams = flag_risks(team_summary(roster_df))
    market = league_position_market(roster_df)

    week_label = "current" if week is None else f"week {week}"
    lines = [f"# Cap Health — {year} ({week_label} snapshot)", ""]
    lines.append(f"Cap: **${SALARY_CAP:,}**  ·  Teams: **{NUM_TEAMS}**  ·  Active roster max: **{ACTIVE_ROSTER_MAX}**")
    lines.append("")

    lines.append("## Team summary")
    show = teams[[
        "franchise", "roster_size", "committed", "remaining", "remaining_pct",
        "top3_pct_of_cap", "expiring_next", "y2_remaining", "y3plus_remaining",
        "avg_years_remaining", "flags",
    ]].copy()
    show["committed"] = show["committed"].apply(lambda v: f"${int(v):,}")
    show["remaining"] = show["remaining"].apply(lambda v: f"${int(v):,}")
    lines.append(show.to_markdown(index=False))
    lines.append("")

    lines.append("## League position market (rostered salaries)")
    lines.append(market.to_markdown(index=False))
    lines.append("")

    lines.append("## Top 5 expiring contracts per team (years_remaining = 1)")
    for fid, sub in roster_df[roster_df["years_remaining"] == 1].groupby("franchise_id"):
        franchise = sub["franchise"].iloc[0]
        top5 = sub.nlargest(5, "salary")[["name", "position", "salary"]].copy()
        if top5.empty:
            continue
        top5["salary"] = top5["salary"].apply(lambda v: f"${int(v):,}")
        lines.append(f"### {franchise}")
        lines.append(top5.to_markdown(index=False))
        lines.append("")

    md.write_text("\n".join(lines))
    print(f"Wrote {md} and {csv}", file=sys.stderr)
    return md


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--week", type=int, default=1)
    p.add_argument("--current", action="store_true", help="Fetch current/offseason roster snapshot (omit MFL W param)")
    p.add_argument("--history-start", type=int, default=2017)
    args = p.parse_args()

    week = None if args.current else args.week
    history = mfl.HistoricalBids.load(args.history_start, args.year)
    df = build_roster_dataframe(args.year, week, history)
    write_report(args.year, week, df)


if __name__ == "__main__":
    main()
