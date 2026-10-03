"""Train, calibrate and evaluate the air-raid danger model; write weights and a model card.

    uv run python -m predict.strike.train            # both targets
    uv run python -m predict.strike.train --target new

Targets (see features.py): `active` (an alert is active in the next 6 h) and `new` (a new alert
starts in the next 6 h; the warning signal, harder because an ongoing alert gives it away less).

Time-split only (§16.4): fit on issue times before VALID_FROM, calibrate (isotonic) on
VALID_FROM–TEST_FROM, report on TEST_FROM onwards. The model is compared with two baselines
that a skeptical analyst would use instead:
- climatology: the region's historical rate for that block of the day (training period);
- persistence: the training-period rate given whether the region had an alert in the last block.
The card states plainly whether the model beats them.
"""

from __future__ import annotations

import argparse
import json
import logging
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from history.regions import BY_ISO, boundaries
from pipeline.config import HISTORY_DIR, MODELS_DIR
from pipeline.io import read_table, write_json
from predict.strike import features as F

log = logging.getLogger("terrestrial")

VALID_FROM = pd.Timestamp("2025-07-01", tz="UTC")
TEST_FROM = pd.Timestamp("2026-01-01", tz="UTC")
TARGETS = {
    "active": (
        "y_active",
        "Air-raid alert active, next 6 hours",
        "Any air-raid alert active in the region during the next 6-hour UTC block.",
    ),
    "new": (
        "y_new",
        "New air-raid alert, next 6 hours",
        "A new air-raid alert starts in the region during the next 6-hour UTC block.",
    ),
}
PARAMS = {
    "max_iter": 400,
    "learning_rate": 0.05,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 60,
    "l2_regularization": 1.0,
    "random_state": 0,
}


def load_frame() -> tuple[pd.DataFrame, pd.Timestamp]:
    alerts = read_table("alerts", HISTORY_DIR)
    viina = read_table("viina_events", HISTORY_DIR)
    weather = read_table("weather_daily", HISTORY_DIR)
    cutoff = alerts["start"].max()
    # Last issue block whose target block is fully observed before the data cutoff.
    end = cutoff.floor(f"{F.BLOCK_H}h") - pd.Timedelta(hours=F.BLOCK_H)
    frame = F.build(alerts, viina, weather, boundaries(), end)
    frame = frame.dropna(subset=["cloud", "precip", "wind"])  # weather archive ends yesterday
    return frame, cutoff


def baselines(train: pd.DataFrame, test: pd.DataFrame, y: str) -> dict[str, np.ndarray]:
    clim = train.groupby(["iso", "block_of_day"])[y].mean()
    persist = train.assign(was=train["m_1"] > 0).groupby(["iso", "was"])[y].mean()
    return {
        "climatology": clim.reindex(pd.MultiIndex.from_frame(test[["iso", "block_of_day"]])).to_numpy(),
        "persistence": persist.reindex(pd.MultiIndex.from_arrays([test["iso"], test["m_1"] > 0])).to_numpy(),
    }


def scores(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {
        "brier": round(float(brier_score_loss(y, p)), 4),
        "log_loss": round(float(log_loss(y, p)), 4),
        "roc_auc": round(float(roc_auc_score(y, p)), 4),
        "pr_auc": round(float(average_precision_score(y, p)), 4),
    }


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    out = []
    for b in range(bins):
        m = idx == b
        if m.sum():
            out.append(
                {
                    "bin": f"{edges[b]:.1f}–{edges[b + 1]:.1f}",
                    "n": int(m.sum()),
                    "predicted": round(float(p[m].mean()), 3),
                    "observed": round(float(y[m].mean()), 3),
                }
            )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(TARGETS), action="append")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    frame, cutoff = load_frame()
    for target in args.target or sorted(TARGETS):
        train_one(frame, cutoff, target)


