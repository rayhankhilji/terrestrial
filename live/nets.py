"""Nets: groups of military craft that appear to share a mission (CLAUDE.md §16.1, M3).

Every TICK_S the engine looks at the last WINDOW_MIN minutes of every military track and scores
each pair on evidence that two craft are working together. Each piece of evidence is
computed in code and kept as a reason on the link:

- proximity:   fraction of shared airborne minutes within NEAR_KM (strong) / FAR_KM (weak);
- rendezvous:  a tanker and another aircraft within RENDEZVOUS_KM and RENDEZVOUS_ALT_M
               (air-to-air refuelling geometry);
- formation:   same callsign family, flight numbers within FORMATION_NUM, and close together;
- same origin: departed the same airfield within SAME_ORIGIN_MIN of each other;
- co-orbit:    both flying racetracks/orbits whose centres are within COORBIT_KM.

Links scoring at least LINK_MIN form a weighted graph; Louvain communities on it are the nets.
A lone ISR / AEW / patrol / tanker aircraft flying an orbit is a one-member net (an orbit is
a mission in itself). Net ids are kept stable across ticks by membership overlap (Jaccard), so
a net can be followed as aircraft join and leave; formation and dissolution raise alerts.

Mission labels: a deterministic rule from the members' roles and the evidence, replaced by Jev's
choice (with its probabilities) when TYPESAFE_API_KEY is configured. The label's source is
always published with it. Nets are inferences: provenance "inferred", never "confirmed".
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
from shapely.geometry import MultiPoint

from live.ai import AIUnavailable, choice, jev
from live.hub import Hub, now_ms
from live.tracks import TrackStore

log = logging.getLogger("terrestrial.live")

TICK_S = 10
WINDOW_MIN = 60
ACTIVE_MIN = 10  # a track must have reported within this to take part
MIN_SHARED_MIN = 8  # minutes both tracks must be airborne to compare them
NEAR_KM = 25.0
FAR_KM = 100.0
RENDEZVOUS_KM = 3.0
RENDEZVOUS_ALT_M = 600.0
FORMATION_KM = 30.0
FORMATION_NUM = 5
SAME_ORIGIN_MIN = 30
COORBIT_KM = 60.0
ORBIT_TURN_DEG = 540.0
ORBIT_RADIUS_KM = 80.0
LINK_MIN = 0.4
JACCARD_KEEP = 0.5
ORBIT_ROLES = {"isr", "electronic", "maritime_patrol", "tanker"}
CALLSIGN = re.compile(r"^([A-Z]{2,6})(\d{1,4})")

MISSIONS = {
    "air_refuelling": "A tanker with receivers meeting in refuelling geometry",
    "isr_orbit": "Reconnaissance / AEW aircraft holding an orbit or racetrack",
    "maritime_patrol": "Maritime patrol aircraft or helicopters over the sea",
    "airlift": "Transport aircraft moving together or along one route",
    "fighter_cap": "Fighters on combat air patrol or escort",
    "rotary_ops": "Helicopters operating together",
    "training": "Trainer aircraft or a training sortie",
    "vip_transport": "Government or staff transport",
    "unknown": "None of the above is clearly supported",
}


@dataclass
class TrackView:
    id: str
    label: str
    props: dict
    t: np.ndarray  # seconds
    lon: np.ndarray
    lat: np.ndarray
    alt: np.ndarray
    hdg: np.ndarray
    ground: np.ndarray
    orbit: tuple[float, float] | None = None  # orbit centre (lon, lat)


@dataclass
class Link:
    a: str
    b: str
    weight: float
    why: list[str] = field(default_factory=list)


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def _turn_total(hdg: np.ndarray) -> float:
    h = hdg[~np.isnan(hdg)]
    if len(h) < 3:
        return 0.0
    d = np.diff(h)
    d = (d + 180) % 360 - 180
    return float(np.abs(d).sum())


def orbit_centre(v: TrackView) -> tuple[float, float] | None:
    airborne = ~v.ground
    if airborne.sum() < 10:
        return None
    lon, lat = v.lon[airborne], v.lat[airborne]
    c_lon, c_lat = float(lon.mean()), float(lat.mean())
    radius = float(haversine_km(lon, lat, c_lon, c_lat).max())
    if _turn_total(v.hdg[airborne]) >= ORBIT_TURN_DEG and radius <= ORBIT_RADIUS_KM:
        return c_lon, c_lat
    return None


def views(store: TrackStore, entities: dict[str, dict], now: int) -> list[TrackView]:
    since = now - WINDOW_MIN * 60_000
    active = now - ACTIVE_MIN * 60_000
    out = []
    for eid, pts in store.points.items():
        e = entities.get(eid)
        if e is None or not pts or pts[-1].ts < active:
            continue
        window = [p for p in pts if p.ts >= since]
        if len(window) < 3:
            continue
        v = TrackView(
            id=eid,
            label=e.get("label") or eid,
            props=e.get("props") or {},
            t=np.array([p.ts / 1000 for p in window]),
            lon=np.array([p.lon for p in window]),
            lat=np.array([p.lat for p in window]),
            alt=np.array([p.alt for p in window]),
            hdg=np.array([np.nan if p.hdg is None else p.hdg for p in window], dtype=float),
            ground=np.array([p.ground for p in window]),
        )
        v.orbit = orbit_centre(v) if eid.startswith("aircraft:") else None
        out.append(v)
    return out


def _resample(v: TrackView, grid: np.ndarray):
    """Positions on the minute grid; NaN outside the track's span or while on the ground."""
    inside = (grid >= v.t[0]) & (grid <= v.t[-1])
    lon = np.where(inside, np.interp(grid, v.t, v.lon), np.nan)
    lat = np.where(inside, np.interp(grid, v.t, v.lat), np.nan)
    alt = np.where(inside, np.interp(grid, v.t, v.alt), np.nan)
    ground = np.interp(grid, v.t, v.ground.astype(float)) > 0.5
    lon[ground] = np.nan
    return lon, lat, alt


