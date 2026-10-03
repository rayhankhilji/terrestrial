"""Reachability ellipse tests (CLAUDE.md §6). Pure geometry: no fixtures needed."""

import math

import pytest
from shapely.geometry import Point

from pipeline import geo
from pipeline.config import IMPOSSIBLE_RADIUS_KM, KM_PER_NM
from pipeline.envelope import reachability_ellipse, vessel_class, vmax_for

V = 16.5  # 15 kn × 1.1 buffer


def test_coincident_endpoints_give_circle_of_radius_half_d():
    lon, lat, hours = 32.0, 43.5, 20.0
    env = reachability_ellipse(lon, lat, lon, lat, hours, V)

    half_d = V * hours * KM_PER_NM / 2
    assert not env.impossible
    assert env.polygon.is_valid
    assert env.semi_major_km == pytest.approx(half_d)
    assert env.semi_minor_km == pytest.approx(half_d)
    for vx, vy in env.polygon.exterior.coords:
        assert geo.distance_km(lon, lat, vx, vy) == pytest.approx(half_d, rel=1e-3)


def test_known_gap_contains_midpoint_and_excludes_beyond_reach():
    a = (31.0, 43.4)
    b = (33.2, 44.1)
    hours = 30.0
    env = reachability_ellipse(*a, *b, hours, V)

    d = V * hours * KM_PER_NM
    mid = geo.midpoint(*a, *b)
    assert not env.impossible
    assert env.polygon.is_valid
    assert env.max_distance_km == pytest.approx(d)
    assert env.polygon.contains(Point(mid))
    assert env.polygon.contains(Point(a)) and env.polygon.contains(Point(b))
    # A point D + 1 km from the midpoint is unreachable in every direction.
    for azimuth in range(0, 360, 30):
        far = geo.destination(*mid, azimuth, d + 1)
        assert not env.polygon.contains(Point(far))


def test_ellipse_axes_are_oriented_along_the_gap():
    a = (30.5, 43.0)
    b = (34.0, 44.0)
    hours = 24.0
    env = reachability_ellipse(*a, *b, hours, V)

    mid = geo.midpoint(*a, *b)
    az_major, _, _ = geo.GEOD.inv(*mid, *b)
    for azimuth, semi_axis in (
        (az_major, env.semi_major_km),
        (az_major + 180, env.semi_major_km),
        (az_major + 90, env.semi_minor_km),
        (az_major - 90, env.semi_minor_km),
    ):
        assert env.polygon.contains(Point(geo.destination(*mid, azimuth, semi_axis - 2)))
        assert not env.polygon.contains(Point(geo.destination(*mid, azimuth, semi_axis + 2)))
    # The foci identity b² = a² − c² holds.
    assert env.semi_minor_km == pytest.approx(math.sqrt(env.semi_major_km**2 - env.focal_half_km**2))


def test_impossible_gap_sets_flag_and_falls_back_to_circle_at_b():
    a = (30.0, 43.0)
    b = (32.5, 43.0)  # ~200 km apart
    env = reachability_ellipse(*a, *b, 2.0, V)  # D ≈ 61 km

    assert env.impossible
    assert env.semi_minor_km == 0.0
    assert env.polygon.contains(Point(b))
    assert not env.polygon.contains(Point(a))
    for vx, vy in env.polygon.exterior.coords:
        assert geo.distance_km(*b, vx, vy) == pytest.approx(IMPOSSIBLE_RADIUS_KM, rel=1e-3)


@pytest.mark.parametrize("hours", [0.0, -3.0, float("nan")])
def test_non_positive_duration_raises(hours):
    with pytest.raises(ValueError):
        reachability_ellipse(31.0, 43.0, 31.5, 43.2, hours, V)


def test_non_finite_coordinates_raise():
    with pytest.raises(ValueError):
        reachability_ellipse(float("nan"), 43.0, 31.5, 43.2, 10.0, V)


@pytest.mark.parametrize(
    ("vessel_type", "expected"),
    [
        ("Oil Tanker", "tanker"),
        ("CARGO", "cargo"),
        ("bulk_carrier", "cargo"),
        ("fishing", "default"),
        (None, "default"),
    ],
)
def test_vessel_class_mapping(vessel_type, expected):
    assert vessel_class(vessel_type) == expected


def test_vmax_includes_buffer():
    assert vmax_for("tanker") == pytest.approx(16.5)
    assert vmax_for("cargo") == pytest.approx(15.4)
