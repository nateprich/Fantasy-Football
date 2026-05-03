# Fantasy Football — Analytics

Personal analytics projects for The League (MFL #13522) — a 16-team dynasty/salary-cap league.
See [docs/DESIGN.md](docs/DESIGN.md) for design notes and the constitution constants in
[`lib/league.py`](lib/league.py).

## Projects

| Folder | What it does |
| --- | --- |
| `lib/` | Shared MFL API client + escalation math + league constants. |
| `salary_efficiency/` | Joins season fantasy points with escalated salaries; reports steals, overpays, $/PPG by tier. Includes a multi-year NPV model that values every contract as an asset (`npv.py`). |
| `cap_health/` | Per-team committed cap, top-3 concentration, contract-year distribution, expirations, risk flags. |
| `draft_value/` | Realized NPV per rookie draft slot 2017–2024. Pick value curve, by round × position, top hits and worst misses. |
| `Top 30 Salary/` | Original JS exporter (top 30 salary-by-position-by-year .xlsx). Still works; superseded by Python going forward. |

## Documentation

- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) — **Read this first.** The deep "why" behind every modeling decision, what was tried and rejected, validation results, known limitations.
- [`docs/DESIGN.md`](docs/DESIGN.md) — High-level design + roadmap.
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — Chronological model changes.
- [`out/salary_efficiency/validation.md`](out/salary_efficiency/validation.md) — Most recent foundation validation run.

## Setup

```bash
cd ~/code/Fantasy-Football
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Salary efficiency (last completed season, market fit on 1 year of data)
python -m salary_efficiency.analyze --year 2025

# More stable market fit using 3 seasons of pricing signal
python -m salary_efficiency.analyze --year 2025 --years-back 3

# Multi-year NPV model — values every contract as an asset (default 20% discount rate)
python -m salary_efficiency.npv --year 2026 --discount 0.20 --by-team

# Draft pick value curve from 2017–2024 drafts
python -m draft_value.analyze --start 2017 --through 2024 --years-since 4 --discount 0.20

# Cap health snapshot for the current season
python -m cap_health.analyze --year 2026 --week 1
```

Reports are written to `out/<project>/<year>.md` and `.csv`. The `.cache/` directory holds
raw MFL JSON responses to avoid hammering the API on re-runs (delete it to force a fresh pull).
