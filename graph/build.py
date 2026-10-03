"""Stage 8 — graph: load the network into TuringDB with one commit per day (CLAUDE.md §8).

1. Base commit from a JSONL import: Vessel, Port (AOIs) and Sanction nodes, LISTED edges.
2. For each day of the window that has events, one change: that day's Gap, Encounter and
   SarDetection nodes, COMMIT, then the edges that attach them (MATCH existing nodes, CREATE),
   then CHANGE SUBMIT. Days without events map to the previous day's commit.
3. data/processed/commits.json records the graph name and {date: commit hash}.

Every node has `uid` ("vessel:…", "gap:…", "port:…") plus `kind` and `title`, so queries can
walk the graph generically; every edge has `provenance` and `source`. Each run loads into a
fresh graph name (graphs are immutable history; the newest one is recorded as active).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date

import pandas as pd

from graph.client import TuringDB, literal, props
from pipeline import aoi
from pipeline.config import GRAPH_NAME, NEAR_AOI_KM, PROCESSED_DIR, TURING_DIR
from pipeline.io import read_table, write_json

log = logging.getLogger("terrestrial")

COMMITS = PROCESSED_DIR / "commits.json"
BATCH = 60  # edges per MATCH…CREATE statement


def _clean(v):
    if v is None or (isinstance(v, float) and v != v) or v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if hasattr(v, "item"):
        return v.item()
    return v


def base_jsonl(scores: pd.DataFrame, sanctions: pd.DataFrame) -> list[dict]:
    lines: list[dict] = []
    nid = 0
    vessel_node: dict[str, int] = {}
    for r in scores.itertuples(index=False):
        lines.append(
            {
                "type": "node", "id": str(nid), "labels": ["Vessel"],
                "properties": {k: v for k, v in {
                    "uid": f"vessel:{r.vessel_id}", "kind": "vessel", "title": _clean(r.name) or r.vessel_id,
                    "vessel_id": r.vessel_id, "imo": _clean(r.imo), "mmsi": _clean(r.mmsi), "name": _clean(r.name),
                    "flag": _clean(r.flag), "vessel_type": _clean(r.vessel_type), "sanctioned": bool(r.listed), "risk": int(r.risk),
                }.items() if v is not None},
            }
        )  # fmt: skip
        vessel_node[r.vessel_id] = nid
        nid += 1
    for a in aoi.AOIS:
        lines.append(
            {
                "type": "node", "id": str(nid), "labels": ["Port"],
                "properties": {"uid": f"port:{a.name}", "kind": "port", "title": a.name, "name": a.name, "lat": a.lat, "lon": a.lon, "occupied_ua": a.occupied_ua},
            }
        )  # fmt: skip
        nid += 1
    rel = 0
    by_imo = {r.imo: r for r in sanctions.itertuples(index=False) if r.imo}
    by_mmsi = {m: r for r in sanctions.itertuples(index=False) for m in r.mmsis}
    sanction_node: dict[str, int] = {}
    for r in scores[scores["listed"]].itertuples(index=False):
        entry = by_imo.get(r.imo) if r.imo else None
        entry = entry or (by_mmsi.get(str(r.mmsi)) if r.mmsi else None)
        if entry is None:
            continue
        if entry.os_id not in sanction_node:
            lines.append(
                {
                    "type": "node", "id": str(nid), "labels": ["Sanction"],
                    "properties": {
                        "uid": f"sanction:{entry.os_id}", "kind": "sanction", "title": entry.name,
                        "source": "OpenSanctions", "program": ";".join(entry.datasets), "topics": ";".join(entry.topics), "url": entry.url,
                    },
                }
            )  # fmt: skip
            sanction_node[entry.os_id] = nid
            nid += 1
        lines.append(
            {
                "type": "relationship", "id": str(rel), "label": "LISTED",
                "start": {"id": str(vessel_node[r.vessel_id])}, "end": {"id": str(sanction_node[entry.os_id])},
                "properties": {"provenance": "observed", "source": "OpenSanctions"},
            }
        )  # fmt: skip
        rel += 1
    return lines


def _create_nodes(db: TuringDB, label: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), 200):
        db.query("CREATE " + ", ".join(f"(:{label} {props(r)})" for r in rows[i : i + 200]))


def _create_edges(db: TuringDB, edges: list[tuple[str, str, str, dict]]) -> None:
    """edges: (from uid, edge label, to uid, properties). Endpoints are matched by uid."""
    for i in range(0, len(edges), BATCH):
        chunk = edges[i : i + BATCH]
        uids = sorted({u for a, _, b, _ in chunk for u in (a, b)})
        alias = {u: f"n{k}" for k, u in enumerate(uids)}
        match = ", ".join(f"({alias[u]} {{uid: {literal(u)}}})" for u in uids)
        create = ", ".join(f"({alias[a]})-[:{lab} {props(p)}]->({alias[b]})" for a, lab, b, p in chunk)
        db.query(f"MATCH {match} CREATE {create}")


def day_changes(day: pd.Timestamp, t: dict[str, pd.DataFrame]) -> tuple[dict[str, list[dict]], list[tuple]]:
    """Nodes and edges for one day of events."""
    nodes: dict[str, list[dict]] = {"Gap": [], "Encounter": [], "SarDetection": []}
    edges: list[tuple] = []
    nxt = day + pd.Timedelta(days=1)
    known = t["known_vessels"]
    gaps = t["gaps"][(t["gaps"]["start"] >= day) & (t["gaps"]["start"] < nxt)]
    impossible = t["impossible"]
    for g in gaps.itertuples(index=False):
        uid = f"gap:{g.gap_id}"
        nodes["Gap"].append(
            {
                "uid": uid, "kind": "gap", "title": f"AIS gap {g.start:%d %b}", "gap_id": g.gap_id,
                "start": g.start.isoformat(), "end": None if g.open else g.end.isoformat(), "duration_h": float(g.duration_h),
                "off_lat": float(g.off_lat), "off_lon": float(g.off_lon),
                "on_lat": None if g.open else float(g.on_lat), "on_lon": None if g.open else float(g.on_lon),
                "impossible": g.gap_id in impossible, "day": day.date().isoformat(),
            }
        )  # fmt: skip
        if g.vessel_id in known:
            edges.append(
                (f"vessel:{g.vessel_id}", "HAD_GAP", uid, {"provenance": "observed", "source": "GFW events"})
            )
        ends = [(g.off_lon, g.off_lat)] + ([] if g.open else [(g.on_lon, g.on_lat)])
        near = set()
        for lon, lat in ends:
            for a in aoi.AOIS:
                if float(a.distance_km(lon, lat)) <= NEAR_AOI_KM:
                    near.add(a.name)
        for name in sorted(near):
            edges.append((uid, "NEAR", f"port:{name}", {"provenance": "observed", "source": "Terrestrial"}))
    cands = t["candidates"]
    day_c = cands[(cands["ts"] >= day) & (cands["ts"] < nxt)] if not cands.empty else cands
    for sar_id, group in day_c.groupby("sar_id") if not day_c.empty else []:
        r = group.iloc[0]
        uid = f"sar:{sar_id}"
        nodes["SarDetection"].append(
            {
                "uid": uid,
                "kind": "sar",
                "title": f"Radar {r['ts']:%d %b %H:00}",
                "sar_id": sar_id,
                "date": r["ts"].isoformat(),
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "day": day.date().isoformat(),
            }
        )
        for c in group.itertuples(index=False):
            edges.append(
                (
                    f"gap:{c.gap_id}",
                    "CANDIDATE",
                    uid,
                    {"provenance": "inferred", "source": "Terrestrial", "consistent": bool(c.consistent)},
                )
            )
        if r["in_aoi"]:
            edges.append((uid, "AT", f"port:{r['in_aoi']}", {"provenance": "observed", "source": "GFW SAR"}))
    encs = t["encounters"][(t["encounters"]["start"] >= day) & (t["encounters"]["start"] < nxt)]
    seen = set()
    for e in encs.itertuples(index=False):
        key = tuple(sorted((e.vessel_id, e.other_vessel_id))) + (e.start.isoformat(),)
        if key in seen:  # GFW lists an encounter once per participant
            continue
        seen.add(key)
        uid = f"enc:{e.enc_id}"
        nodes["Encounter"].append(
            {
                "uid": uid,
                "kind": "encounter",
                "title": f"Encounter {e.start:%d %b}",
                "enc_id": e.enc_id,
                "start": e.start.isoformat(),
                "end": e.end.isoformat(),
                "lat": float(e.lat),
                "lon": float(e.lon),
                "day": day.date().isoformat(),
            }
        )
        for vid in (e.vessel_id, e.other_vessel_id):
            if vid in known:
                edges.append(
                    (f"vessel:{vid}", "IN_ENCOUNTER", uid, {"provenance": "observed", "source": "GFW events"})
                )
    visits = t["port_visits"]
    for v in visits[(visits["start"] >= day) & (visits["start"] < nxt) & visits["aoi"].notna()].itertuples(
        index=False
    ):
        if v.vessel_id in known:
            edges.append(
                (
                    f"vessel:{v.vessel_id}",
                    "VISITED",
                    f"port:{v.aoi}",
                    {
                        "provenance": "observed",
                        "source": "GFW events",
                        "start": v.start.isoformat(),
                        "end": v.end.isoformat(),
                    },
                )
            )
    return nodes, edges


def build(start: date, end: date) -> dict:
    scores = read_table("scores")
    tables = {
        "gaps": read_table("gaps"),
        "candidates": read_table("candidates"),
        "encounters": read_table("encounters"),
        "port_visits": read_table("port_visits"),
        "impossible": set(read_table("gap_envelopes").query("impossible")["gap_id"]),
        "known_vessels": set(scores["vessel_id"]),
    }
    graph = f"{GRAPH_NAME}_{time.strftime('%Y%m%d_%H%M%S')}"
    lines = base_jsonl(scores, read_table("sanctions"))
    jsonl = TURING_DIR / "data" / f"{graph}.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    db = TuringDB()
    db.query(f"LOAD JSONL '{jsonl.name}' AS {graph}", graph="default")
    db.use(graph)
    log.info("  base commit: %d nodes/edges from %s", len(lines), jsonl.name)

    commits: dict[str, str] = {}
    head = db.head()
    day = pd.Timestamp(start, tz="UTC")
    stop = pd.Timestamp(end, tz="UTC")
    n_changes = 0
    while day < stop:
        nodes, edges = day_changes(day, tables)
        if any(nodes.values()) or edges:
            with db.change():
                for label, rows in nodes.items():
                    if rows:
                        _create_nodes(db, label, rows)
                db.commit()
                _create_edges(db, edges)
            head = db.head()
            n_changes += 1
        commits[day.date().isoformat()] = head
        day += pd.Timedelta(days=1)
    record = {"graph": graph, "start": start.isoformat(), "end": end.isoformat(), "days": commits}
    write_json(COMMITS, record)
    log.info("  %d daily changes committed to %s (%d days mapped)", n_changes, graph, len(commits))
    return record


def stage(start: date, end: date, args) -> None:
    build(start, end)
