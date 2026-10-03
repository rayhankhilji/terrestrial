"""GNSS-interference grid and craft ranking on real saved ADS-B responses."""

import json
import time
from pathlib import Path

import pytest

from history.regions import parse_boundaries
from live.gnss import BAD_NACP, GnssGrid, level
from live.hub import Hub
from live.milclass import MilClassifier
from live.sources import adsb
from live.threat import ThreatBoard, Ukraine, score
from reference.aircraftdb import load_from as load_register
from reference.icao_ranges import load_from as load_ranges

FIX = Path(__file__).parent / "fixtures"
CLASSIFY = MilClassifier(
    load_register(FIX / "reference" / "basic_ac_db_sample.json.gz"),
    load_ranges(FIX / "reference" / "flags.js"),
)


@pytest.fixture(scope="module")
def ukraine():
    return Ukraine(parse_boundaries((FIX / "history" / "ukr_adm1_simplified.geojson").read_text()))


def _point_body():
    return json.loads((FIX / "live" / "adsbfi_point.json").read_text())


def test_gnss_levels_follow_gpsjam_thresholds():
    assert level(0.0) == "low" and level(0.02) == "low" and level(0.05) == "medium" and level(0.11) == "high"


def test_gnss_grid_bins_real_traffic_and_skips_version_0():
    body = _point_body()
    t = body["now"] / 1000 if body["now"] > 1e11 else body["now"]
    grid = GnssGrid()
    for ac in body["aircraft"]:
        grid.observe(ac, t)
    ents = grid.entities(int(t * 1000))
    assert ents and all(e["props"]["aircraft"] >= 3 for e in ents)
    seen = {h for c in grid.cells.values() for h in c.seen}
    v0 = [
        ac["hex"] for ac in body["aircraft"] if (ac.get("version") or 0) < 1 and ac.get("nac_p") is not None
    ]
    assert v0 and not set(v0) & seen
    # The saved response has v2 airliners over Romania/Bulgaria reporting NACp 0: high interference.
    assert any(e["props"]["level"] == "high" and e["props"]["degraded"] >= 2 for e in ents)
    for e in ents:
        bad = sum(1 for _, b, nacp in grid.cells[tuple(map(int, e["id"].split(":")[1:]))].seen.values() if b)
        assert bad == e["props"]["degraded"]
    assert all(nacp < BAD_NACP for c in grid.cells.values() for _, b, nacp in c.seen.values() if b)


def test_distance_to_ukraine(ukraine):
    assert ukraine.distance_km(30.52, 50.45)[0] == 0  # Kyiv
    assert ukraine.distance_km(33.52, 44.61)[0] == 0  # Sevastopol: Ukrainian territory
    d, _ = ukraine.distance_km(28.0, 43.2)  # off Varna
    assert 150 < d < 350


def test_friendly_military_is_never_scored_as_a_threat(ukraine):
    body = json.loads((FIX / "live" / "adsblol_mil_subset.json").read_text())
    t = time.time()
    for ac in body["ac"]:
        e = adsb.to_entity(ac, t, CLASSIFY(ac, True))
        if e is None:
            continue
        s = score(e, ukraine, {}, None, int(t * 1000))
        if e["props"]["state_code"] in ("us", "fr", "ca", "bg"):
            assert s["threat"] == 0
        assert 0 <= s["priority"] <= 100
        assert s["priority"] == min(100, round(s["threat"] + 0.5 * s["intel"]))


def test_unattributed_aircraft_near_ukraine_scores_threat_with_reasons(ukraine):
    body = _point_body()
    t = time.time()
    mil = [e for ac in body["aircraft"] if (e := adsb.to_entity(ac, t, CLASSIFY(ac)))]
    e = min(mil, key=lambda e: ukraine.distance_km(e["lon"], e["lat"])[0])  # BRIO66, ~120 km off Crimea
    e["props"] = {**e["props"], "state_code": None, "state": None}  # attribution removed for the test
    s = score(e, ukraine, {}, None, int(t * 1000))
    assert s["threat"] > 0
    assert any("Ukrainian territory" in r["reason"] for r in s["reasons"])
    assert any("not attributed" in r["reason"] for r in s["reasons"])


def test_board_annotates_live_entities(ukraine):
    hub = Hub()
    t = time.time()
    for ac in json.loads((FIX / "live" / "adsblol_mil_subset.json").read_text())["ac"]:
        if e := adsb.to_entity(ac, t, CLASSIFY(ac, True)):
            hub.upsert(e)
    board = ThreatBoard()
    board.ukraine = ukraine
    assert board.update(hub, int(t * 1000)) == len(hub.of_kind("aircraft"))
    assert all("threat" in e["props"] for e in hub.of_kind("aircraft"))
    # The annotation survives the next position report from the source.
    first = hub.of_kind("aircraft")[0]
    again = {
        **first,
        "props": {k: v for k, v in first["props"].items() if k != "threat"},
        "ts": first["ts"] + 1000,
        "lon": first["lon"] + 0.01,
    }
    hub.upsert(again)
    assert "threat" in hub.entities[first["id"]]["props"]
