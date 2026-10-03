"""Nets engine on real recorded tracks, plus the pair scorer on constructed geometry.

- recorded_tanker_orbit_clean73.jsonl: USAF tanker CLEAN73 holding an orbit (real recording).
- recorded_mai_helicopters.jsonl: four Romanian Interior Ministry EC135s flying separate
  missions in different cities on the same day (real recording): they must not form a net.
"""

import json
from pathlib import Path

import numpy as np

from live.hub import Hub
from live.nets import NetsEngine, TrackView, rule_mission, score_pair
from live.tracks import TrackStore

FIX = Path(__file__).parent / "fixtures" / "live"


def _replay(name):
    store, entities = TrackStore(), {}
    for line in (FIX / name).read_text().splitlines():
        e = json.loads(line)["e"]
        store.add(e)
        entities[e["id"]] = e
    return store, entities


def test_tanker_orbit_is_a_one_member_refuelling_net():
    store, entities = _replay("recorded_tanker_orbit_clean73.jsonl")
    engine = NetsEngine(store)
    now = max(p.ts for pts in store.points.values() for p in pts)
    views, links, groups = engine.compute(entities, now)
    assert groups == [{"aircraft:ae63ca"}]
    (v,) = views
    assert v.orbit is not None and v.props["role"] == "tanker"
    assert rule_mission(views, links) == "air_refuelling"


def test_helicopters_on_separate_missions_do_not_form_a_net():
    store, entities = _replay("recorded_mai_helicopters.jsonl")
    engine = NetsEngine(store)
    stamps = sorted(p.ts for pts in store.points.values() for p in pts)
    for now in stamps[len(stamps) // 4 :: max(1, len(stamps) // 8)]:
        visible = {k: v for k, v in entities.items() if k in store.points}
        _, links, groups = engine.compute(visible, now)
        assert links == [] and groups == []


def _view(id_, callsign, lon0, role="fighter", state="us"):
    t = np.arange(0, 30 * 60, 30.0) + 1_000_000
    lon = lon0 + np.linspace(0, 1.5, len(t))
    return TrackView(
        id=id_, label=callsign, props={"callsign": callsign, "role": role, "state_code": state},
        t=t, lon=lon, lat=np.full(len(t), 45.0), alt=np.full(len(t), 9000.0),
        hdg=np.full(len(t), 90.0), ground=np.zeros(len(t), dtype=bool),
    )  # fmt: skip


def test_pair_flying_in_formation_is_linked_with_reasons():
    a, b = _view("aircraft:a", "VIPER11", 30.0), _view("aircraft:b", "VIPER12", 30.01)
    grid = np.arange(1_000_000, 1_000_000 + 30 * 60, 60.0)
    link = score_pair(a, b, grid, {})
    assert link is not None and link.weight >= 0.9
    assert any("callsign series VIPER" in r for r in link.why)
    assert any("within 25 km for 100%" in r for r in link.why)
    far = _view("aircraft:c", "EAGLE1", 36.0)
    assert score_pair(a, far, grid, {}) is None


def test_net_ids_are_stable_while_membership_overlaps():
    engine = NetsEngine(TrackStore())
    (first,) = engine._assign_ids([{"a", "b", "c"}], now=1)[0].values()
    engine.nets = {first.id: first}
    current, formed, dissolved = engine._assign_ids([{"a", "b", "c", "d"}], now=2)
    assert list(current) == [first.id] and formed == [] and dissolved == []
    engine.nets = current
    current, formed, dissolved = engine._assign_ids([{"x", "y"}], now=3)
    assert first.id not in current and [n.id for n in dissolved] == [first.id] and len(formed) == 1


def test_update_publishes_net_entities_and_removes_dissolved():
    store, entities = _replay("recorded_tanker_orbit_clean73.jsonl")
    hub = Hub()
    hub.entities.update(entities)
    engine = NetsEngine(store)
    now = max(p.ts for pts in store.points.values() for p in pts)
    (net,) = engine.update(hub, now)
    e = hub.entities[net.id]
    assert e["kind"] == "net" and e["prov"] == "inferred"
    assert e["props"]["mission"] == "air_refuelling" and e["props"]["mission_src"] == "rule"
    assert e["props"]["members"][0]["id"] == "aircraft:ae63ca" and len(e["props"]["hull"]) > 3
    assert any(a["title"].startswith("Net formed") for a in hub.alerts)
    engine.update(hub, now + 2 * 3600 * 1000)  # the tanker is no longer active: the net dissolves
    assert net.id not in hub.entities
