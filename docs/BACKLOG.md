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
