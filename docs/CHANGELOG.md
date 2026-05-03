# Changelog

All meaningful changes to the analytics models. Format: date · commit · summary.

## 2026-05-XX · trade fairness evaluator

Added `trade_eval/evaluate.py` — combines player NPV (from salary_efficiency.npv)
with the realized pick-value curve (from draft_value.analyze) to value any trade in
dollars.

Asset syntax:
- Player: any unambiguous substring of the name. Disambiguates by exact match first,
  then highest-salary match.
- Pick: `<year> <round>.<pick>` for a specific slot, or `<year> <round>` for a
  round-only valuation. Future-year picks are time-discounted at the same rate.

Picks are valued at the **median** historical realized NPV of that slot (not mean) to
limit single-outlier pull. Falls back to round-level median if the exact slot has no
history.

Sanity tests:
- Puka Nacua (3yr/\$574K) for Drake London (4yr/\$7.7M): -\$9.9M swing, "LOPSIDED."
  Correct — London is the worst contract in the league.
- Ja'Marr Chase (1yr/\$425K) for Puka Nacua (3yr/\$574K) + 2027 1.05: +\$273K swing,
  "FAIR." Correct — Chase's elite 1-yr expiring deal balances Puka's cheap 3-yr deal
  minus the low-EV early 1st.
- Amon-Ra St. Brown (1yr/\$425K) for Ja'Marr Chase (1yr/\$425K): -\$324K swing, "FAIR."
  Chase slightly ahead on trailing points; within the fair band.

Verdict bands:
- |diff| < \$500K: FAIR
- < \$2M: SLIGHT EDGE
- < \$5M: CLEAR WIN
- \$5M+: LOPSIDED

## 2026-05-XX · draft pick value model

Added `draft_value/analyze.py` — realized-NPV-per-pick model that walks forward from
each historical draft (2017–2024), tracks each picked player's contribution through
their first 4 years on a roster, and aggregates by overall slot and (round × position).

Method: rookie salary is the constitution's slotted value (e.g. $3.4M for 1.01 RB,
$575K for 2.01 QB). Realized NPV uses the same per-position power-law market curve
as the main efficiency model, discounted at 20%/yr.

**Headline findings (2017–2024 drafts):**
- **Top-4 picks have NEGATIVE average realized NPV.** The rookie salary slot ($3.4M RB,
  $3.5M WR, $3M QB) is so high that even hits like Saquon Barkley, Breece Hall, and
  Jonathan Taylor failed to clear it. Median 1.01 NPV = -$5.4M.
- **Sweet spot is picks 9–28.** Average NPV swings positive at pick 9 and stays there.
- **QB at 1.13–2.08 is the league's best draft slot:** averages +$1.5M to +$2.1M NPV.
- **RB at 1.01–1.04 is a -$3.9M average trap.** Every team that took an RB top-4 lost
  money in expected value, including the "hits."
- **Best historical contracts came from rounds 2–3, not 1.01–1.04:** Mahomes (2.01),
  Allen (2.09), Lamar (2.06), Hurts (2.18), Puka (3.15), Kamara (1.10).

Strategic implications: trade DOWN from picks 1–4 unless absolutely targeting a QB.
Picks 9–28 are mispriced trade currency.

Also: added `fetch_draft_results` to `lib/mfl.py` and stronger 429 backoff (30s base,
up to 8 retries, ~4 minute cap).

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
