# Changelog

All meaningful changes to the analytics models. Format: date · commit · summary.

## 2026-05-09 · competitiveness demoted to snapshot + cutdown separated

The initial `competitiveness/` analyzer overreached by labeling teams as
contender/rebuilder from one projected-points table. That was too strong for an
uncalibrated model.

Changes:
- `competitiveness.analyze` now outputs a projected legal-lineup snapshot: rank,
  percentile, gap to the projected playoff line, and bench skill depth.
- Contender/rebuilder labels were removed from default output. Optional quartile
  bands are explicitly marked experimental and neutral.
- Legal lineup selection now enforces the constitution's minimum 1 RB / 1 WR / 1 TE
  requirement instead of simply taking the top 6 RB/WR/TE.
- Added `competitiveness.backtest` as a retrospective lineup-strength sanity check.
  It is not a true forecast backtest unless run against archived preseason projections.
- Added `cutdown.analyze` as a separate roster-slot tool using live MFL roster data
  first and NPV/projections as overlays.
- Fixed FantasyPros rookie rankings snapshots to use `type=rookies` (plural), the
  actual rookie-only endpoint.

## 2026-05-04 · methodology clarification — rookie extension eligibility

Reverted a brief speculative re-read of the constitution. The rookie extension rule
applies to **2026+ rookies only**, per commissioner clarification, even though the
constitution's literal text doesn't restrict to a draft year. The 5th-year option
and compensatory pick rules are explicitly 2026+ only and were already correctly
documented.

Implication: pre-2026 rookies on existing rosters (Drake Maye, Bucky Irving,
Puka Nacua, Harold Fannin, etc.) are NOT extension-eligible. Their multi-year
contract value caps at the historical curve, no extension premium applies.

## 2026-05-04 · aging curves wired into NPV (risk flag default + opt-in multiplier)

`aging/fit_curves.py` produces population-level aging curves: per-(position, age)
performance × survival, peak-relative. n=1849 player-seasons across 2017-2025.

`aging/scoring.py` exposes shared helpers: `player_age()`, `aging_multiplier()`
(blended with elite gating), `aging_risk()` (composite score + LOW/MED/HIGH/EXTREME
flag), `trailing_ratio_from_history()`.

Integration into `salary_efficiency.npv`:
- Always: adds `age`, `trailing_3y_ratio`, `aging_risk_flag` columns to the report.
- New `--with-aging` flag: applies blended aging multiplier to year-1+ projected
  market salary. Year 0 unscaled (FP projection already accounts for current age).

Risk score formula: `position_age_risk × (0.5 + contract_yrs/5 × 0.5) ×
(1 - elite_protection × 0.3)`. Position thresholds: RB cliff at 28, WR at 30,
TE at 31, QB at 35.

Headline aging findings (2017-2025):
- All 4 positions peak in **expected** value at age 24 (perf × survival).
- RB falls off a cliff: 100% expected at 24 → 76% at 26 → 50% at 27 → 33% at 28.
- WR is durable: 96-100% through 25, 78% at 26, 75% at 27.
- TE noisiest. QB has elite-survivor effect (Brady, Rodgers, etc.).

Caveats:
- Curves are survivorship-conditional; they UNDER-penalize elite/HOF players.
- Risk flag uses position-aware thresholds + trailing-3y elite gating to
  partially compensate. The combination flags Aaron Jones / Kareem Hunt /
  Keenan Allen / Henry / Mike Evans / Kupp / Hill correctly while leaving
  Allen / Lamar / Bijan / Puka / Drake Maye as LOW.

## 2026-05-04 · auction_prep package (max_bid, tier_bands, cap_stress)

Three modules powering free-agent auction decisions:

