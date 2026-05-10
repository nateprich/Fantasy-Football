# Design Notes

System overview + roadmap. For modeling depth, see [METHODOLOGY.md](METHODOLOGY.md).
For chronology, see [CHANGELOG.md](CHANGELOG.md). For future ideas, see [BACKLOG.md](BACKLOG.md).

## League constants (per constitution, May 2026)

- 16 teams, 4 divisions of 4
- $45M salary cap, league min $425K
- Active roster max 22 + 3 practice squad (rookies only, 50% cap hit) = **25 contract slots total**
- Contracts 1–5 years, 10% annual escalation on Feb 15
- Starting lineup: 1 QB, 1–4 RB, 1–4 WR, 1–4 TE, 1 PK, 1 Def (9 starters; 6 RB/WR/TE flex slots)
- Auction-style FA bidding (eBay format, 36-hr clock, $25K min increase)
- In-season BBID waivers Sun 10pm – Wed 7pm PT, then FCFS Wed 7pm – Sun 10am
- **Rookie extensions** (2026+ rookies only — see note): 2 extra years, top-5 positional avg formula
- **5th-year team option** (2026+ first-rounders only): one extra year, top-10 positional avg
- **Compensatory picks** (2026+ drafts only): 3rd-round pick when a drafted player signs elsewhere
- Veteran extensions sunset Feb 15, 2028
- Waiver penalties: 50% current-year + 0–45% next-year, scaling with years remaining

> The constitution's literal text on rookie extensions doesn't restrict to a draft year, but the
> commissioner has clarified the rule applies to 2026+ rookies only. Pre-2026 rookies on existing
> rosters are NOT extension-eligible. Documents in this repo treat 2026+ as the eligibility cutoff.

All constants live in [`../lib/league.py`](../lib/league.py).

## System architecture

```
                            MFL API (cached)
                            FantasyPros API (cached)
                                        |
                                        v
                     lib/  (clients, escalation math, league constants)
                                        |
       +--------------------------+----+----+--------------------------+
       v                          v         v                          v
  salary_efficiency/        draft_value/    cap_health/           aging/
  (market curve fit,        (pick value     (per-team             (population aging
   per-season surplus,       curve from      cap forecast,         curves +
   multi-year NPV,           2017-2024)      risk flags)           per-player
   validation suite)                                               risk score)
       |                          |                                   |
       +-------------+------------+-----------------------------------+
                     v
              trade_eval/                  auction_prep/
              (deal grading               (max bid, tier bands,
               in dollars)                 cap-stress index)

              competitiveness/             cutdown/
              (lineup snapshot            (cut-impact report,
               + validation)               live roster first)
```

**Data flow:** MFL endpoints (rosters, auction history, weekly results, draft results,
transactions) get cached as raw JSON to `.cache/<year>/`. FantasyPros projections + rankings
get cached to `.cache/fantasypros/`. Derived dataframes (season frames, market fits, NPV
tables) are recomputed each run from cache (~5-10 sec per CLI invocation). Snapshots of FP
rankings/projections are written daily to `data/fp_*/<date>/` and committed to git as a
longitudinal dataset.

## Module responsibilities

### `lib/`
- `league.py` — constitution constants
- `mfl.py` — MFL REST client + escalation math + on-disk cache
- `fantasypros.py` — FP REST client + cache
- `snapshot_fp_projections.py` / `snapshot_fp_rankings.py` — daily/weekly snapshot jobs
- `fp_rank_delta.py` — compare two snapshot dates

### `salary_efficiency/`
- `analyze.py` — per-season surplus report (steals/overpays). Power-law market curve fit.
- `npv.py` — multi-year asset value (NPV of remaining contract years). Reads age + risk flags.
- `validate.py` — foundation validation (persistence, CV, PAR, reliability)

### `draft_value/`
- `analyze.py` — realized NPV per draft slot from 2017–2024 picks. Outputs the pick value curve.

### `cap_health/`
- `analyze.py` — per-team cap, top-3 concentration, contract-year distribution, expirations.

### `aging/`
- `fit_curves.py` — per-position aging curves (performance × survival, peak-relative).
- `scoring.py` — shared module: `player_age()`, `aging_multiplier()`, `aging_risk()`,
  `trailing_ratio_from_history()`. Used by `salary_efficiency.npv`.

### `trade_eval/`
- `evaluate.py` — CLI grader. Combines player NPV with pick-value curve. Verdict bands.
- `pick_inventory.py` — league-wide pick inventory (2026 remaining + 2027) with values.

### `auction_prep/`
- `max_bid.py` — per-player max bid in three flavors: NPV-disciplined / cap-relative / market p75.
- `tier_bands.py` — production-tier salary p25/p50/p75 per position.
- `cap_stress.py` — pre-auction league cap forecast and inflation/deflation signal.

### `competitiveness/`
- `analyze.py` — projected legal-lineup snapshot. Reports rank/percentile/gap to playoff line;
  not a win-now/rebuild verdict.
- `backtest.py` — retrospective lineup-strength check against historical team scoring.
- `lineup.py` — shared legal lineup selection logic.

### `cutdown/`
- `analyze.py` — cut-impact report for roster-size decisions. Pulls live roster first, then overlays
  NPV/projections where available and flags missing projection data for scouting review.

