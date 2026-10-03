"""News events from GDELT 2.0 (free, no key, new export every 15 minutes).

Keeps events located inside the theatre at city or region level (country-centroid rows are
too coarse to place on a map) whose CAMEO root code is about force or coercion. One entity
per (article, place); the article URL is the pivot back to the primary source.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import logging
import zipfile
from datetime import UTC, datetime, timedelta

import httpx

from live.hub import Hub
from pipeline.config import THEATRE_BBOX

log = logging.getLogger("terrestrial.live")

NAME = "gdelt"
LAST_UPDATE = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
FILE_URL = "http://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip"
POLL_S = 60
BACKFILL_FILES = 8  # two hours on startup

# Column positions in the GDELT 2.0 events export (61 tab-separated columns).
C_ID, C_ACTOR1, C_ACTOR2, C_CODE, C_ROOT = 0, 6, 16, 26, 28
C_GOLDSTEIN, C_MENTIONS, C_SOURCES, C_TONE = 30, 31, 32, 34
C_GEO_TYPE, C_GEO_NAME, C_GEO_CC, C_LAT, C_LON, C_ADDED, C_URL = 51, 52, 53, 56, 57, 59, 60
N_COLUMNS = 61

ROOTS = {
    "15": "Exhibit force posture",
    "16": "Reduce relations / sanctions",
    "17": "Coerce",
    "18": "Assault",
    "19": "Fight",
    "20": "Unconventional mass violence",
}
CODES = {
    "150": "Military or police power demonstration",
    "152": "Increase military alert status",
    "154": "Mobilise armed forces",
    "163": "Impose embargo, boycott or sanctions",
    "171": "Seize or damage property",
    "172": "Impose administrative sanctions",
    "173": "Arrest or detain",
    "180": "Unconventional violence",
    "183": "Bombing",
    "186": "Assassination",
    "190": "Conventional military force",
    "191": "Blockade",
    "192": "Occupy territory",
    "193": "Small-arms fighting",
    "194": "Artillery and tanks",
    "195": "Aerial weapons",
    "196": "Violate ceasefire",
}
PLACE_TYPES = {"4", "5"}  # world city, world ADM1 (excludes 1 = country centroid)


def parse(text: str) -> list[dict]:
    min_lon, min_lat, max_lon, max_lat = THEATRE_BBOX
    out: dict[str, dict] = {}
    for row in csv.reader(io.StringIO(text), delimiter="\t"):
        if len(row) != N_COLUMNS:
            raise ValueError(f"GDELT export row has {len(row)} columns, expected {N_COLUMNS}")
        if row[C_ROOT] not in ROOTS or row[C_GEO_TYPE] not in PLACE_TYPES or not row[C_LAT]:
            continue
        lat, lon = float(row[C_LAT]), float(row[C_LON])
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        key = hashlib.sha1(f"{row[C_URL]}|{lat:.3f}|{lon:.3f}".encode()).hexdigest()[:16]
        added = datetime.strptime(row[C_ADDED], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        code = row[C_CODE]
        entity = out.get(key)
        if entity is None:
            entity = out[key] = {
                "id": f"news:{key}",
                "kind": "news",
                "label": CODES.get(code) or ROOTS[row[C_ROOT]],
                "lon": lon,
                "lat": lat,
                "ts": int(added.timestamp() * 1000),
                "src": NAME,
                "prov": "observed",
                "props": {
                    "place": row[C_GEO_NAME],
                    "country": row[C_GEO_CC],
                    "url": row[C_URL],
                    "codes": [],
                    "actors": [],
                    "goldstein": float(row[C_GOLDSTEIN] or 0),
                    "mentions": int(row[C_MENTIONS] or 0),
                    "tone": round(float(row[C_TONE] or 0), 2),
                    "event_ids": [],
                },
            }
        p = entity["props"]
        if code not in p["codes"]:
            p["codes"].append(code)
        for actor in (row[C_ACTOR1], row[C_ACTOR2]):
            if actor and actor not in p["actors"]:
                p["actors"].append(actor)
        p["event_ids"].append(row[C_ID])
        p["goldstein"] = min(p["goldstein"], float(row[C_GOLDSTEIN] or 0))
        p["mentions"] = max(p["mentions"], int(row[C_MENTIONS] or 0))
    return list(out.values())


async def _load(http: httpx.AsyncClient, url: str) -> list[dict]:
    response = await http.get(url)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8", errors="replace")
    return parse(text)


async def run(hub: Hub) -> None:
    hub.source(NAME)
    seen_stamp = None
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
        while True:
            try:
                latest = (await http.get(LAST_UPDATE)).text.split("\n")[0].split()[-1]
                stamp = latest.rsplit("/", 1)[-1].split(".")[0]
                if stamp != seen_stamp:
                    stamps = [stamp]
                    if seen_stamp is None:
                        t = datetime.strptime(stamp, "%Y%m%d%H%M%S")
                        stamps = [
                            (t - timedelta(minutes=15 * k)).strftime("%Y%m%d%H%M%S")
                            for k in range(BACKFILL_FILES)
                        ][::-1]
                    total, missing = 0, []
                    for s in stamps:
                        try:
                            entities = await _load(http, FILE_URL.format(stamp=s))
                        except httpx.HTTPStatusError as exc:
                            # GDELT occasionally lists an export before it is published, or skips
                            # one; note it and carry on rather than dropping the whole cycle.
                            if exc.response.status_code != 404:
                                raise
                            missing.append(s)
                            continue
                        for entity in entities:
                            hub.upsert(entity)
                            total += 1
                    if stamp not in missing:
                        seen_stamp = stamp  # otherwise retry the latest export on the next poll
                    note = f"; not yet published: {', '.join(missing)}" if missing else ""
                    hub.source_ok(NAME, f"{total} force/coercion events in theatre from export {stamp}{note}")
            except (httpx.HTTPError, ValueError, zipfile.BadZipFile, IndexError) as exc:
                hub.source_error(NAME, f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(POLL_S)
