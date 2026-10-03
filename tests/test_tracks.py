"""Track store: thinning, flight segmentation and landing airfield, on a real recorded track.

`recorded_track_rch647.jsonl` holds verbatim hub recording lines for a USAF C-17 (RCH647)
on final approach into Varna, ending on the ground.
"""

import json
from pathlib import Path

from live.tracks import AirfieldIndex, TrackStore
from reference.airfields import parse as parse_airfields

FIX = Path(__file__).parent / "fixtures"
ID = "aircraft:ae0812"


def _upserts():
    return [
        json.loads(line)["e"]
        for line in (FIX / "live" / "recorded_track_rch647.jsonl").read_text().splitlines()
    ]


def _store():
    return TrackStore(AirfieldIndex(parse_airfields(FIX / "reference" / "airports_sample.csv")))


def test_thinning_keeps_fewer_points_but_the_landing():
    store = _store()
    upserts = _upserts()
    for e in upserts:
        store(None, e)
    pts = store.points[ID]
    assert 10 < len(pts) < len(upserts)
    assert pts[-1].ground  # the ground transition is never thinned away
    assert all(b.ts > a.ts for a, b in zip(pts, list(pts)[1:], strict=False))


def test_landing_matched_to_airfield():
    store = _store()
    for e in _upserts():
        store(None, e)
    last_ts = store.points[ID][-1].ts
    flights = store.flights(ID, hours=48, now=last_ts + 1000)
    landing = flights[-1]["landing"]
    assert landing is not None and landing["icao"] == "LBWN" and landing["km"] < 8
    track = store.track(ID)
    assert track["label"] == "RCH647" and track["points"] == len(store.points[ID])


def test_rebuild_from_recordings(tmp_path):
    from datetime import UTC, datetime

    day = datetime.now(UTC).strftime("%Y%m%d")
    (tmp_path / f"{day}.jsonl").write_text((FIX / "live" / "recorded_track_rch647.jsonl").read_text())
    store = TrackStore()
    assert store.rebuild(tmp_path) > 0
    assert ID in store.points


def test_untracked_entities_are_ignored():
    store = TrackStore()
    store(None, {"id": "news:1", "kind": "news", "lon": 30.0, "lat": 45.0, "ts": 1, "props": {}})
    store(
        None,
        {"id": "vessel:1", "kind": "vessel", "lon": 30.0, "lat": 45.0, "ts": 1, "props": {"military": False}},
    )
    assert store.points == {}
