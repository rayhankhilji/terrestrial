"""Daily weather per region, 2022 → yesterday, from the Open-Meteo historical archive.

One point per region (the representative point of its boundary). Variables chosen because they
plausibly affect air operations: cloud cover (optical reconnaissance, drone navigation),
precipitation, and maximum 10 m wind (drone flight). Training uses observed weather; live
predictions use the forecast for the same variables (CLAUDE.md §16.4: same feature, same
definition; the source differs and the model card says so).
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import httpx
import pandas as pd

from history.regions import HISTORY_RAW, boundaries
from pipeline.config import HISTORY_DIR
from pipeline.io import read_json, write_json, write_table

log = logging.getLogger("terrestrial")

URL = "https://archive-api.open-meteo.com/v1/archive"
VARIABLES = ["cloud_cover_mean", "precipitation_sum", "wind_speed_10m_max"]
START = date(2022, 2, 24)
CACHE = HISTORY_RAW / "weather"
# The free API weighs a request by locations × days × variables; small chunks with a pause stay
# under its per-minute limit, and a 429 waits for the window to pass.
LOCATIONS_PER_CALL = 9
PAUSE_S = 6.0
RETRIES = 6


def parse(payload: list[dict], isos: list[str]) -> pd.DataFrame:
    if len(payload) != len(isos):
        raise ValueError(f"Open-Meteo returned {len(payload)} locations for {len(isos)} regions")
    frames = []
    for iso, loc in zip(isos, payload, strict=True):
        daily = loc["daily"]
        missing = [v for v in VARIABLES if v not in daily]
        if missing:
            raise ValueError(f"Open-Meteo daily variables missing: {missing}")
        frames.append(
            pd.DataFrame(
                {"iso": iso, "date": pd.to_datetime(daily["time"]), **{v: daily[v] for v in VARIABLES}}
            )
        )
    return pd.concat(frames, ignore_index=True)


def _get(params: dict) -> list[dict]:
    for attempt in range(RETRIES):
        response = httpx.get(URL, params=params, timeout=180.0)
        if response.status_code == 429:
            wait = 65 * (attempt + 1)
            log.info("  weather: rate limited, waiting %d s", wait)
            time.sleep(wait)
            continue
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else [payload]  # one location → an object
    raise RuntimeError(f"Open-Meteo archive still rate limited after {RETRIES} attempts")


def _year_chunk(
    year: int, end: date, isos: list[str], lons: list[float], lats: list[float], refresh: bool
) -> list[dict]:
    first = max(START, date(year, 1, 1))
    last = min(end, date(year, 12, 31))
    path = CACHE / f"archive_{year}.json"
    complete = last < end  # past years never change; the current year is re-fetched daily
    if path.exists() and not refresh:
        cached = read_json(path)
        if cached.get("isos") == isos and (complete or cached.get("end") == last.isoformat()):
            return cached["payload"]
    payload: list[dict] = []
    for i in range(0, len(isos), LOCATIONS_PER_CALL):
        payload += _get(
            {
                "latitude": ",".join(map(str, lats[i : i + LOCATIONS_PER_CALL])),
                "longitude": ",".join(map(str, lons[i : i + LOCATIONS_PER_CALL])),
                "start_date": first.isoformat(),
                "end_date": last.isoformat(),
                "daily": ",".join(VARIABLES),
                "timezone": "UTC",
            }
        )
        time.sleep(PAUSE_S)
    write_json(path, {"isos": isos, "end": last.isoformat(), "payload": payload})
    return payload


def fetch(refresh: bool = False, today: date | None = None) -> pd.DataFrame:
    geoms = boundaries()
    isos = sorted(geoms)
    lons = [geoms[i].lon for i in isos]
    lats = [geoms[i].lat for i in isos]
    end = (today or date.today()) - timedelta(days=1)
    frames = [
        parse(_year_chunk(y, end, isos, lons, lats, refresh), isos) for y in range(START.year, end.year + 1)
    ]
    weather = pd.concat(frames, ignore_index=True).sort_values(["iso", "date"]).reset_index(drop=True)
    write_table(weather, "weather_daily", directory=HISTORY_DIR)
    log.info(
        "  weather: %d region-days, %s → %s",
        len(weather),
        weather["date"].min().date(),
        weather["date"].max().date(),
    )
    return weather
