"""Craft ranking: a transparent, heuristic threat / intelligence score per live craft (M7).

Mirrors the vessel risk score (§7): additive, capped, every point a stored reason with its
evidence, weights in pipeline/config.py (THREAT). Two axes:

- threat (to Ukraine): hostile-state attribution, strike/ISR/UAV role, distance to Ukrainian
  territory, heading towards it, predicted landing near it;
- intel (significance to watch): emergency squawk, missing callsign, degraded own GNSS or
  flying through an interference area, ISR close to Ukraine, member of a mission net,
  holding an orbit, a recent re-route.

Priority = threat + intel_share × intel, capped. It ranks what an analyst should look at
first; it is not a probability and says nothing about intent. Almost every aircraft that
broadcasts is NATO or partner: their threat axis is near zero by construction and the
ranking is driven by intelligence significance, which the UI states.
"""

from __future__ import annotations

import asyncio
import logging
import math

from shapely.geometry import Point
from shapely.ops import nearest_points, unary_union

from history.regions import boundaries
from live.gnss import GnssGrid
from live.hub import Hub, now_ms
from pipeline.config import HOSTILE_STATES, THREAT

log = logging.getLogger("terrestrial.live")

NAME = "threat"
EVERY_S = 5
STRIKE_ROLES = {"strike", "bomber", "fighter"}
ISR_ROLES = {"isr", "electronic", "maritime_patrol"}
EMERGENCY = {"7500": "unlawful interference", "7600": "radio failure", "7700": "general emergency"}
MISSION_NETS = {"air_refuelling", "isr_orbit", "maritime_patrol", "fighter_cap"}
INBOUND_DEG = 30
INBOUND_MAX_KM = 500
REROUTE_RECENT_MS = 30 * 60 * 1000


class Ukraine:
    """Ukraine's internationally recognised territory (all ADM1 regions incl. Crimea)."""

    def __init__(self, geoms: dict | None = None):
        self.shape = unary_union([g.geometry for g in (geoms or boundaries()).values()]).buffer(0)
        self.boundary = self.shape.boundary

    def distance_km(self, lon: float, lat: float) -> tuple[float, tuple[float, float]]:
        """Approximate distance (0 inside) and the nearest point on the border."""
        p = Point(lon, lat)
        if self.shape.contains(p):
            return 0.0, (lon, lat)
        q = nearest_points(p, self.boundary)[1]
        return _hav_km(lon, lat, q.x, q.y), (q.x, q.y)


