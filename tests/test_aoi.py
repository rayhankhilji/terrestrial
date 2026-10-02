"""AOI geometry helpers."""

import pytest

from pipeline import aoi


def test_point_at_port_centre_is_inside():
    assert aoi.containing(33.52, 44.61)[0] == "Sevastopol"


def test_open_sea_point_is_outside_every_aoi():
    assert aoi.containing(32.0, 43.5)[0] is None


def test_overlapping_circles_resolve_to_nearest_centre():
    # Kerch (36.47, 45.35) and Port Kavkaz (36.67, 45.33) overlap; a point by Port Kavkaz.
    assert aoi.containing(36.66, 45.33)[0] == "Port Kavkaz"
    assert aoi.containing(36.48, 45.35)[0] == "Kerch"


def test_nearest_occupied_distance():
    names, dists = aoi.nearest_occupied([33.52, 37.79], [44.61, 44.72])
    assert names[0] == "Sevastopol" and dists[0] == pytest.approx(0.0, abs=1e-6)
    # Novorossiysk is not occupied; its nearest occupied AOI is Kerch (~120 km).
    assert names[1] == "Kerch" and 100 < dists[1] < 150
