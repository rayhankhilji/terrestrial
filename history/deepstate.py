"""DeepStateMap.Live: occupied territory, the front line, attack directions, Russian unit
positions and the airfields Russia operates from (M4/M7).

DeepState publishes its map as KML-derived GeoJSON (`/api/history/last`, and every past
snapshot via `/api/history/public` → `/api/history/{id}/geojson`). Feature names are
"<Ukrainian> /// <English> /// <i18n key>"; the key is what we classify on:

- `geoJSON.status.occupied`, `geoJSON.territories.{crimea,ordlo,tuzla}` → occupied Ukraine;
- `geoJSON.status.unknown` → contested / unknown status (grey zone);
- `geoJSON.status.attack_direction` → direction-of-attack markers;
- `geoJSON.units.*` → DeepState's estimated Russian unit positions;
- `geoJSON.airfield.*`, `geoJSON.airbase.*`, `geoJSON.airport.*` → airfields used by Russia.

The map also carries political polygons outside Ukraine (e.g. "occupied" Karelia, Prussia,
Transnistria); only territory inside Ukraine's international borders is treated as occupied.
The front line is the part of occupied Ukraine's boundary that lies within ~3 km of free
Ukraine (so the Dnipro line counts; the Russian border and coastlines do not). DeepState data: © DeepStateMap.Live, attribution required.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field

import httpx
from shapely.geometry import LineString, MultiLineString, Point, mapping, shape
from shapely.ops import unary_union

from reference.cache import USER_AGENT

LAST_URL = "https://deepstatemap.live/api/history/last"
OCCUPIED_KEYS = (
    "geoJSON.status.occupied",
    "geoJSON.territories.crimea",
    "geoJSON.territories.ordlo",
    "geoJSON.territories.tuzla",
)
UNKNOWN_KEY = "geoJSON.status.unknown"
ATTACK_KEY = "geoJSON.status.attack_direction"
AIR_PREFIXES = ("geoJSON.airfield.", "geoJSON.airbase.", "geoJSON.airport.")
UNIT_PREFIX = "geoJSON.units."
SLIVER_DEG = 0.05
FRONT_BUFFER_DEG = (
    0.03  # ~3 km: occupied edges this close to free Ukraine are front (river crossings included)
)
TAG = re.compile(r"<[^>]+>")


def _key(name: str | None) -> str:
    return (name or "").split("///")[-1].strip()


def _english(name: str | None) -> str:
    parts = [p.strip() for p in (name or "").split("///")]
    return (parts[1] if len(parts) >= 3 else parts[0]).replace("\xa0", " ").strip()


def _2d(geom: dict) -> dict:
    """DeepState coordinates carry a zero altitude; drop it."""

    def strip(c):
        return [strip(x) for x in c] if isinstance(c[0], list) else c[:2]

    return {"type": geom["type"], "coordinates": strip(geom["coordinates"])}


@dataclass
class Front:
    snapshot_id: int
    datetime: str
    occupied: object  # shapely (Multi)Polygon inside Ukraine
    unknown: object
    front: object  # shapely (Multi)LineString
    attack_directions: list[tuple[float, float]] = field(default_factory=list)
    units: list[dict] = field(default_factory=list)
    airfields: list[dict] = field(default_factory=list)

    def distance_km(self, lon: float, lat: float) -> float:
        """Approximate distance to the front line (0 on it); km per degree at that latitude."""
        if self.front.is_empty:
            return float("inf")
        p = Point(lon, lat)
        # Scale longitude so planar distance approximates km near this latitude.
        k = 111.32
        c = max(0.2, abs(math.cos(math.radians(lat))))
        nearest = self.front.interpolate(self.front.project(p))
        return ((nearest.x - lon) ** 2 * (k * c) ** 2 + (nearest.y - lat) ** 2 * k**2) ** 0.5

    def occupied_contains(self, lon: float, lat: float) -> bool:
        return self.occupied.contains(Point(lon, lat))

    def geojson(self, tolerance: float = 0.003) -> dict:
        feats = [
            {
                "type": "Feature",
                "properties": {"layer": "occupied"},
                "geometry": mapping(self.occupied.simplify(tolerance)),
            },
            {
                "type": "Feature",
                "properties": {"layer": "unknown"},
                "geometry": mapping(self.unknown.simplify(tolerance)),
            },
            {
                "type": "Feature",
                "properties": {"layer": "front"},
                "geometry": mapping(self.front.simplify(tolerance)),
            },
        ]
        feats += [
            {
                "type": "Feature",
                "properties": {"layer": "attack_direction"},
                "geometry": {"type": "Point", "coordinates": list(p)},
            }
            for p in self.attack_directions
        ]
        feats += [
            {
                "type": "Feature",
                "properties": {"layer": "unit", "name": u["name"], "key": u["key"]},
                "geometry": {"type": "Point", "coordinates": [u["lon"], u["lat"]]},
            }
            for u in self.units
        ]
        feats += [
            {
                "type": "Feature",
                "properties": {"layer": "airfield", "name": a["name"], "key": a["key"]},
                "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]},
            }
            for a in self.airfields
        ]
        return {
            "type": "FeatureCollection",
            "snapshot": self.snapshot_id,
            "datetime": self.datetime,
            "features": feats,
        }


def parse(payload: dict, ukraine, land=None) -> Front:
    """`payload` is an /api/history/last (or /{id}/geojson wrapped the same way) response;
    `ukraine` a shapely geometry of Ukraine's internationally recognised territory; `land` an
    optional land mask (reference/land.py) so that sea inside the administrative shapes is
    never mistaken for free territory next to the occupied coast."""
    fc = payload.get("map", payload)
    if fc.get("type") != "FeatureCollection":
        raise ValueError("DeepState response is not a FeatureCollection; has the API changed?")
    occupied, unknown, attacks, units, air = [], [], [], [], []
    for f in fc["features"]:
        geom = f.get("geometry")
        if not geom:
            continue
        k = _key(f["properties"].get("name"))
        g = shape(_2d(geom))
        if g.geom_type in ("Polygon", "MultiPolygon"):
            if k in OCCUPIED_KEYS:
                occupied.append(g.buffer(0))
            elif k == UNKNOWN_KEY:
                unknown.append(g.buffer(0))
        elif g.geom_type == "Point":
            if k == ATTACK_KEY:
                attacks.append((round(g.x, 5), round(g.y, 5)))
            elif k.startswith(UNIT_PREFIX):
                units.append(
                    {"key": k, "name": _english(f["properties"].get("name")), "lon": g.x, "lat": g.y}
                )
            elif k.startswith(AIR_PREFIXES):
                air.append({"key": k, "name": _english(f["properties"].get("name")), "lon": g.x, "lat": g.y})
    if not occupied:
        raise ValueError("DeepState response has no occupied-territory polygons; has the schema changed?")
    inside = ukraine.buffer(0.05)
    occ = unary_union(occupied).intersection(inside)
    grey = unary_union(unknown).intersection(inside) if unknown else Point(0, 0).buffer(0)
    # The front is where occupied territory meets free Ukraine (across the Dnipro too), which
    # excludes the Russian border and every coastline whatever their resolution.
    # Morphological opening drops slivers (< ~10 km wide) that differing coastline resolutions
    # leave between the simplified border and DeepState's polygons.
    free = (ukraine.intersection(land) if land is not None else ukraine).difference(occ)
    free = free.buffer(-SLIVER_DEG).buffer(SLIVER_DEG)
    line = occ.boundary.intersection(free.buffer(FRONT_BUFFER_DEG))
    if line.geom_type == "GeometryCollection":
        line = unary_union([g for g in line.geoms if g.geom_type in ("LineString", "MultiLineString")])
    if isinstance(line, LineString):
        line = MultiLineString([line])
    return Front(
        snapshot_id=int(payload.get("id") or 0),
        datetime=str(payload.get("datetime") or ""),
        occupied=occ,
        unknown=grey,
        front=line,
        attack_directions=attacks,
        units=units,
        airfields=air,
    )


def describe_update(entry: dict) -> str:
    """Plain English text of a DeepState history entry (HTML links stripped)."""
    return html.unescape(TAG.sub("", entry.get("descriptionEn") or entry.get("description") or "")).strip()


def fetch_last(http: httpx.Client | None = None) -> dict:
    client = http or httpx.Client(timeout=60.0, headers={"User-Agent": USER_AGENT})
    r = client.get(LAST_URL)
    r.raise_for_status()
    return r.json()


def fetch_history_index(http: httpx.Client | None = None) -> list[dict]:
    client = http or httpx.Client(timeout=60.0, headers={"User-Agent": USER_AGENT})
    r = client.get("https://deepstatemap.live/api/history/public")
    r.raise_for_status()
    return r.json()
