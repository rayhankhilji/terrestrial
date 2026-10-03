"""DeepState front-line parsing on a verbatim subset of a real /api/history/last response."""

import json
from pathlib import Path

import pytest
from shapely.ops import unary_union

from history.deepstate import describe_update, parse
from history.regions import parse_boundaries

FIX = Path(__file__).parent / "fixtures" / "history"


@pytest.fixture(scope="module")
def front():
    ukraine = unary_union(
        [g.geometry for g in parse_boundaries((FIX / "ukr_adm1_simplified.geojson").read_text()).values()]
    )
    return parse(json.loads((FIX / "deepstate_last_sample.json").read_text()), ukraine)


def test_occupied_ukraine_only(front):
    assert front.occupied_contains(34.10, 44.95)  # Simferopol, Crimea
    assert front.occupied_contains(37.80, 48.00)  # Donetsk (ORDLO)
    assert not front.occupied_contains(30.52, 50.45)  # Kyiv
    # DeepState's political polygons outside Ukraine are never "occupied Ukraine".
    assert not front.occupied_contains(20.51, 54.71)  # Kaliningrad ("Prussia")
    assert not front.occupied_contains(29.63, 46.84)  # Tiraspol (Transnistria)


def test_points_are_classified_by_key(front):
    assert len(front.attack_directions) == 6
    assert len(front.units) == 5 and all(u["key"].startswith("geoJSON.units.") for u in front.units)
    assert len(front.airfields) == 6 and all("///" not in a["name"] for a in front.airfields)


def test_front_line_and_geojson(front):
    assert not front.front.is_empty
    gj = front.geojson()
    layers = {f["properties"]["layer"] for f in gj["features"]}
    assert {"occupied", "unknown", "front", "attack_direction", "unit", "airfield"} <= layers
    assert gj["snapshot"] == 1790879037


def test_update_text_is_plain_english():
    entries = json.loads((FIX / "deepstate_history_index_tail.json").read_text())
    text = describe_update(entries[-1])
    assert text and "<" not in text and "href" not in text
