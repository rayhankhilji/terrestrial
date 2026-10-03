"""Reachability envelope of an AIS gap (CLAUDE.md §6).

A ship that switched AIS off at A and back on at B after T hours, never exceeding v knots,
can only have visited points P with dist(A,P) + dist(P,B) <= D = v·T·1.852 km: an ellipse
with foci A and B. It is built in an azimuthal-equidistant projection centred on the A–B
midpoint, where distances and azimuths from the centre are exact.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
from shapely.geometry import Polygon

from pipeline import geo
from pipeline.config import (
    ELLIPSE_POINTS,
    IMPOSSIBLE_RADIUS_KM,
    KM_PER_NM,
    SPEED_BUFFER,
    VMAX_KN,
)
from pipeline.io import read_table, write_table

# Keep a strictly positive minor axis so a gap flown at exactly max speed still yields a
# valid (very thin) polygon instead of a degenerate line.
MIN_SEMI_MINOR_KM = 0.5


@dataclass(frozen=True)
class Envelope:
    polygon: Polygon
    vmax_kn: float
    max_distance_km: float  # D
    semi_major_km: float  # a = D / 2
    semi_minor_km: float  # b = sqrt(a² - c²); 0 when impossible
    focal_half_km: float  # c = dist(A, B) / 2
    impossible: bool


def vessel_class(vessel_type: str | None) -> str:
    """Map a free-text vessel type to a speed class key of VMAX_KN."""
    text = (vessel_type or "").lower()
    if "tanker" in text:
        return "tanker"
    if any(k in text for k in ("cargo", "bulk", "container", "general")):
        return "cargo"
    return "default"


def vmax_for(vessel_type: str | None) -> float:
    """Max speed in knots for a vessel type, including the safety buffer."""
    return VMAX_KN[vessel_class(vessel_type)] * SPEED_BUFFER


def reachability_ellipse(
    a_lon: float,
    a_lat: float,
    b_lon: float,
    b_lat: float,
    duration_h: float,
    vmax_kn: float,
    n_points: int = ELLIPSE_POINTS,
) -> Envelope:
    """Ellipse of all points reachable between AIS-off at A and AIS-on at B."""
    coords = (a_lon, a_lat, b_lon, b_lat)
    if not all(math.isfinite(v) for v in coords):
        raise ValueError(f"gap endpoints must be finite, got A=({a_lon}, {a_lat}) B=({b_lon}, {b_lat})")
    if not (math.isfinite(duration_h) and duration_h > 0):
        raise ValueError(f"gap duration must be positive hours, got {duration_h}")
    if not (math.isfinite(vmax_kn) and vmax_kn > 0):
        raise ValueError(f"max speed must be positive knots, got {vmax_kn}")

    max_distance = vmax_kn * duration_h * KM_PER_NM
    a = max_distance / 2.0
    c = geo.distance_km(a_lon, a_lat, b_lon, b_lat) / 2.0

    if a < c:
        return Envelope(
            polygon=geo.circle(b_lon, b_lat, IMPOSSIBLE_RADIUS_KM, n_points),
            vmax_kn=vmax_kn,
            max_distance_km=max_distance,
            semi_major_km=a,
            semi_minor_km=0.0,
            focal_half_km=c,
            impossible=True,
        )

    b = max(math.sqrt(a * a - c * c), MIN_SEMI_MINOR_KM)
    mid_lon, mid_lat = geo.midpoint(a_lon, a_lat, b_lon, b_lat)
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={mid_lat} +lon_0={mid_lon} +datum=WGS84 +units=m +no_defs")
    to_local = Transformer.from_crs("EPSG:4326", local, always_xy=True)
    to_wgs84 = Transformer.from_crs(local, "EPSG:4326", always_xy=True)

    theta = 0.0
    if c > 0:
        ax, ay = to_local.transform(a_lon, a_lat)
        bx, by = to_local.transform(b_lon, b_lat)
        theta = math.atan2(by - ay, bx - ax)

    t = np.linspace(0.0, 2.0 * math.pi, n_points, endpoint=False)
    x = a * 1e3 * np.cos(t)
    y = b * 1e3 * np.sin(t)
    xr = x * math.cos(theta) - y * math.sin(theta)
    yr = x * math.sin(theta) + y * math.cos(theta)
    lons, lats = to_wgs84.transform(xr, yr)

    return Envelope(
        polygon=Polygon(zip(lons, lats, strict=True)),
        vmax_kn=vmax_kn,
        max_distance_km=max_distance,
        semi_major_km=a,
        semi_minor_km=b,
        focal_half_km=c,
        impossible=False,
    )


log = logging.getLogger("terrestrial")


def envelopes(gaps: pd.DataFrame, vessels: pd.DataFrame) -> pd.DataFrame:
    """One reachability envelope per closed gap."""
    vessel_type = vessels.set_index("vessel_id")["vessel_type"].to_dict()
    rows = []
    for g in gaps.itertuples(index=False):
        vtype = vessel_type.get(g.vessel_id)
        env = reachability_ellipse(g.off_lon, g.off_lat, g.on_lon, g.on_lat, g.duration_h, vmax_for(vtype))
        rows.append(
            {
                "gap_id": g.gap_id,
                "vessel_id": g.vessel_id,
                "polygon_wkt": env.polygon.wkt,
                "vmax_kn": env.vmax_kn,
                "speed_class": vessel_class(vtype),
                "max_distance_km": env.max_distance_km,
                "semi_major_km": env.semi_major_km,
                "semi_minor_km": env.semi_minor_km,
                "focal_half_km": env.focal_half_km,
                "area_km2": math.pi * env.semi_major_km * env.semi_minor_km
                if not env.impossible
                else math.pi * IMPOSSIBLE_RADIUS_KM**2,
                "impossible": env.impossible,
            }
        )
    return pd.DataFrame(rows)


def stage(start: date, end: date, args) -> None:
    gaps = read_table("gaps")
    closed = gaps[~gaps["open"]]
    log.info("  in: %d gaps (%d closed)", len(gaps), len(closed))
    out = envelopes(closed, read_table("vessels"))
    write_table(out, "gap_envelopes")
    log.info("  %d impossible gaps (could not cover A→B at max speed)", int(out["impossible"].sum()))
