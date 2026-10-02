"""Geodesy helpers on the WGS84 ellipsoid (pyproj) plus shapely constructors.

All distances are geodesic kilometres. Coordinates are always (lon, lat) in that order,
matching GeoJSON.
"""

from __future__ import annotations

import numpy as np
from pyproj import Geod
from shapely.geometry import Polygon

GEOD = Geod(ellps="WGS84")


def distance_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    _, _, metres = GEOD.inv(lon1, lat1, lon2, lat2)
    return metres / 1000.0


def distance_km_many(lon1, lat1, lon2, lat2) -> np.ndarray:
    """Vectorised geodesic distance; arguments broadcast like numpy arrays."""
    lon1, lat1, lon2, lat2 = np.broadcast_arrays(
        np.asarray(lon1, dtype=float),
        np.asarray(lat1, dtype=float),
        np.asarray(lon2, dtype=float),
        np.asarray(lat2, dtype=float),
    )
    _, _, metres = GEOD.inv(lon1, lat1, lon2, lat2)
    return np.asarray(metres) / 1000.0


def midpoint(lon1: float, lat1: float, lon2: float, lat2: float) -> tuple[float, float]:
    """Geodesic midpoint of the A→B segment."""
    azimuth, _, metres = GEOD.inv(lon1, lat1, lon2, lat2)
    if metres == 0:
        return lon1, lat1
    lon, lat, _ = GEOD.fwd(lon1, lat1, azimuth, metres / 2.0)
    return float(lon), float(lat)


def destination(lon: float, lat: float, azimuth_deg: float, km: float) -> tuple[float, float]:
    out_lon, out_lat, _ = GEOD.fwd(lon, lat, azimuth_deg, km * 1000.0)
    return float(out_lon), float(out_lat)


def circle(lon: float, lat: float, radius_km: float, n_points: int = 64) -> Polygon:
    """Geodesic circle as a polygon with `n_points` vertices."""
    if radius_km <= 0:
        raise ValueError(f"circle radius must be positive, got {radius_km}")
    azimuths = np.linspace(0.0, 360.0, n_points, endpoint=False)
    lons, lats, _ = GEOD.fwd(
        np.full(n_points, lon), np.full(n_points, lat), azimuths, np.full(n_points, radius_km * 1e3)
    )
    return Polygon(zip(lons, lats, strict=True))
