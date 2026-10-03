"""Live air-raid danger forecasts from the trained models (CLAUDE.md §16.4).

Forecasts use the training unit exactly: at each 6-hour UTC boundary the models are issued for
the block that has just begun, from complete data up to that boundary. Inputs:
- alerts: the sirens dataset (refreshed daily) merged with Terrestrial's own alert poller log,
  which covers the hours since the dataset's last refresh. If the poller was not watching for
  part of that gap, the forecast is flagged `degraded` with the reason;
- VIINA air attacks (history table, lag already built into the features);
- weather: the Open-Meteo daily forecast for the target day at each region's point.

Each region also gets drivers: for the most influential features, how much the probability
changes if that feature is set to the region's median of the previous 30 days ("why is it
higher or lower than usual for this region").
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
import joblib
import numpy as np
import pandas as pd

from history.regions import BY_ISO, boundaries
from live.sources.alerts import read_log
from pipeline.config import HISTORY_DIR, MODELS_DIR
from pipeline.io import read_json, read_table
from predict.strike import features as F

log = logging.getLogger("terrestrial.live")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
LOOKBACK_DAYS = 45  # enough for the 30-day alert window and the lagged 28-day VIINA window
MAX_UNWATCHED_MIN = 10
P_MIN = 0.005
DRIVER_FEATURES = [
    "m_1",
    "m_4",
    "m_28",
    "m_120",
    "starts_4",
    "active_at_issue",
    "since_last_h",
    "nb_m_1",
    "nat_regions_1",
    "nat_regions_4",
    "viina_region_7d",
    "cloud",
    "wind",
    "moon",
]
FEATURE_LABELS = {
    "m_1": "alert minutes, last 6 h",
    "m_4": "alert minutes, last 24 h",
    "m_28": "alert minutes, last 7 days",
    "m_120": "alert minutes, last 30 days",
    "starts_4": "alerts started, last 24 h",
    "active_at_issue": "alert active at issue time",
    "since_last_h": "hours since the last alert ended",
    "nb_m_1": "neighbouring regions' alert minutes, last 6 h",
    "nat_regions_1": "regions under alert, last 6 h",
    "nat_regions_4": "region-blocks under alert nationally, last 24 h",
    "viina_region_7d": "reported air attacks in the region (VIINA, lagged)",
    "cloud": "cloud cover forecast",
    "wind": "max wind forecast",
    "moon": "moon illumination",
}


@dataclass
class Model:
    target: str
    version: str
    card: dict
    model: object
    calibrator: object

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        # Isotonic steps can reach exactly 0 or 1; no forecast of this kind is that certain.
        return np.clip(
            self.calibrator.predict(self.model.predict_proba(X[F.FEATURES])[:, 1]), P_MIN, 1 - P_MIN
        )


def load_models() -> dict[str, Model]:
    out = {}
    for target in ("new", "active"):
        latest = MODELS_DIR / "strike" / target / "latest.json"
        if not latest.exists():
            continue
        version = read_json(latest)["version"]
        folder = MODELS_DIR / "strike" / target / version
        blob = joblib.load(folder / "model.joblib")
        if blob["features"] != F.FEATURES:
            raise RuntimeError(f"model {target}/{version} was trained on different features; retrain it")
        out[target] = Model(
            target, version, read_json(folder / "model_card.json"), blob["model"], blob["calibrator"]
        )
    return out


def merge_intervals(df: pd.DataFrame) -> pd.DataFrame:
    """Union of overlapping alert intervals per region (dataset and poller overlap)."""
    rows = []
    for iso, g in df.sort_values(["iso", "start"]).groupby("iso"):
        cur_s = cur_e = None
        for s, e in zip(g["start"], g["end"], strict=True):
            if cur_s is None or s > cur_e:
                if cur_s is not None:
                    rows.append((iso, cur_s, cur_e))
                cur_s, cur_e = s, e
            else:
                cur_e = max(cur_e, e)
        if cur_s is not None:
            rows.append((iso, cur_s, cur_e))
    out = pd.DataFrame(rows, columns=["iso", "start", "end"])
    out["naive"] = False
    out["minutes"] = (out["end"] - out["start"]).dt.total_seconds() / 60
    return out


def unwatched_minutes(spans: list[tuple[int, int]], start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Minutes in [start, end] not covered by the poller's watching spans."""
    a, b = start.value // 1_000_000, end.value // 1_000_000
    covered = 0
    for s, e in spans:
        lo, hi = max(a, s), min(b, e)
        if hi > lo:
            covered += hi - lo
    return max(0.0, (b - a - covered) / 60_000)