**`max_bid.py`** — per-player max bid in 3 flavors per contract length:
1. NPV-disciplined (breakeven at NPV=0, conservative)
2. Cap-relative (scaled by user's cap-room percentile vs. league)
3. Market p75 (75th-percentile auction price at the player's projected tier)

The three numbers let the user decide based on context: deal-hunting, surplus-cap
deployment, or must-win-this-auction.

**`tier_bands.py`** — pooled p25/p50/p75 salary by production tier per position.
Replaces eyeball-the-top-30 with proper percentile bands.

**`cap_stress.py`** — projects each franchise's committed cap into next year's
auction. Aggregates into flush/balanced/stressed buckets and emits a market
inflation/deflation signal.

First 2027 cap-stress run: 8 teams projected cap-flush → INFLATIONARY market
expected. Pacific Pigskins projects over cap (forced seller). MCM at $23.8M
projected room (3rd most in league).

## 2026-05-03 · pick inventory tool (`trade_eval/pick_inventory.py`)

Pulls 2026 remaining picks (from MFL `draftResults`) + 2027 future picks (from
`futureDraftPicks`), values each at the curve, outputs per-franchise summary
ranked by total pick asset value. Saved to `out/trade_eval/pick_inventory.csv`.

Useful for pre-trade reconnaissance: who's picks-rich, who's picks-poor.

## 2026-05-XX · current-season offseason data fetch tolerance

`salary_efficiency.analyze.build_season_dataframe` and `mfl.fetch_season_points`
now gracefully handle missing-data errors for current/future seasons. MFL returns
"Invalid week" or HTTP 503/404 for week-14 fetches mid-offseason. Previously this
cascaded into entire-year skips, which silently halved the years-back window.

Now the fetch falls back to W1-only roster + zero realized points for the
current season. The market fit pools whatever data is available.

Default `--years-back` bumped from 3 to 5 in `auction_prep.max_bid` for more
stable fits.

## 2026-05-XX · daily projections snapshot + cron consolidation

Added `lib/snapshot_fp_projections.py` and replaced the weekly launchd agent
with a daily one (`scripts/daily-snapshot.sh`).

**Behavior matrix:**
| When | What runs |
| --- | --- |
| Sun 7am, year-round | rankings (dynasty, redraft, rookie) |
| Mon-Sat in-season (Sept-Jan) | weekly projections |
| Sun in-season (Sept-Jan) | weekly projections + rankings |
| Mon-Sat off-season (Feb-Aug) | nothing |
| Sun off-season (Feb-Aug) | season-total projections + rankings |

Rationale:
- In-season projections shift daily because of news, injuries, and the
  Thursday/Saturday/Sunday/Monday game cadence. Daily snapshots capture
  the full pre-game/post-game arc.
- Off-season projections rarely change. Weekly is sufficient.
- Rankings are weekly regardless — expert consensus moves slowly.

Storage budget: ~600KB/day during the season (×~150 in-season days = ~90MB
across the season). Manageable; everything commits to git.

This dataset is the foundation for a future add/drop/trade recommendation
engine. Day-over-day projection deltas + ECR drift + waiver-wire context
are exactly the signals it needs.

## 2026-05-XX · weekly FP rankings snapshot

Added `lib/snapshot_fp_rankings.py` and `lib/fp_rank_delta.py`. Snapshots
get committed to git so the longitudinal dataset survives disk failures.

10 ranking types saved per snapshot:
- dynasty (overall + per-position QB/RB/WR/TE)
- redraft (overall)
- rookie (per-position QB/RB/WR/TE; the "ALL" position is rejected for the
  rookie type)

Scheduled via launchd: `com.nateprich.fantasy.snapshot` runs Sundays at
8am PT. Wrapper script auto-commits and pushes after each successful pull.
First baseline snapshot taken 2026-05-03 (mid-rookie-draft) — exactly the
right moment to anchor the time series.

The intent is to use this longitudinal data later to:
- See how dynasty ECR drifts over the offseason and through key events
- Test whether ranks at draft time predict end-of-season production
- Measure how fast experts react to news vs lag it
- Decide if/when to incorporate ECR into the NPV model as an
  age/upside-adjustment factor

## 2026-05-XX · FantasyPros projections integration

Added `lib/fantasypros.py` — thin client for the FantasyPros public API
(free non-commercial tier). Endpoint: `/public/v2/json/nfl/<season>/projections`.
On-disk JSON cache at `.cache/fantasypros/`, key in `.env` as
`FANTASYPROS_API_KEY`.

The API conveniently returns `mflid` on each player record, so we join directly
to MFL data without any name-matching gymnastics.

Wired into both forward-looking models:
- `salary_efficiency/npv.py`: FP season projection now drives projected_pts when
  available; falls back to trailing-2yr avg per player. New columns
  `projected_pts` and `projection_source` (FP vs trailing-Ny). Flags: `--no-fp`,
  `--scoring {points,points_ppr,points_half}`.
- `trade_eval/evaluate.py`: same flags; player basis now shows projection source.

Historical analyzers (`analyze.py`, `validate.py`, `draft_value/`) are unchanged
\u2014 those measure realized outcomes, not forward projections.

First run: 356/402 active rostered players matched FP projections for 2025.
Top NPV jumped meaningfully for FP-bullish breakout candidates (e.g. Bucky Irving
\$5.2M \u2192 \$7.6M because FP projects 255 pts vs 191 trailing). Stable
veterans largely unchanged. Drake London still flagged as cut-worthy even with
bullish FP \u2014 \$7.7M \u00d7 4yr commitment too painful.

Failure modes handled: API down, missing key, mflid missing for a player. All
fall back gracefully to trailing-avg, model still produces output.

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
