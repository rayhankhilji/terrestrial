"""Airfields from OurAirports (public domain): display layer and landing candidates.

`airports.csv` columns used: ident, type, name, latitude_deg, longitude_deg, elevation_ft,
iso_country, municipality, icao_code, keywords. OurAirports has no military flag, so an airfield
is classed military when its name (or, for unambiguous terms, its keywords) says so (Air Base, AB, Air Force, Naval Air
Station, Аэродром…); the matching rule is recorded on each row. Wikidata's military-airbase
facilities (live/sources/wikidata.py) are an independent second source.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pipeline.config import MIL_BBOX
from reference.cache import cached

URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
KEEP_TYPES = {"large_airport", "medium_airport", "small_airport", "heliport", "seaplane_base"}
MILITARY = re.compile(
    r"\b(air ?base|AB|AFB|air force|military|naval air|NAS|RAF|army air(field)?|airbase|"
    r"aviabaza|авиабаза|аэродром [а-яё-]+ \(в/ч\)|helipad .*navy|navy|base a[ée]rienne|fliegerhorst|"
    r"baza lotnictwa|baza lotnicza|base aerea|hava üssü)\b",
    re.IGNORECASE,
)
# Keywords often keep historical names ("RAF Aldergrove", "former military"), so only the
# unambiguous terms count there; the full pattern applies to the current name.
MILITARY_KEYWORDS = re.compile(
    r"\b(air ?base|airbase|naval air|army air(field)?|aviabaza|авиабаза|navy)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Airfield:
    ident: str
    name: str
    kind: str  # OurAirports type
    lon: float
    lat: float
    elevation_m: float | None
    country: str
    icao: str | None
    military: bool
    military_rule: str | None  # the text that matched, as evidence


def _inside(lon: float, lat: float, bbox) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def parse(path: Path, bbox=MIL_BBOX) -> list[Airfield]:
    out = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["type"] not in KEEP_TYPES:
                continue
            lon, lat = float(row["longitude_deg"]), float(row["latitude_deg"])
            if not _inside(lon, lat, bbox):
                continue
            match = MILITARY.search(row["name"]) or MILITARY_KEYWORDS.search(row["keywords"])
            elevation = row.get("elevation_ft")
            out.append(
                Airfield(
                    ident=row["ident"],
                    name=row["name"],
                    kind=row["type"],
                    lon=lon,
                    lat=lat,
                    elevation_m=round(float(elevation) * 0.3048, 1) if elevation else None,
                    country=row["iso_country"],
                    icao=row.get("icao_code") or None,
                    military=match is not None,
                    military_rule=match.group(0) if match else None,
                )
            )
    if not out:
        raise ValueError(f"no airfields parsed from {path}; has the CSV format changed?")
    return out


@lru_cache(maxsize=1)
def airfields(refresh: bool = False) -> tuple[Airfield, ...]:
    return tuple(parse(cached(URL, "ourairports_airports.csv", refresh=refresh, max_age_days=30)))
