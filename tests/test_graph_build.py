"""Graph build (CLAUDE.md §8): base import lines, per-day changes and edge statements.

Vessel/event frames are minimal synthetic tables; sanctions come from the real sample fixture.
"""

import json
import uuid
from pathlib import Path

import pandas as pd
import pytest

from graph.build import _create_edges, _create_nodes, base_jsonl, day_changes
from graph.client import TuringDB
from graph.queries import _expand
from pipeline.config import TURING_DIR
from pipeline.opensanctions import normalise as normalise_sanctions

SAMPLE = Path(__file__).parent / "fixtures" / "opensanctions" / "maritime_sample.csv"
DAY = pd.Timestamp("2026-08-01", tz="UTC")


def _scores(sanctions):
    listed = sanctions[sanctions["sanctioned"] & sanctions["imo"].notna()].iloc[0]
    return pd.DataFrame(
        [
            {"vessel_id": "v1", "name": "ALPHA", "flag": "RUS", "imo": listed["imo"], "mmsi": None, "vessel_type": "tanker", "risk": 70, "listed": True},
            {"vessel_id": "v2", "name": None, "flag": None, "imo": None, "mmsi": "273000000", "vessel_type": None, "risk": 10, "listed": False},
        ]
    )  # fmt: skip


def test_base_jsonl_ids_are_dense_and_edges_point_at_existing_nodes():
    sanctions = normalise_sanctions(SAMPLE)
    lines = base_jsonl(_scores(sanctions), sanctions)
    nodes = [line for line in lines if line["type"] == "node"]
    rels = [line for line in lines if line["type"] == "relationship"]
    assert [int(n["id"]) for n in nodes] == list(range(len(nodes)))
    assert [int(r["id"]) for r in rels] == list(range(len(rels)))
    node_ids = {n["id"] for n in nodes}
    assert len(rels) == 1 and rels[0]["label"] == "LISTED"
    assert rels[0]["start"]["id"] in node_ids and rels[0]["end"]["id"] in node_ids
    assert rels[0]["properties"] == {"provenance": "observed", "source": "OpenSanctions"}
    titles = {n["properties"]["uid"]: n["properties"]["title"] for n in nodes}
    assert titles["vessel:v2"] == "v2"  # falls back to the id when the name is missing
    assert "port:Sevastopol" in titles
    # Null properties are dropped rather than written as null.
    v2 = next(n for n in nodes if n["properties"]["uid"] == "vessel:v2")
    assert "imo" not in v2["properties"] and "name" not in v2["properties"]


def _tables(gaps=(), candidates=(), encounters=(), visits=()):
    return {
        "gaps": pd.DataFrame(
            list(gaps),
            columns=["gap_id", "vessel_id", "start", "end", "duration_h", "off_lon", "off_lat", "on_lon", "on_lat", "open"],
        ),
        "candidates": pd.DataFrame(list(candidates), columns=["gap_id", "sar_id", "ts", "lon", "lat", "consistent", "in_aoi"]),
        "encounters": pd.DataFrame(
            list(encounters), columns=["enc_id", "vessel_id", "other_vessel_id", "start", "end", "lon", "lat"]
        ),
        "port_visits": pd.DataFrame(list(visits), columns=["visit_id", "vessel_id", "start", "end", "aoi"]),
        "impossible": {"g1"},
        "known_vessels": {"v1", "v2"},
    }  # fmt: skip


