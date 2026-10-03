"""Graph queries for the API (CLAUDE.md §8): network, shadow cluster, diff — all time-aware.

TuringDB's Cypher subset has no collect()/DISTINCT aggregation over paths, so the vessel
network is a breadth-first expansion done with explicit one-pattern-per-relationship queries
(shared encounter, shared port, shared candidate detection); Python assembles the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from graph.client import TuringDB, literal
from pipeline.config import PROCESSED_DIR
from pipeline.io import read_json

COMMITS = PROCESSED_DIR / "commits.json"

# Patterns linking one vessel to another, with the intermediate nodes they pass through.
LINKS = {
    "encounter": "MATCH (v:Vessel)-[r1:IN_ENCOUNTER]->(m:Encounter)<-[r2:IN_ENCOUNTER]-(w:Vessel) WHERE v.uid IN {uids} AND w.uid <> v.uid "
    "RETURN v.uid, m.uid, m.title, m.kind, w.uid, w.title, w.kind, w.risk, w.sanctioned",
    "port": "MATCH (v:Vessel)-[r1:VISITED]->(m:Port)<-[r2:VISITED]-(w:Vessel) WHERE v.uid IN {uids} AND w.uid <> v.uid "
    "RETURN v.uid, m.uid, m.title, m.kind, w.uid, w.title, w.kind, w.risk, w.sanctioned",
    "detection": "MATCH (v:Vessel)-[:HAD_GAP]->(g:Gap)-[:CANDIDATE]->(m:SarDetection)<-[:CANDIDATE]-(h:Gap)<-[:HAD_GAP]-(w:Vessel) "
    "WHERE v.uid IN {uids} AND w.uid <> v.uid RETURN v.uid, m.uid, m.title, m.kind, w.uid, w.title, w.kind, w.risk, w.sanctioned",
}


@dataclass
class Network:
    nodes: dict[str, dict] = field(default_factory=dict)
    links: dict[str, dict] = field(default_factory=dict)

    def as_json(self) -> dict:
        return {"nodes": list(self.nodes.values()), "links": list(self.links.values())}


def commits() -> dict:
    return read_json(COMMITS)


def commit_for(as_of: str | None) -> str | None:
    """Commit hash of the graph as of a date (None → HEAD)."""
    if not as_of:
        return None
    days = commits()["days"]
    eligible = [d for d in days if d <= as_of]
    if not eligible:
        return next(iter(days.values()))
    return days[max(eligible)]


def _client() -> TuringDB:
    record = commits()
    db = TuringDB()
    db.ensure_loaded(record["graph"])
    db.use(record["graph"])
    return db


def _expand(db: TuringDB, vessel_uid: str, hops: int) -> Network:
    net = Network()
    root = db.query(
        f"MATCH (v:Vessel) WHERE v.uid = {literal(vessel_uid)} RETURN v.uid, v.title, v.risk, v.sanctioned"
    )
    if not root:
        raise KeyError(vessel_uid)
    r = root[0]
    net.nodes[vessel_uid] = {
        "id": vessel_uid,
        "kind": "vessel",
        "title": r["v.title"],
        "risk": r["v.risk"],
        "sanctioned": r["v.sanctioned"],
        "depth": 0,
    }
    frontier = [vessel_uid]
    for depth in range(1, hops + 1):
        if not frontier:
            break
        uids = "[" + ", ".join(literal(u) for u in frontier) + "]"
        found = []
        for via, pattern in LINKS.items():
            for row in db.query(pattern.format(uids=uids)):
                found.append((via, row))
        next_frontier = []
        for via, row in found:
            v, m, w = row["v.uid"], row["m.uid"], row["w.uid"]
            net.nodes.setdefault(m, {"id": m, "kind": row["m.kind"], "title": row["m.title"], "depth": depth})
            if w not in net.nodes:
                net.nodes[w] = {
                    "id": w,
                    "kind": "vessel",
                    "title": row["w.title"],
                    "risk": row["w.risk"],
                    "sanctioned": row["w.sanctioned"],
                    "depth": depth,
                }
                next_frontier.append(w)
            for a, b in ((v, m), (w, m)):
                key = f"{a}|{b}"
                net.links.setdefault(
                    key,
                    {
                        "id": key,
                        "source": a,
                        "target": b,
                        "via": via,
                        "provenance": "inferred" if via == "detection" else "observed",
                    },
                )
        frontier = next_frontier
    return net


def network(vessel_id: str, hops: int = 2, as_of: str | None = None) -> dict:
    hops = max(1, min(3, hops))
    db = _client()
    commit = commit_for(as_of)
    if commit:
        with db.at_commit(commit):
            net = _expand(db, f"vessel:{vessel_id}", hops)
    else:
        net = _expand(db, f"vessel:{vessel_id}", hops)
    return {**net.as_json(), "as_of": as_of, "commit": commit, "hops": hops}


def shadow_cluster(as_of: str | None = None) -> dict:
    """Vessels within two encounter hops of any sanctions-listed vessel."""
    db = _client()

    def run() -> Network:
        net = Network()
        listed = db.query("MATCH (s:Vessel) WHERE s.sanctioned = true RETURN s.uid, s.title, s.risk")
        frontier = []
        for r in listed:
            net.nodes[r["s.uid"]] = {
                "id": r["s.uid"],
                "kind": "vessel",
                "title": r["s.title"],
                "risk": r["s.risk"],
                "sanctioned": True,
                "depth": 0,
            }
            frontier.append(r["s.uid"])
        for depth in (1, 2):
            if not frontier:
                break
            uids = "[" + ", ".join(literal(u) for u in frontier) + "]"
            nxt = []
            for row in db.query(LINKS["encounter"].format(uids=uids)):
                v, m, w = row["v.uid"], row["m.uid"], row["w.uid"]
                net.nodes.setdefault(
                    m, {"id": m, "kind": "encounter", "title": row["m.title"], "depth": depth}
                )
                if w not in net.nodes:
                    net.nodes[w] = {
                        "id": w,
                        "kind": "vessel",
                        "title": row["w.title"],
                        "risk": row["w.risk"],
                        "sanctioned": row["w.sanctioned"],
                        "depth": depth,
                    }
                    nxt.append(w)
                for a, b in ((v, m), (w, m)):
                    net.links.setdefault(
                        f"{a}|{b}",
                        {
                            "id": f"{a}|{b}",
                            "source": a,
                            "target": b,
                            "via": "encounter",
                            "provenance": "observed",
                        },
                    )
            frontier = nxt
        return net

    commit = commit_for(as_of)
    if commit:
        with db.at_commit(commit):
            net = run()
    else:
        net = run()
    return {**net.as_json(), "as_of": as_of, "commit": commit}


def diff(start: str, end: str, vessel_id: str | None = None, hops: int = 2) -> dict:
    """What appeared in the network (or the shadow cluster) between two dates."""
    if vessel_id:
        before, after = network(vessel_id, hops, start), network(vessel_id, hops, end)
    else:
        before, after = shadow_cluster(start), shadow_cluster(end)
    before_nodes = {n["id"] for n in before["nodes"]}
    before_links = {link["id"] for link in before["links"]}
    return {
        "from": start,
        "to": end,
        "added_nodes": [n for n in after["nodes"] if n["id"] not in before_nodes],
        "added_links": [link for link in after["links"] if link["id"] not in before_links],
        "removed_nodes": sorted(before_nodes - {n["id"] for n in after["nodes"]}),
    }
