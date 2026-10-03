"""TuringDB client: literal rendering (pure) and an integration round-trip (needs server)."""

import uuid

import pytest

from graph.client import TuringDB, TuringDBError, literal, props


def test_literals_escape_quotes_and_backslashes():
    assert literal("O'NEIL") == "'O\\'NEIL'"
    assert literal("a\\b") == "'a\\\\b'"
    assert literal(True) == "true"
    assert literal(3) == "3"
    assert literal(2.5) == "2.5"


def test_non_finite_float_is_rejected():
    with pytest.raises(ValueError):
        literal(float("inf"))


def test_props_drop_nulls_and_validate_keys():
    assert props({"a": 1, "b": None, "c": float("nan"), "d": "x"}) == "{a: 1, d: 'x'}"
    with pytest.raises(ValueError):
        props({"bad key": 1})


@pytest.mark.graph
def test_change_commit_and_time_travel_round_trip():
    db = TuringDB()
    name = f"test_{uuid.uuid4().hex[:8]}"
    db.query(f"CREATE GRAPH {name}")
    db.use(name)
    with db.change():
        db.query("CREATE (:Vessel {vessel_id: 'a', name: 'O\\'NEIL'}), (:Port {name: 'Kerch'})")
    before_edges = db.head()
    with db.change():
        db.query(
            "MATCH (v:Vessel {vessel_id: 'a'}), (p:Port {name: 'Kerch'}) "
            "CREATE (v)-[:VISITED {provenance: 'observed'}]->(p)"
        )
    assert db.query("MATCH (v)-[e:VISITED]->(p) RETURN v.name, type(e)") == [
        {"v.name": "O'NEIL", "type(e)": "VISITED"}
    ]
    with db.at_commit(before_edges):
        assert db.query("MATCH ()-[e]->() RETURN count(e)") == [{"count(e)": 0}]


@pytest.mark.graph
def test_failed_change_is_discarded():
    db = TuringDB()
    name = f"test_{uuid.uuid4().hex[:8]}"
    db.query(f"CREATE GRAPH {name}")
    db.use(name)
    with pytest.raises(TuringDBError), db.change():
        db.query("CREATE (:Vessel {vessel_id: 'a'})")
        db.query("THIS IS NOT CYPHER")
    assert db.query("MATCH (n) RETURN count(n)") == [{"count(n)": 0}]
