# Auction Prep — Design Doc

Status: **scaffolded, not implemented.** Build target: late January 2027 ahead
of the March auction period.

## Goal

Help set bids during the free-agent auction by giving:
1. A per-player max bid (NPV-positive at our discount rate)
2. Tier-level confidence bands (what a "tier-1 RB" historically costs)
3. League cap-stress signal (will the market be hot or cold this auction)

Existing tools (`salary_efficiency.npv`, `lib.fantasypros`, `cap_health`)
already produce most of the inputs. This module composes them into one
auction-time workflow.

## Modules

### `max_bid.py` — per-player max bid

**Inputs:**
- Player (by name or MFL id)
- Contract length to evaluate (1–5 years)
- Discount rate (default 20%)

**Logic:**
- Pull FP projection for current season → market salary via power-law curve
- Compute NPV across requested contract years (with 10% escalation)
- Subtract a margin (default 0%, configurable) to get the bid that keeps NPV ≥ 0
- Output max bids for 1, 2, 3, 4, 5-year contract terms

**CLI:**
```
python -m auction_prep.max_bid --player "Brian Thomas Jr"
python -m auction_prep.max_bid --player 16614 --discount 0.20 --margin 0.10
python -m auction_prep.max_bid --position WR --top 30   # batch mode
```

**Output (single player):**
```
Brian Thomas Jr (WR)
  Projected: 245 pts (FP)
  Market salary (Y1): $4,200,000

  Term  | Max bid | NPV at max  | NPV at $425K min
  ------|---------|-------------|------------------
  1-yr  |  $4.2M  |     $0      |   $3,775,000
  2-yr  |  $7.1M  |     $0      |   $6,675,000
  3-yr  |  $8.9M  |     $0      |   $8,475,000
  4-yr  | $10.2M  |     $0      |   $9,775,000
  5-yr  | $11.0M  |     $0      |  $10,575,000
```

### `tier_bands.py` — production-tier price bands

**Inputs:** position, year(s) to pool

**Logic:**
- Bin each historical auction by realized points / FP projection at acquisition
- Compute 25 / 50 / 75 percentile salary per tier × year
- Output a position curve with confidence bands

**Output (RB, 2025 cohort):**
```
Tier  | Pts range  | n  | p25     | p50     | p75
------|-----------|----|---------|---------|--------
1     | 280+      | 8  | $5.8M   | $7.1M   | $8.9M
2     | 220-279   | 12 | $3.1M   | $4.2M   | $5.5M
3     | 160-219   | 18 | $1.4M   | $2.1M   | $3.0M
4     | 120-159   | 22 | $625K   | $850K   | $1.4M
5     | <120      | 35 | $425K   | $475K   | $625K
```

Replaces the eyeball-the-top-30 step with proper bands. Pairs with `max_bid.py`.

### `cap_stress.py` — league cap-stress index

**Inputs:** target year (defaults to upcoming auction year)

**Logic:**
- For each team: project committed cap going into auction (current contracts +
  10% escalation - expirations)
- Compute room
- Aggregate signal: count teams in cap-flush / balanced / stressed buckets

**Output:**
```
2027 Pre-Auction Cap Forecast (post-Feb-15 escalation, expirations applied)
  Cap-flush (>$15M room): 4 teams
  Balanced ($5-15M):       6 teams
  Cap-stressed (<$5M):     6 teams

Market signal: BALANCED (slight inflation expected on top tier; flat elsewhere)
```

## Out of scope (don't build)

- ML auction price prediction (n=583 over 5 years; too small)
- KeepTradeCut scraping (TOS-dubious, fragile)
- Real-time bid bot (auction is async over 36 hours)
- Per-player win-probability simulator (overkill)

## Dependencies on existing modules

- `lib.fantasypros.projected_points_by_mflid()` — projections
- `salary_efficiency.analyze.fit_position_market()` — market curve
- `salary_efficiency.npv.player_npv()` — NPV math
- `cap_health.analyze.team_summary()` — cap-stress per team
- `lib.mfl.fetch_auction_results()` — historical auction data

Nothing new to build at the data layer; this is pure composition.