## Key models in 30 seconds each

**Power-law market curve** (`salary = c * points^k` per position, log-log fit on the productive
tail). Replaces eyeball-the-top-30 with a per-player price tied to projected production.

**NPV** (sum of `(market − salary_t) / (1+r)^t` over remaining years). Cut option floors the
loss. Defaults to 20% discount rate. Optional aging multiplier on year-1+ market salary.

**Pick value curve** (smoothed historical realized NPV per overall slot). Picks 1-8 are net
negative (rookie tax > production); picks 9-26 are roughly fungible at $700K-$1M; picks 27-48
gradually decline. Trade eval uses median curve value with future-year discounting.

**Aging curves** (population-level performance × survival, peak-relative). Used as a *risk
flag* (LOW/MED/HIGH/EXTREME) by default. Optional NPV multiplier via `--with-aging`.

**Trade verdict bands** (|diff| <$500K = FAIR, <$2M = SLIGHT EDGE, <$5M = CLEAR WIN, $5M+ =
LOPSIDED). User decides; tool grades.

**Auction max bids** (three flavors: NPV-zero breakeven, cap-relative scaling by your league
percentile, market p75 from tier history). User picks based on context.

**Projected lineup snapshot** (best legal lineup from current projections). Useful for rank,
percentile, and gap-to-playoff-line context, but intentionally not a posture verdict.

**Cutdown impact** (one-player removal deltas). Measures starting-lineup loss, bench-depth loss,
and NPV/cut-floor context; missing projections are flagged rather than treated as zero real value.

## What's intentionally NOT modeled

- **Career arcs in year-1 projection.** FP already factors current age into next-year numbers.
  Aging curves handle years 2+ only.
- **External market benchmarks.** No KeepTradeCut scraping (TOS-dubious), no FantasyPros
  dynasty rank-to-dollar calibration (would need a calibration model). The market is the
  league's own history. Dynasty ECR is snapshotted daily for future longitudinal analysis.
- **ML auction price prediction.** Sample size too small (583 auctions over 5 years).
- **Real-time bid agent.** Auction is async over 36-hour windows; manual is fine.
- **Per-player matchup adjustments / weekly variance.** Not yet. The recommendation engine
  in BACKLOG would need this.
- **Win-now/rebuild verdicts.** The lineup snapshot is an input, not a decision model. Posture
  requires projections, depth, cap, NPV, trade market, schedule, injuries, and real W-L signal.

## Validation status (last run on 2017–2025)

- **Persistence (year-over-year surplus correlation):** Pearson r 0.44–0.71 overall, 0.08–0.83
  per position. Surplus is real signal.
- **Functional form (5-fold CV MAE):** power-law wins at every position over linear and
  log-linear.
- **Points vs. PAR:** raw points wins at every position. (Unusual vs. mainstream fantasy
  literature — likely an exploitable inefficiency in this league.)
- **PK/Def reliability:** k≈0.15, salary CV 0.5 → commoditized. Excluded from rankings.

See [`out/salary_efficiency/validation.md`](../out/salary_efficiency/validation.md) for the
full validation report.

## Roadmap

Built and operational:
- [x] Salary efficiency with validation
- [x] Multi-year NPV
- [x] Cap health
- [x] Draft pick value curve
- [x] Trade fairness evaluator
- [x] Pick inventory across the league
- [x] FantasyPros projections integration
- [x] Daily snapshot system + auto-commit to git
- [x] Aging curves + per-player risk flag in NPV
- [x] Auction prep (max bid, tier bands, cap stress)
- [x] Projected lineup snapshot + retrospective validation scaffold
- [x] Cutdown impact analyzer

In [BACKLOG.md](BACKLOG.md):
- Tag/extension calculator (high priority for Feb 2027 decisions)
- Recommendation engine (in-season add/drop/trade alerts)
- Aging curves wired into year-1 projection
- Roster construction view (cap_health × NPV cross-cut)
- Trade-deadline urgency adjustment
- Opponent-side trade simulator
- Historical sweep 2021–2025 (cosmetic)
- Compensatory pick handler in trade eval

## Notes on accuracy and limits

- The model UNDERSTATES 2026+ pick value — extension premium isn't priced in. Treat the
  pick curve as a conservative floor for 2026+ picks specifically.
- Linear / smoothed curves can't capture step functions at tier breaks. WR p75 in tier 1
  is $7.7M but tier 2 is $4.2M — a real cliff the smooth fit misses.
- Single-year NPV reports are 2026 snapshots. Don't optimize against them in isolation —
  the model operates on one year at a time but trades and contracts span multiple.
- PK/Def carry option value (insurance + trade bait) the model can't quantify. Their NPV
  near zero is fine; don't read it as "always cut to min depth."
- Competitiveness output is uncalibrated until it is backtested against archived preseason
  projections. The retrospective backtest is a sanity check, not proof of forecast accuracy.
- Aging curves are population averages. They UNDER-penalize elite/HOF-caliber players
  (the curves don't see selection bias on quality). The blended `--with-aging` mode and
  the risk flag both attempt to compensate, but the user should override when the model's
  signal is obviously wrong on a specific player.
