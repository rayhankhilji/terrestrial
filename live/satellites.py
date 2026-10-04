"""Imaging satellites overhead: live positions, ground tracks and pass windows (M7).

Orbital elements come from CelesTrak's GP API (OMM JSON, refreshed every ELEMENTS_MAX_AGE_H)
and are propagated with SGP4. Only satellites in CATALOG are tracked: operational imaging
missions with public specifications. Each entry carries the sensor (SAR sees through cloud and
at night; optical needs daylight and clear sky) and an *access radius*: how far from the
ground track the instrument can image (published swath or field of regard, rounded).

A pass is the interval in which a point lies within a satellite's access radius. It is an
**opportunity**, not an acquisition: Sentinel-1/2 follow fixed public plans, commercial SAR
(ICEYE, Capella, Umbra) images only where a customer tasks it. The UI says so.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
from sgp4 import omm
from sgp4.api import Satrec, SatrecArray

from live.hub import Hub
from pipeline.config import REFERENCE_DIR
from reference.cache import USER_AGENT

log = logging.getLogger("terrestrial.live")

NAME = "celestrak"
GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
ELEMENTS_MAX_AGE_H = 12
PUBLISH_S = 10
TRACK_MIN = 45  # ground track ahead, minutes
EARTH_KM = 6371.0
WGS84_A = 6378.137
WGS84_F = 1 / 298.257223563


@dataclass(frozen=True)
class Mission:
    query: str  # CelesTrak NAME= query
    pattern: str  # regex on OBJECT_NAME selecting operational spacecraft
    sensor: str  # "SAR" | "optical"
    operator: str
    access_km: float  # imaging reach either side of the ground track
    tasked: bool  # commercial tasking (vs a fixed public acquisition plan)


CATALOG = (
    Mission("SENTINEL-1", r"^SENTINEL-1[ACD]$", "SAR", "ESA / Copernicus", 350, False),
    Mission("SENTINEL-2", r"^SENTINEL-2[ABC]$", "optical", "ESA / Copernicus", 145, False),
    Mission("LANDSAT", r"^LANDSAT [89]$", "optical", "USGS / NASA", 93, False),
    Mission("ICEYE", r"^ICEYE-", "SAR", "ICEYE", 400, True),
    Mission("CAPELLA", r"^CAPELLA-", "SAR", "Capella Space", 400, True),
    Mission("UMBRA", r"^UMBRA-", "SAR", "Umbra", 400, True),
    Mission("COSMO-SKYMED", r"^COSMO-SKYMED", "SAR", "ASI (Italy)", 400, True),
    Mission("RADARSAT", r"^RADARSAT-2$", "SAR", "MDA (Canada)", 400, True),
    Mission("TERRASAR", r"^TERRASAR-X$", "SAR", "DLR / Airbus", 300, True),
    Mission("PAZ", r"^PAZ$", "SAR", "Hisdesat (Spain)", 300, True),
    Mission("SAOCOM", r"^SAOCOM 1[AB]$", "SAR", "CONAE (Argentina)", 300, True),
)


@dataclass
class Sat:
    name: str
    norad: int
    mission: Mission
    satrec: Satrec
    epoch: str


def gmst(jd_ut1: np.ndarray) -> np.ndarray:
    """Greenwich mean sidereal time (rad), IAU 1982 (sufficient for TEME → ECEF here)."""
    t = (jd_ut1 - 2451545.0) / 36525.0
    g = 67310.54841 + (876600.0 * 3600 + 8640184.812866) * t + 0.093104 * t**2 - 6.2e-6 * t**3
    return np.radians((g % 86400) / 240.0)


def teme_to_geodetic(r: np.ndarray, jd: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """TEME position (km, [..., 3]) → (lon°, lat°, altitude km) on WGS84 (polar motion ignored)."""
    th = gmst(jd)
    x = r[..., 0] * np.cos(th) + r[..., 1] * np.sin(th)
    y = -r[..., 0] * np.sin(th) + r[..., 1] * np.cos(th)
    z = r[..., 2]
    lon = np.degrees(np.arctan2(y, x))
    p = np.hypot(x, y)
    e2 = WGS84_F * (2 - WGS84_F)
    lat = np.arctan2(z, p * (1 - e2))
    for _ in range(4):
        n = WGS84_A / np.sqrt(1 - e2 * np.sin(lat) ** 2)
        h = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1 - e2 * n / (n + h)))
    n = WGS84_A / np.sqrt(1 - e2 * np.sin(lat) ** 2)
    h = p / np.cos(lat) - n
    return lon, np.degrees(lat), h


def parse_elements(rows: list[dict], mission: Mission) -> list[Sat]:
    out = []
    for row in rows:
        if not re.search(mission.pattern, row["OBJECT_NAME"]):
            continue
        sat = Satrec()
        omm.initialize(sat, row)
        out.append(Sat(row["OBJECT_NAME"], int(row["NORAD_CAT_ID"]), mission, sat, row["EPOCH"]))
    return out


def _julian(ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unix seconds → (jd, fraction) arrays for SGP4."""
    jd = ts / 86400.0 + 2440587.5
    whole = np.floor(jd - 0.5) + 0.5
    return whole, jd - whole


