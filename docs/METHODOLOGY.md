# Methodology — Salary Efficiency Foundation

This document captures the **why** behind every modeling decision in the salary efficiency
analysis. It exists so future-Nate (or future-Proxy) can pick this up six months from now and
understand not just what the code does, but why it does it that way and what was rejected.

Companion files:
- [`DESIGN.md`](DESIGN.md) — high-level design + roadmap.
- [`../out/salary_efficiency/validation.md`](../out/salary_efficiency/validation.md) — most recent validation run output.
- [`CHANGELOG.md`](CHANGELOG.md) — chronological record of model changes.

---

## 1. The problem

The League is a 16-team dynasty/salary-cap league with auction free agency, multi-year
contracts (1–5 years), and 10% annual salary escalation. The fundamental analytical question:

> **Which contracts on which rosters are giving teams the most production per dollar of cap?**

Answering this lets us:
- Identify trade targets (other teams' overpriced contracts you can pry away cheap).
- Identify your own steals (don't trade them away).
- Price tag/extension decisions correctly.
- Eventually evaluate trade fairness in dollars, not vibes.

## 2. Data sources

All data comes from MyFantasyLeague's REST API (league #13522). The Python client lives in
[`../lib/mfl.py`](../lib/mfl.py) and caches all responses to `.cache/<year>/<endpoint>.json`
to avoid hammering the API on re-runs.

| Endpoint | What it gives | Used for |
|---|---|---|
| `players?DETAILS=1` | id → name, position, NFL team | Player metadata, position normalization |
| `league` | franchise list, divisions | Team names |
| `rosters?W=N` | franchise → players with salary, contractYear | Salary snapshot, **contractYear = years remaining** |
| `auctionResults` | offseason auction winning bids | Original acquisition bid (escalation root) |
| `transactions?TRANS_TYPE=BBID_WAIVER` | in-season blind-bid claims | Original acquisition bid for waiver pickups |
| `weeklyResults?W=N` | per-player fantasy points that week | Season-total production |

### Critical MFL gotcha: `contractYear` semantics

The MFL roster endpoint returns a field called `contractYear`. **It is years remaining, not
"which year of the contract".** It counts down annually on Feb 15. The original JS exporter
documented this and the Python port preserves the same behavior.

### Salary escalation logic

Per the constitution, every player under contract receives a 10% raise on Feb 15. So a
player auctioned in 2023 for $5M is on the 2025 roster at $5M × 1.1² = $6.05M.

We don't trust the MFL `salary` field on the rosters endpoint to reflect this reliably —
the JS exporter's comments warn about it explicitly. Instead the Python client walks
backwards through historical auction + BBID data to find the **original** winning bid,
then applies escalation:

```
escalated_salary = original_bid × (1.10)^(target_year - acquisition_year)
```

We look back up to `MAX_CONTRACT_YEARS - 1` (4 years) since contracts max out at 5 years.
If we can't find an original bid, we fall back to the MFL roster `salary` field.

This logic lives in `HistoricalBids.escalated_salary()` in [`../lib/mfl.py`](../lib/mfl.py).

## 3. The salary-efficiency model

### Goal

For each (player, season), compute a **surplus value**:

```
surplus = market_salary − actual_salary
```

Positive surplus = team paid below market for the production they got. Negative = overpay.

### Modeling `market_salary`

This is the hard part. We need to know what salary the market would have paid for a given
level of production at a given position. We don't have an external benchmark — the league's
own auction history is the only signal. So we fit a market curve from realized salary vs
realized points.

#### What we tried and rejected

| Approach | Why rejected |
|---|---|
| **Linear fit** `salary = a·points + b` | Understates the elite tier. Salaries grow super-linearly with production at the top of the curve (the league pays a premium for studs). Linear fit made every elite player look like an overpay. CV MAE confirmed power-law beats linear at every position. |
| **Log-linear** `salary = a·log(points) + b` | Concave. Same problem as linear at the elite tier — flattens the top. CV MAE worse than power-law at every position. |
| **Fit on full rostered population** | Roster is dominated by minimum-salary bench depth. ~70% of rostered players are at or near $425K. Fitting `salary ~ points` on this set gives `k < 0.5` (basically flat) and absurd predictions. The "market" we want to model is what teams pay for **starters**, not depth. |
| **Points Above Replacement (PAR)** instead of raw points | Tested at every skill position. Power-law on raw points beats power-law on PAR every time. **Interesting finding:** this league prices absolute production, not scarcity premium. (Most fantasy literature recommends VORP/PAR; in this league, raw points wins.) Could be a tradeable inefficiency — see Roadmap. |

#### What we use

**Power law per position, fit on the productive tail:**

```python
salary = c × points^k     # fit via log-log linear regression
```

- One fit per position (QB, RB, WR, TE, PK, Def).
- Productive tail = top 2× tier-size by points per position per year (e.g. top 24 QBs/year,
  top 48 RBs/year, top 48 WRs/year, top 24 TEs/year). This filters out min-salary depth
  noise without arbitrary salary thresholds.
- Multi-year pooling option (`--years-back N`) stabilizes the fit by combining multiple
  seasons. Each season's productive tail is computed independently then concatenated.
- Floor at league minimum: `predicted_salary = max(LEAGUE_MIN_SALARY, c × points^k)`.

The fit is implemented in `salary_efficiency.analyze.fit_position_market`.

#### Steal/overpay filtering

In production output:
- **Steals** require ≥4 weeks with non-zero scoring. Otherwise injured/cut players show up
  with min salary and zero points, falsely flagged as steals.
- **PK/Def excluded** entirely from steal/overpay rankings. Validation showed kickers and
  defenses are fully commoditized in this league (`k ≈ 0.15`, low salary CV) — there is no
  meaningful price/production relationship to mine, just noise.

## 4. Foundation validation

Before building NPV / trade evaluator on top, we validated the foundation with four checks
in [`../salary_efficiency/validate.py`](../salary_efficiency/validate.py).

### (a) Year-over-year persistence — the most important check

If surplus values don't carry across years, the metric is mostly luck and any downstream
analytics built on it are noise.

**Method:** for each consecutive year pair, take all players with ≥50 points in both years,
correlate their surplus values via Pearson and Spearman.

**Result (2021–2025):** persistence is consistently positive across all skill positions.
| Year pair | Overall Pearson r |
|---|---|
| 2021→22 | 0.48 |
| 2022→23 | 0.68 |
| 2023→24 | **0.71** |
| 2024→25 | 0.44 |

Per-position values range from 0.08 (RB 2021→22, weak year) to 0.83 (QB 2023→24).

**Conclusion:** surplus is real signal, not noise. Foundation is safe to build on.

**Caveat — survivorship bias:** the persistence check is measured only on players who
played significant snaps in both years. Players cut after one bad year vanish from the
analysis. So we may be overestimating how robust surplus is for "stars" while not
measuring it for "busts." The bias direction makes our trade evaluator more conservative
when valuing multi-year contracts, which is the right error to make.

### (b) Functional form via cross-validation

5-fold CV MAE on each (position, functional form) — held-out prediction error.

**Result:** power-law wins on every position by a comfortable margin (~5–15% lower MAE
than linear or log-linear). Switch to power-law was the right call.

### (c) Raw points vs Points Above Replacement (PAR)

Same CV but with `x = points` vs `x = points − replacement_level`.

**Replacement levels used:** QB 16, RB 40, WR 48, TE 16 (matches typical fantasy depth
charts for a 16-team league with starting requirements 1 QB, 1–4 RB, 1–4 WR, 1–4 TE).

**Result:** raw points wins at every skill position. Don't switch.

**This is interesting.** Standard fantasy analysis says VORP/PAR should win because
positional scarcity matters. In this league, it doesn't — owners pay for absolute production.
That's likely an exploitable inefficiency: high-PAR players (elite RBs, elite TEs) are
probably being underpriced relative to their roster impact. Worth following up.

### (d) Per-position reliability

`k`, R², salary CV per position on the productive-tail fit.

**Result:**
- QB/RB/WR/TE: `k ≈ 0.45–1.05`, low R² (0.06–0.16). Low R² is **not** a death knell — it
  reflects huge year-over-year point variance (injuries, breakouts, busts). The persistence
  test (a) is the better signal/noise check, and it passed.
- PK/Def: `k ≈ 0.14–0.16`, salary CV ≈ 0.5. Truly commoditized. Excluded from production
  surplus rankings.

## 5. Known limitations

These are documented because they will matter when we extend to NPV / trades / etc.

1. **Single fit, not piecewise.** Real auction markets probably have step functions at tier
   breaks (top-12 QB premium, top-24 RB cliff, etc). Power-law smooths through these.
   Could swap to isotonic regression or quantile mapping later if accuracy at tier
   transitions becomes important.

2. **Circular market.** The "market" we fit is the league's own pricing. If the league
   collectively overpays at QB, the curve absorbs that bias and individual QB overpays look
   reasonable relative to it. Mitigation: pool multiple seasons. Better fix later: import
   FantasyPros / KeepTradeCut auction values as an external benchmark.

3. **Realized vs expected points.** We use realized fantasy points. A WR who busted is
   captured at high salary + low points, dragging the curve down. We're conflating fair
   price with average outcome including busts. A future improvement is to fit on
   preseason projections (or trailing 2-year average) instead of single-year realized.

4. **No injury/risk adjustment.** Player A and Player B both score 200 pts. Player A played
   17 games at 12 ppg. Player B played 11 games at 18 ppg. They look identical to the model.
   Could weight by `weeks_with_score` later.

5. **Survivorship bias in (a).** Already discussed. Leans the persistence estimate optimistic.

6. **Contract length not yet priced.** Single-year surplus says nothing about whether a
   $500K player has 1 year remaining or 4. The NPV extension (next project) is exactly
   designed to fix this.

## 5b. Multi-year NPV (the contract-value model)

Single-year surplus tells you how good a contract was *this year*. NPV tells you how
valuable a contract is *as an asset*. A cheap 1-year deal that produced $2M of surplus
is worth far less than a cheap 4-year deal at the same surplus, because the cheap 4-year
deal keeps producing.

### Formula

```
NPV = Σ_{t=0..n-1}  surplus_t / (1 + r)^t
where:
  n          = years remaining on contract
  surplus_t  = projected_market_salary − actual_salary_t
  actual_salary_t = current_salary × 1.10^t      (constitution: 10% annual escalation)
  r          = discount rate (default 20%)
```

### Design choices

- **Projection: trailing 2-year average points.** Validation showed single-year point
  variance is huge (R² 0.06–0.16 on the salary-vs-points fit). Pooling stabilizes the
  projection. Falls back to 1 year of data if 2 aren't available; falls back to current
  year if no prior data.

- **Discount rate 20% default.** Higher than typical corporate finance because dynasty
  fantasy has high carrying risk: injury, bust, retirement, scoring rule changes. Adjust
  via `--discount`. At 0% you're claiming future surplus is as certain as today's; at
  35%+ you're claiming a multi-year deal is barely worth more than 1 year.

- **Held market salary constant across the projection window.** A player's projected
  market salary uses their trailing-2-year points and doesn't grow over time. Real
  career trajectories aren't flat (RBs decline fast, QBs peak late) but a flat
  assumption is simpler and avoids over-fitting.

- **Cut option as a floor.** Per the constitution, waiving a player costs 50% of
  current-year salary plus a 0–45% next-year penalty depending on years remaining
  (1yr=0%, 2yr=15%, 3yr=25%, 4yr=35%, 5yr=45%). A rational owner will cut if `NPV(keep)
  < -cost(cut)`. Player asset value = `max(NPV_keep, -cut_cost)`. Without this floor,
  bad multi-year contracts go arbitrarily negative; with it, the worst case is bounded
  by what cutting would actually cost.

- **No-data players have NPV = 0.** Without trailing point history (rookies, veterans
  off IR, post-trade no-show) we can't honestly claim a player is a steal or overpay.
  Set surplus to 0 by definition. This avoids the failure mode where rookies with no
  production get assigned the position median market salary, producing fake huge NPVs.

### Known limitations of the NPV model

1. **Static market salary.** Doesn't model career arcs. An aging RB at 25 with 200 pts
   today gets the same projected production at 28. Reality: he's likely worth less.

2. **Static market curve.** The fitted `c × points^k` is the average of the last 3
   seasons — assumes the market doesn't drift. If the league inflates QB pricing in 2026
   (which is a real possibility), our 2025-fit model will under-price 2026 QBs.

3. **Cut decision is one-shot.** The model doesn't account for "cut next year for less
   penalty" optionality. A 4-year deal could be cut after year 1 for a smaller penalty
   than now. Could improve with a recursive optimal-cut policy, but probably overkill.

4. **No projection uncertainty.** Two players with the same trailing average get the
  same projection regardless of how volatile their week-to-week scoring was. Could
  add a confidence band based on weekly variance.

### Projection source: FantasyPros (when available)

When `FANTASYPROS_API_KEY` is set in `.env`, NPV and trade_eval pull the FantasyPros
expert-consensus season projection for the target year and use that as
`projected_pts` instead of trailing-2yr average. The FP API returns `mflid` per
player, so the join is direct and reliable.

Behavior:
- FP projection wins when available; per-player fallback to trailing-avg.
- `--no-fp` flag forces trailing-avg only.
- `--scoring {points, points_ppr, points_half}` selects the scoring system to pull
  (default: full PPR).
- API outage or missing key → graceful fallback, no crash.

This is a strict improvement on year-1 projection; year-2+ in NPV still assumes flat
production through the contract (FP doesn't publish multi-year projections).
Historical analyzers (`analyze.py`, `validate.py`, `draft_value/`) are intentionally
unaffected — they measure what *did* happen, not what was projected.

## 5c. Draft pick value

Picks are major trade currency. Without a price for them, the trade evaluator can't
compare a "player + 2026 1.05" trade to a player-for-player swap. The draft value
model assigns a dollar value to each pick slot based on what it has historically
produced.

### Method

1. For each historical draft year (default 2017–2024), pull all picks from MFL.
2. For each picked player, walk forward 4 years (covering the rookie contract). For each
   year the player was on an active roster, compute their (market_salary − actual_salary)
   surplus using the same per-position power-law curve as the main efficiency model.
3. NPV that surplus stream at 20%.
4. Aggregate by overall pick slot (mean and median realized NPV) → pick value curve.
5. Slice by (round × position) and (pick range × position) to find structural mispricings.

### Why "realized" not "expected"

We don't have rookie projections from the time of each draft. Realized NPV uses what
actually happened. With ~8 drafts of data per slot, the noise is significant — a single
elite hit (Mahomes, Allen, Puka) shifts the mean dramatically. The 3-pick rolling smooth
in the report mitigates slot-to-slot variance.

### Caveats

- **2025 drafts are excluded** from the default run because rookies have only 1 year of
  data. Their realized NPV would be heavily biased by year-1 outcomes.
- **Trades reduce the original pick's measured value.** If a team drafts a player and
  trades him in year 2, the model still credits the original pick for years 3 and 4 of
  on-roster surplus. This slightly overstates pick value (the originating team only
  realized 2 years of it). Could be tightened later by checking franchise persistence.
- **Cuts truncate the value.** Players cut after one bad season vanish from the data
  for years 2–4, correctly making the pick look bad.
- **Sample size is small for specific (round × position) cells.** Read those tables as
  signal direction, not precise numbers.

## 5d. Trade fairness evaluator

Combines player NPV and pick value into a single dollar verdict on any trade.

### Method

For each side of the trade:
1. **Players:** look up current roster row, project trailing points, compute NPV with
   the same `player_npv` function used in `salary_efficiency.npv`.
2. **Picks:** look up median realized NPV at that overall slot from the pick value
   curve (median, not mean — limits outlier pull from a single Mahomes-tier hit).
   Future-year picks are discounted at the same rate as player NPV.
3. Sum each side's totals, take the diff, classify into one of four verdict bands.

### Verdict bands

|diff| < $500K → FAIR · < $2M → SLIGHT EDGE · < $5M → CLEAR WIN · $5M+ → LOPSIDED

### Caveats

- **Picks valued at median.** Mean is dragged way up by single elite hits (Mahomes 2.01,
  Allen 2.09, Puka 3.15). The median is a better "what you should expect" number for a
  trade, but it understates the lottery-ticket value of high picks.
- **Player NPV inherits all NPV caveats** (see section 5b): static market salary, no
  career-arc modeling, single discount rate.
- **No accounting for roster fit, taxi squad, or strategic timing.** A trade that's
  -\$1M in raw NPV but consolidates two starters into one elite player and a roster
  spot can still be a win in real terms.
- **Doesn't model trade-deadline urgency.** If you're 6-0 and need a piece for a
  championship run, a -\$2M-NPV trade can be worth it; if you're 2-4 and rebuilding,
  even +\$2M might not help.

## 6. Files and where things live

```
lib/
  league.py                Constitution constants (cap, escalation, lineup, etc.)
  mfl.py                   MFL API client + escalation math + caching
salary_efficiency/
  analyze.py               Production analyzer: outputs steals/overpays per season
  validate.py              Foundation validation suite (a/b/c/d above)
  npv.py                   Multi-year NPV asset-value model
draft_value/
  analyze.py               Realized NPV per draft slot, by round/position
trade_eval/
  evaluate.py              CLI trade fairness evaluator (combines NPV + pick value)
cap_health/
  analyze.py               Per-team cap & contract-aging report
docs/
  DESIGN.md                Project layout + roadmap
  METHODOLOGY.md           This file. Decisions and rationale.
  CHANGELOG.md             Chronological model changes.
out/
  salary_efficiency/
    YYYY.md / YYYY.csv     Per-season analysis outputs
    validation.md          Most recent validate.py run
  cap_health/
    YYYY.md / YYYY_rosters.csv
.cache/                    On-disk JSON cache of MFL API responses (gitignored)
Top 30 Salary/             Original JS exporter, untouched and still working.
```

## 7. How to run things

```bash
cd ~/code/Fantasy-Football
source .venv/bin/activate

# Single-season salary efficiency report
python -m salary_efficiency.analyze --year 2025

# Same, but pool 3 seasons of pricing for a more stable market fit
python -m salary_efficiency.analyze --year 2025 --years-back 3

# Foundation validation suite (run when changing the model)
python -m salary_efficiency.validate --years 2021 2022 2023 2024 2025

# Multi-year NPV (asset value of every contract)
python -m salary_efficiency.npv --year 2026 --discount 0.20 --by-team

# Cap health snapshot for the current season
python -m cap_health.analyze --year 2026 --week 1
```

Outputs are written to `out/<project>/<year>.md` (and CSV). The `.cache/` directory holds
raw MFL JSON responses; delete it to force a fresh API pull.
