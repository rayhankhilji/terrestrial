"""Live layer: source parsers on real saved responses, hub fan-out, correlator and sentinels."""

import json
import time
from pathlib import Path

import pytest

from live.correlate import Correlator
from live.hub import Hub, now_ms
from live.sentinels import DEFAULTS, SentinelEngine, validate
from live.sources import adsb, gdelt, wikidata
from pipeline.config import THEATRE_BBOX

FIX = Path(__file__).parent / "fixtures" / "live"


def _load(name):
    return json.loads((FIX / name).read_text())


def test_adsb_point_response_parses_to_aircraft_entities():
    body = _load("adsbfi_point.json")
    received = time.time()
    entities = [e for ac in body["aircraft"] if (e := adsb.to_entity(ac, received, False))]
    assert entities
    for e in entities:
        assert e["id"].startswith("aircraft:") and e["kind"] == "aircraft"
        assert -90 <= e["lat"] <= 90 and -180 <= e["lon"] <= 180
        assert e["alt"] >= 0
        assert e["ts"] <= received * 1000 + 1


def test_adsb_military_feed_marks_aircraft_military():
    body = _load("adsblol_mil_subset.json")
    entities = [e for ac in body["ac"] if (e := adsb.to_entity(ac, time.time(), True))]
    assert entities and all(e["props"]["military"] for e in entities)


def test_gdelt_parser_keeps_located_force_events_in_theatre():
    text = (FIX / "gdelt_export_sample.tsv").read_text()
    events = gdelt.parse(text)
    assert events
    min_lon, min_lat, max_lon, max_lat = THEATRE_BBOX
    for e in events:
        assert min_lat <= e["lat"] <= max_lat and min_lon <= e["lon"] <= max_lon
        assert e["props"]["url"].startswith("http")
        assert all(c[:2] in gdelt.ROOTS for c in e["props"]["codes"])
    assert len({e["id"] for e in events}) == len(events)


def test_gdelt_rejects_schema_change():
    with pytest.raises(ValueError):
        gdelt.parse("only\tthree\tcolumns\n")


def _facilities():
    return _load("wikidata_facilities_subset.json")


def test_fire_near_refinery_links_and_alerts_with_provenance():
    hub = Hub()
    facilities = _facilities()
    for f in facilities:
        hub.upsert(wikidata.to_entity(f))
    hub.listeners.append(Correlator(facilities))
    refinery = next(f for f in facilities if f["type"] == "refinery")
    hub.upsert(
        {
            "id": "fire:unit",
            "kind": "fire",
            "label": "Thermal anomaly",
            "lon": refinery["lon"] + 0.01,
            "lat": refinery["lat"],
            "ts": now_ms(),
            "src": "firms",
            "prov": "observed",
            "props": {},
        }
    )
    links = [r for r in hub.relations.values() if r["rel"] == "THERMAL_ANOMALY_AT"]
    assert links and all(r["prov"] == "inferred" and "fire:unit" in r["why"] for r in links)
    assert any(a["title"].startswith("Thermal anomaly at") for a in hub.alerts)


def test_default_sentinels_are_valid_and_cycles_are_rejected():
    for s in DEFAULTS:
        validate(s)
    bad = {
        "id": "x",
        "name": "cycle",
        "nodes": [
            {"id": "s", "type": "source", "params": {"kind": "aircraft"}},
            {"id": "a", "type": "and", "params": {}},
            {"id": "b", "type": "and", "params": {}},
            {"id": "z", "type": "alert", "params": {}},
        ],
        "edges": [["s", "a"], ["a", "b"], ["b", "a"], ["b", "z"]],
    }
    with pytest.raises(ValueError, match="cycle"):
        validate(bad)


def test_sentinel_fires_once_per_cooldown_on_real_military_aircraft(tmp_path, monkeypatch):
    monkeypatch.setattr("live.sentinels.STORE", tmp_path / "sentinels.json")
    engine = SentinelEngine()
    engine.put(
        {
            "id": "mil",
            "name": "Military aircraft",
            "enabled": True,
            "nodes": [
                {"id": "s", "type": "source", "params": {"kind": "aircraft"}},
                {
                    "id": "m",
                    "type": "compare",
                    "params": {"field": "props.military", "op": "==", "value": True},
                },
                {
                    "id": "a",
                    "type": "alert",
                    "params": {"severity": "info", "title": "Mil {label}", "cooldown_min": 60},
                },
            ],
            "edges": [["s", "m"], ["m", "a"]],
        }
    )
    hub = Hub()
    hub.listeners.append(engine)
    ac = _load("adsblol_mil_subset.json")["ac"][0]
    entity = adsb.to_entity(ac, time.time(), True)
    hub.upsert(entity)
    moved = {**entity, "lat": entity["lat"] + 0.01, "ts": entity["ts"] + 1000}
    hub.upsert(moved)
    fired = [a for a in hub.alerts if a.get("sentinel") == "mil"]
    assert len(fired) == 1 and fired[0]["title"] == f"Mil {entity['label']}"
    assert engine.hits["mil"]["a"] == 2  # the node passed twice; the cooldown suppressed one alert


def test_hub_fans_out_and_expires():
    hub = Hub()
    q = hub.subscribe()
    body = _load("adsbfi_point.json")
    entity = adsb.to_entity(body["aircraft"][0], time.time(), False)
    hub.upsert(entity)
    assert q.get_nowait()["t"] == "upsert"
    hub.entities[entity["id"]]["rx"] -= 10 * 60 * 1000
    hub.expire()
    assert entity["id"] not in hub.entities
    assert q.get_nowait() == {"t": "remove", "ids": [entity["id"]]}
