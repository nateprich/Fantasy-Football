"""Salary efficiency analysis.

Builds a per-season dataset of (player, position, salary, season points) and
computes:
  - Position market curve (linear fit of salary on points using real auction signal).
  - Surplus value = predicted_salary - actual_salary.
  - Top steals / overpays per position and overall.
  - $/PPG by tier (top-12 QB/TE, top-24 RB/WR, top-16 PK/Def).

Run:
    python -m salary_efficiency.analyze --year 2025
    python -m salary_efficiency.analyze --year 2025 --years-back 3   # use 3 years of market data
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from lib import mfl
from lib.league import LEAGUE_MIN_SALARY, SCORING_POSITIONS

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "salary_efficiency"

TIER_SIZES = {"QB": 12, "RB": 24, "WR": 24, "TE": 12, "PK": 16, "Def": 16}


@dataclass
class SeasonRow:
    year: int
    player_id: str
    name: str
    position: str
    franchise: str | None
    salary: float
    contract_year: int
    points: float
    weeks_with_score: int


def build_season_dataframe(year: int, history: mfl.HistoricalBids) -> pd.DataFrame:
    print(f"[{year}] fetching player metadata, rosters, weekly results...", file=sys.stderr)
    players = mfl.fetch_player_metadata(year)
    franchises = mfl.fetch_franchises(year)
    rosters_w1 = mfl.fetch_rosters(year, week=1)
    try:
        rosters_w14 = mfl.fetch_rosters(year, week=14)
    except RuntimeError as e:
        if "Invalid week" in str(e):
            rosters_w14 = {}
        else:
            raise
    try:
        season_pts = mfl.fetch_season_points(year)
    except RuntimeError as e:
        if "Invalid week" in str(e):
            season_pts = {}
        else:
            raise

    # Merge rosters: prefer the snapshot with higher salary (covers in-season pickups w/ contracts)
    by_player: dict[str, dict] = {}
    for fid, plist in {**rosters_w1, **rosters_w14}.items():
        for p in plist:
            existing = by_player.get(p["player_id"])
            if existing is None or p["salary"] > existing["salary"]:
                by_player[p["player_id"]] = {**p, "franchise_id": fid}

    rows: list[SeasonRow] = []
    for pid, info in by_player.items():
        meta = players.get(pid)
        if not meta or meta["position"] not in SCORING_POSITIONS:
            continue
        escalated = history.escalated_salary(pid, year)
        salary = escalated if escalated > 0 else info["salary"]
        pts = season_pts.get(pid, {})
        rows.append(SeasonRow(
            year=year,
            player_id=pid,
            name=meta["name"],
            position=meta["position"],
            franchise=franchises.get(info["franchise_id"], {}).get("name"),
            salary=salary,
            contract_year=info["contract_year"],
            points=pts.get("points", 0.0),
            weeks_with_score=pts.get("weeks_with_score", 0),
        ))
    return pd.DataFrame([r.__dict__ for r in rows])


def fit_position_market(df: pd.DataFrame) -> dict[str, dict]:
    """Power-law fit per position: salary = c * points^k, fit via log-log regression.

    Why power law instead of linear: elite-tier salaries grow faster than mid-tier production.
    A linear fit understates the top of the curve, making elite players look like overpays.
    Power law (k > 1) captures the convex elite premium that auction markets actually pay.

    Fit only on the *productive* tail per position per year (top-N by points), since the
    "market" we want to model is what teams pay for real production — not minimum-salary
    bench depth, which dominates the dataset and flattens the curve.

    Falls back to median salary when sample is too small.

    Returns {position: {"k": exponent, "c": coefficient, "n": sample size, "median": float}}.
    """
    import numpy as np

    fits: dict[str, dict] = {}
    for pos in SCORING_POSITIONS:
        # Take top-N by points per year for this position, then pool.
        n_per_year = TIER_SIZES[pos] * 2  # 2x tier size = real fantasy starters + immediate backups
        frames = []
        for yr, yr_df in df[df["position"] == pos].groupby("year"):
            frames.append(yr_df.nlargest(n_per_year, "points"))
        sub = pd.concat(frames) if frames else df.iloc[0:0]
        sub = sub[(sub["salary"] > LEAGUE_MIN_SALARY) & (sub["points"] > 0)]
        median = float(sub["salary"].median()) if len(sub) else float(LEAGUE_MIN_SALARY)
        if len(sub) < 5:
            fits[pos] = {"k": 0.0, "c": median, "n": len(sub), "median": median}
            continue
        log_pts = np.log(sub["points"].values)
        log_sal = np.log(sub["salary"].values)
        k, log_c = np.polyfit(log_pts, log_sal, 1)
        fits[pos] = {"k": float(k), "c": float(np.exp(log_c)), "n": int(len(sub)), "median": median}
    return fits


def _predict(fit: dict, points: float) -> float:
    if points <= 0 or fit["k"] == 0.0:
        return max(LEAGUE_MIN_SALARY, fit["median"])
    return max(LEAGUE_MIN_SALARY, fit["c"] * (points ** fit["k"]))


def apply_market(df: pd.DataFrame, fits: dict[str, dict]) -> pd.DataFrame:
    out = df.copy()
    out["market_salary"] = out.apply(
        lambda r: _predict(fits[r["position"]], r["points"]),
        axis=1,
    )
    out["surplus"] = out["market_salary"] - out["salary"]
    out["dollars_per_point"] = out.apply(
        lambda r: r["salary"] / r["points"] if r["points"] > 0 else float("inf"),
        axis=1,
    )
    return out


def tier_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for pos, n in TIER_SIZES.items():
        sub = df[df["position"] == pos].nlargest(n, "points")
        if sub.empty:
            continue
        rows.append({
            "position": pos,
            "tier_size": n,
            "avg_points": round(sub["points"].mean(), 1),
            "avg_salary": round(sub["salary"].mean()),
            "median_salary": round(sub["salary"].median()),
            "avg_$_per_point": round(sub[sub["points"] > 0]["dollars_per_point"].mean(), 0),
        })
    return pd.DataFrame(rows)


def write_report(year: int, df: pd.DataFrame, fits: dict[str, dict], top_n: int = 15) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = OUT_DIR / f"{year}.md"
    csv = OUT_DIR / f"{year}.csv"
    df.sort_values("surplus", ascending=False).to_csv(csv, index=False)

    lines = [f"# Salary Efficiency — {year}", ""]
    lines.append(f"Players analyzed: **{len(df)}**  ·  Avg salary: **${int(df['salary'].mean()):,}**")
    lines.append("")

    lines.append("## Position market curves (power law)")
    lines.append("`predicted_salary = c * points^k` (log-log fit, players with salary > league min). k > 1 = convex / elite premium.")
    lines.append("")
    lines.append("| Pos | k | c | n |")
    lines.append("|---|---:|---:|---:|")
    for pos, f in fits.items():
        lines.append(f"| {pos} | {f['k']:.2f} | {f['c']:,.1f} | {f['n']} |")
    lines.append("")

    lines.append("## Tier $/PPG")
    lines.append(tier_summary(df).to_markdown(index=False))
    lines.append("")

    lines.append(f"## Top {top_n} steals (highest surplus value, min 4 scoring weeks; PK/Def excluded — commoditized)")
    cols = ["name", "position", "franchise", "points", "salary", "market_salary", "surplus", "contract_year"]
    skill_only = df[(df["weeks_with_score"] >= 4) & (~df["position"].isin(["PK", "Def"]))]
    qualifying = skill_only
    steals = qualifying.nlargest(top_n, "surplus")[cols]
    lines.append(_money_md(steals))
    lines.append("")

    lines.append(f"## Top {top_n} overpays (lowest surplus value; PK/Def excluded)")
    overpays = df[~df["position"].isin(["PK", "Def"])].nsmallest(top_n, "surplus")[cols]
    lines.append(_money_md(overpays))
    lines.append("")

    lines.append(f"## Top {top_n} steals by position (min 4 scoring weeks)")
    for pos in SCORING_POSITIONS:
        if pos in ("PK", "Def"):
            continue
        sub = qualifying[qualifying["position"] == pos].nlargest(5, "surplus")[cols]
        if sub.empty:
            continue
        lines.append(f"### {pos}")
        lines.append(_money_md(sub))
        lines.append("")

    md.write_text("\n".join(lines))
    print(f"Wrote {md} and {csv}", file=sys.stderr)
    return md


def _money_md(df: pd.DataFrame) -> str:
    fmt = df.copy()
    for c in ("salary", "market_salary", "surplus"):
        if c in fmt.columns:
            fmt[c] = fmt[c].apply(lambda v: f"${int(v):,}")
    if "points" in fmt.columns:
        fmt["points"] = fmt["points"].apply(lambda v: f"{v:.1f}")
    return fmt.to_markdown(index=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, required=True, help="Target season to analyze")
    p.add_argument("--history-start", type=int, default=2017, help="Earliest year for auction/BBID lookback")
    p.add_argument("--years-back", type=int, default=1,
                   help="Pool N seasons (target year and N-1 prior) when fitting the market curve")
    p.add_argument("--top", type=int, default=15)
    args = p.parse_args()

    print(f"Loading historical bids {args.history_start}..{args.year}...", file=sys.stderr)
    history = mfl.HistoricalBids.load(args.history_start, args.year)

    target_df = build_season_dataframe(args.year, history)
    if args.years_back > 1:
        market_frames = [target_df]
        for y in range(args.year - 1, args.year - args.years_back, -1):
            market_frames.append(build_season_dataframe(y, history))
        market_df = pd.concat(market_frames, ignore_index=True)
    else:
        market_df = target_df

    fits = fit_position_market(market_df)
    enriched = apply_market(target_df, fits)
    write_report(args.year, enriched, fits, top_n=args.top)


if __name__ == "__main__":
    main()
