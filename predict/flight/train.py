"""Train and evaluate the flight-destination ranker; write weights, priors and a model card.

    uv run python -m predict.flight.train

Data: military flights in the adsb.lol archive extracts (`data/history/adsb/*.parquet`, see
history/adsb_archive.py), all days concatenated so flights crossing midnight stay whole.
Labels: the airfield a flight landed at (flights.py); flights that left coverage are censored
and never used as labels.

Time split only (§16.4): flights that start before TEST_FROM train the model (the last
archived day is the test set when there are three or more days, otherwise the last 20 % of
flights by start time). Priors (airframe / type / callsign family → landing airfield) only ever
count flights that ended before the snapshot's flight began.

Evaluated per snapshot (every 5 minutes of each test flight) against the baselines an analyst
would use without a model:
- nearest ahead: the reachable airfield with the least effort (distance, penalised off-track);
- return to origin: the take-off airfield when known, else nearest ahead;
- airframe history: where this airframe landed most often before, else nearest ahead.
Top-1 / top-3 accuracy overall and by time to landing. The card says plainly if the model
does not beat them.
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

from live.milclass import MilClassifier
from live.tracks import AirfieldIndex
from pipeline.config import HISTORY_DIR, MODELS_DIR
from pipeline.io import write_json
from predict.flight import features as F
from predict.flight.flights import Flight, endurance_table, segment
from reference.airfields import airfields

log = logging.getLogger("terrestrial")

ARCHIVE = HISTORY_DIR / "adsb"
BUCKETS = [(0, 15), (15, 30), (30, 60), (60, 120), (120, 10_000)]
PARAMS = {
    "max_iter": 300,
    "learning_rate": 0.06,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
    "random_state": 0,
}


def load_positions(days: list[str] | None = None) -> pd.DataFrame:
    paths = sorted(ARCHIVE.glob("*.parquet"))
    if days:
        paths = [p for p in paths if p.stem in days]
    if not paths:
        raise FileNotFoundError(
            f"no archive days in {ARCHIVE}; run: uv run python -m history.adsb_archive --days 7"
        )
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


def describe(fl: Flight, classify: MilClassifier, endurance: dict[str, float]) -> F.Airframe:
    c = classify({"hex": fl.hex, "t": fl.type, "r": fl.reg, "flight": fl.callsign, "dbFlags": 1})
    return F.Airframe(
        hex=fl.hex,
        type=fl.type,
        callsign=fl.callsign,
        role=c.role,
        rotary=c.airframe in ("helicopter", "tiltrotor"),
        country=(c.state_code or "").upper() or None,
        endurance_min=endurance.get(fl.type or "", endurance["*"]),
    )


def flight_rows(fl: Flight, ac: F.Airframe, fields: F.Fields, priors: F.Priors) -> list[pd.DataFrame]:
    pts = fl.points
    ts = pts["ts"].astype("int64").to_numpy() / 1e9
    arrays = [pts[c].to_numpy(dtype=float) for c in ("lat", "lon", "alt_m", "gs_kn", "track")]
    out = []
    for i in F.snapshot_indices(ts):
        kin = F.kinematics(ts, *arrays, i)
        rows = F.snapshot_rows(fields, priors, ac, kin, (ts[i] - ts[0]) / 60, fl.origin)
        rows["t"] = ts[i]
        rows["to_land_min"] = (ts[-1] - ts[i]) / 60
        out.append(rows)
    return out


def build(flights: list[Flight], fields: F.Fields, classify: MilClassifier,
          endurance: dict[str, float]) -> tuple[pd.DataFrame, F.Priors]:  # fmt: skip
    """Rows for every labelled flight, with priors that only know flights ended before it began.
    Returns the rows and the priors over all flights (what serving starts from)."""
    by_start = sorted(flights, key=lambda f: f.start)
    by_end = sorted((f for f in flights if f.landing), key=lambda f: f.end)
    priors, j, frames = F.Priors(), 0, []
    for fid, fl in enumerate(by_start):
        while j < len(by_end) and by_end[j].end < fl.start:
            e = by_end[j]
            priors.add(e.hex, e.type, e.callsign, e.landing)
            j += 1
        if not fl.landing:
            continue
        for k, rows in enumerate(flight_rows(fl, describe(fl, classify, endurance), fields, priors)):
            rows["flight"] = fid
            rows["snap"] = fid * 10_000 + k
            rows["y"] = (rows["cand"] == fl.landing).astype(int)
            rows["start"] = fl.start.timestamp()
            frames.append(rows)
    for e in by_end[j:]:
        priors.add(e.hex, e.type, e.callsign, e.landing)
    if not frames:
        raise RuntimeError("no labelled flights: check the archive and airfield data")
    return pd.concat(frames, ignore_index=True), priors


def normalise(df: pd.DataFrame, score: np.ndarray) -> np.ndarray:
    s = pd.Series(score, index=df.index)
    return (s / s.groupby(df["snap"]).transform("sum")).to_numpy()


def top_k(df: pd.DataFrame, score: np.ndarray, k: int) -> pd.Series:
    """Per snapshot: whether the true airfield is among the k highest scores."""
    d = df[["snap", "y"]].assign(s=score)
    d["rank"] = d.groupby("snap")["s"].rank(ascending=False, method="first")
    return d[d["rank"] <= k].groupby("snap")["y"].max().reindex(df["snap"].unique(), fill_value=0)


def baseline_scores(df: pd.DataFrame) -> dict[str, np.ndarray]:
    ahead = -df["effort_rank"].to_numpy(dtype=float)
    origin = np.where((df["origin_known"] == 1) & (df["is_origin"] == 1), 1000.0, 0.0) + ahead
    history = np.where(df["hex_prior_n"] > 0, df["hex_prior"] * 1000.0, 0.0) + ahead
    return {"nearest ahead": ahead, "return to origin": origin, "airframe history": history}


def evaluate(df: pd.DataFrame, scores: dict[str, np.ndarray]) -> dict:
    snaps = df.groupby("snap")["to_land_min"].first()
    out = {}
    for name, s in scores.items():
        t1, t3 = top_k(df, s, 1), top_k(df, s, 3)
        row = {
            "top1": round(float(t1.mean()), 4),
            "top3": round(float(t3.mean()), 4),
            "by_time_to_landing": {},
        }
        for lo, hi in BUCKETS:
            m = snaps[(snaps >= lo) & (snaps < hi)].index
            if len(m):
                label = f"{lo}–{hi} min" if hi < 10_000 else f"≥ {lo} min"
                row["by_time_to_landing"][label] = {
                    "n": len(m),
                    "top1": round(float(t1.loc[m].mean()), 4),
                    "top3": round(float(t3.loc[m].mean()), 4),
                }
        out[name] = row
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", nargs="*", help="archive days to use (default: all)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    started = time.monotonic()

    positions = load_positions(args.days)
    days = sorted(positions["ts"].dt.date.astype(str).unique())
    fields_list = airfields()
    flights = segment(positions, AirfieldIndex(fields_list))
    labelled = [f for f in flights if f.landing]
    log.info("%d days, %d flights, %d with a landing airfield", len(days), len(flights), len(labelled))

    starts = pd.Series([f.start for f in flights])
    test_from = pd.Timestamp(days[-1], tz="UTC") if len(days) >= 3 else starts.quantile(0.8)
    train_flights = [f for f in flights if f.start < test_from]
    endurance = endurance_table(train_flights)
    fields = F.Fields(fields_list)
    classify = MilClassifier.load()
    df, priors = build(flights, fields, classify, endurance)
    train = df[df["start"] < test_from.timestamp()]
    test = df[df["start"] >= test_from.timestamp()]
    coverage = float(test.groupby("snap")["y"].max().mean())
    log.info("rows: train %d (%d snapshots), test %d (%d snapshots); truth among candidates %.1f%%",
             len(train), train["snap"].nunique(), len(test), test["snap"].nunique(), 100 * coverage)  # fmt: skip

    model = HistGradientBoostingClassifier(**PARAMS)
    model.fit(train[F.FEATURES], train["y"])
    p = normalise(test, model.predict_proba(test[F.FEATURES])[:, 1])
    results = {"model": evaluate(test, {"model": p})["model"]} | evaluate(test, baseline_scores(test))
    truth = p[test["y"].to_numpy() == 1]
    log_loss = float(-np.log(np.clip(truth, 1e-6, 1)).mean())
    beats = all(
        results["model"][k] > results[b][k] for b in results if b != "model" for k in ("top1", "top3")
    )

    sample_snaps = test["snap"].drop_duplicates().sample(min(3000, test["snap"].nunique()), random_state=0)
    sample = test[test["snap"].isin(sample_snaps)]
    imp = permutation_importance(
        model, sample[F.FEATURES], sample["y"], scoring="average_precision", n_repeats=3, random_state=0
    )
    importance = sorted(
        (
            {"feature": f, "ap_drop": round(float(m), 4)}
            for f, m in zip(F.FEATURES, imp.importances_mean, strict=True)
        ),
        key=lambda r: -r["ap_drop"],
    )

    version = time.strftime("%Y%m%d-%H%M%S")
    out = MODELS_DIR / "flight" / version
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"model": model, "features": F.FEATURES, "endurance": endurance, "priors": priors.to_dict()},
        out / "model.joblib",
    )
    test_flights = [f for f in labelled if f.start >= test_from]
    card = {
        "name": "Military flight destination",
        "target_id": "destination",
        "version": version,
        "target": "The airfield a military flight will land at, given its observed track so far.",
        "unit": "flight snapshot every 5 minutes after take-off (last 5 minutes excluded) × candidate airfield",
        "data": {
            "positions": f"adsb.lol globe_history daily archive (ODbL), readsb military flag, inside the theatre box; days {days[0]} → {days[-1]}",
            "airfields": "OurAirports (public domain)",
            "labels": "landing airfield: last point on the ground, or low and descending, within 6 km of an airfield; flights that leave coverage are censored and not used",
        },
        "split": {"train": f"flights starting < {test_from}", "test": f"flights starting ≥ {test_from}"},
        "rows": {
            "train": len(train),
            "test": len(test),
            "train_flights": int(train["flight"].nunique()),
            "test_flights": int(test["flight"].nunique()),
            "test_snapshots": int(test["snap"].nunique()),
        },
        "candidate_coverage_test": round(coverage, 4),
        "algorithm": f"scikit-learn HistGradientBoostingClassifier {PARAMS} over (snapshot, candidate) rows; probabilities normalised within each snapshot",
        "features": F.FEATURES,
        "test_scores": results,
        "test_log_loss_true_airfield": round(log_loss, 4),
        "beats_baselines": beats,
        "permutation_importance_test": importance,
        "endurance_minutes_p95": {k: round(v) for k, v in sorted(endurance.items())},
        "test_landings_by_airfield": pd.Series([f.landing for f in test_flights])
        .value_counts()
        .head(15)
        .to_dict(),
        "limitations": [
            "Only aircraft that broadcast ADS-B and carry the military flag; Russian military aviation is not in the data.",
            f"Trained on {len(days)} archived day(s); rare destinations and airframes seen once are weakly learned.",
            "Endurance is the 95th percentile of observed flight durations per type, not fuel: tanker support or diversions are not observed.",
            "Flights that leave receiver coverage (over the sea, over Russia/Belarus) have no label and are excluded, so long over-water missions are under-represented.",
            "Carriers are not candidates unless observed live (none were in the training data).",
        ],
    }
    write_json(out / "model_card.json", card)
    write_json(MODELS_DIR / "flight" / "latest.json", {"version": version})
    log.info(
        "test scores:\n%s",
        json.dumps({k: {m: v[m] for m in ("top1", "top3")} for k, v in results.items()}, indent=1),
    )
    log.info("beats baselines: %s; top features: %s", beats, importance[:8])
    log.info("model %s written in %.0fs", version, time.monotonic() - started)


if __name__ == "__main__":
    main()
