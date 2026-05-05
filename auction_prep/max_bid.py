"""Per-player max-bid calculator.

Computes three flavors of max bid for each contract length 1–5 years:

  1. NPV-disciplined: the salary at which the contract NPV is exactly zero
     at the configured discount rate. This is the conservative ceiling.
  2. Market-anchored (tier p75): the 75th-percentile auction salary at the
     player's projected production tier over the last 5 years. This is the
     price required to actually win the auction.
  3. Cap-relative: NPV-disciplined max scaled by your cap-room percentile vs.
     the league. If you're cap-flush you can rationally pay above NPV-zero
     because you have nowhere better to spend the cap.

User picks bid based on context: NPV-disciplined for "find a deal", market-
anchored for "I need this player and will pay market", cap-relative for
"I have surplus cap and want to use it."

Math (NPV-disciplined):
    NPV(s, n, r) = sum_{t=0..n-1} (M − s · 1.10^t) / (1+r)^t
    s_breakeven = (N·M) / Σ (1.10^t / (1+r)^t),  N = Σ 1/(1+r)^t

Run:
    python -m auction_prep.max_bid --player "Puka Nacua"
    python -m auction_prep.max_bid --position WR --top 30
    python -m auction_prep.max_bid --player "Puka" --my-team "Midwestside"
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from lib import mfl
from lib.fantasypros import projected_points_by_mflid
from lib.league import ANNUAL_ESCALATION, LEAGUE_MIN_SALARY, SALARY_CAP, SCORING_POSITIONS
from salary_efficiency.analyze import build_season_dataframe, fit_position_market
from salary_efficiency.npv import predict_market
from auction_prep.tier_bands import assign_tier

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "auction_prep"


def breakeven_salary(market: float, years: int, discount: float) -> float:
    """Solve for the year-0 salary that makes NPV exactly zero."""
    if market <= LEAGUE_MIN_SALARY or years <= 0:
        return float(LEAGUE_MIN_SALARY)
    n_factor = sum(1 / ((1 + discount) ** t) for t in range(years))
    s_factor = sum(((1 + ANNUAL_ESCALATION) ** t) / ((1 + discount) ** t) for t in range(years))
    return (n_factor * market) / s_factor


def fmt_money(v: float) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"${int(v):,}"


def build_tier_lookup(market_frames: list[pd.DataFrame], position: str) -> pd.DataFrame:
    """Return p25/p50/p75 salary by tier for the given position, pooled across frames."""
    pooled = pd.concat([f[f["position"] == position] for f in market_frames], ignore_index=True)
    pooled = pooled[pooled["salary"] >= LEAGUE_MIN_SALARY].copy()
    pooled["tier"] = pooled["points"].apply(lambda p: assign_tier(p, position))
    return (
        pooled.groupby("tier")
        .agg(n=("salary", "count"),
             p25=("salary", lambda s: int(np.percentile(s, 25))),
             p50=("salary", lambda s: int(np.percentile(s, 50))),
             p75=("salary", lambda s: int(np.percentile(s, 75))))
        .reset_index()
    )


def cap_relative_factor(my_team: str, source_year: int) -> tuple[float, dict]:
    """Compute a multiplicative factor based on cap room vs. league distribution.

    Returns (factor, info_dict).
    Factor:
      median room      → 1.00x
      75th percentile  → 1.10x
      90th+ percentile → 1.20x
      <25th percentile → 0.95x  (don't overspend when stressed)
    """
    franchises = mfl.fetch_franchises(source_year)
    rosters = mfl.fetch_rosters(source_year, week=1)
    rooms = []
    my_room = None
    for fid, players in rosters.items():
        committed_next = sum(p["salary"] * (1 + ANNUAL_ESCALATION)
                             for p in players if int(p.get("contract_year") or 0) > 1)
        room = SALARY_CAP - committed_next
        rooms.append(room)
        if my_team and my_team.lower() in (franchises.get(fid, {}).get("name", "") or "").lower():
            my_room = room

    if my_room is None or not rooms:
        return 1.0, {"available": False}

    pct = (sum(1 for r in rooms if r <= my_room) - 1) / max(len(rooms) - 1, 1) * 100
    if pct >= 90:
        factor = 1.20
    elif pct >= 75:
        factor = 1.10
    elif pct >= 25:
        factor = 1.00
    else:
        factor = 0.95
    return factor, {
        "available": True,
        "my_room": my_room,
        "median_room": float(np.median(rooms)),
        "percentile": pct,
        "factor": factor,
    }


def evaluate_player(market: float, discount: float, margin: float,
                    cap_factor: float, tier_p75: float | None) -> list[dict]:
    rows = []
    for years in range(1, 6):
        be = breakeven_salary(market, years, discount)
        max_npv = max(LEAGUE_MIN_SALARY, be * (1 - margin))
        max_cap_rel = max(LEAGUE_MIN_SALARY, be * cap_factor)
        # Market-anchored: p75 only meaningful for 1-2yr; multi-year overpays compound badly
        market_anchor = tier_p75 if (tier_p75 and years <= 2) else None
        rows.append({
            "term_years": years,
            "market_y1": int(market),
            "max_npv": int(max_npv),
            "max_cap_rel": int(max_cap_rel),
            "market_p75": int(market_anchor) if market_anchor else None,
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
                   help="Seasons to pool for market fit + tier bands (default 5)")
    p.add_argument("--scoring", default="points_ppr",
                   choices=["points", "points_ppr", "points_half"])
    p.add_argument("--my-team", help="Your franchise name (substring) for cap-relative scaling")
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

    # Cap-relative factor
    cap_factor, cap_info = (1.0, {"available": False})
    if args.my_team:
        cap_factor, cap_info = cap_relative_factor(args.my_team, args.year)

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
            "tier": assign_tier(pts, meta["position"]),
            "market_y1": int(market),
        })
    df = pd.DataFrame(rows)

    # Tier bands per position
    tier_bands_by_pos = {pos: build_tier_lookup(market_frames, pos) for pos in SCORING_POSITIONS}

    def tier_p75(pos: str, tier: str) -> float | None:
        sub = tier_bands_by_pos.get(pos, pd.DataFrame())
        if sub.empty:
            return None
        match = sub[sub["tier"] == tier]
        if match.empty:
            return None
        return float(match.iloc[0]["p75"])

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
        p75 = tier_p75(row["position"], row["tier"])
        evals = evaluate_player(row["market_y1"], args.discount, args.margin, cap_factor, p75)
        print()
        print(f"{row['name']} ({row['position']}, {row['tier']})")
        print(f"  Projected: {row['projected_pts']:.1f} pts (FP)")
        print(f"  Modeled market salary (Y1): {fmt_money(row['market_y1'])}")
        print(f"  {row['tier']} {row['position']} auction history (last {args.years_back}y, p25/p50/p75): "
              f"{_tier_summary(tier_bands_by_pos[row['position']], row['tier'])}")
        if cap_info.get("available"):
            print(f"  Cap context: your room ${int(cap_info['my_room']):,}, "
                  f"league median ${int(cap_info['median_room']):,}, "
                  f"you are {cap_info['percentile']:.0f}th percentile → "
                  f"factor {cap_info['factor']:.2f}x")
        else:
            print(f"  Cap context: pass --my-team to enable cap-relative bidding")
        print(f"  Discount: {args.discount:.0%} · Margin: {args.margin:.0%}")
        print()
        print(f"  Term  | NPV-disc.   | Cap-relative | Market p75 (T1-2y)")
        print(f"  ------|-------------|--------------|-------------------")
        for r in evals:
            mp = fmt_money(r["market_p75"]) if r["market_p75"] else "—"
            print(f"   {r['term_years']}-yr | {fmt_money(r['max_npv']):>11} | "
                  f"{fmt_money(r['max_cap_rel']):>12} | {mp:>17}")
        print()
        print("  How to read:")
        print("    NPV-disc.    : breakeven if you want NPV ≥ 0")
        print("    Cap-relative : same, scaled by your cap percentile in the league")
        print("    Market p75   : what teams ACTUALLY pay at this tier (last 5y).")
        print("                   Bid here if you need this player, accept negative NPV.")
        print("                   Only shown for 1-2 yr terms (multi-year overpays compound).")
        return

    # Batch mode
    sub = df[df["position"] == args.position].nlargest(args.top, "projected_pts")
    out = []
    for _, row in sub.iterrows():
        p75 = tier_p75(row["position"], row["tier"])
        for e in evaluate_player(row["market_y1"], args.discount, args.margin, cap_factor, p75):
            out.append({
                "name": row["name"],
                "pos": row["position"],
                "tier": row["tier"],
                "projected_pts": round(row["projected_pts"], 1),
                **e,
            })
    out_df = pd.DataFrame(out)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv = OUT_DIR / f"max_bid_{args.position}_{args.year}.csv"
    out_df.to_csv(csv, index=False)
    print(f"\nWrote {csv}", file=sys.stderr)

    pivot_npv = out_df.pivot_table(index=["name", "pos", "tier", "projected_pts", "market_y1"],
                                    columns="term_years", values="max_npv",
                                    aggfunc="first").reset_index()
    pivot_npv.columns.name = None
    pivot_npv = pivot_npv.rename(columns={1: "1y_npv", 2: "2y_npv", 3: "3y_npv", 4: "4y_npv", 5: "5y_npv"})

    # Add 1y / 2y market p75 + cap-relative
    one_yr = out_df[out_df["term_years"] == 1].set_index("name")[["max_cap_rel", "market_p75"]].rename(
        columns={"max_cap_rel": "1y_cap_rel", "market_p75": "1y_mkt_p75"})
    pivot = pivot_npv.merge(one_yr, on="name", how="left")
    pivot = pivot.sort_values("market_y1", ascending=False)
    for c in ("market_y1", "1y_npv", "2y_npv", "3y_npv", "4y_npv", "5y_npv", "1y_cap_rel", "1y_mkt_p75"):
        if c in pivot.columns:
            pivot[c] = pivot[c].apply(lambda v: fmt_money(v) if pd.notna(v) else "")
    print()
    print(pivot.to_string(index=False))


def _tier_summary(tier_df: pd.DataFrame, tier: str) -> str:
    match = tier_df[tier_df["tier"] == tier]
    if match.empty:
        return "(no data)"
    r = match.iloc[0]
    return f"n={int(r['n'])}  ${int(r['p25']):,} / ${int(r['p50']):,} / ${int(r['p75']):,}"


if __name__ == "__main__":
    main()