def _hav_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    la1, la2 = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def _bearing(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def score(e: dict, ukraine: Ukraine, nets_of: dict[str, dict], gnss: GnssGrid | None, now: int) -> dict:
    """Breakdown for one aircraft or vessel entity."""
    p = e.get("props") or {}
    w = THREAT
    reasons: list[dict] = []

    def add(axis: str, points: int, reason: str, evidence: str | None = None) -> None:
        reasons.append({"axis": axis, "points": points, "reason": reason, "evidence": evidence})

    state = (p.get("state_code") or p.get("flag_code") or "").lower()
    if state in HOSTILE_STATES:
        add(
            "threat",
            w.hostile_state,
            f"operated by {p.get('state') or state.upper()}",
            "; ".join(p.get("mil_evidence") or []) or None,
        )
    role = p.get("role") or p.get("naval_role")
    if role in STRIKE_ROLES:
        add("threat", w.strike_role, f"{role} role", p.get("designation") or p.get("type"))
    elif role in ISR_ROLES:
        add("threat", w.isr_role, f"{role.replace('_', ' ')} role", p.get("designation") or p.get("type"))
    if p.get("uav"):
        add("threat", w.uav, "uncrewed aircraft", p.get("type"))
    if e["kind"] == "vessel" and p.get("military"):
        add(
            "threat",
            w.naval_warship,
            "naval or law-enforcement vessel",
            "; ".join(p.get("mil_evidence") or []) or None,
        )

    dist, (blon, blat) = ukraine.distance_km(e["lon"], e["lat"])
    where = "over Ukraine" if dist == 0 else f"{dist:.0f} km from Ukrainian territory"
    if dist <= 50:
        add("threat", w.near_ukraine_50km, where)
    elif dist <= 150:
        add("threat", w.near_ukraine_150km, where)
    elif dist <= 300:
        add("threat", w.near_ukraine_300km, where)
    hdg, spd = e.get("hdg"), e.get("spd") or 0
    if 0 < dist <= INBOUND_MAX_KM and hdg is not None and spd > 100:
        off = abs((_bearing(e["lon"], e["lat"], blon, blat) - hdg + 180) % 360 - 180)
        if off <= INBOUND_DEG:
            add(
                "threat",
                w.inbound_to_ukraine,
                f"heading towards Ukraine ({off:.0f}° off the nearest border point)",
            )
    pred = p.get("pred") or {}
    if pred.get("dest_lon") is not None:
        d_dest, _ = ukraine.distance_km(pred["dest_lon"], pred["dest_lat"])
        if d_dest <= 150 and pred.get("p", 0) >= 0.3:
            add(
                "threat",
                w.destination_near_ukraine,
                f"likely landing {pred.get('dest_icao') or pred['dest']} {d_dest:.0f} km from Ukraine ({pred['p']:.0%}, model estimate)",
            )

    squawk = str(p.get("squawk") or "")
    if squawk in EMERGENCY:
        add("intel", w.emergency_squawk, f"squawking {squawk} ({EMERGENCY[squawk]})")
    if e["kind"] == "aircraft" and not p.get("callsign"):
        add("intel", w.no_callsign, "no callsign broadcast")
    nacp = p.get("nac_p")
    if e["kind"] == "aircraft" and nacp is not None and nacp < 8 and not p.get("on_ground"):
        add("intel", w.own_gnss_degraded, f"own GNSS accuracy degraded (NACp {nacp})")
    if gnss is not None and (cell := gnss.degraded_near(e["lon"], e["lat"])) and cell["level"] != "low":
        add(
            "intel",
            w.gnss_interference_area,
            f"in a {cell['level']} GNSS-interference area ({cell['frac']:.0%} of {cell['n']} aircraft degraded)",
        )
    if role in ISR_ROLES and dist <= 300:
        add("intel", w.isr_near_ukraine, f"{role.replace('_', ' ')} within {max(dist, 1):.0f} km of Ukraine")
    net = nets_of.get(e["id"])
    if net and net.get("mission") in MISSION_NETS:
        add("intel", w.mission_net, f"in a {net['mission'].replace('_', ' ')} net", net.get("id"))
    if pred.get("on_station"):
        add("intel", w.on_station, "holding an orbit")
    if p.get("reroute_at") and now - p["reroute_at"] <= REROUTE_RECENT_MS:
        add("intel", w.reroute_recent, "re-routed in the last 30 minutes")

    if state and state not in HOSTILE_STATES:
        # A craft attributed to a non-hostile state is not a threat to Ukraine wherever it flies;
        # its position and role still matter for the intelligence axis.
        reasons = [r for r in reasons if r["axis"] != "threat"]
        add("threat", 0, f"attributed to {p.get('state') or state.upper()}: not scored as a threat")
    elif not state:
        add("threat", 0, "state not attributed: scored as potentially hostile")
    threat = min(w.cap, sum(r["points"] for r in reasons if r["axis"] == "threat"))
    intel = min(w.cap, sum(r["points"] for r in reasons if r["axis"] == "intel"))
    priority = min(w.cap, round(threat + w.intel_share * intel))
    return {
        "priority": priority,
        "threat": threat,
        "intel": intel,
        "dist_ua_km": round(dist),
        "reasons": reasons,
    }


class ThreatBoard:
    def __init__(self, gnss: GnssGrid | None = None):
        self.gnss = gnss
        self.ukraine: Ukraine | None = None

    def update(self, hub: Hub, now: int) -> int:
        if self.ukraine is None:
            self.ukraine = Ukraine()
        nets_of = {}
        for n in hub.of_kind("net"):
            for m in (n.get("props") or {}).get("members", []):
                nets_of[m["id"]] = {"id": n["id"], "mission": n["props"].get("mission")}
        n = 0
        for kind in ("aircraft", "vessel"):
            for e in list(hub.of_kind(kind)):
                if not (e.get("props") or {}).get("military"):
                    continue
                s = score(e, self.ukraine, nets_of, self.gnss, now)
                prev = (e.get("props") or {}).get("threat")
                if prev is None or {k: prev.get(k) for k in s} != s:
                    hub.annotate(e["id"], "threat", s)
                n += 1
        return n


async def run(hub: Hub, board: ThreatBoard) -> None:
    hub.source(NAME)
    board.ukraine = await asyncio.to_thread(Ukraine)
    while True:
        n = board.update(hub, now_ms())
        hub.source_ok(NAME, f"{n} craft ranked")
        await asyncio.sleep(EVERY_S)
