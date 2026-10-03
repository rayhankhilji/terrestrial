"""Live destination and path prediction for airborne military aircraft (CLAUDE.md §16.1, M6).

For each airborne aircraft with a track in the live track store, the trained ranker
(predict/flight/train.py) scores candidate airfields from the current flight's history, using
exactly the training feature code (features.py). The result is:

- destinations: the top airfields with probabilities (normalised over the candidates);
- for each, a predicted path: great circle from the current position at the current altitude,
  then a 3:1 descent to the field, with an ETA from ground speed corrected for winds aloft
  (Open-Meteo pressure-level forecast at the aircraft's level, when available);
- an endurance estimate: time airborne against the type's 95th percentile flight duration
  (a type-level statistic, never fuel; labelled as an estimate);
- on station: an aircraft that has turned through more than ORBIT_TURN_DEG in 15 minutes
  while making little net progress is flagged as orbiting.

Priors (where this airframe, type and callsign family landed before) start from the archive
and grow with every landing the live track store observes.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field

import httpx
import joblib
import numpy as np

from pipeline.config import MODELS_DIR
from pipeline.io import read_json
from predict.flight import features as F
from reference.airfields import Airfield

log = logging.getLogger("terrestrial.live")

TOP_N = 3
ORBIT_TURN_DEG = 540.0
ORBIT_STRAIGHTNESS = 0.35
DESCENT_KM_PER_KM = 3.0 * 1.852 / 1000 * 3.28084  # 3 nm per 1000 ft: km of track per m of height
MAX_AGE_S = 120
WIND_URL = "https://api.open-meteo.com/v1/forecast"
LEVELS = (925, 850, 700, 500, 300, 250)  # hPa


@dataclass
class FlightModel:
    version: str
    card: dict
    model: object
    endurance: dict[str, float]
    priors: F.Priors

    @staticmethod
    def latest_version() -> str | None:
        latest = MODELS_DIR / "flight" / "latest.json"
        return read_json(latest)["version"] if latest.exists() else None

    @classmethod
    def load(cls) -> FlightModel | None:
        version = cls.latest_version()
        if version is None:
            return None
        folder = MODELS_DIR / "flight" / version
        blob = joblib.load(folder / "model.joblib")
        if blob["features"] != F.FEATURES:
            raise RuntimeError(f"flight model {version} was trained on different features; retrain it")
        return cls(
            version, read_json(folder / "model_card.json"), blob["model"], blob["endurance"],
            F.Priors.from_dict(blob["priors"]),
        )  # fmt: skip


def pressure_level(alt_m: float) -> int:
    """Nearest available pressure level to an altitude (ISA)."""
    p = 1013.25 * (1 - 2.25577e-5 * max(alt_m, 0.0)) ** 5.25588
    return min(LEVELS, key=lambda lv: abs(lv - p))


def tailwind(speed_kn: float, from_deg: float, bearing: float) -> float:
    """Component of a wind blowing FROM `from_deg` along a course of `bearing` (kn, + = tail)."""
    return speed_kn * math.cos(math.radians((from_deg + 180) - bearing))


def parse_winds(payload: dict) -> dict:
    """Open-Meteo hourly pressure-level response → {level: (hours[epoch s], speed kn, from deg)}."""
    hourly = payload["hourly"]
    if payload.get("hourly_units", {}).get(f"wind_speed_{LEVELS[0]}hPa") not in ("kn", None):
        raise ValueError("winds aloft must be requested in knots")
    hours = np.array([np.datetime64(t, "s").astype("int64") for t in hourly["time"]])
    out = {}
    for lv in LEVELS:
        sp, dr = hourly.get(f"wind_speed_{lv}hPa"), hourly.get(f"wind_direction_{lv}hPa")
        if sp is None or dr is None:
            raise ValueError(f"winds aloft response has no {lv} hPa level")
        out[lv] = (hours, np.array(sp, dtype=float), np.array(dr, dtype=float))
    return out


class Winds:
    """Winds aloft per 1° cell, fetched on demand and cached for an hour."""

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=10.0)
        self.cache: dict[tuple[int, int], tuple[float, dict]] = {}

    def at(
        self, lon: float, lat: float, alt_m: float, at_s: float | None = None
    ) -> tuple[float, float] | None:
        cell = (round(lon), round(lat))
        hit = self.cache.get(cell)
        if hit is None or time.time() - hit[0] > 3600:
            try:
                r = self.http.get(
                    WIND_URL,
                    params={
                        "latitude": cell[1], "longitude": cell[0], "wind_speed_unit": "kn",
                        "hourly": ",".join(f"wind_speed_{lv}hPa,wind_direction_{lv}hPa" for lv in LEVELS),
                        "forecast_days": 2, "past_days": 1, "timezone": "UTC",
                    },
                )  # fmt: skip
                r.raise_for_status()
                hit = (time.time(), parse_winds(r.json()))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                log.warning("winds aloft unavailable at %s: %s", cell, exc)
                return None
            self.cache[cell] = hit
        hours, sp, dr = hit[1][pressure_level(alt_m)]
        i = int(np.argmin(np.abs(hours - (at_s or time.time()))))
        if np.isnan(sp[i]) or np.isnan(dr[i]):
            return None
        return float(sp[i]), float(dr[i])


def great_circle(lon1: float, lat1: float, lon2: float, lat2: float, n: int) -> np.ndarray:
    """n points (lon, lat) along the great circle, endpoints included."""
    p1 = np.radians([lat1, lon1])
    p2 = np.radians([lat2, lon2])
    a = np.array([math.cos(p1[0]) * math.cos(p1[1]), math.cos(p1[0]) * math.sin(p1[1]), math.sin(p1[0])])
    b = np.array([math.cos(p2[0]) * math.cos(p2[1]), math.cos(p2[0]) * math.sin(p2[1]), math.sin(p2[0])])
    omega = math.acos(float(np.clip(a @ b, -1, 1)))
    t = np.linspace(0, 1, n)
    if omega < 1e-9:
        return np.column_stack([np.full(n, lon1), np.full(n, lat1)])
    v = (np.sin((1 - t) * omega)[:, None] * a + np.sin(t * omega)[:, None] * b) / math.sin(omega)
    return np.column_stack([np.degrees(np.arctan2(v[:, 1], v[:, 0])), np.degrees(np.arcsin(v[:, 2]))])


def predicted_path(lon: float, lat: float, alt_m: float, dest: Airfield, dist_km: float, gs_kn: float,
                   n: int = 24) -> list[list[float]]:  # fmt: skip
    """[lon, lat, alt_m, seconds from now] along the great circle, holding altitude until the
    top of descent, then descending on a 3:1 profile to the field elevation."""
    pts = great_circle(lon, lat, dest.lon, dest.lat, n)
    field_alt = dest.elevation_m or 0.0
    descent_km = max(alt_m - field_alt, 0.0) * DESCENT_KM_PER_KM
    speed = max(gs_kn, 60.0) * 1.852 / 3600  # km/s
    out = []
    for k, (plon, plat) in enumerate(pts):
        along = dist_km * k / (n - 1)
        to_go = dist_km - along
        alt = field_alt + min(max(alt_m - field_alt, 0.0), to_go / DESCENT_KM_PER_KM) if descent_km else alt_m
        out.append([round(float(plon), 5), round(float(plat), 5), round(alt), round(along / speed)])
    return out


@dataclass
class Prediction:
    at: int
    destinations: list[dict]
    on_station: bool
    elapsed_min: float
    endurance_min: float
    origin: str | None
    candidates: int
    winds: dict | None = None
    changes: list[dict] = field(default_factory=list)

    def compact(self) -> dict:
        top = self.destinations[0]
        return {
            "dest": top["ident"],
            "dest_name": top["name"],
            "dest_icao": top["icao"],
            "dest_lon": top["lon"],
            "dest_lat": top["lat"],
            "p": top["p"],
            "eta_min": top["eta_min"],
            "alts": [{"ident": d["ident"], "p": d["p"]} for d in self.destinations[1:]],
            "on_station": self.on_station,
            "endurance_left_min": round(max(self.endurance_min - self.elapsed_min, 0.0)),
            "at": self.at,
        }


class Predictor:
    """Scores destinations for live aircraft from the track store's current flight."""

    def __init__(self, model: FlightModel, fields: F.Fields, airfields: dict[str, Airfield],
                 winds: Winds | None = None):  # fmt: skip
        self.model = model
        self.fields = fields
        self.airfields = airfields
        self.winds = winds
        self.history: dict[str, deque] = {}
        self.last: dict[str, tuple[str, float]] = {}
        self.learned: set[tuple[str, int]] = set()

    def learn(self, hex_: str, type_: str | None, callsign: str | None, landing: str, end_ms: int) -> bool:
        """Add an observed live landing to the priors (once per flight)."""
        key = (hex_, end_ms)
        if key in self.learned or landing not in self.fields.index:
            return False
        self.learned.add(key)
        self.model.priors.add(hex_, type_, callsign, landing)
        return True

    def airframe(self, e: dict) -> F.Airframe:
        p = e.get("props") or {}
        t = p.get("type")
        return F.Airframe(
            hex=(p.get("icao24") or e["id"].split(":", 1)[-1]).lower(),
            type=t,
            callsign=p.get("callsign"),
            role=p.get("role") or "unknown",
            rotary=p.get("airframe") in ("helicopter", "tiltrotor"),
            country=(p.get("state_code") or "").upper() or None,
            endurance_min=self.model.endurance.get(t or "", self.model.endurance["*"]),
        )

    def predict(
        self, e: dict, flight: dict, now_ms: int, winds: bool = False, paths: bool = False
    ) -> Prediction | None:
        pts = flight["points"]  # [ts ms, lon, lat, alt, spd, hdg, ground]
        if len(pts) < 3 or pts[-1][6] or now_ms - pts[-1][0] > MAX_AGE_S * 1000:
            return None
        arr = np.array([[r[0] / 1000, r[2], r[1], r[3], np.nan if r[4] is None else r[4],
                         np.nan if r[5] is None else r[5]] for r in pts], dtype=float)  # fmt: skip
        ts, lat, lon, alt, gs, trk = arr.T
        i = len(ts) - 1
        kin = F.kinematics(ts, lat, lon, alt, gs, trk, i)
        ac = self.airframe(e)
        origin = (flight.get("origin") or {}).get("ident")
        elapsed = (ts[i] - ts[0]) / 60
        rows = F.snapshot_rows(self.fields, self.model.priors, ac, kin, elapsed, origin)
        score = self.model.model.predict_proba(rows[F.FEATURES])[:, 1]
        p = score / score.sum()
        order = np.argsort(-p)[:TOP_N]
        wind = self.winds.at(kin["lon"], kin["lat"], kin["alt_m"]) if (winds and self.winds) else None
        dests = []
        for j in order:
            a = self.airfields[rows["cand"].iloc[j]]
            dist = float(rows["dist_km"].iloc[j])
            course = _bearing(kin["lon"], kin["lat"], a.lon, a.lat)
            gs_route = kin["gs_kn"]
            if wind is not None:
                # Ground speed now includes the wind along the current track; re-apply it along the route.
                tas = kin["gs_kn"] - tailwind(*wind, kin["track"])
                gs_route = max(tas + tailwind(*wind, course), 60.0)
            d = {
                "ident": a.ident, "name": a.name, "icao": a.icao, "lon": a.lon, "lat": a.lat,
                "country": a.country, "military": a.military, "p": round(float(p[j]), 4),
                "dist_km": round(dist, 1), "course": round(course), "gs_route_kn": round(gs_route),
                "eta_min": round(dist / (max(gs_route, 60.0) * 1.852) * 60, 1),
            }  # fmt: skip
            if paths:
                d["path"] = predicted_path(kin["lon"], kin["lat"], kin["alt_m"], a, dist, gs_route)
            dests.append(d)
        on_station = kin["turn_15"] >= ORBIT_TURN_DEG and kin["straightness"] <= ORBIT_STRAIGHTNESS
        pred = Prediction(
            at=int(pts[-1][0]), destinations=dests, on_station=on_station, elapsed_min=round(elapsed, 1),
            endurance_min=round(ac.endurance_min), origin=origin, candidates=len(rows),
            winds=None if wind is None else {"level_hpa": pressure_level(kin["alt_m"]), "speed_kn": wind[0], "from_deg": wind[1]},
        )  # fmt: skip
        pred.changes = self._changes(e["id"], pred)
        return pred

    def _changes(self, eid: str, pred: Prediction) -> list[dict]:
        """Record when the most likely destination changes ("re-routed")."""
        top = pred.destinations[0]
        hist = self.history.setdefault(eid, deque(maxlen=20))
        prev = self.last.get(eid)
        if prev is not None and prev[0] != top["ident"]:
            hist.appendleft(
                {"at": pred.at, "from": prev[0], "to": top["ident"], "p_from": prev[1], "p_to": top["p"]}
            )
        self.last[eid] = (top["ident"], top["p"])
        return list(hist)


def _bearing(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360