def positions(sats: list[Sat], ts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(lon, lat, alt km, ok) arrays of shape [n_sats, n_times]."""
    jd, fr = _julian(np.asarray(ts, dtype=float))
    err, r, _ = SatrecArray([s.satrec for s in sats]).sgp4(jd, fr)
    lon, lat, alt = teme_to_geodetic(r, jd + fr)
    return lon, lat, alt, err == 0


def ground_km(lon1, lat1, lon2, lat2):
    la1, la2 = np.radians(lat1), np.radians(lat2)
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def passes(
    sats: list[Sat], lon: float, lat: float, start: float, hours: float = 24, step_s: float = 20
) -> list[dict]:
    """Every window in [start, start + hours) in which (lon, lat) is within a satellite's access
    radius, soonest first. Daylight is reported for optical passes (they need it)."""
    ts = start + np.arange(0, hours * 3600, step_s)
    slon, slat, _, ok = positions(sats, ts)
    d = ground_km(slon, slat, lon, lat)
    reach = np.array([s.mission.access_km for s in sats])[:, None]
    inside = (d <= reach) & ok
    out = []
    for i, s in enumerate(sats):
        row = inside[i]
        if not row.any():
            continue
        edges = np.flatnonzero(np.diff(np.concatenate([[0], row.astype(int), [0]])))
        for a, b in zip(edges[::2], edges[1::2], strict=True):
            mid = (a + b - 1) // 2
            t_mid = ts[mid]
            out.append(
                {
                    "satellite": s.name,
                    "norad": s.norad,
                    "sensor": s.mission.sensor,
                    "operator": s.mission.operator,
                    "tasked": s.mission.tasked,
                    "start": int(ts[a] * 1000),
                    "end": int((ts[b - 1] + step_s) * 1000),
                    "closest_km": round(float(d[i, a:b].min())),
                    "daylight": bool(solar_elevation(lon, lat, t_mid) > 0)
                    if s.mission.sensor == "optical"
                    else None,
                }
            )
    out.sort(key=lambda p: p["start"])
    return out


def solar_elevation(lon: float, lat: float, t: float) -> float:
    """Approximate solar elevation (deg) at a place and unix time (NOAA low-precision)."""
    d = t / 86400.0 + 2440587.5 - 2451545.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.00000036 * d)
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    gmst_h = (18.697374558 + 24.06570982441908 * d) % 24
    ha = math.radians(gmst_h * 15 + lon) - ra
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))


class Constellation:
    def __init__(self, cache_dir: Path = REFERENCE_DIR / "celestrak"):
        self.cache_dir = cache_dir
        self.sats: list[Sat] = []
        self.loaded_at = 0.0

    def _rows(self, mission: Mission, http: httpx.Client) -> list[dict]:
        path = self.cache_dir / f"{mission.query.lower()}.json"
        fresh = path.exists() and time.time() - path.stat().st_mtime < ELEMENTS_MAX_AGE_H * 3600
        if not fresh:
            try:
                r = http.get(GP_URL, params={"NAME": mission.query, "FORMAT": "json"})
                r.raise_for_status()
                rows = r.json()
                if not isinstance(rows, list):
                    raise ValueError(f"CelesTrak {mission.query}: expected a list")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rows), encoding="utf-8")
            except (httpx.HTTPError, ValueError) as exc:
                if not path.exists():
                    raise
                log.warning("CelesTrak %s refresh failed (%s); using cached elements", mission.query, exc)
        return json.loads(path.read_text(encoding="utf-8"))

    def load(self) -> int:
        sats: list[Sat] = []
        with httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}) as http:
            for m in CATALOG:
                sats += parse_elements(self._rows(m, http), m)
                time.sleep(0.5)  # CelesTrak asks for gentle use
        if not sats:
            raise RuntimeError("no satellites parsed from CelesTrak")
        self.sats, self.loaded_at = sats, time.time()
        return len(sats)

    def entities(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        ts = now + np.arange(0, TRACK_MIN * 60 + 1, 90)
        lon, lat, alt, ok = positions(self.sats, ts)
        out = []
        for i, s in enumerate(self.sats):
            if not ok[i, 0]:
                continue
            track = [
                [round(float(lon[i, k]), 3), round(float(lat[i, k]), 3)] for k in range(len(ts)) if ok[i, k]
            ]
            out.append(
                {
                    "id": f"satellite:{s.norad}",
                    "kind": "satellite",
                    "label": s.name,
                    "lon": float(lon[i, 0]),
                    "lat": float(lat[i, 0]),
                    "alt": round(float(alt[i, 0]) * 1000),
                    "ts": int(now * 1000),
                    "src": NAME,
                    "prov": "inferred",
                    "props": {
                        "norad": s.norad,
                        "sensor": s.mission.sensor,
                        "operator": s.mission.operator,
                        "access_km": s.mission.access_km,
                        "tasked": s.mission.tasked,
                        "alt_km": round(float(alt[i, 0])),
                        "elements_epoch": s.epoch,
                        "track": track,
                    },
                }
            )
        return out


async def run(hub: Hub, sky: Constellation) -> None:
    hub.source(NAME)
    while True:
        if time.time() - sky.loaded_at > ELEMENTS_MAX_AGE_H * 3600:
            try:
                n = await asyncio.to_thread(sky.load)
                log.info("satellites: %d imaging satellites loaded", n)
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                hub.source_error(NAME, f"{type(exc).__name__}: {exc}")
                if not sky.sats:
                    await asyncio.sleep(300)
                    continue
        ents = await asyncio.to_thread(sky.entities)
        for e in ents:
            hub.upsert(e)
        sar = sum(1 for s in sky.sats if s.mission.sensor == "SAR")
        hub.source_ok(
            NAME, f"{len(ents)} imaging satellites ({sar} radar), elements ≤ {ELEMENTS_MAX_AGE_H} h old"
        )
        await asyncio.sleep(PUBLISH_S)
