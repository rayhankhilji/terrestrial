"""Static areas of interest: port centres with a fixed radius (CLAUDE.md §4).

Coordinates are approximate port centres, adequate for 15 km radii.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
from shapely.geometry import Polygon

from pipeline import geo
from pipeline.config import AOI_RADIUS_KM


@dataclass(frozen=True)
class AOI:
    name: str
    lat: float
    lon: float
    occupied_ua: bool
    radius_km: float = AOI_RADIUS_KM

    @cached_property
    def polygon(self) -> Polygon:
        return geo.circle(self.lon, self.lat, self.radius_km)

    def distance_km(self, lon, lat):
        """Distance from the port centre (scalar or vectorised)."""
        return geo.distance_km_many(lon, lat, self.lon, self.lat)


AOIS: tuple[AOI, ...] = (
    AOI("Sevastopol", 44.61, 33.52, True),
    AOI("Feodosia", 45.03, 35.40, True),
    AOI("Kerch", 45.35, 36.47, True),
    AOI("Berdyansk", 46.75, 36.79, True),
    AOI("Mariupol", 47.10, 37.55, True),
    AOI("Port Kavkaz", 45.33, 36.67, False),
    AOI("Novorossiysk", 44.72, 37.79, False),
)

AOI_BY_NAME = {a.name: a for a in AOIS}
OCCUPIED = tuple(a for a in AOIS if a.occupied_ua)


def containing(lon, lat) -> np.ndarray:
    """Name of the AOI containing each point (nearest centre if circles overlap), else None.

    Kerch and Port Kavkaz are ~15 km apart, so their circles overlap; the nearest port
    centre wins, which keeps the assignment deterministic.
    """
    lon = np.atleast_1d(np.asarray(lon, dtype=float))
    lat = np.atleast_1d(np.asarray(lat, dtype=float))
    dists = np.stack([a.distance_km(lon, lat) for a in AOIS])  # (n_aoi, n_points)
    nearest = dists.argmin(axis=0)
    inside = dists[nearest, np.arange(lon.size)] <= np.array([a.radius_km for a in AOIS])[nearest]
    names = np.array([a.name for a in AOIS], dtype=object)[nearest]
    return np.where(inside, names, None)


def nearest_occupied(lon, lat) -> tuple[np.ndarray, np.ndarray]:
    """(name, distance_km) of the nearest occupied-UA AOI centre for each point."""
    lon = np.atleast_1d(np.asarray(lon, dtype=float))
    lat = np.atleast_1d(np.asarray(lat, dtype=float))
    dists = np.stack([a.distance_km(lon, lat) for a in OCCUPIED])
    idx = dists.argmin(axis=0)
    names = np.array([a.name for a in OCCUPIED], dtype=object)[idx]
    return names, dists[idx, np.arange(lon.size)]
