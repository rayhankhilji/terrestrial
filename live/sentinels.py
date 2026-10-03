"""Sentinels: analyst-programmable detection rules, evaluated on every live update.

A sentinel is a small dataflow graph — a source node, filter nodes, AND/OR joins, and an
alert sink — edited visually in the web app (nodes and wires). For each incoming entity the
engine evaluates the graph; an alert node fires when its input passes, subject to a
per-(sentinel, entity) cooldown. Per-node pass counters stream back to the editor so the
wires light up as signals flow.

Node types and params
  source     {kind: aircraft|vessel|fire|news|station}
  area       {area: occupied | any_aoi | <AOI name>}
  near       {target: facility|fire|news, facility_type?: refinery|port|naval base|airbase, km}
  compare    {field: dotted path e.g. props.military / spd / alt, op: == != > >= < <= contains, value}
  listed     {}   — OpenSanctions sanctions-relevant match
  and / or   {}   — join several inputs
  alert      {severity: info|warn|high, title: template with {label}, cooldown_min}
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import numpy as np

from live.correlate import haversine_km
from live.hub import Hub, now_ms
from pipeline import aoi as aois
from pipeline.config import DATA_DIR
from pipeline.io import write_json

log = logging.getLogger("terrestrial.live")

STORE = DATA_DIR / "sentinels.json"
TICK_S = 0.25

DEFAULTS: list[dict] = [
    {
        "id": "uav-black-sea",
        "name": "Unmanned aircraft over the Black Sea",
        "enabled": True,
        "nodes": [
            {"id": "s", "type": "source", "x": 40, "y": 80, "params": {"kind": "aircraft"}},
            {
                "id": "f",
                "type": "compare",
                "x": 300,
                "y": 80,
                "params": {"field": "props.uav", "op": "==", "value": True},
            },
            {
                "id": "a",
                "type": "alert",
                "x": 560,
                "y": 80,
                "params": {"severity": "warn", "title": "UAV {label} over the Black Sea", "cooldown_min": 30},
            },
        ],
        "edges": [["s", "f"], ["f", "a"]],
    },
    {
        "id": "listed-near-novorossiysk",
        "name": "Listed hull near a refinery or port",
        "enabled": True,
        "nodes": [
            {"id": "s", "type": "source", "x": 40, "y": 60, "params": {"kind": "vessel"}},
            {"id": "l", "type": "listed", "x": 280, "y": 20, "params": {}},
            {
                "id": "n",
                "type": "near",
                "x": 280,
                "y": 150,
                "params": {"target": "facility", "facility_type": "refinery", "km": 25},
            },
            {
                "id": "p",
                "type": "near",
                "x": 280,
                "y": 260,
                "params": {"target": "facility", "facility_type": "port", "km": 5},
            },
            {"id": "o", "type": "or", "x": 520, "y": 200, "params": {}},
            {"id": "j", "type": "and", "x": 700, "y": 100, "params": {}},
            {
                "id": "a",
                "type": "alert",
                "x": 900,
                "y": 100,
                "params": {
                    "severity": "high",
                    "title": "Listed vessel {label} near energy/port infrastructure",
                    "cooldown_min": 120,
                },
            },
        ],
        "edges": [
            ["s", "l"],
            ["s", "n"],
            ["s", "p"],
            ["n", "o"],
            ["p", "o"],
            ["l", "j"],
            ["o", "j"],
            ["j", "a"],
        ],
    },
    {
        "id": "vessel-near-fire",
        "name": "Vessel within 10 km of a fresh thermal anomaly",
        "enabled": True,
        "nodes": [
            {"id": "s", "type": "source", "x": 40, "y": 80, "params": {"kind": "vessel"}},
            {"id": "n", "type": "near", "x": 300, "y": 80, "params": {"target": "fire", "km": 10}},
            {
                "id": "a",
                "type": "alert",
                "x": 560,
                "y": 80,
                "params": {
                    "severity": "warn",
                    "title": "{label} within 10 km of a thermal anomaly",
                    "cooldown_min": 180,
                },
            },
        ],
        "edges": [["s", "n"], ["n", "a"]],
    },
    {
        "id": "slow-tanker-occupied",
        "name": "Slow or stationary hull inside an occupied port AOI",
        "enabled": True,
        "nodes": [
            {"id": "s", "type": "source", "x": 40, "y": 80, "params": {"kind": "vessel"}},
            {"id": "z", "type": "area", "x": 260, "y": 80, "params": {"area": "occupied"}},
            {
                "id": "v",
                "type": "compare",
                "x": 480,
                "y": 80,
                "params": {"field": "spd", "op": "<", "value": 2},
            },
            {
                "id": "a",
                "type": "alert",
                "x": 700,
                "y": 80,
                "params": {
                    "severity": "high",
                    "title": "{label} stationary in an occupied port AOI",
                    "cooldown_min": 240,
                },
            },
        ],
        "edges": [["s", "z"], ["z", "v"], ["v", "a"]],
    },
]

OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a is not None and a > b,
    ">=": lambda a, b: a is not None and a >= b,
    "<": lambda a, b: a is not None and a < b,
    "<=": lambda a, b: a is not None and a <= b,
    "contains": lambda a, b: a is not None and str(b).lower() in str(a).lower(),
}


def _field(entity: dict, path: str) -> Any:
    value: Any = entity
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def validate(sentinel: dict) -> dict:
    """Raise ValueError on a malformed sentinel; return it normalised."""
    nodes = {n["id"]: n for n in sentinel.get("nodes", [])}
    if not sentinel.get("id") or not sentinel.get("name"):
        raise ValueError("sentinel needs an id and a name")
    sources = [n for n in nodes.values() if n["type"] == "source"]
    if len(sources) != 1:
        raise ValueError("a sentinel needs exactly one source node")
    if not any(n["type"] == "alert" for n in nodes.values()):
        raise ValueError("a sentinel needs at least one alert node")
    known = {"source", "area", "near", "compare", "listed", "and", "or", "alert"}
    for n in nodes.values():
        if n["type"] not in known:
            raise ValueError(f"unknown node type {n['type']!r}")
        if n["type"] == "compare" and n["params"].get("op") not in OPS:
            raise ValueError(f"unknown operator {n['params'].get('op')!r}")
    for a, b in sentinel.get("edges", []):
        if a not in nodes or b not in nodes:
            raise ValueError(f"edge {a}->{b} references a missing node")
    order = _topo(nodes, sentinel["edges"])  # raises on cycles
    return {**sentinel, "_order": order}


def _topo(nodes: dict, edges: list) -> list[str]:
    incoming = {n: 0 for n in nodes}
    for _, b in edges:
        incoming[b] += 1
    ready = [n for n, d in incoming.items() if d == 0]
    order = []
    while ready:
        n = ready.pop()
        order.append(n)
        for a, b in edges:
            if a == n:
                incoming[b] -= 1
                if incoming[b] == 0:
                    ready.append(b)
    if len(order) != len(nodes):
        raise ValueError("sentinel graph has a cycle")
    return order


class SentinelEngine:
    def __init__(self) -> None:
        self.sentinels: dict[str, dict] = {}
        self.hits: dict[str, dict[str, int]] = {}
        self._cooldown: dict[str, float] = {}
        self._last_tick = 0.0
        self._dirty = False
        stored = json.loads(STORE.read_text()) if STORE.exists() else DEFAULTS
        for s in stored:
            self.sentinels[s["id"]] = validate(s)

    # --- persistence / CRUD -----------------------------------------------------------------

    def list(self) -> list[dict]:
        return [{k: v for k, v in s.items() if not k.startswith("_")} for s in self.sentinels.values()]

    def put(self, sentinel: dict) -> dict:
        checked = validate(sentinel)
        self.sentinels[checked["id"]] = checked
        self.hits.pop(checked["id"], None)
        self._save()
        return {k: v for k, v in checked.items() if not k.startswith("_")}

    def delete(self, sentinel_id: str) -> None:
        if sentinel_id not in self.sentinels:
            raise KeyError(sentinel_id)
        del self.sentinels[sentinel_id]
        self._save()

    def _save(self) -> None:
        write_json(STORE, self.list())

    # --- evaluation -------------------------------------------------------------------------

    def _passes(self, hub: Hub, node: dict, e: dict) -> bool:
        p = node["params"]
        t = node["type"]
        if t == "source":
            return e["kind"] == p.get("kind")
        if t == "area":
            inside = aois.containing(e["lon"], e["lat"])[0]
            area = p.get("area", "occupied")
            if area == "occupied":
                return bool(inside) and aois.AOI_BY_NAME[inside].occupied_ua
            if area == "any_aoi":
                return bool(inside)
            return inside == area
        if t == "compare":
            return OPS[p["op"]](_field(e, p["field"]), p.get("value"))
        if t == "listed":
            listing = (e.get("props") or {}).get("sanctions")
            return bool(listing and listing.get("sanctioned"))
        if t == "near":
            return self._near(hub, e, p)
        raise ValueError(t)

    def _near(self, hub: Hub, e: dict, p: dict) -> bool:
        target, km = p.get("target", "facility"), float(p.get("km", 10))
        cutoff = now_ms() - 24 * 3600 * 1000
        others = [
            o
            for o in hub.of_kind(target)
            if o["id"] != e["id"]
            and "lat" in o
            and (target == "facility" or o.get("ts", 0) >= cutoff)
            and (not p.get("facility_type") or o.get("props", {}).get("type") == p["facility_type"])
        ]
        if not others:
            return False
        d = haversine_km(
            e["lat"], e["lon"], np.array([o["lat"] for o in others]), np.array([o["lon"] for o in others])
        )
        return bool((d <= km).any())

    def __call__(self, hub: Hub, e: dict) -> None:
        if "lat" not in e:
            return
        for s in self.sentinels.values():
            if not s.get("enabled", True):
                continue
            nodes = {n["id"]: n for n in s["nodes"]}
            inputs: dict[str, list[str]] = {n: [] for n in nodes}
            for a, b in s["edges"]:
                inputs[b].append(a)
            passed: dict[str, bool] = {}
            for nid in s["_order"]:
                node = nodes[nid]
                ins = [passed[i] for i in inputs[nid]]
                if node["type"] == "source":
                    ok = self._passes(hub, node, e)
                    if not ok:
                        break  # different stream; nothing downstream can pass
                elif node["type"] == "and":
                    ok = bool(ins) and all(ins)
                elif node["type"] == "or":
                    ok = any(ins)
                elif node["type"] == "alert":
                    ok = bool(ins) and all(ins)
                    if ok:
                        self._fire(hub, s, node, e)
                else:
                    ok = bool(ins) and all(ins) and self._passes(hub, node, e)
                passed[nid] = ok
                if ok:
                    counters = self.hits.setdefault(s["id"], {})
                    counters[nid] = counters.get(nid, 0) + 1
                    self._dirty = True
        self._tick(hub)

    def _fire(self, hub: Hub, s: dict, node: dict, e: dict) -> None:
        p = node["params"]
        key = f"{s['id']}:{node['id']}:{e['id']}"
        t = time.monotonic()
        if t - self._cooldown.get(key, -1e18) < float(p.get("cooldown_min", 30)) * 60:
            return
        self._cooldown[key] = t
        hub.alert(
            {
                "id": f"sentinel:{key}:{now_ms()}",
                "severity": p.get("severity", "warn"),
                "title": str(p.get("title", s["name"])).replace("{label}", str(e.get("label"))),
                "body": f"Sentinel “{s['name']}” matched.",
                "entities": [e["id"]],
                "lon": e["lon"],
                "lat": e["lat"],
                "prov": "inferred",
                "why": [e["id"]],
                "sentinel": s["id"],
            }
        )

    def _tick(self, hub: Hub) -> None:
        t = time.monotonic()
        if self._dirty and t - self._last_tick >= TICK_S:
            self._last_tick = t
            self._dirty = False
            hub.publish({"t": "sentinel_hits", "hits": self.hits})
