# Backlog

Ideas and improvements for this project, parked for later. Not formally tracked.

## Recommendation engine (in-season)

The daily snapshot system gathers everything needed to generate proactive add/drop/trade
recommendations during the season. Build this once we have a few weeks of in-season
daily data flowing (i.e. starting ~Sept-Oct 2026).

**Inputs already in the repo:**
- `data/fp_projections/<date>/week_NN/` — daily weekly projections per position
- `data/fp_snapshots/<date>/` — weekly dynasty/redraft/rookie ECR
- `salary_efficiency.npv` — multi-year contract NPV
- `trade_eval.evaluate` — trade fairness in dollars
- `draft_value/picks.csv` — pick value curve

**New inputs needed:**
- MFL free-agent feed (already accessible via the existing `lib/mfl.py` client; just
  needs a `fetch_free_agents` function).
- Injury / status field — FP projections include this; we just don't surface it yet.

**Output (run daily, deliver via iMessage or email):**

```
⚠️  Player X projection dropped 4.2 pts WoW (e.g. 18.1 → 13.9 for week 8).
    Cut floor binds, but trade window is closing — shop now.

💡  Waiver target: Player Y
    FP weekly projection up 3.8 pts WoW. Roster opening: drop someone
    underwater. Expected gain: +$1.3M asset value.

📊  Trade idea: shop one of the 4yr/$7M+ overpays
    Cap-stress + multi-year commit makes them hard to move; package with
    sweetener picks to a cap-flush team.
```

**Notes:**
- Probably ~1 weekend of scaffolding once we have data.
- Depends on the daily cron firing reliably. Watch the logs once season starts.
- Long-term: hook into the existing Proxy / iMessage agent infrastructure (mac-mini)
  so recommendations land in iMessage like Jeffy/Ripley do.

## Other ideas

- **Auction prep / max-bid module.** See dedicated section below — the largest
  unbuilt project, slated for January 2027 build.
- **Aging curves.** NPV currently holds projected market salary flat through a
  multi-year contract. Real career arcs differ by position (RBs decline at 28+, QBs
  peak at 28-32). Could fit per-position aging curves from the historical dataset and
  feed them into NPV's year-2+ projections.
- **External market benchmark.** Currently the "fair market" is the league's own
  pricing (circular). Could pull KeepTradeCut numeric values (no API; would need
  scraping — fragile and TOS-questionable) or use FantasyPros dynasty ECR with a
  rank→dollars calibration.
- **Trade-deadline urgency adjustment.** Same trade is worth different amounts to a
  6-0 contender vs a 2-4 rebuilder. Could weight surplus by current standings + playoff
  odds.
- **Opponent-side trade simulator.** Today the trade evaluator only verdicts. Could
  flip it: "given my roster + their roster, suggest the best trade I should propose."
  Needs both teams' NPV diffs *and* roster-fit considerations (don't trade your QB1
  to acquire a 4th RB).
- **Historical sweep 2021-2025.** Apply the final model retroactively to find each
  season's biggest steals/overpays. Cosmetic, low priority.
- **Compensatory pick handler.** Constitution awards 3rd-round comp picks for FA
  losses; not currently modeled in pick value or trade eval.
- **Roster construction view.** Pair `cap_health` with `npv` to flag teams with
  structural weakness (e.g., cap-flush but with $0 of multi-year asset value).

## Auction prep / max-bid module

Free-agent auction starts the 3rd Thursday in March. Build in late January so
it's ready for bidding.

### What the existing exporter does
The `Top 30 Salary` JS exporter pulls top 30 salaries by position by year,
escalation-adjusted. Useful but per-position-average — not tied to per-player
projections.

### What to build

**Module 1: Per-player max-bid calculator (`auction_prep/max_bid.py`)**

Given a player + contract length, output the bid that keeps the contract
NPV-positive at the configured discount rate.

```
Player X
1-yr: market $4.2M → max bid $3.4M
2-yr: NPV $7.1M → max bid $5.7M
3-yr: NPV $8.9M → max bid $7.1M
```

Reuses `salary_efficiency.npv` and `lib.fantasypros`. Outputs a CSV ranked by
position with max bids at 1/2/3/4/5-yr terms.

**Module 2: Tier curves with confidence bands**

Replace the top-30 eyeballing with 25th / 50th / 75th percentile bands per
(position, production tier). Helps with bid pacing.

**Module 3: League cap-stress index**

Pre-auction, compute each team's projected cap room. Aggregate signal:
fewer cap-flush teams → softer market, more cap-flush teams → inflation.

### Constraints / what NOT to build
- No ML price predictor — sample size too small.
- No KeepTradeCut scraping — fragile, TOS-dubious.
- No real-time bid agent — async 36-hour auction windows.

## In-season data feeds (build before September)

Two free signals to layer on top of FP projections for the recommendation engine
(see Recommendation engine section above). Both are higher-signal than parsing
fantasy newsletter prose.

### NFL injury status surfacing

FantasyPros projections already include injury status fields (`player_injury_status`,
`player_injury_notes`). We don't currently surface these. In-season they're the
single most actionable signal — a "Q" or "OUT" for a starter is a 24-hour trade
window or waiver pickup trigger.