def _callsign(v: TrackView) -> tuple[str, int] | None:
    m = CALLSIGN.match((v.props.get("callsign") or "").upper())
    return (m.group(1), int(m.group(2))) if m else None


def score_pair(
    a: TrackView, b: TrackView, grid: np.ndarray, origins: dict[str, tuple[str, int]]
) -> Link | None:
    la = _resample(a, grid)
    lb = _resample(b, grid)
    both = ~np.isnan(la[0]) & ~np.isnan(lb[0])
    why: list[str] = []
    w = 0.0
    if both.sum() >= MIN_SHARED_MIN:
        d = haversine_km(la[0][both], la[1][both], lb[0][both], lb[1][both])
        near, far = float((d <= NEAR_KM).mean()), float((d <= FAR_KM).mean())
        if near > 0.2 or far > 0.5:
            w += 0.6 * near + 0.25 * far
            why.append(
                f"within {NEAR_KM:.0f} km for {near:.0%} and {FAR_KM:.0f} km for {far:.0%} of {int(both.sum())} shared minutes"
            )
        roles = {a.props.get("role"), b.props.get("role")}
        if "tanker" in roles:
            dalt = np.abs(la[2][both] - lb[2][both])
            hit = (d <= RENDEZVOUS_KM) & (dalt <= RENDEZVOUS_ALT_M)
            if hit.any():
                w += 0.6
                why.append(
                    f"tanker rendezvous: {float(d[hit].min()):.1f} km apart within {RENDEZVOUS_ALT_M:.0f} m altitude"
                )
        ca, cb = _callsign(a), _callsign(b)
        if (
            ca
            and cb
            and ca[0] == cb[0]
            and abs(ca[1] - cb[1]) <= FORMATION_NUM
            and float(d.min()) <= FORMATION_KM
        ):
            w += 0.5
            why.append(f"callsign series {ca[0]} {ca[1]}/{cb[1]} flying together")
    oa, ob = origins.get(a.id), origins.get(b.id)
    if oa and ob and oa[0] == ob[0] and abs(oa[1] - ob[1]) <= SAME_ORIGIN_MIN * 60_000:
        w += 0.3
        why.append(f"departed {oa[0]} {abs(oa[1] - ob[1]) / 60000:.0f} min apart")
    if a.orbit and b.orbit:
        dc = float(haversine_km(a.orbit[0], a.orbit[1], b.orbit[0], b.orbit[1]))
        if dc <= COORBIT_KM:
            w += 0.4
            why.append(f"orbits centred {dc:.0f} km apart")
    if w and a.props.get("state_code") and a.props.get("state_code") == b.props.get("state_code"):
        w *= 1.15
    w = min(1.0, w)
    return Link(a.id, b.id, round(w, 3), why) if w >= LINK_MIN else None