def train_one(frame: pd.DataFrame, cutoff: pd.Timestamp, target: str) -> None:
    ycol, title, definition = TARGETS[target]
    started = time.monotonic()
    train = frame[frame["issue"] < VALID_FROM]
    valid = frame[(frame["issue"] >= VALID_FROM) & (frame["issue"] < TEST_FROM)]
    test = frame[frame["issue"] >= TEST_FROM]
    log.info("rows: train %d, calibrate %d, test %d (cutoff %s)", len(train), len(valid), len(test), cutoff)

    model = HistGradientBoostingClassifier(
        categorical_features=[F.FEATURES.index(c) for c in F.CATEGORICAL], **PARAMS
    )
    model.fit(train[F.FEATURES], train[ycol])
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(model.predict_proba(valid[F.FEATURES])[:, 1], valid[ycol])

    y = test[ycol].to_numpy()
    raw = model.predict_proba(test[F.FEATURES])[:, 1]
    p = iso.predict(raw)
    base = baselines(train, test, ycol)
    results = {"model (calibrated)": scores(y, p), "model (uncalibrated)": scores(y, raw)}
    results |= {name: scores(y, b) for name, b in base.items()}
    bss = {
        name: round(1 - results["model (calibrated)"]["brier"] / results[name]["brier"], 4) for name in base
    }
    beats = all(v > 0 for v in bss.values()) and all(
        results["model (calibrated)"]["roc_auc"] > results[name]["roc_auc"] for name in base
    )

    per_region = {}
    for iso_code, g in test.groupby("iso"):
        if g[ycol].nunique() == 2:
            per_region[iso_code] = {
                "name": BY_ISO[iso_code].name,
                "n": len(g),
                "rate": round(float(g[ycol].mean()), 3),
                "roc_auc": round(
                    float(roc_auc_score(g[ycol], iso.predict(model.predict_proba(g[F.FEATURES])[:, 1]))), 3
                ),
            }

    sample = test.sample(min(20000, len(test)), random_state=0)
    imp = permutation_importance(
        model, sample[F.FEATURES], sample[ycol], scoring="roc_auc", n_repeats=3, random_state=0
    )
    importance = sorted(
        (
            {"feature": f, "roc_auc_drop": round(float(m), 4)}
            for f, m in zip(F.FEATURES, imp.importances_mean, strict=True)
        ),
        key=lambda r: -r["roc_auc_drop"],
    )

    version = time.strftime("%Y%m%d-%H%M%S")
    out = MODELS_DIR / "strike" / target / version
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"model": model, "calibrator": iso, "features": F.FEATURES, "regions": F.MODEL_REGIONS},
        out / "model.joblib",
    )
    card = {
        "name": title,
        "target_id": target,
        "version": version,
        "target": definition,
        "unit": "region × 6-hour block; issued at the end of each block",
        "regions": {r: BY_ISO[r].name for r in F.MODEL_REGIONS},
        "excluded_regions": "Crimea, Sevastopol and Luhansk: no air-raid alert data (occupied).",
        "data": {
            "alerts": "Vadimkin/ukrainian-air-raid-sirens-dataset volunteer_data_en.csv, 2022-02-25 → cutoff",
            "viina": f"VIINA 2.0 air attacks (air strike or air defence), lagged {F.VIINA_LAG_DAYS} days",
            "weather": "Open-Meteo archive (observed) in training; forecast in live use",
            "cutoff": str(cutoff),
        },
        "split": {
            "train": f"< {VALID_FROM.date()}",
            "calibration": f"{VALID_FROM.date()} → {TEST_FROM.date()}",
            "test": f"≥ {TEST_FROM.date()}",
        },
        "rows": {"train": len(train), "calibration": len(valid), "test": len(test)},
        "test_base_rate": round(float(y.mean()), 4),
        "algorithm": f"scikit-learn HistGradientBoostingClassifier {PARAMS}, isotonic calibration",
        "features": F.FEATURES,
        "test_scores": results,
        "brier_skill_vs": bss,
        "beats_baselines": beats,
        "reliability_test": reliability(y, p),
        "per_region_test": per_region,
        "permutation_importance_test": importance,
        "limitations": [
            "Predicts air-raid alerts, i.e. that Ukraine's air defence detected a threat to the region, not that a strike will land or where.",
            "Learns from 2022–2025 patterns; new tactics or a ceasefire change the base rates and the model will lag behind.",
            "Live alert history between the daily dataset refresh and now comes from Terrestrial's own alert poller; if it was not running, recent features are incomplete and the prediction is flagged.",
            "Weather is observed in training but forecast in live use.",
            "VIINA counts come from news reports, whose volume changes over time.",
        ],
    }
    write_json(out / "model_card.json", card)
    write_json(MODELS_DIR / "strike" / target / "latest.json", {"version": version})
    log.info("[%s] test scores:\n%s", target, json.dumps(results, indent=1))
    log.info("Brier skill vs baselines: %s; beats baselines: %s", bss, beats)
    log.info("top features: %s", importance[:8])
    log.info("model %s written in %.0fs", version, time.monotonic() - started)


if __name__ == "__main__":
    main()
