"""Shared lineup scoring helpers.

The constitution requires 1 QB, 1-4 RB, 1-4 WR, 1-4 TE, 1 PK, and 1 Def.
That means the skill group is six total RB/WR/TE starters with at least one
from each skill position and no more than four from any one skill position.
"""
from __future__ import annotations

import pandas as pd

from lib.league import FLEX_TOTAL, STARTING_DEF, STARTING_PK, STARTING_QB

SKILL_POSITIONS = ["RB", "WR", "TE"]
MAX_SKILL_BY_POSITION = 4


def select_starting_lineup(team_df: pd.DataFrame, points_col: str = "projected_pts") -> tuple[pd.DataFrame, dict]:
    """Return the best legal starting lineup and summary components."""
    selected_indices: list = []
    missing: list[str] = []

    def take_top(position: str, count: int) -> pd.DataFrame:
        sub = team_df[team_df["position"] == position]
        if sub.empty:
            return sub
        return sub.nlargest(count, points_col)

    qb = take_top("QB", STARTING_QB)
    if len(qb) < STARTING_QB:
        missing.append("QB")
    selected_indices.extend(qb.index.tolist())

    skill_counts = {pos: 0 for pos in SKILL_POSITIONS}
    for position in SKILL_POSITIONS:
        top = take_top(position, 1)
        if top.empty:
            missing.append(position)
            continue
        selected_indices.extend(top.index.tolist())
        skill_counts[position] += len(top)

    skill_df = team_df[team_df["position"].isin(SKILL_POSITIONS)]
    while sum(skill_counts.values()) < FLEX_TOTAL:
        already_selected = set(selected_indices)
        candidates = skill_df[~skill_df.index.isin(already_selected)].copy()
        candidates = candidates[candidates["position"].map(skill_counts) < MAX_SKILL_BY_POSITION]
        if candidates.empty:
            break
        idx = candidates[points_col].idxmax()
        selected_indices.append(idx)
        skill_counts[team_df.loc[idx, "position"]] += 1

    if sum(skill_counts.values()) < FLEX_TOTAL:
        missing.append("RB/WR/TE depth")

    pk = take_top("PK", STARTING_PK)
    if len(pk) < STARTING_PK:
        missing.append("PK")
    selected_indices.extend(pk.index.tolist())

    defense = take_top("Def", STARTING_DEF)
    if len(defense) < STARTING_DEF:
        missing.append("Def")
    selected_indices.extend(defense.index.tolist())

    lineup = team_df.loc[list(dict.fromkeys(selected_indices))].copy()
    skill_lineup = lineup[lineup["position"].isin(SKILL_POSITIONS)]

    components = {
        "qb_pts": float(lineup[lineup["position"] == "QB"][points_col].sum()),
        "rb_pts": float(skill_lineup[skill_lineup["position"] == "RB"][points_col].sum()),
        "wr_pts": float(skill_lineup[skill_lineup["position"] == "WR"][points_col].sum()),
        "te_pts": float(skill_lineup[skill_lineup["position"] == "TE"][points_col].sum()),
        "skill_pts": float(skill_lineup[points_col].sum()),
        "pk_pts": float(lineup[lineup["position"] == "PK"][points_col].sum()),
        "def_pts": float(lineup[lineup["position"] == "Def"][points_col].sum()),
        "starting_pts": float(lineup[points_col].sum()),
        "starters_filled": len(missing) == 0,
        "missing_slots": ", ".join(dict.fromkeys(missing)) or "",
    }
    return lineup, components


def bench_skill_points(team_df: pd.DataFrame, points_col: str = "projected_pts") -> dict:
    """Return non-starter QB/RB/WR/TE depth points after legal lineup selection."""
    lineup, _ = select_starting_lineup(team_df, points_col=points_col)
    bench = team_df[~team_df.index.isin(lineup.index)]
    bench_skill = bench[bench["position"].isin(["QB", *SKILL_POSITIONS])]
    return {
        "bench_skill_pts": float(bench_skill[points_col].sum()),
        "bench_count": int(len(bench_skill)),
    }
