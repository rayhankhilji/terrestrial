"""Sea state and cloud at each AOI from Open-Meteo (free, no key), refreshed every 15 min.

Beyond current conditions this is a small predictive layer: from the 48 h hourly forecast it
derives (a) the next window calm enough for ship-to-ship transfers (significant wave height
below STS_MAX_WAVE_M, a common operational limit for STS mooring) and (b) the next window
clear enough for optical satellite imagery (cloud cover below OPTICAL_MAX_CLOUD_PCT).
These are forecast-based estimates and are labelled as such.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from live.hub import Hub, now_ms
from pipeline.aoi import AOIS

log = logging.getLogger("terrestrial.live")

NAME = "open-meteo"
INTERVAL_S = 15 * 60
STS_MAX_WAVE_M = 1.5
OPTICAL_MAX_CLOUD_PCT = 20

MARINE = "https://marine-api.open-meteo.com/v1/marine"
WEATHER = "https://api.open-meteo.com/v1/forecast"


def _windows(times: list[str], values: list[float | None], ok) -> list[dict]:
    """Contiguous runs of hours where ok(value) holds: [{start, end, hours}]."""
    if len(times) != len(values):
        raise ValueError("forecast times and values differ in length")
    runs, start = [], None
    for i, v in enumerate(values):
        good = v is not None and ok(v)
        if good and start is None:
            start = i
        if (not good or i == len(times) - 1) and start is not None:
            end = i if good else i - 1
            runs.append({"start": times[start], "end": times[end], "hours": end - start + 1})
            start = None
    return runs


async def _fetch(http: httpx.AsyncClient) -> list[dict]:
    lats = ",".join(str(a.lat) for a in AOIS)
    lons = ",".join(str(a.lon) for a in AOIS)
    common = {"latitude": lats, "longitude": lons, "forecast_days": 2, "timezone": "UTC"}
    marine, weather = await asyncio.gather(
        http.get(MARINE, params={**common, "hourly": "wave_height", "current": "wave_height,wave_direction"}),
        http.get(
            WEATHER,
            params={
                **common,
                "hourly": "cloud_cover",
                "current": "cloud_cover,wind_speed_10m,wind_direction_10m,visibility",
            },
        ),
    )
    marine.raise_for_status()
    weather.raise_for_status()
    m, w = marine.json(), weather.json()
    m = m if isinstance(m, list) else [m]
    w = w if isinstance(w, list) else [w]
    if len(m) != len(AOIS) or len(w) != len(AOIS):
        raise ValueError(f"Open-Meteo returned {len(m)}/{len(w)} locations for {len(AOIS)} AOIs")
    return list(zip(m, w, strict=True))


def _entity(aoi, marine: dict, weather: dict) -> dict:
    hm, hw = marine.get("hourly", {}), weather.get("hourly", {})
    sts = _windows(hm.get("time", []), hm.get("wave_height", []), lambda v: v < STS_MAX_WAVE_M)
    optical = _windows(hw.get("time", []), hw.get("cloud_cover", []), lambda v: v < OPTICAL_MAX_CLOUD_PCT)
    cur_m, cur_w = marine.get("current", {}), weather.get("current", {})
    observed = cur_w.get("time") or cur_m.get("time")
    ts = (
        int(datetime.fromisoformat(observed).replace(tzinfo=UTC).timestamp() * 1000) if observed else now_ms()
    )
    return {
        "id": f"station:{aoi.name}",
        "kind": "station",
        "label": f"{aoi.name} conditions",
        "lon": aoi.lon,
        "lat": aoi.lat,
        "ts": ts,
        "src": NAME,
        "prov": "observed",
        "props": {
            "aoi": aoi.name,
            "occupied_ua": aoi.occupied_ua,
            "wave_m": cur_m.get("wave_height"),
            "wave_dir": cur_m.get("wave_direction"),
            "cloud_pct": cur_w.get("cloud_cover"),
            "wind_kmh": cur_w.get("wind_speed_10m"),
            "wind_dir": cur_w.get("wind_direction_10m"),
            "visibility_m": cur_w.get("visibility"),
            "forecast": {
                "kind": "model estimate (Open-Meteo 48 h forecast)",
                "sts_max_wave_m": STS_MAX_WAVE_M,
                "sts_windows": sts,
                "optical_max_cloud_pct": OPTICAL_MAX_CLOUD_PCT,
                "optical_windows": optical,
                "wave_h": hm.get("wave_height", []),
                "cloud_h": hw.get("cloud_cover", []),
                "times": hm.get("time", []),
            },
        },
    }


async def run(hub: Hub) -> None:
    hub.source(NAME)
    async with httpx.AsyncClient(timeout=30.0) as http:
        while True:
            try:
                for aoi, (marine, weather) in zip(AOIS, await _fetch(http), strict=True):
                    hub.upsert(_entity(aoi, marine, weather))
                hub.source_ok(NAME, f"conditions + 48 h forecast at {len(AOIS)} ports")
                await asyncio.sleep(INTERVAL_S)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                hub.source_error(NAME, f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(60)
