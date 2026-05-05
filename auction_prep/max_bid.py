"""Per-player max-bid calculator.

Computes the bid above which a contract goes NPV-negative at the configured
discount rate, for each contract length 1–5 years. Reuses the existing NPV
machinery; the only new logic is the inversion (given desired NPV, solve for
the salary at signing).

Math:
    NPV(s, n, r) = sum_{t=0..n-1} (M − s · 1.10^t) / (1+r)^t
    where M = projected market salary, s = signing salary, n = contract years,
    r = discount rate. NPV is linear in s, so:
        s_breakeven = (N·M) / Σ (1.10^t / (1+r)^t)
    where N = Σ 1/(1+r)^t for t=0..n-1.

We then apply an optional `margin` (0.10 = 10%) to bid 10% under the breakeven.

Run:
    python -m auction_prep.max_bid --player "Brian Thomas Jr"
    python -m auction_prep.max_bid --position WR --top 30
    python -m auction_prep.max_bid --player "Puka" --discount 0.25 --margin 0.10
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from lib import mfl
from lib.fantasypros import projected_points_by_mflid
from lib.league import ANNUAL_ESCALATION, LEAGUE_MIN_SALARY, SCORING_POSITIONS
from salary_efficiency.analyze import build_season_dataframe, fit_position_market
from salary_efficiency.npv import predict_market

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "auction_prep"


def breakeven_salary(market: float, years: int, discount: float) -> float:
    """Solve for the year-0 salary that makes NPV exactly zero."""
    if market <= LEAGUE_MIN_SALARY or years <= 0:
        return float(LEAGUE_MIN_SALARY)
    n_factor = sum(1 / ((1 + discount) ** t) for t in range(years))
    s_factor = sum(((1 + ANNUAL_ESCALATION) ** t) / ((1 + discount) ** t) for t in range(years))
    return (n_factor * market) / s_factor


def fmt_money(v: float) -> str:
    return f"${int(v):,}"


def evaluate_player(market: float, discount: float, margin: float) -> list[dict]:
    rows = []
    for years in range(1, 6):
        be = breakeven_salary(market, years, discount)
        max_bid = max(LEAGUE_MIN_SALARY, be * (1 - margin))
        rows.append({
            "term_years": years,
            "market_y1": int(market),
            "breakeven_bid": int(be),
            "max_bid": int(max_bid),
        })
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--player", help="Player name (substring match) or MFL id")
    p.add_argument("--position", help="Position to batch-rank (QB/RB/WR/TE)")
    p.add_argument("--top", type=int, default=30, help="Top N players in batch mode")
    p.add_argument("--discount", type=float, default=0.20)
    p.add_argument("--margin", type=float, default=0.0,
                   help="Surplus margin (e.g. 0.10 = bid 10% under NPV-zero)")
    p.add_argument("--year", type=int, default=date.today().year,
                   help="Season for projections")
    p.add_argument("--years-back", type=int, default=5,
                   help="Seasons to pool for the market fit (default 5)")
    p.add_argument("--scoring", default="points_ppr",
                   choices=["points", "points_ppr", "points_half"])
    args = p.parse_args()

    if not args.player and not args.position:
        print("ERROR: pass --player or --position", file=sys.stderr)
        sys.exit(2)

    print(f"Loading historical bids 2017..{args.year}...", file=sys.stderr)
    history = mfl.HistoricalBids.load(2017, args.year)

    print(f"Building market fit pooled over last {args.years_back} seasons...", file=sys.stderr)
    market_frames = []
    for y in range(args.year, args.year - args.years_back, -1):
        try:
            market_frames.append(build_season_dataframe(y, history))
        except Exception as e:  # noqa: BLE001
            print(f"  WARN: skip {y}: {e}", file=sys.stderr)
    fits = fit_position_market(pd.concat(market_frames, ignore_index=True))

    print(f"Loading FantasyPros projections for {args.year}...", file=sys.stderr)
    fp = projected_points_by_mflid(args.year, scoring=args.scoring)

    players = mfl.fetch_player_metadata(args.year)
    rows = []
    for pid, meta in players.items():
        if meta["position"] not in SCORING_POSITIONS:
            continue
        pts = fp.get(pid, 0.0)
        if pts <= 0:
            continue
        market = predict_market(fits, meta["position"], pts)
        rows.append({
            "player_id": pid,
            "name": meta["name"],
            "position": meta["position"],
            "projected_pts": pts,
            "market_y1": int(market),
        })
    df = pd.DataFrame(rows)

    if args.player:
        q = args.player.lower()
        if args.player.isdigit():
            sub = df[df["player_id"] == args.player]
        else:
            sub = df[df["name"].str.lower().str.contains(q, na=False)]
        if sub.empty:
            print(f"ERROR: player '{args.player}' not found in projections", file=sys.stderr)
            sys.exit(1)
        if len(sub) > 1:
            exact = sub[sub["name"].str.lower() == q]
            row = exact.iloc[0] if not exact.empty else sub.sort_values("projected_pts", ascending=False).iloc[0]
        else:
            row = sub.iloc[0]
        evals = evaluate_player(row["market_y1"], args.discount, args.margin)
        print()
        print(f"{row['name']} ({row['position']})")
        print(f"  Projected: {row['projected_pts']:.1f} pts (FP)")
        print(f"  Market salary (Y1): {fmt_money(row['market_y1'])}")
        print(f"  Discount rate: {args.discount:.0%}  ·  Margin: {args.margin:.0%}")
        print()
        print(f"  Term  | Breakeven   | Max bid")
        print(f"  ------|-------------|-----------")
        for r in evals:
            print(f"   {r['term_years']}-yr | {fmt_money(r['breakeven_bid']):>11} | {fmt_money(r['max_bid']):>9}")
        return

    # Batch mode
    sub = df[df["position"] == args.position].nlargest(args.top, "projected_pts")
    out = []
    for _, row in sub.iterrows():
        for e in evaluate_player(row["market_y1"], args.discount, args.margin):
            out.append({
                "name": row["name"],
                "pos": row["position"],
                "projected_pts": round(row["projected_pts"], 1),
                **e,
            })
    out_df = pd.DataFrame(out)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv = OUT_DIR / f"max_bid_{args.position}_{args.year}.csv"
    out_df.to_csv(csv, index=False)

    pivot = out_df.pivot_table(index=["name", "pos", "projected_pts", "market_y1"],
                                columns="term_years", values="max_bid",
                                aggfunc="first").reset_index()
    pivot.columns.name = None
    pivot = pivot.rename(columns={1: "1y", 2: "2y", 3: "3y", 4: "4y", 5: "5y"})
    pivot = pivot.sort_values("market_y1", ascending=False)
    for c in ("market_y1", "1y", "2y", "3y", "4y", "5y"):
        if c in pivot.columns:
            pivot[c] = pivot[c].apply(lambda v: fmt_money(v) if pd.notna(v) else "")
    print()
    print(pivot.to_string(index=False))
    print(f"\nWrote {csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
