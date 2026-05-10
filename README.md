# Fantasy Football — Analytics

Personal analytics for The League (MFL #13522), a 16-team dynasty/salary-cap league.

## Documentation

- **[`docs/DESIGN.md`](docs/DESIGN.md)** — System overview, module map, what's built and what isn't.
- **[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md)** — Deep modeling decisions, what was tried and rejected, validation, known limits.
- **[`docs/CHANGELOG.md`](docs/CHANGELOG.md)** — Chronological model and feature changes.
- **[`docs/BACKLOG.md`](docs/BACKLOG.md)** — Future ideas parked for later.
- **[`auction_prep/DESIGN.md`](auction_prep/DESIGN.md)** — Auction tooling spec.
- **[`out/salary_efficiency/validation.md`](out/salary_efficiency/validation.md)** — Most recent foundation validation run.

## Projects

| Folder | What it does |
| --- | --- |
| `lib/` | Shared MFL + FantasyPros API clients, escalation math, league constants, snapshot jobs. |
| `salary_efficiency/` | Per-season surplus, multi-year NPV, validation suite. The foundation. |
| `cap_health/` | Per-team committed cap, top-3 concentration, contract-year distribution, risk flags. |
| `draft_value/` | Realized NPV per rookie draft slot 2017–2024. Pick value curve. |
| `aging/` | Per-position aging curves (performance × survival) + per-player risk scoring. |
| `trade_eval/` | Trade fairness CLI + league-wide pick inventory. |
| `auction_prep/` | Per-player max-bid calculator, tier bands, league cap-stress index. |
| `competitiveness/` | Projected lineup snapshot + retrospective validation checks. |
| `cutdown/` | Cut-impact analyzer for roster-size decisions. |
| `Top 30 Salary/` | Original JS exporter (still works). Superseded by Python going forward. |

## Setup

```bash
cd ~/code/Fantasy-Football
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### FantasyPros projections (recommended)

NPV, trade_eval, and auction_prep all use FantasyPros expert-consensus projections when
available, falling back to trailing-2-year average otherwise. To enable:

1. Apply for a free non-commercial API key at https://api.fantasypros.com/
2. Drop it in `.env` as `FANTASYPROS_API_KEY=your_key_here`

`.env` is gitignored. Pass `--no-fp` to any CLI to disable.

## Usage

```bash
# Per-season salary efficiency report
python -m salary_efficiency.analyze --year 2025 --years-back 3

# Foundation validation (run after model changes)
python -m salary_efficiency.validate --years 2021 2022 2023 2024 2025

# Multi-year NPV — values every contract as an asset
python -m salary_efficiency.npv --year 2026 --discount 0.20 --by-team
python -m salary_efficiency.npv --year 2026 --with-aging   # apply aging multipliers to year 2+

# Cap health snapshot for the current season
python -m cap_health.analyze --year 2026 --week 1

# Draft pick value curve (2017–2024 drafts, 4-year tracking window)
python -m draft_value.analyze --start 2017 --through 2024 --years-since 4 --discount 0.20

# Trade fairness evaluator
python -m trade_eval.evaluate --year 2026 \
    --side-a "Puka Nacua" "2027 1.05" \
    --side-b "Drake London" "2026 2.07"

# League-wide pick inventory (with values)
python -m trade_eval.pick_inventory --my-franchise "Midwestside"

# Aging curve fit (regenerate after season)
python -m aging.fit_curves --start 2017 --end 2025

# Auction prep
python -m auction_prep.max_bid --player "Puka Nacua" --my-team "Midwestside"
python -m auction_prep.max_bid --position WR --top 30 --my-team "Midwestside"
python -m auction_prep.tier_bands --position RB --years 2021 2022 2023 2024 2025
python -m auction_prep.cap_stress --year 2027 --source-year 2026

# Projected lineup snapshot, retrospective validation, and cutdown impact
python -m competitiveness.analyze --year 2026 --my-team "Midwestside"
python -m competitiveness.backtest --years 2021 2022 2023 2024 2025
python -m cutdown.analyze --year 2026 --my-team "Midwestside"
```

Reports are written to `out/<project>/<year>.md` (and CSV). The `.cache/` directory holds raw
MFL + FantasyPros JSON; delete it to force fresh API pulls.

## Snapshots

The daily snapshot job (`scripts/daily-snapshot.sh`, registered with launchd at 7am PT) pulls
FantasyPros projections + rankings to `data/fp_*/<date>/`, auto-commits, and pushes. Smart
cadence:

| When | What |
|---|---|
| Sun 7am, year-round | All ranking types (dynasty, redraft, rookie) |
| Mon-Sat in-season (Sept-Jan) | Weekly projections |
| Sun in-season | Weekly projections + rankings |
| Mon-Sat off-season (Feb-Aug) | Nothing |
| Sun off-season | Season-total projections + rankings |

Snapshots are committed to git (~600KB/day in-season, ~26MB/year) so the longitudinal
dataset is portable.

## License / privacy

Personal project. Snapshots include public-domain projection data. No private league info
beyond what the league publicly displays at theleague.us.