@dataclass
class DangerService:
    models: dict[str, Model] = field(default_factory=dict)
    forecast: pd.DataFrame | None = None
    forecast_at: float = 0.0
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=30.0))

    def __post_init__(self) -> None:
        self.models = load_models()

    @property
    def available(self) -> bool:
        return bool(self.models)

    def weather(self, isos: list[str]) -> pd.DataFrame:
        if self.forecast is not None and time.time() - self.forecast_at < 3600:
            return self.forecast
        geoms = boundaries()
        response = self.http.get(
            FORECAST_URL,
            params={
                "latitude": ",".join(str(geoms[i].lat) for i in isos),
                "longitude": ",".join(str(geoms[i].lon) for i in isos),
                "daily": "cloud_cover_mean,precipitation_sum,wind_speed_10m_max",
                "timezone": "UTC",
                "forecast_days": 2,
                "past_days": 1,
            },
        )
        response.raise_for_status()
        payload = response.json()
        payload = payload if isinstance(payload, list) else [payload]
        frames = [
            pd.DataFrame(
                {
                    "iso": iso,
                    "date": pd.to_datetime(loc["daily"]["time"]),
                    **{
                        k: loc["daily"][k]
                        for k in ("cloud_cover_mean", "precipitation_sum", "wind_speed_10m_max")
                    },
                }
            )
            for iso, loc in zip(isos, payload, strict=True)
        ]
        self.forecast, self.forecast_at = pd.concat(frames, ignore_index=True), time.time()
        return self.forecast

    def compute(self, now: pd.Timestamp | None = None) -> dict:
        now = now or pd.Timestamp.now(tz="UTC")
        issue = now.floor(f"{F.BLOCK_H}h")
        last_block = issue - pd.Timedelta(hours=F.BLOCK_H)
        origin = (issue - pd.Timedelta(days=LOOKBACK_DAYS)).floor("D")

        dataset = read_table("alerts", HISTORY_DIR)
        cutoff = dataset["start"].max()
        polled, spans = read_log()
        recent = dataset[dataset["end"] > origin]
        merged = merge_intervals(
            pd.concat([recent[["iso", "start", "end"]], polled[["iso", "start", "end"]]], ignore_index=True)
        )
        gap = unwatched_minutes(spans, cutoff, issue)
        degraded = []
        if gap > MAX_UNWATCHED_MIN:
            degraded.append(
                f"alert history between the dataset refresh ({cutoff:%d %b %H:%M} UTC) and {issue:%H:%M} UTC has {gap:.0f} unwatched minutes; recent-alert features may be too low"
            )

        viina = read_table("viina_events", HISTORY_DIR)
        viina_lag = (issue.tz_convert(None).normalize() - viina["date"].max()).days
        if viina_lag > F.VIINA_LAG_DAYS + 2:
            degraded.append(f"VIINA is {viina_lag} days behind (features assume ≤ {F.VIINA_LAG_DAYS})")

        frame = F.build(
            merged,
            viina,
            self.weather(sorted(BY_ISO)),
            boundaries(),
            last_block,
            origin=origin,
            with_target=False,
        )
        current = frame[frame["issue"] == issue].copy()
        history = frame[(frame["issue"] < issue) & (frame["issue"] >= issue - pd.Timedelta(days=30))]
        if current[["cloud", "precip", "wind"]].isna().any().any():
            raise RuntimeError("weather forecast missing for the target day")

        typical = history.groupby("iso")[DRIVER_FEATURES].median()
        probs = {t: m.predict(current) for t, m in self.models.items()}
        main = self.models.get("new") or next(iter(self.models.values()))
        # Ablations in one batch: each region's row with one feature set to its 30-day median.
        current = current.reset_index(drop=True)
        variants = []
        for feat in DRIVER_FEATURES:
            alt = current.copy()
            alt[feat] = typical.reindex(current["iso"])[feat].to_numpy()
            variants.append(alt)
        ablated = main.predict(pd.concat(variants, ignore_index=True)).reshape(
            len(DRIVER_FEATURES), len(current)
        )
        out_regions = {}
        for i, row in enumerate(current.itertuples(index=False)):
            drivers = []
            for k, feat in enumerate(DRIVER_FEATURES):
                delta = float(probs[main.target][i] - ablated[k, i])
                if abs(delta) >= 0.01:
                    drivers.append(
                        {
                            "feature": feat,
                            "label": FEATURE_LABELS[feat],
                            "value": round(float(getattr(row, feat)), 2),
                            "typical": round(float(typical.loc[row.iso, feat]), 2),
                            "delta_p": round(delta, 3),
                        }
                    )
            drivers.sort(key=lambda d: -abs(d["delta_p"]))
            out_regions[row.iso] = {
                "iso": row.iso,
                "name": BY_ISO[row.iso].name,
                **{f"p_{t}": round(float(p[i]), 4) for t, p in probs.items()},
                "drivers": drivers[:5],
            }
        return {
            "issued": issue.isoformat(),
            "valid_from": issue.isoformat(),
            "valid_to": (issue + timedelta(hours=F.BLOCK_H)).isoformat(),
            "computed": now.isoformat(),
            "dataset_cutoff": cutoff.isoformat(),
            "viina_latest": str(viina["date"].max().date()),
            "degraded": degraded,
            "models": {
                t: {
                    "version": m.version,
                    "name": m.card["name"],
                    "beats_baselines": m.card["beats_baselines"],
                }
                for t, m in self.models.items()
            },
            "regions": out_regions,
        }


def cards() -> dict[str, dict]:
    return {t: m.card for t, m in load_models().items()}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(DangerService().compute(), indent=1, default=str)[:4000])
