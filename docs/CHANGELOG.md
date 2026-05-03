# Changelog

All meaningful changes to the analytics models. Format: date · commit · summary.

## 2026-05-03 · multi-year NPV surplus

Added `salary_efficiency/npv.py` — values each player's contract as the NPV of remaining
years of surplus, escalated 10%/yr per the constitution and discounted at a configurable
rate (default 20%, reflecting dynasty risk).

Key design choices:
- **Trailing 2-year average points** for projection. Single-year is too noisy (validation
  showed point variance is huge year-to-year).
- **Cut option floor.** Per the constitution, waiving costs 50% of current salary now
  plus a 0–45% next-year hit (scales with years remaining). Each player's contract value
  is `max(NPV of keeping, -cost of cutting)` — a rational owner cuts if keeping is worse.
- **No-data players default to NPV = 0.** Without trailing point history (rookies, or
  veterans coming off injury) the model can't claim steal or overpay, so surplus is zero.
  Avoids the bug where rookies with no production were treated as fairly priced at the
  position median market salary, then looked like top-of-league contracts.

Outputs added:
- Top N most valuable contracts (real producers on cheap multi-year deals)
- Top N worst contracts (with cut floor applied)
- Contracts the model says should be cut (NPV worse than cut cost)
- Best contract by position (top 5 each)
- `--by-team`: per-franchise roster asset value rollup

First run on 2025 data found:
- Bucky Irving, Jayden Daniels, Puka Nacua, Chase Brown as top assets — multi-year cheap
  deals with proven production.
- Drake London (4yr/$7.7M), Deshaun Watson (4yr/$6.6M), AJ Brown (4yr/$7.6M) as worst
  contracts — long-tail overpays. Each represents $20M+ of gross negative NPV before
  the cut floor.
- 30 contracts league-wide where keep value < cut cost. Most are $7M+ veterans with 1
  year remaining (Allen, Jacobs, McCaffrey, Adams, Higgins, etc).
- Defending champ Computer Jocks ranked last in roster asset value — common pattern
  after a championship run, where you've paid up to win and the bill comes due.

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
