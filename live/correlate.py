"""Cross-source inference on the live stream: effects, causes and anomalies.

Every relation or alert produced here is `prov: "inferred"` and carries `why`: the ids of the
observed facts it rests on, so the UI can show the chain. Wording stays probabilistic.

Rules
- Thermal anomaly within FIRE_KM of a facility           → THERMAL_ANOMALY_AT (+ alert)
- News event (force/coercion) within NEWS_KM of facility → REPORTED_AT
- Both on the same facility within CORROBORATE_H          → "corroborated" alert
- AIS vessel inside an occupied-port AOI                  → PRESENT_IN_AOI (+ alert)
- AIS implied speed between reports > JUMP_KN             → possible spoofing alert
- OpenSanctions-listed hull transmitting                  → alert
- Military / unmanned aircraft entering the theatre       → alert
"""

from __future__ import annotations

import math
import time

import numpy as np

from live.hub import Hub, now_ms
from pipeline import aoi as aois

FIRE_KM = 5.0
NEWS_KM = 12.0
CORROBORATE_H = 24
JUMP_KN = 60.0
STRIKE_RELEVANT = {"refinery", "port", "naval base", "airbase"}


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * np.arcsin(np.sqrt(a))


class Correlator:
    def __init__(self, facilities: list[dict]):
        self.facilities = facilities
        self.lat = np.array([f["lat"] for f in facilities], dtype=float)
        self.lon = np.array([f["lon"] for f in facilities], dtype=float)
        self._last_alert: dict[str, float] = {}
        self._last_pos: dict[str, tuple[float, float, int]] = {}
        self._links: dict[str, dict[str, list[str]]] = {}  # facility qid -> {"fire": [...], "news": [...]}

    def _near(self, lat: float, lon: float, km: float) -> list[tuple[dict, float]]:
        if not self.facilities:
            return []
        d = haversine_km(lat, lon, self.lat, self.lon)
        idx = np.nonzero(d <= km)[0]
        return sorted(((self.facilities[i], float(d[i])) for i in idx), key=lambda x: x[1])

    def _alert_once(self, hub: Hub, key: str, every_s: float, alert: dict) -> None:
        t = time.monotonic()
        if t - self._last_alert.get(key, -1e18) < every_s:
            return
        self._last_alert[key] = t
        hub.alert({"id": f"{key}:{now_ms()}", "prov": "inferred", **alert})

    def __call__(self, hub: Hub, e: dict) -> None:
        handler = getattr(self, f"_on_{e['kind']}", None)
        if handler:
            handler(hub, e)

    # --- fires and news → facilities ---------------------------------------------------------

    def _link(self, hub: Hub, e: dict, km: float, rel: str, bucket: str) -> None:
        for facility, dist in self._near(e["lat"], e["lon"], km):
            fid = f"facility:{facility['qid']}"
            hub.relate(
                {
                    "id": f"{rel}:{e['id']}:{fid}",
                    "rel": rel,
                    "a": e["id"],
                    "b": fid,
                    "prov": "inferred",
                    "src": "terrestrial",
                    "why": [e["id"], fid],
                    "detail": f"{dist:.1f} km from {facility['name']}",
                }
            )
            links = self._links.setdefault(facility["qid"], {"fire": [], "news": []})
            links[bucket].append(e["id"])
            self._check_corroboration(hub, facility, links)
            if bucket == "fire" and facility["type"] in STRIKE_RELEVANT:
                self._alert_once(
                    hub,
                    f"fire:{facility['qid']}",
                    3600,
                    {
                        "severity": "high" if facility["type"] == "refinery" else "warn",
                        "title": f"Thermal anomaly at {facility['name']}",
                        "body": f"VIIRS hotspot {dist:.1f} km from this {facility['type']}; consistent with fire, flaring or a strike.",
                        "entities": [e["id"], fid],
                        "lon": e["lon"],
                        "lat": e["lat"],
                        "why": [e["id"], fid],
                    },
                )

    def _check_corroboration(self, hub: Hub, facility: dict, links: dict[str, list[str]]) -> None:
        cutoff = now_ms() - CORROBORATE_H * 3600 * 1000
        fires = [i for i in links["fire"] if hub.entities.get(i, {}).get("ts", 0) >= cutoff]
        news = [i for i in links["news"] if hub.entities.get(i, {}).get("ts", 0) >= cutoff]
        if fires and news:
            headline = hub.entities[news[-1]]["label"]
            self._alert_once(
                hub,
                f"corroborated:{facility['qid']}",
                6 * 3600,
                {
                    "severity": "high",
                    "title": f"Corroborated: {facility['name']}",
                    "body": f"{len(fires)} thermal anomal{'y' if len(fires) == 1 else 'ies'} and {len(news)} news report(s) "
                    f"(latest: {headline}) within {CORROBORATE_H} h. Two independent sources are consistent with an incident here.",
                    "entities": [*fires[-3:], *news[-3:], f"facility:{facility['qid']}"],
                    "lon": facility["lon"],
                    "lat": facility["lat"],
                    "why": [*fires, *news],
                },
            )

    def _on_fire(self, hub: Hub, e: dict) -> None:
        self._link(hub, e, FIRE_KM, "THERMAL_ANOMALY_AT", "fire")

    def _on_news(self, hub: Hub, e: dict) -> None:
        self._link(hub, e, NEWS_KM, "REPORTED_AT", "news")

    # --- vessels ------------------------------------------------------------------------------

    def _on_vessel(self, hub: Hub, e: dict) -> None:
        mmsi = e["props"].get("mmsi")
        name = e["label"]
        previous = self._last_pos.get(e["id"])
        self._last_pos[e["id"]] = (e["lat"], e["lon"], e["ts"])
        if previous and e["ts"] > previous[2]:
            hours = (e["ts"] - previous[2]) / 3.6e6
            km = float(haversine_km(previous[0], previous[1], e["lat"], e["lon"]))
            knots = km / 1.852 / hours if hours > 0 else math.inf
            if km > 2 and knots > JUMP_KN:
                self._alert_once(
                    hub,
                    f"jump:{mmsi}",
                    1800,
                    {
                        "severity": "warn",
                        "title": f"AIS position jump: {name}",
                        "body": f"{km:.0f} km in {hours * 60:.0f} min implies {knots:.0f} kn; consistent with spoofing or MMSI sharing.",
                        "entities": [e["id"]],
                        "lon": e["lon"],
                        "lat": e["lat"],
                        "why": [e["id"]],
                    },
                )

        inside = aois.containing(e["lon"], e["lat"])[0]
        if inside:
            area = aois.AOI_BY_NAME[inside]
            hub.relate(
                {
                    "id": f"PRESENT_IN_AOI:{e['id']}:{inside}",
                    "rel": "PRESENT_IN_AOI",
                    "a": e["id"],
                    "b": f"station:{inside}",
                    "prov": "observed",
                    "src": "aisstream",
                    "why": [e["id"]],
                    "detail": f"AIS position inside the {inside} AOI",
                }
            )
            if area.occupied_ua:
                self._alert_once(
                    hub,
                    f"occupied:{mmsi}:{inside}",
                    6 * 3600,
                    {
                        "severity": "high",
                        "title": f"{name} transmitting inside occupied {inside}",
                        "body": "AIS position within 15 km of an occupied Ukrainian port.",
                        "entities": [e["id"]],
                        "lon": e["lon"],
                        "lat": e["lat"],
                        "why": [e["id"]],
                    },
                )

        listing = e["props"].get("sanctions")
        if listing and listing.get("sanctioned"):
            self._alert_once(
                hub,
                f"listed:{mmsi}",
                6 * 3600,
                {
                    "severity": "high",
                    "title": f"Listed vessel transmitting: {name}",
                    "body": f"Matches OpenSanctions entry “{listing['name']}” on {listing['matched_on'].upper()} ({', '.join(listing['topics'])}).",
                    "entities": [e["id"]],
                    "lon": e["lon"],
                    "lat": e["lat"],
                    "why": [e["id"]],
                    "url": listing["url"],
                },
            )

    # --- aircraft -----------------------------------------------------------------------------

    def _on_aircraft(self, hub: Hub, e: dict) -> None:
        p = e["props"]
        if not (p.get("military") or p.get("uav")):
            return
        what = "Unmanned aircraft" if p.get("uav") else "Military aircraft"
        self._alert_once(
            hub,
            f"mil:{p['icao24']}",
            1800,
            {
                "severity": "info" if not p.get("uav") else "warn",
                "title": f"{what} in theatre: {e['label']}",
                "body": f"Type {p.get('type') or 'unknown'}, {round((e.get('alt') or 0) / 0.3048):,} ft, {e.get('spd') or '?'} kn (public ADS-B).",
                "entities": [e["id"]],
                "lon": e["lon"],
                "lat": e["lat"],
                "why": [e["id"]],
            },
        )
