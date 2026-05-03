"""Foundation sanity checks for the salary efficiency model.

Runs four validations:
  (a) Year-over-year surplus persistence — same-player surplus correlation across consecutive years.
  (b) Cross-validation MAE — power-law vs linear vs log-linear, 5-fold CV per position.
  (c) PAR vs raw-points fit — does Points Above Replacement explain salary better than raw points?
  (d) PK/Def reliability — fit quality for kickers/defenses (justifies excluding them from surplus rankings).

Run:
    python -m salary_efficiency.validate --years 2021 2022 2023 2024 2025
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from lib import mfl
from lib.league import LEAGUE_MIN_SALARY, SCORING_POSITIONS

from salary_efficiency.analyze import (
    TIER_SIZES,
    apply_market,
    build_season_dataframe,
    fit_position_market,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "salary_efficiency"


# ── (a) Persistence ───────────────────────────────────────────────────────

def persistence_check(per_year: dict[int, pd.DataFrame], min_points: float = 50.0) -> pd.DataFrame:
    """Pearson + Spearman correlation of per-player surplus across consecutive years."""
    rows = []
    years = sorted(per_year)
    for y1, y2 in zip(years[:-1], years[1:]):
        a = per_year[y1][["player_id", "position", "surplus", "points"]].rename(columns={"surplus": "s1", "points": "p1"})
        b = per_year[y2][["player_id", "surplus", "points"]].rename(columns={"surplus": "s2", "points": "p2"})
        m = a.merge(b, on="player_id")
        m = m[(m["p1"] >= min_points) & (m["p2"] >= min_points)]
        if len(m) < 10:
            continue
        for pos in [None, "QB", "RB", "WR", "TE"]:
            sub = m if pos is None else m[m["position"] == pos]
            if len(sub) < 10:
                continue
            r_pearson = sub["s1"].corr(sub["s2"], method="pearson")
            r_spearman = sub["s1"].corr(sub["s2"], method="spearman")
            rows.append({
                "year_pair": f"{y1}->{y2}",
                "position": pos or "ALL",
                "n": len(sub),
                "pearson_r": round(r_pearson, 3),
                "spearman_r": round(r_spearman, 3),
            })
    return pd.DataFrame(rows)


# ── (b) Cross-validated model comparison ─────────────────────────────────

def _fit_predict(form: str, x_train, y_train, x_pred):
    """Return predictions for given form. x is points, y is salary."""
    if form == "linear":
        a, b = np.polyfit(x_train, y_train, 1)
        return np.maximum(LEAGUE_MIN_SALARY, a * x_pred + b)
    if form == "log_linear":
        # salary = a*log(points) + b; reasonable concave alternative
        lx = np.log(np.maximum(x_train, 1))
        a, b = np.polyfit(lx, y_train, 1)
        return np.maximum(LEAGUE_MIN_SALARY, a * np.log(np.maximum(x_pred, 1)) + b)
    if form == "power":
        # log-log: salary = c * points^k
        ok = (x_train > 0) & (y_train > 0)
        k, log_c = np.polyfit(np.log(x_train[ok]), np.log(y_train[ok]), 1)
        return np.maximum(LEAGUE_MIN_SALARY, np.exp(log_c) * np.maximum(x_pred, 1) ** k)
    raise ValueError(form)


def cv_compare(pooled: pd.DataFrame, k_folds: int = 5, seed: int = 42) -> pd.DataFrame:
    """5-fold CV per position. Reports MAE in dollars for each functional form."""
    rng = np.random.default_rng(seed)
    rows = []
    for pos in SCORING_POSITIONS:
        # Same productive-tail filter as production fit
        n_per_year = TIER_SIZES[pos] * 2
        frames = []
        for yr, yr_df in pooled[pooled["position"] == pos].groupby("year"):
            frames.append(yr_df.nlargest(n_per_year, "points"))
        sub = pd.concat(frames) if frames else pooled.iloc[0:0]
        sub = sub[(sub["salary"] > LEAGUE_MIN_SALARY) & (sub["points"] > 0)].copy()
        if len(sub) < 25:
            rows.append({"position": pos, "n": len(sub), "linear": None, "log_linear": None, "power": None, "winner": "INSUFFICIENT_DATA"})
            continue
        idx = np.arange(len(sub))
        rng.shuffle(idx)
        folds = np.array_split(idx, k_folds)
        x = sub["points"].values
        y = sub["salary"].values
        mae_by_form = {}
        for form in ("linear", "log_linear", "power"):
            errs = []
            for f in folds:
                mask = np.ones(len(sub), dtype=bool)
                mask[f] = False
                try:
                    pred = _fit_predict(form, x[mask], y[mask], x[~mask])
                    errs.append(np.mean(np.abs(pred - y[~mask])))
                except Exception:  # noqa: BLE001
                    errs.append(np.nan)
            mae_by_form[form] = float(np.nanmean(errs))
        winner = min(mae_by_form, key=mae_by_form.get)
        rows.append({
            "position": pos,
            "n": len(sub),
            "linear": round(mae_by_form["linear"]),
            "log_linear": round(mae_by_form["log_linear"]),
            "power": round(mae_by_form["power"]),
            "winner": winner,
        })
    return pd.DataFrame(rows)


# ── (c) Points Above Replacement ─────────────────────────────────────────

REPLACEMENT_RANK = {"QB": 16, "RB": 40, "WR": 48, "TE": 16, "PK": 16, "Def": 16}


def add_par(df: pd.DataFrame) -> pd.DataFrame:
    """For each (year, position) compute replacement-level points = Nth-best, then PAR = points - repl."""
    out = df.copy()
    out["par"] = 0.0
    for (yr, pos), sub in out.groupby(["year", "position"]):
        rank_n = REPLACEMENT_RANK.get(pos, 24)
        sorted_pts = sub["points"].sort_values(ascending=False).reset_index(drop=True)
        repl = float(sorted_pts.iloc[min(rank_n - 1, len(sorted_pts) - 1)]) if len(sorted_pts) else 0.0
        out.loc[sub.index, "par"] = (sub["points"] - repl).clip(lower=0)
    return out


def cv_compare_par(pooled: pd.DataFrame, k_folds: int = 5, seed: int = 42) -> pd.DataFrame:
    """Same CV, but x = PAR instead of raw points."""
    rng = np.random.default_rng(seed)
    rows = []
    for pos in SCORING_POSITIONS:
        n_per_year = TIER_SIZES[pos] * 2
        frames = []
        for yr, yr_df in pooled[pooled["position"] == pos].groupby("year"):
            frames.append(yr_df.nlargest(n_per_year, "points"))
        sub = pd.concat(frames) if frames else pooled.iloc[0:0]
        sub = sub[(sub["salary"] > LEAGUE_MIN_SALARY) & (sub["par"] > 0)].copy()
        if len(sub) < 25:
            rows.append({"position": pos, "n": len(sub), "power_points": None, "power_par": None})
            continue
        idx = np.arange(len(sub))
        rng.shuffle(idx)
        folds = np.array_split(idx, k_folds)
        results = {}
        for label, xcol in (("power_points", "points"), ("power_par", "par")):
            errs = []
            x = sub[xcol].values
            y = sub["salary"].values
            for f in folds:
                mask = np.ones(len(sub), dtype=bool)
                mask[f] = False
                try:
                    pred = _fit_predict("power", x[mask], y[mask], x[~mask])
                    errs.append(np.mean(np.abs(pred - y[~mask])))
                except Exception:  # noqa: BLE001
                    errs.append(np.nan)
            results[label] = round(float(np.nanmean(errs)))
        rows.append({"position": pos, "n": len(sub), **results, "winner": min(results, key=results.get)})
    return pd.DataFrame(rows)


# ── (d) PK/Def reliability ───────────────────────────────────────────────

def reliability(pooled: pd.DataFrame) -> pd.DataFrame:
    """Variance of salary within productive tail. High variance + low slope = noise."""
    rows = []
    for pos in SCORING_POSITIONS:
        # Use the same productive-tail filter as the production model
        n_per_year = TIER_SIZES[pos] * 2
        frames = []
        for yr, yr_df in pooled[pooled["position"] == pos].groupby("year"):
            frames.append(yr_df.nlargest(n_per_year, "points"))
        sub = pd.concat(frames) if frames else pooled.iloc[0:0]
        sub = sub[(sub["salary"] > LEAGUE_MIN_SALARY) & (sub["points"] > 0)]
        if len(sub) < 5:
            continue
        cv_salary = sub["salary"].std() / sub["salary"].mean()
        # R^2 of power-law fit
        lx = np.log(sub["points"].values)
        ly = np.log(sub["salary"].values)
        k, log_c = np.polyfit(lx, ly, 1)
        ly_pred = k * lx + log_c
        ss_res = float(np.sum((ly - ly_pred) ** 2))
        ss_tot = float(np.sum((ly - ly.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        rows.append({
            "position": pos,
            "n": len(sub),
            "k": round(float(k), 2),
            "r2": round(r2, 3),
            "salary_cv": round(float(cv_salary), 2),
            "median_salary": int(sub["salary"].median()),
            # PK/Def: low k AND low salary variance -> truly commoditized, no signal.
            # Skill positions: low R^2 reflects huge year-over-year point variance, not lack
            # of signal. Persistence check (a) is the decisive test for those.
            "verdict": "COMMODITIZED — exclude from surplus" if abs(k) < 0.25 and float(cv_salary) < 0.7 else "OK (rely on persistence test)",
        })
    return pd.DataFrame(rows)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=int, nargs="+", required=True)
    p.add_argument("--history-start", type=int, default=2017)
    args = p.parse_args()

    print(f"Loading historical bids {args.history_start}..{max(args.years)}...", file=sys.stderr)
    history = mfl.HistoricalBids.load(args.history_start, max(args.years))

    per_year_raw: dict[int, pd.DataFrame] = {}
    for y in args.years:
        per_year_raw[y] = build_season_dataframe(y, history)

    pooled = pd.concat(per_year_raw.values(), ignore_index=True)
    fits = fit_position_market(pooled)

    # Per-year enriched dataframes (using single shared market fit on all pooled data)
    per_year: dict[int, pd.DataFrame] = {y: apply_market(df, fits) for y, df in per_year_raw.items()}

    # Add PAR to pooled for (c)
    pooled_par = add_par(pooled)

    print("\n=== (a) Year-over-year surplus persistence ===", file=sys.stderr)
    persistence = persistence_check(per_year)
    print(persistence.to_string(index=False))

    print("\n=== (b) Cross-validated MAE: linear vs log-linear vs power ===", file=sys.stderr)
    cv = cv_compare(pooled)
    print(cv.to_string(index=False))

    print("\n=== (c) Power-law on PAR vs raw points (5-fold CV MAE) ===", file=sys.stderr)
    cv_par = cv_compare_par(pooled_par)
    print(cv_par.to_string(index=False))

    print("\n=== (d) PK/Def reliability ===", file=sys.stderr)
    rel = reliability(pooled)
    print(rel.to_string(index=False))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = OUT_DIR / "validation.md"
    lines = [
        f"# Salary Efficiency — Foundation Validation",
        f"Years analyzed: {', '.join(map(str, args.years))}",
        "",
        "## (a) Year-over-year surplus persistence",
        "Pearson and Spearman correlation of per-player surplus across consecutive years",
        "(min 50 pts in both years). r >= 0.3 is meaningful signal; r < 0.1 is noise.",
        "",
        persistence.to_markdown(index=False),
        "",
        "## (b) Cross-validated MAE: linear vs log-linear vs power-law",
        "5-fold CV; lower is better. `winner` = lowest MAE.",
        "",
        cv.to_markdown(index=False),
        "",
        "## (c) Power-law on PAR (points above replacement) vs raw points",
        "Replacement ranks: QB 16, RB 40, WR 48, TE 16. Lower MAE wins.",
        "",
        cv_par.to_markdown(index=False),
        "",
        "## (d) Reliability per position",
        "R² of power-law fit on log-log scale. Low R² + low |k| = noise; exclude from surplus rankings.",
        "",
        rel.to_markdown(index=False),
        "",
    ]
    md.write_text("\n".join(lines))
    print(f"\nWrote {md}", file=sys.stderr)


if __name__ == "__main__":
    main()
