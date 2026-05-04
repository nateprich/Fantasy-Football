"""FantasyPros API client.

Free non-commercial tier. Key in .env as FANTASYPROS_API_KEY.

Endpoints used (more available, see https://api.fantasypros.com/):
  - GET /public/v2/json/nfl/<season>/projections?position=<POS>&week=<W>
    week=0 (or "draft") returns season-total projection.

Conveniently includes `mflid` on each player record, so we can join directly to MFL
data without any name-matching gymnastics.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.fantasypros.com/public/v2/json/nfl"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "fantasypros"
DEFAULT_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
REQUEST_DELAY = 0.5


def _api_key() -> str:
    key = os.environ.get("FANTASYPROS_API_KEY")
    if key:
        return key
    # Try .env file in the repo root
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("FANTASYPROS_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("FANTASYPROS_API_KEY not set (env var or .env file)")


def _cache_path(season: int, position: str, week: int) -> Path:
    return CACHE_DIR / str(season) / f"projections_{position}_w{week}.json"


def fetch_projections(season: int, position: str, week: int = 0, *, use_cache: bool = True) -> dict:
    """Fetch projections for one (season, position, week). week=0 = season total."""
    cache = _cache_path(season, position, week)
    if use_cache and cache.exists():
        return json.loads(cache.read_text())

    url = f"{BASE_URL}/{season}/projections"
    params = {"position": position, "week": week or "draft"}
    headers = {"x-api-key": _api_key()}

    last_err = None
    for attempt in range(5):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  FP 429 rate-limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data))
            time.sleep(REQUEST_DELAY)
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"FantasyPros fetch failed: {position} {season} W{week}: {last_err}")


def projected_points_by_mflid(season: int, *, week: int = 0, positions=DEFAULT_POSITIONS,
                              scoring: str = "points_ppr") -> dict[str, float]:
    """Return mflid (str) -> projected season points across all positions.

    `scoring` is the key inside the per-player stats dict. Common options:
      points       (standard)
      points_ppr   (full PPR)
      points_half  (half PPR)
    """
    out: dict[str, float] = {}
    for pos in positions:
        try:
            data = fetch_projections(season, pos, week=week)
        except Exception as e:  # noqa: BLE001
            print(f"  WARN: {pos} {season} skipped: {e}")
            continue
        for p in data.get("players", []):
            mflid = p.get("mflid")
            if not mflid:
                continue
            stats = p.get("stats", {})
            pts = stats.get(scoring) or stats.get("points") or 0
            try:
                out[str(mflid)] = float(pts)
            except (TypeError, ValueError):
                continue
    return out