Implementation: ~30 min. Extend `lib/fantasypros.py` to expose injury fields,
add column to NPV report and trade_eval output, flag in recommendation engine.

### Sleeper trending API

`https://api.sleeper.app/players/nfl/trending/add?lookback_hours=24&limit=25`
returns the most-added players across all Sleeper leagues in the last 24 hours.
Free, no auth, real-time market signal.

Use case: catch breakout players 12-24 hours before FP rankings update. If a
trending-up player is unowned in our league, surface as a waiver target.

Implementation: ~30 min. New `lib/sleeper.py` client, daily snapshot, integrate
into recommendation engine output.

### What we are NOT doing

- Newsletter ingestion (signal/noise too poor for the time investment)
- Twitter/X beat reporter scraping (rate-limit/auth nightmare)
- Pay-walled premium content scraping (legal/TOS issues)

## Pre/post-NFL-draft FP rankings drift study (2027 cycle)

The FantasyPros API returns blended consensus ranks but not individual expert
rankings or per-expert dates. We can't filter to "only post-draft rankings"
directly. However, we CAN watch the aggregate min/max/std drift over time.

When some experts update post-draft and others don't, we'd expect:
- `rank_std` to spike (experts diverge as some incorporate new info)
- `rank_min` and `rank_max` to widen
- `rank_ave` to drift toward the post-draft consensus over 2-3 weeks as
  more experts refresh

If we snapshot rookie rankings DAILY for the 2-3 weeks before AND after
the 2027 NFL draft (~April 23-26), we can:

1. Establish the pre-draft baseline (where consensus settled)
2. Watch which players see the biggest std spike post-draft (= most
   contested by experts who've updated vs. those who haven't)
3. Identify players whose `rank_min` (their highest believer) jumps post-
   draft = the experts who've updated are bullish
4. Spot the inflection point when std re-tightens = "consensus has
   reformed" and the rankings are usable again
5. Use the std-spike magnitude as a noise filter: high-std rookies are
   harder to trust mid-transition

This addresses Nate's correct objection that "rankings dated today doesn't
mean experts updated today." We can't see expert dates, but we CAN see
the statistical fingerprint of partial updates.

### Implementation
- The daily snapshot system already runs. Just need to start a 2027
  baseline ~3 weeks before the 2027 NFL draft (~early April 2027).
- Add a script that loads consecutive snapshots and reports per-player
  std/min/max deltas. Highlight biggest movers.
- Maybe extend to dynasty rankings too, not just rookie.

### Potential side benefit
Once we have multiple cycles of this data (2027, 2028, ...) we could
build a "draft-day adjustment factor" that estimates how much a given
player's rank_ave is likely to move post-draft, based on their pre-draft
std and tier. Useful for trading rookies in the days after the NFL draft
when most owners are reacting to fresh news.

## Comp pick / extension / tag calculator

The 2026+ rule changes (rookie extensions, comp picks for walked rookies)
create a multi-year decision graph for every rookie on the roster:

  Year 1-3: extend now? extend later? trade? hold?
  Year 4: extend? tag? let walk for comp pick?
  Trade: trade-acquired rookies must be extended in same league year of
    acquisition (deadline Feb 14)

Build a calculator: given a rookie's projected NPV, age, salary trajectory,
position, and contract year, output the EV of each path:
  - Extend now (locks +2 yrs at top-5 positional avg)
  - Wait and extend in Year 3 (different pricing)
  - Tag (1 yr at top-3 positional avg)
  - Let walk for comp pick (3rd-round pick in the next draft)
  - Trade (price assumes counterparty applies extension)

For a marginal rookie, walk-for-comp-pick is often the right answer. For an
obvious hit, extend-early is right. The middle is what's interesting and
where small mispricings compound across a full roster of rookies.

Roughly 2-3 hrs to build. Reuses NPV + pick value curve. Output: per-player
recommendation table.

## Comp-pick-aware trade pricing

When trading a 2026+ rookie to another team, you transfer not just the
contract but ALSO the compensatory pick option. Most other owners aren't
yet pricing this in (the rule is brand new for the 2026 draft, so the
first comp-pick payout doesn't happen until 2030+).

The trade evaluator should add a small premium (~$200-400K) to traded 2026+
rookies to reflect the future comp-pick option the receiving team will
inherit. Current model treats them as just contract value.

Roughly 30 min to add. Update trade_eval/evaluate.py to flag and adjust.

## Comp-pick May-1 deadline arbitrage

Comp pick is only awarded if the walked player signs with another team in
auction BEFORE May 1. After that, no comp pick. This creates a real trade
window:

  - You hold a player you don't want to extend
  - Auction runs March-Aug
  - If the player is going to sign somewhere by April 30, holding for the
    comp pick beats trading for less than ~$300K (the 3rd-round comp value)
  - If the player likely signs after May 1 OR doesn't sign at all, trading
    for any positive value beats letting them walk for nothing

Tool to build: given a player's market value (FP / our model) and
projected sign date (could estimate from FA auction history), recommend
hold-for-comp-pick vs. trade-now. Most owners will think about this on
March 31 in the panic of decision day. We can plan months earlier.

Roughly 1 hr. New script that flags rookies-likely-to-walk and computes
break-even trade value vs. comp pick.
