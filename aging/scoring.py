"""Aging-related player scoring helpers.

Three things provided here:

1. `player_age(birthdate, year)`: integer age at Sept 1 of a given season.
2. `aging_multiplier(position, age, trailing_ratio)`: blended performance multiplier
   for a future season. Combines the population-level aging curve with an elite
   gating dampener so HOF-caliber players aren't wrongly penalized.
3. `aging_risk(position, age, years_remaining, trailing_ratio)`: 0-1 risk score
   plus a categorical flag (LOW/MED/HIGH/EXTREME) for structural age risk over
   the contract window.

Curve data is loaded from out/aging/curves.csv on first call and cached.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd

CURVES_PATH = Path(__file__).resolve().parent.parent / "out" / "aging" / "curves.csv"

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")


@lru_cache(maxsize=1)
def _curve_index() -> dict[tuple[str, int], float]:
    """Return {(position, age): expected_multiplier} from curves.csv. Empty if missing."""
    if not CURVES_PATH.exists():
        return {}
    df = pd.read_csv(CURVES_PATH)
    return {(r["position"], int(r["age"])): float(r["expected_multiplier"])
            for _, r in df.iterrows()}


def player_age(birthdate_unix: int | None, year: int, ref_month: int = 9, ref_day: int = 1) -> int | None:
    """Age in years at the season reference date (defaults Sept 1 of `year`)."""
    if not birthdate_unix:
        return None
    try:
        dob = datetime.fromtimestamp(int(birthdate_unix))
    except (TypeError, ValueError, OSError):
        return None
    ref = datetime(year, ref_month, ref_day)
    return ref.year - dob.year - (1 if (ref.month, ref.day) < (dob.month, dob.day) else 0)


def aging_multiplier(position: str, age: int | None, trailing_ratio: float | None) -> float:
    """Blended aging multiplier for one future season.

    Returns 1.0 (no adjustment) if data is missing.

    Formula:
      curve_m = expected_multiplier from population aging curve at this (pos, age)
      dampener = max(0, 1 - (clamp(trailing_ratio, 1.0, 2.0) - 1.0))
                 1.0 at trailing ratio = 1.0 (avg) → full curve impact
                 0.0 at trailing ratio = 2.0+ (elite) → no curve impact
      return 1.0 - (1.0 - curve_m) * dampener
    """
    if age is None or position not in SKILL_POSITIONS:
        return 1.0
    curve = _curve_index()
    if not curve:
        return 1.0
    raw = curve.get((position, age))
    if raw is None:
        return 1.0
    if trailing_ratio is None:
        # Without an elite signal, apply curve at full strength
        return float(raw)
    elite_strength = max(1.0, min(float(trailing_ratio), 2.0))
    dampener = max(0.0, 1.0 - (elite_strength - 1.0))
    return 1.0 - (1.0 - float(raw)) * dampener


def _position_age_risk(position: str, age: int) -> float:
    """Position-aware structural risk (0-1) at a given age. Hard-coded thresholds
    based on RB cliff at 28, WR cliff at 30, TE cliff at 31, QB cliff at 35."""
    if position == "RB":
        if age <= 25: return 0.05
        if age <= 27: return 0.15
        if age <= 29: return 0.45
        if age == 30: return 0.70
        return 0.90
    if position == "WR":
        if age <= 27: return 0.05
        if age <= 29: return 0.15
        if age <= 31: return 0.35
        if age <= 33: return 0.55
        return 0.80
    if position == "TE":
        if age <= 26: return 0.10
        if age <= 30: return 0.20
        if age <= 33: return 0.40
        return 0.65
    if position == "QB":
        if age <= 32: return 0.05
        if age <= 35: return 0.20
        if age <= 38: return 0.35
        return 0.55
    return 0.30


def _flag(score: float) -> str:
    if score >= 0.50:
        return "EXTREME"
    if score >= 0.30:
        return "HIGH"
    if score >= 0.12:
        return "MED"
    return "LOW"


def aging_risk(position: str, age: int | None, years_remaining: int,
               trailing_ratio: float | None) -> dict:
    """Composite aging risk score + flag.

    Returns {"score": float in [0,1], "flag": str, "age": int or None}
    """
    if age is None:
        return {"score": 0.0, "flag": "?", "age": None}
    age_r = _position_age_risk(position, age)
    contract_r = max(0, years_remaining) / 5.0
    elite_strength = min(float(trailing_ratio or 1.0), 2.0)
    elite_protection = max(0.0, elite_strength - 1.0)
    score = age_r * (0.5 + contract_r * 0.5) * (1.0 - elite_protection * 0.3)
    score = max(0.0, min(score, 1.0))
    return {"score": round(score, 2), "flag": _flag(score), "age": age}


def trailing_ratio_from_history(player_id: str,
                                history_pts: dict[int, dict[str, dict]],
                                position: str,
                                target_year: int,
                                pos_medians: dict[tuple[int, str], float],
                                window: int = 3) -> float | None:
    """Compute mean (player_pts / pos_median) over the last `window` years of data.

    history_pts: {year: {player_id: {"points": float, "weeks_with_score": int}}}
    pos_medians: {(year, position): median_points} for active scorers (>50 pts)
    """
    ratios = []
    for offset in range(1, window + 1):
        y = target_year - offset
        season = history_pts.get(y, {}).get(player_id)
        if not season:
            continue
        pts = season.get("points", 0.0)
        if pts < 50:
            continue
        med = pos_medians.get((y, position))
        if not med or med <= 0:
            continue
        ratios.append(pts / med)
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def position_medians(history_pts: dict[int, dict[str, dict]],
                     player_positions: dict[str, str]) -> dict[tuple[int, str], float]:
    """Compute (year, position) median points across active scorers (>50 pts)."""
    out: dict[tuple[int, str], list[float]] = {}
    for y, by_pid in history_pts.items():
        for pid, info in by_pid.items():
            pts = info.get("points", 0.0)
            if pts < 50:
                continue
            pos = player_positions.get(pid)
            if pos not in SKILL_POSITIONS:
                continue
            out.setdefault((y, pos), []).append(pts)
    medians = {}
    for k, vals in out.items():
        if vals:
            sorted_vals = sorted(vals)
            n = len(sorted_vals)
            medians[k] = sorted_vals[n // 2] if n % 2 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    return medians
