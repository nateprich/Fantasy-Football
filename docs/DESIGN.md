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

## Foundation validation (2021–2025)

Run `python -m salary_efficiency.validate --years 2021 2022 2023 2024 2025` to regenerate.
Output at [`out/salary_efficiency/validation.md`](../out/salary_efficiency/validation.md).

Findings that justify building on this foundation:

- **(a) Persistence:** Per-player surplus correlates strongly across consecutive years —
  Pearson r typically 0.4–0.7 across all skill positions. 2023→2024 was the strongest
  (r = 0.71 overall, 0.83 at QB). 2024→2025 weakest at QB (r = 0.33) but still positive.
  Conclusion: surplus carries real signal year-to-year, not noise.
- **(b) Functional form:** Power-law beats linear and log-linear in 5-fold CV MAE for every
  scoring position. Confirms the switch from the original linear fit was correct.
- **(c) PAR vs raw points:** Points-Above-Replacement does not improve fit — raw points wins
  at every skill position. Salary-paid is more closely tied to absolute production than to
  scarcity premium in this league.
- **(d) PK/Def commoditized:** k = 0.14 / 0.16 with low salary CV (0.5) confirms kicker and
  defense salaries are essentially flat regardless of production. Excluded from steal/overpay
  rankings in production output.

Skill-position fits show low R² (0.06–0.16) — unsurprising given how much realized fantasy
points vary year over year (injuries, breakouts, busts). The persistence test (a) is the
better signal-vs-noise check, and it passed.

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
