# Changelog

All meaningful changes to the analytics models. Format: date · commit · summary.

## 2026-05-03 · `721141a` · Foundation validation suite + PK/Def exclusion

Added `salary_efficiency/validate.py` with four sanity checks:
- (a) Year-over-year surplus persistence
- (b) 5-fold CV MAE: linear vs log-linear vs power-law
- (c) Raw points vs Points-Above-Replacement
- (d) Per-position reliability (k, R², salary CV)

Findings on 2021–2025 data:
- Persistence Pearson r 0.44–0.71 overall, per-position 0.08–0.83. Surplus carries real
  signal year-to-year. Foundation is safe for NPV / trade builds.
- Power-law wins CV MAE at every position. Linear was wrong; switch confirmed correct.
- PAR does not improve fit at any skill position. Stay with raw points. (Side note: this
  is unusual vs most fantasy literature — likely an exploitable inefficiency.)
- PK/Def k≈0.15 with low salary CV → commoditized. Excluded from production steals/overpays.

Added scipy to requirements (pandas spearman dependency).

## 2026-05-03 · `03662fd` · Power-law market fit on productive tail

Replaced linear `salary = a·points + b` fit with power-law `salary = c·points^k` via log-log
regression. Linear fit understated the elite tier and made every top player look like an
overpay.

Critical fix: fit only on top-N by points per position per year (2× tier size). Full
rostered population is dominated by min-salary bench depth, which flattens the curve to
nonsensical k<<1 exponents. Productive tail recovers k≈1 (slightly higher for WR/TE).

Steals filter: require ≥4 weeks with non-zero scoring. Drops injured/cut players who
falsely showed up at the top of the surplus list.

After this change Allen at $12M still flags as #1 overpay (correct — no QB has been paid
that much). McCaffrey at $9M / 333pts dropped off overpay list (model now prices elite RB
production correctly).

## 2026-05-03 · `43185e4` · Initial Python port

Added analytics in Python alongside the existing JS exporter:
- `lib/mfl.py`: Python port of MFL data layer with on-disk JSON cache and salary escalation
- `lib/league.py`: constants from the league constitution
- `salary_efficiency/`: per-season $/PPG + linear-fit market surplus report (replaced in next commit)
- `cap_health/`: per-team committed/remaining cap, top-3 concentration, contract-year
  distribution, expirations, risk flags
- `docs/DESIGN.md` + `README.md`: layout and modeling notes

Smoke-tested on 2025: 16 teams, 402 scoring players, expected results.

Existing JS Top 30 Salary exporter unchanged.