def test_day_changes_builds_nodes_and_observed_or_inferred_edges():
    t0 = DAY + pd.Timedelta(hours=3)
    t = _tables(
        gaps=[
            # Ends near Sevastopol (44.61, 33.52) → NEAR edge.
            {"gap_id": "g1", "vessel_id": "v1", "start": t0, "end": t0 + pd.Timedelta(hours=30), "duration_h": 30.0,
             "off_lon": 31.0, "off_lat": 43.0, "on_lon": 33.5, "on_lat": 44.5, "open": False},
            {"gap_id": "g2", "vessel_id": "ghost", "start": t0, "end": pd.NaT, "duration_h": 12.0,
             "off_lon": 31.0, "off_lat": 43.0, "on_lon": None, "on_lat": None, "open": True},
            # Next day: not part of this change.
            {"gap_id": "g3", "vessel_id": "v1", "start": DAY + pd.Timedelta(days=1), "end": DAY + pd.Timedelta(days=2),
             "duration_h": 24.0, "off_lon": 31.0, "off_lat": 43.0, "on_lon": 31.5, "on_lat": 43.2, "open": False},
        ],
        candidates=[
            {"gap_id": "g1", "sar_id": "s1", "ts": t0 + pd.Timedelta(hours=5), "lon": 33.52, "lat": 44.61, "consistent": True, "in_aoi": "Sevastopol"},
        ],
        encounters=[
            # GFW lists each encounter once per participant: must collapse to one node.
            {"enc_id": "e1", "vessel_id": "v1", "other_vessel_id": "v2", "start": t0, "end": t0 + pd.Timedelta(hours=4), "lon": 37.0, "lat": 44.0},
            {"enc_id": "e1b", "vessel_id": "v2", "other_vessel_id": "v1", "start": t0, "end": t0 + pd.Timedelta(hours=4), "lon": 37.0, "lat": 44.0},
        ],
        visits=[
            {"visit_id": "p1", "vessel_id": "v2", "start": t0, "end": t0 + pd.Timedelta(hours=8), "aoi": "Novorossiysk"},
            {"visit_id": "p2", "vessel_id": "v2", "start": t0, "end": t0 + pd.Timedelta(hours=8), "aoi": None},
        ],
    )  # fmt: skip
    nodes, edges = day_changes(DAY, t)
    assert [n["gap_id"] for n in nodes["Gap"]] == ["g1", "g2"]
    g1 = nodes["Gap"][0]
    assert g1["impossible"] is True and g1["day"] == "2026-08-01"
    assert nodes["Gap"][1]["end"] is None and nodes["Gap"][1]["on_lat"] is None
    assert len(nodes["Encounter"]) == 1 and len(nodes["SarDetection"]) == 1

    by_label = {}
    for a, label, b, p in edges:
        by_label.setdefault(label, []).append((a, b, p))
    assert by_label["HAD_GAP"] == [
        ("vessel:v1", "gap:g1", {"provenance": "observed", "source": "GFW events"})
    ]
    assert ("gap:g1", "port:Sevastopol") in [(a, b) for a, b, _ in by_label["NEAR"]]
    assert by_label["CANDIDATE"][0][2]["provenance"] == "inferred"
    assert by_label["AT"] == [("sar:s1", "port:Sevastopol", {"provenance": "observed", "source": "GFW SAR"})]
    assert sorted(a for a, _, _ in by_label["IN_ENCOUNTER"]) == ["vessel:v1", "vessel:v2"]
    assert [(a, b) for a, b, _ in by_label["VISITED"]] == [("vessel:v2", "port:Novorossiysk")]


class _Recorder:
    def __init__(self):
        self.statements = []

    def query(self, cypher, **_):
        self.statements.append(cypher)
        return []


def test_create_edges_matches_endpoints_by_quoted_uid():
    db = _Recorder()
    _create_edges(db, [("vessel:O'NEIL", "HAD_GAP", "gap:g1", {"provenance": "observed"})])
    (stmt,) = db.statements
    assert "{uid: 'vessel:O\\'NEIL'}" in stmt and "{uid: 'gap:g1'}" in stmt
    assert "-[:HAD_GAP {provenance: 'observed'}]->" in stmt


@pytest.mark.graph
def test_load_day_change_and_expand_network_on_server():
    """End to end against a live TuringDB: base JSONL import, one daily change, network walk."""
    sanctions = normalise_sanctions(SAMPLE)
    graph = f"test_build_{uuid.uuid4().hex[:8]}"
    jsonl = TURING_DIR / "data" / f"{graph}.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text("\n".join(json.dumps(line) for line in base_jsonl(_scores(sanctions), sanctions)) + "\n")
    try:
        db = TuringDB()
        db.query(f"LOAD JSONL '{jsonl.name}' AS {graph}", graph="default")
        db.use(graph)
        base = db.head()
        t0 = DAY + pd.Timedelta(hours=3)
        nodes, edges = day_changes(
            DAY,
            _tables(
                encounters=[
                    {"enc_id": "e1", "vessel_id": "v1", "other_vessel_id": "v2", "start": t0,
                     "end": t0 + pd.Timedelta(hours=4), "lon": 37.0, "lat": 44.0},
                ],
                visits=[{"visit_id": "p1", "vessel_id": "v2", "start": t0, "end": t0, "aoi": "Novorossiysk"}],
            ),
        )  # fmt: skip
        with db.change():
            for label, rows in nodes.items():
                if rows:
                    _create_nodes(db, label, rows)
            db.commit()
            _create_edges(db, edges)
        after = db.head()
        assert after != base

        net = _expand(db, "vessel:v1", 2)
        assert {"vessel:v1", "vessel:v2", "enc:e1"} <= set(net.nodes)
        assert net.links["vessel:v2|enc:e1"]["via"] == "encounter"
        with db.at_commit(base):
            assert set(_expand(db, "vessel:v1", 2).nodes) == {"vessel:v1"}
    finally:
        jsonl.unlink(missing_ok=True)