def rule_mission(members: list[TrackView], links: list[Link]) -> str:
    roles = [m.props.get("role") for m in members]
    frames = [m.props.get("airframe") for m in members]
    if "tanker" in roles and any("rendezvous" in r for link in links for r in link.why):
        return "air_refuelling"
    if all(r in ("isr", "electronic") for r in roles) and any(m.orbit for m in members):
        return "isr_orbit"
    if any(r == "maritime_patrol" for r in roles):
        return "maritime_patrol"
    if all(f in ("helicopter", "tiltrotor") for f in frames):
        return "rotary_ops"
    if all(r in ("fighter", "strike") for r in roles):
        return "fighter_cap"
    if all(r == "trainer" for r in roles):
        return "training"
    if all(r in ("airlift",) for r in roles):
        return "airlift"
    if all(r == "vip" for r in roles):
        return "vip_transport"
    if len(members) == 1 and roles[0] == "tanker" and members[0].orbit:
        return "air_refuelling"
    return "unknown"


@dataclass
class Net:
    id: str
    members: set[str]
    since: int
    mission: str = "unknown"
    mission_p: float | None = None
    mission_src: str = "rule"
    signature: str = ""


class NetsEngine:
    def __init__(self, store: TrackStore):
        self.store = store
        self.nets: dict[str, Net] = {}
        self.counter = 0
        self.pending: set[str] = set()

    def compute(
        self, entities: dict[str, dict], now: int
    ) -> tuple[list[TrackView], list[Link], list[set[str]]]:
        vs = views(self.store, entities, now)
        grid = np.arange(now / 1000 - WINDOW_MIN * 60, now / 1000, 60.0)
        origins = {}
        for v in vs:
            flights = self.store.flights(v.id, hours=WINDOW_MIN / 60 + 6, now=now)
            if flights and flights[-1]["origin"]:
                origins[v.id] = (flights[-1]["origin"]["ident"], flights[-1]["start"])
        links = []
        for i, a in enumerate(vs):
            for b in vs[i + 1 :]:
                # Cheap reject: more than 600 km apart now and no shared origin.
                if (
                    haversine_km(a.lon[-1], a.lat[-1], b.lon[-1], b.lat[-1]) > 600
                    and origins.get(a.id, ("a",))[0] != origins.get(b.id, ("b",))[0]
                ):
                    continue
                link = score_pair(a, b, grid, origins)
                if link:
                    links.append(link)
        g = nx.Graph()
        g.add_weighted_edges_from((link.a, link.b, link.weight) for link in links)
        groups = (
            [set(c) for c in nx.community.louvain_communities(g, weight="weight", seed=0)] if links else []
        )
        grouped = set().union(*groups) if groups else set()
        for v in vs:
            if v.id not in grouped and v.orbit and v.props.get("role") in ORBIT_ROLES:
                groups.append({v.id})
        return vs, links, groups

    def _assign_ids(self, groups: list[set[str]], now: int) -> tuple[dict[str, Net], list[Net], list[Net]]:
        current: dict[str, Net] = {}
        formed: list[Net] = []
        free = dict(self.nets)
        for members in sorted(groups, key=len, reverse=True):
            best, best_j = None, 0.0
            for nid, net in free.items():
                j = len(members & net.members) / len(members | net.members)
                if j > best_j:
                    best, best_j = nid, j
            if best is not None and best_j >= JACCARD_KEEP:
                net = free.pop(best)
                net.members = members
            else:
                self.counter += 1
                net = Net(id=f"net:{self.counter}", members=members, since=now)
                formed.append(net)
            current[net.id] = net
        return current, formed, list(free.values())

    def update(self, hub: Hub, now: int | None = None) -> list[Net]:
        now = now or now_ms()
        vs, links, groups = self.compute(hub.entities, now)
        by_id = {v.id: v for v in vs}
        current, formed, dissolved = self._assign_ids(groups, now)
        self.nets = current
        for net in current.values():
            members = [by_id[m] for m in sorted(net.members)]
            net_links = [link for link in links if link.a in net.members and link.b in net.members]
            signature = ",".join(
                sorted(f"{m.props.get('role')}/{m.props.get('state_code')}" for m in members)
            )
            if net.mission_src == "rule" or signature != net.signature:
                net.mission, net.mission_p, net.mission_src = rule_mission(members, net_links), None, "rule"
                if jev.available and signature != net.signature:
                    self.pending.add(net.id)
            net.signature = signature
            hub.upsert(self._entity(net, members, net_links, now))
        for net in formed:
            if len(net.members) > 1 or net.mission != "unknown":
                e = hub.entities.get(net.id)
                hub.alert(
                    {
                        "id": f"alert:{net.id}:formed",
                        "severity": "info",
                        "title": f"Net formed: {e['label'] if e else net.id}",
                        "body": f"{len(net.members)} craft · likely {net.mission.replace('_', ' ')} ({net.mission_src}).",
                        "entities": [net.id, *sorted(net.members)],
                        "lon": e["lon"] if e else None,
                        "lat": e["lat"] if e else None,
                        "prov": "inferred",
                        "why": sorted(net.members),
                    }
                )
        if dissolved:
            hub.remove([n.id for n in dissolved])
        return list(current.values())

    def _entity(self, net: Net, members: list[TrackView], links: list[Link], now: int) -> dict:
        states = sorted({m.props.get("state_code") for m in members if m.props.get("state_code")})
        orgs = sorted({m.props.get("org") for m in members if m.props.get("org")})
        lon = float(np.mean([m.lon[-1] for m in members]))
        lat = float(np.mean([m.lat[-1] for m in members]))
        recent = [(x, y) for m in members for x, y in zip(m.lon[-30:], m.lat[-30:], strict=True)]
        pad = 0.08 / max(0.2, math.cos(math.radians(lat)))
        hull = MultiPoint(recent).convex_hull.buffer(pad, quad_segs=4).simplify(0.01)
        ring = (
            [[round(x, 4), round(y, 4)] for x, y in hull.exterior.coords]
            if hull.geom_type == "Polygon"
            else []
        )
        org = (
            orgs[0]
            if len(orgs) == 1
            else f"Coalition ({', '.join(s.upper() for s in states)})"
            if len(states) > 1
            else (orgs[0] if orgs else None)
        )
        n = int(net.id.split(":")[1])
        label = f"NET {n} · {net.mission.replace('_', ' ')}"
        return {
            "id": net.id,
            "kind": "net",
            "label": label,
            "lon": lon,
            "lat": lat,
            "ts": max(int(m.t[-1] * 1000) for m in members),
            "src": "nets",
            "prov": "inferred",
            "props": {
                "members": [
                    {
                        "id": m.id,
                        "label": m.label,
                        "role": m.props.get("role"),
                        "airframe": m.props.get("airframe"),
                        "state_code": m.props.get("state_code"),
                        "orbit": m.orbit is not None,
                    }
                    for m in members
                ],
                "states": states,
                "state_code": states[0] if len(states) == 1 else None,
                "org": org,
                "mission": net.mission,
                "mission_p": net.mission_p,
                "mission_src": net.mission_src,
                "links": [{"a": link.a, "b": link.b, "w": link.weight, "why": link.why} for link in links],
                "hull": ring,
                "since": net.since,
                "window_min": WINDOW_MIN,
            },
        }

    async def label_pending(self, hub: Hub) -> None:
        """Ask Jev for the mission of nets whose composition changed (rule label stays until then)."""
        while self.pending:
            nid = self.pending.pop()
            net = self.nets.get(nid)
            e = hub.entities.get(nid)
            if net is None or e is None:
                continue
            state = {
                "members": e["props"]["members"],
                "evidence": [r for link in e["props"]["links"] for r in link["why"]],
                "states": e["props"]["states"],
                "organisation": e["props"]["org"],
            }
            try:
                answers = await jev.ask(
                    state,
                    {
                        "mission": choice(
                            "Which mission are these military aircraft most likely flying together?", MISSIONS
                        )
                    },
                )
            except AIUnavailable as exc:
                log.warning("nets: Jev labelling skipped for %s: %s", nid, exc)
                continue
            a = answers["mission"]
            net.mission, net.mission_p, net.mission_src = (
                a.value,
                (a.probabilities or {}).get(a.value),
                f"{a.model} · call {a.call}",
            )
            props = {
                **e["props"],
                "mission": net.mission,
                "mission_p": net.mission_p,
                "mission_src": net.mission_src,
                "mission_probabilities": a.probabilities,
            }
            hub.upsert(
                {**e, "label": f"NET {nid.split(':')[1]} · {net.mission.replace('_', ' ')}", "props": props}
            )


async def run(hub: Hub, engine: NetsEngine) -> None:
    hub.source("nets")
    while True:
        try:
            nets = engine.update(hub)
            hub.source_ok("nets", f"{len(nets)} nets over {sum(len(n.members) for n in nets)} craft")
            if engine.pending:
                await engine.label_pending(hub)
        except Exception as exc:  # surfaced on the status pill and logged; the loop keeps running
            log.exception("nets engine failed")
            hub.source_error("nets", f"{type(exc).__name__}: {exc}")
        await asyncio.sleep(TICK_S)
