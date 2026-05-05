"""MyFantasyLeague API client + salary escalation logic.

Python port of the data layer originally written in JS (Top 30 Salary/export-salaries.mjs).
All endpoints documented at https://api.myfantasyleague.com/.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests

from .league import (
    ANNUAL_ESCALATION,
    EXCLUDED_POSITIONS,
    LEAGUE_ID,
    MAX_CONTRACT_YEARS,
)

BASE_URL = "https://www49.myfantasyleague.com"
USER_AGENT = "fantasy-football-analytics/0.1 (nateprich)"
REQUEST_DELAY = 2.0
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


# ── HTTP layer ────────────────────────────────────────────────────────────

def _ensure_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _cache_path(year: int, type_: str, params: dict) -> Path:
    key_parts = [type_] + [f"{k}={v}" for k, v in sorted(params.items()) if k not in {"L", "JSON"}]
    fname = "_".join(key_parts).replace("/", "_") or type_
    return CACHE_DIR / str(year) / f"{fname}.json"


def fetch(year: int, type_: str, *, use_cache: bool = True, **params) -> dict:
    """Fetch a single MFL export endpoint, with on-disk JSON caching."""
    qs = {"L": LEAGUE_ID, "JSON": "1", "TYPE": type_, **{k: str(v) for k, v in params.items()}}
    cache_file = _cache_path(year, type_, params)
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())

    url = f"{BASE_URL}/{year}/export"
    last_err = None
    for attempt in range(8):
        try:
            r = requests.get(url, params=qs, headers={"User-Agent": USER_AGENT}, timeout=30)
            if r.status_code == 429:
                wait = 30 + 30 * attempt  # 30, 60, 90, 120, ... up to ~4 minutes
                print(f"  429 rate-limited on {type_} {year} attempt {attempt+1}/8, sleeping {wait}s", flush=True)
                last_err = RuntimeError("HTTP 429")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(f"MFL error: {data['error']}")
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data))
            time.sleep(REQUEST_DELAY)
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"MFL fetch failed: {type_} {year} ({params}): {last_err}")


# ── Helpers ───────────────────────────────────────────────────────────────

def normalize_position(pos: str | None) -> str | None:
    if not pos:
        return None
    if pos in EXCLUDED_POSITIONS:
        return None
    upper = pos.upper()
    if upper == "DEF":
        return "Def"
    if upper == "K":
        return "PK"
    return {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "PK": "PK"}.get(upper)


def format_name(mfl_name: str | None) -> str:
    if not mfl_name:
        return "Unknown"
    if "," in mfl_name:
        last, first = mfl_name.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return mfl_name


# ── Domain fetchers ───────────────────────────────────────────────────────

def fetch_player_metadata(year: int) -> dict[str, dict]:
    """player_id -> {name, position, team}."""
    data = fetch(year, "players", DETAILS=1)
    out: dict[str, dict] = {}
    for p in _ensure_list(data.get("players", {}).get("player")):
        pid = p.get("id")
        pos = normalize_position(p.get("position"))
        if not pid or not pos:
            continue
        out[pid] = {"name": format_name(p.get("name")), "position": pos, "team": p.get("team")}
    return out


def fetch_rosters(year: int, week: int = 1) -> dict[str, list[dict]]:
    """franchise_id -> list of {player_id, salary, contract_year, status}."""
    data = fetch(year, "rosters", W=week)
    out: dict[str, list[dict]] = {}
    for f in _ensure_list(data.get("rosters", {}).get("franchise")):
        fid = f.get("id")
        if not fid:
            continue
        players = []
        for p in _ensure_list(f.get("player")):
            pid = p.get("id")
            if not pid:
                continue
            players.append({
                "player_id": pid,
                "salary": float(p.get("salary") or 0),
                "contract_year": int(p.get("contractYear") or 0),  # years remaining
                "status": p.get("status"),
            })
        out[fid] = players
    return out


def fetch_draft_results(year: int) -> list[dict]:
    """Rookie draft picks for the given year. Returns list of {round, pick, overall, player_id, franchise}."""
    data = fetch(year, "draftResults")
    units = _ensure_list(data.get("draftResults", {}).get("draftUnit"))
    rows = []
    for unit in units:
        for p in _ensure_list(unit.get("draftPick")):
            try:
                rd = int(p.get("round") or 0)
                pk = int(p.get("pick") or 0)
            except ValueError:
                continue
            if rd == 0 or pk == 0:
                continue
            # Overall pick = (round-1)*16 + pick (16 teams in this league)
            overall = (rd - 1) * 16 + pk
            rows.append({
                "year": year,
                "round": rd,
                "pick": pk,
                "overall": overall,
                "slot": f"{rd}.{pk:02d}",
                "player_id": p.get("player") or None,
                "franchise_id": p.get("franchise"),
            })
    return rows


def fetch_franchises(year: int) -> dict[str, dict]:
    data = fetch(year, "league")
    franchises = _ensure_list(data.get("league", {}).get("franchises", {}).get("franchise"))
    return {f["id"]: {"name": f.get("name"), "division": f.get("division")} for f in franchises if f.get("id")}


def fetch_auction_results(year: int) -> dict[str, float]:
    data = fetch(year, "auctionResults")
    out: dict[str, float] = {}
    for a in _ensure_list(data.get("auctionResults", {}).get("auctionUnit", {}).get("auction")):
        pid = a.get("player")
        bid = float(a.get("winningBid") or 0)
        if pid and bid > 0:
            out[pid] = max(out.get(pid, 0), bid)
    return out


def fetch_bbid_waivers(year: int) -> dict[str, float]:
    """In-season blind-bid waiver claims. Returns player_id -> bid."""
    data = fetch(year, "transactions", TRANS_TYPE="BBID_WAIVER", COUNT=500)
    out: dict[str, float] = {}
    for tx in _ensure_list(data.get("transactions", {}).get("transaction")):
        raw = tx.get("transaction") or ""
        parts = raw.split("|")
        if len(parts) < 3:
            continue
        try:
            bid = float(parts[1])
        except ValueError:
            continue
        if bid <= 0:
            continue
        for pid in [s.strip() for s in parts[2].split(",") if s.strip()]:
            out[pid] = max(out.get(pid, 0), bid)
    return out


def fetch_weekly_results(year: int, week: int) -> dict[str, float]:
    """player_id -> fantasy points for that week (league scoring applied by MFL)."""
    data = fetch(year, "weeklyResults", W=week)
    out: dict[str, float] = {}
    matchups = data.get("weeklyResults", {}).get("matchup")
    for m in _ensure_list(matchups):
        for fr in _ensure_list(m.get("franchise")):
            for p in _ensure_list(fr.get("player")):
                pid = p.get("id")
                pts = p.get("score")
                if pid and pts not in (None, ""):
                    try:
                        out[pid] = float(pts)
                    except ValueError:
                        pass
    return out


def fetch_season_points(year: int, weeks: Iterable[int] = range(1, 18)) -> dict[str, dict]:
    """player_id -> {points, games, weeks_started_or_scored}."""
    totals: dict[str, dict] = {}
    for w in weeks:
        try:
            wk = fetch_weekly_results(year, w)
        except RuntimeError as e:
            if "Invalid week" in str(e):
                break
            raise
        for pid, pts in wk.items():
            t = totals.setdefault(pid, {"points": 0.0, "weeks_with_score": 0})
            t["points"] += pts
            if pts != 0:
                t["weeks_with_score"] += 1
    return totals


# ── Salary escalation ─────────────────────────────────────────────────────

@dataclass
class HistoricalBids:
    by_year: dict[int, dict[str, float]]  # year -> player_id -> max(bid)

    @classmethod
    def load(cls, start_year: int, end_year: int) -> "HistoricalBids":
        merged: dict[int, dict[str, float]] = {}
        for y in range(start_year, end_year + 1):
            auctions = fetch_auction_results(y)
            bbids = fetch_bbid_waivers(y)
            year_map: dict[str, float] = {}
            for src in (auctions, bbids):
                for pid, bid in src.items():
                    year_map[pid] = max(year_map.get(pid, 0), bid)
            merged[y] = year_map
        return cls(merged)

    def escalated_salary(self, player_id: str, target_year: int) -> float:
        """Walk back up to MAX_CONTRACT_YEARS-1 to find original bid, apply 10% per year elapsed."""
        earliest = max(target_year - (MAX_CONTRACT_YEARS - 1), min(self.by_year))
        for y in range(target_year, earliest - 1, -1):
            bid = self.by_year.get(y, {}).get(player_id, 0)
            if bid > 0:
                years_elapsed = target_year - y
                return round(bid * ((1 + ANNUAL_ESCALATION) ** years_elapsed))
        return 0
