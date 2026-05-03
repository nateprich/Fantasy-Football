# Design Notes

## League constants (from constitution, May 2026)

- 16 teams, 4 divisions
- $45M salary cap, league min $425K
- Active roster max 22 + 3 practice squad (rookies only, 50% cap hit)
- Contracts 1–5 years, 10% annual escalation on Feb 15
- Starting lineup: 1 QB, 1–4 RB, 1–4 WR, 1–4 TE, 1 PK, 1 Def (9 starters total; 6 flex slots across RB/WR/TE)
- Auction-style FA bidding (eBay format, $25K min increase)
- In-season BBID waivers Sun 10pm – Wed 7pm PT
- Veteran extensions, rookie extensions, 5th-year team option (1st rounders only, 2026+)
- Waiver penalties scale with years remaining (50% current + 15–45% future)

All of the above lives in [`../lib/league.py`](../lib/league.py).

## MFL contractYear semantics

`contractYear` from the MFL `rosters` endpoint = **years remaining**, not "which year of the contract".
The escalation math therefore walks backwards through historical auction + BBID data to find the
original winning bid, then applies `originalBid * 1.10^(target_year - acquisition_year)`. This is
how the existing JS exporter works and we preserve that behavior in [`../lib/mfl.py`](../lib/mfl.py).

## Salary efficiency model

Goal: identify steals (high points, low salary) and overpays (low points, high salary).

1. Build per-season dataset: (player, position, salary_escalated, season_points, weeks_with_score).
2. For each position, fit a **power law** `salary = c * points^k` via log-log linear regression
   on rows where `salary > league_min` (filters out min-salary noise). `k > 1` produces the
   convex elite premium that linear fits miss. Optionally pool multiple seasons for a more
   stable curve (`--years-back`).
3. Compute `surplus = market_salary - actual_salary`. Positive surplus = team is paying below market
   for the production they got.
4. Report: top steals/overpays overall and per position, plus tier $/PPG (top-12 QB/TE,
   top-24 RB/WR, top-16 PK/Def).

Limitations:
- Power-law fit is much better at the elite tier than linear, but still a smooth fit — true
  market may have step functions at tier breaks (top-12 QB, top-24 RB, etc).
- "Market" is the league's own pricing — can be biased by collusion / soft markets. Pooling seasons
  helps. Outside benchmarks (FantasyPros auction values) could be added later.
- Doesn't account for contract length. A cheap multi-year deal is more valuable than a cheap
  expiring one. Future: add a multi-year surplus metric using NPV.

## Cap health model

Goal: spot forced-sale candidates and roster-construction problems.

For each team:
- Committed / remaining cap, remaining as % of cap.
- Top-3 player share of cap (concentration risk).
- Contract-year distribution: expiring (1), Y2, Y3+. Avg years remaining.
- Risk flags: `OVER_CAP`, `CAP_STRESS` (<$1M), `TOP_HEAVY` (top 3 ≥ 50%), `THIN_ROSTER` (<20),
  `EXPIRATION_CLIFF` (≥8 expiring).

Plus a league-wide position market summary (rostered top-10 / top-30 average salary by position).

## Roadmap (not built yet)

- Trade fairness evaluator (NPV of multi-year surplus on both sides).
- Rookie-pick value chart calibrated on 2017–25 hit rate (which picks turned into top-30-salary players).
- Auction inflation tracker year over year.
- Tag/extension value calculator (matching constitution formulas).
