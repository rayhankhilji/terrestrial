"""Thermal anomalies from NASA FIRMS (VIIRS, near real time; free MAP_KEY).

A VIIRS hotspot at a refinery, depot or port is consistent with a fire, flaring or a strike.
FIRMS cannot tell those apart, so hotspots stay "thermal anomaly" and only become linked to a
facility by the correlator, with the inference spelled out.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import logging
from datetime import UTC, datetime

import httpx

from live.hub import Hub
from pipeline.config import THEATRE_BBOX, optional_key

log = logging.getLogger("terrestrial.live")

NAME = "firms"
URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{product}/{bbox}/{days}"
PRODUCTS = ("VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT")
INTERVAL_S = 10 * 60
EXPECTED = {"latitude", "longitude", "acq_date", "acq_time", "frp", "confidence", "satellite", "daynight"}


def parse(text: str, product: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return []
    missing = EXPECTED - set(reader.fieldnames)
    if missing:
        raise ValueError(f"FIRMS {product}: unexpected columns, missing {sorted(missing)}: {text[:200]!r}")
    out = []
    for r in reader:
        when = datetime.strptime(f"{r['acq_date']} {int(r['acq_time']):04d}", "%Y-%m-%d %H%M").replace(
            tzinfo=UTC
        )
        lat, lon = float(r["latitude"]), float(r["longitude"])
        key = hashlib.sha1(f"{product}|{lat}|{lon}|{when.isoformat()}".encode()).hexdigest()[:16]
        out.append(
            {
                "id": f"fire:{key}",
                "kind": "fire",
                "label": "Thermal anomaly",
                "lon": lon,
                "lat": lat,
                "ts": int(when.timestamp() * 1000),
                "src": NAME,
                "prov": "observed",
                "props": {
                    "frp_mw": float(r["frp"]) if r["frp"] else None,
                    "confidence": r["confidence"],
                    "satellite": r["satellite"],
                    "daynight": r["daynight"],
                    "product": product,
                },
            }
        )
    return out


async def run(hub: Hub) -> None:
    key = optional_key("FIRMS_MAP_KEY")
    if not key:
        hub.source(NAME).state = "disabled"
        hub.source(NAME).detail = "set FIRMS_MAP_KEY in .env (free from NASA FIRMS)"
        return
    min_lon, min_lat, max_lon, max_lat = THEATRE_BBOX
    bbox = f"{min_lon},{min_lat},{max_lon},{max_lat}"
    async with httpx.AsyncClient(timeout=60.0) as http:
        while True:
            try:
                total = 0
                for product in PRODUCTS:
                    response = await http.get(URL.format(key=key, product=product, bbox=bbox, days=1))
                    response.raise_for_status()
                    for entity in parse(response.text, product):
                        hub.upsert(entity)
                        total += 1
                hub.source_ok(NAME, f"{total} VIIRS hotspots in theatre (last 24 h)")
                await asyncio.sleep(INTERVAL_S)
            except (httpx.HTTPError, ValueError) as exc:
                hub.source_error(NAME, f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(120)
