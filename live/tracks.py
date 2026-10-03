"""Server-side track history for military aircraft and naval vessels (CLAUDE.md §16.1, M2).

Every observed position is kept for HISTORY_H hours, thinned to one point per MIN_STEP_S unless
the heading, altitude or ground state changed, so turns and climbs survive thinning. The store
is rebuilt from the hub's JSONL recordings on start-up, so history survives a restart.

Flights are derived on read: a new flight starts after a gap longer than FLIGHT_GAP_S or when
an aircraft leaves the ground. Each flight's first and last points are matched to the nearest
airfield (OurAirports) when they are low enough to be a take-off or landing.
"""

from __future__ import annotations

import json
import logging
import math
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from live.hub import Hub, now_ms
from reference.airfields import Airfield

log = logging.getLogger("terrestrial.live")

HISTORY_H = 48
MIN_STEP_S = 5
TURN_DEG = 2.0
CLIMB_M = 150.0
FLIGHT_GAP_S = 20 * 60
TERMINAL_ALT_M = 900.0  # first/last point below this may be a take-off/landing
AIRFIELD_RADIUS_KM = 8.0
KINDS = ("aircraft", "vessel")
EXACT_KM = 0.6
APPROACH_KM = 20.0
APPROACH_DEG = 30.0
SIZE_BONUS_KM = 1.0
MILITARY_BONUS_KM = 1.5
SIZE_CLASS = {"large_airport": 3, "medium_airport": 2, "small_airport": 1}


@dataclass(slots=True)
class Point:
    ts: int  # observation time, ms
    lon: float
    lat: float
    alt: float  # metres
    spd: float | None
    hdg: float | None
    ground: bool

    def row(self) -> list:
        return [
            self.ts,
            round(self.lon, 5),
            round(self.lat, 5),
            round(self.alt),
            self.spd,
            self.hdg,
            self.ground,
        ]


def _point(e: dict) -> Point:
    p = e.get("props") or {}
    ts = e.get("orig_ts") or e["ts"]
    return Point(
        int(ts),
        float(e["lon"]),
        float(e["lat"]),
        float(e.get("alt") or 0.0),
        e.get("spd"),
        e.get("hdg"),
        bool(p.get("on_ground")),
    )


def _angle(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return 0.0
    d = abs(a - b) % 360
    return min(d, 360 - d)


def tracked(e: dict) -> bool:
    return (
        e.get("kind") in KINDS and bool((e.get("props") or {}).get("military")) and e.get("lon") is not None
    )


class AirfieldIndex:
    """Nearest airfield by great-circle distance (vectorised over ~12k airfields)."""

    def __init__(self, fields: tuple[Airfield, ...] | list[Airfield]):
        self.fields = list(fields)
        self.lon = np.radians([a.lon for a in self.fields])
        self.lat = np.radians([a.lat for a in self.fields])
        self._kind: np.ndarray | None = None
        self._mil: np.ndarray | None = None

    def nearest(
        self, lon: float, lat: float, within_km: float = AIRFIELD_RADIUS_KM
    ) -> tuple[Airfield, float] | None:
        if not self.fields:
            return None
        lo, la = math.radians(lon), math.radians(lat)
        h = (
            np.sin((self.lat - la) / 2) ** 2
            + np.cos(la) * np.cos(self.lat) * np.sin((self.lon - lo) / 2) ** 2
        )
        d = 2 * 6371.0 * np.arcsin(np.sqrt(h))
        i = int(np.argmin(d))
        return (self.fields[i], float(d[i])) if d[i] <= within_km else None

    def landing_site(
        self,
        lon: float,
        lat: float,
        within_km: float = AIRFIELD_RADIUS_KM,
        ground: bool = True,
        heading: float | None = None,
    ) -> tuple[Airfield, float] | None:
        """The airfield an aircraft took off from or landed at (one rule for training and live).

        On the ground: anything within EXACT_KM wins outright (heliports and strips sit beside
        big bases, whose reference point can be kilometres from where an aircraft stops); beyond
        that, larger and military fields are preferred (SIZE_BONUS_KM per size class,
        MILITARY_BONUS_KM). Airborne (last seen low on approach, or first seen climbing out):
        coverage often ends kilometres short of the runway, so the field must lie along
        `heading` (pass the reciprocal for a departure), within APPROACH_KM, and be a runway
        airfield; a field directly below (EXACT_KM) still wins."""
        if not self.fields:
            return None
        if self._kind is None:
            self._kind = np.array([SIZE_CLASS.get(a.kind, 0) for a in self.fields])
            self._mil = np.array([a.military for a in self.fields])
        lo, la = math.radians(lon), math.radians(lat)
        dlon = self.lon - lo
        h = np.sin((self.lat - la) / 2) ** 2 + np.cos(la) * np.cos(self.lat) * np.sin(dlon / 2) ** 2
        d = 2 * 6371.0 * np.arcsin(np.sqrt(h))
        i = int(np.argmin(d))
        if d[i] <= EXACT_KM:
            return self.fields[i], float(d[i])
        bonus = SIZE_BONUS_KM * self._kind + MILITARY_BONUS_KM * self._mil
        if ground or heading is None:
            near = np.where(d <= within_km)[0]
            if not len(near):
                return None
            j = int(near[np.argmin(d[near] - bonus[near])])
            return self.fields[j], float(d[j])
        y = np.sin(dlon) * np.cos(self.lat)
        x = np.cos(la) * np.sin(self.lat) - np.sin(la) * np.cos(self.lat) * np.cos(dlon)
        off = np.abs((np.degrees(np.arctan2(y, x)) - heading + 180) % 360 - 180)
        near = np.where(
            ((d <= within_km) | ((d <= APPROACH_KM) & (off <= APPROACH_DEG))) & (self._kind >= 1)
        )[0]
        if not len(near):
            return None
        j = int(near[np.argmin(d[near] * (1 + off[near] / 45) - bonus[near])])
        return self.fields[j], float(d[j])


class TrackStore:
    """Hub listener: `store(hub, entity)` on every upsert."""

    def __init__(self, airfields: AirfieldIndex | None = None, history_h: float = HISTORY_H):
        self.points: dict[str, deque[Point]] = {}
        self.labels: dict[str, str] = {}
        self.airfields = airfields
        self.history_ms = int(history_h * 3600 * 1000)

    def __call__(self, hub: Hub | None, e: dict) -> None:
        if tracked(e):
            self.add(e)

    def add(self, e: dict) -> None:
        pt = _point(e)
        track = self.points.setdefault(e["id"], deque())
        self.labels[e["id"]] = e.get("label") or e["id"]
        if track:
            last = track[-1]
            if pt.ts <= last.ts:
                return  # out of order or duplicate observation
            keep = (
                pt.ts - last.ts >= MIN_STEP_S * 1000
                or _angle(pt.hdg, last.hdg) >= TURN_DEG
                or abs(pt.alt - last.alt) >= CLIMB_M
                or pt.ground != last.ground
            )
            if not keep:
                return
        track.append(pt)
        cutoff = pt.ts - self.history_ms
        while track and track[0].ts < cutoff:
            track.popleft()

    # --- read side -------------------------------------------------------------------------

    def _terminal(self, pt: Point, kind: str, outbound: bool = False) -> dict | None:
        if self.airfields is None or kind != "aircraft" or (pt.alt > TERMINAL_ALT_M and not pt.ground):
            return None
        heading = None if pt.hdg is None else (pt.hdg + 180) % 360 if outbound else pt.hdg
        hit = self.airfields.landing_site(pt.lon, pt.lat, ground=pt.ground, heading=heading)
        if hit is None:
            return None
        a, km = hit
        return {
            "ident": a.ident,
            "name": a.name,
            "icao": a.icao,
            "country": a.country,
            "military": a.military,
            "lon": a.lon,
            "lat": a.lat,
            "km": round(km, 1),
        }

    def flights(self, entity_id: str, hours: float = HISTORY_H, now: int | None = None) -> list[dict]:
        kind = entity_id.split(":", 1)[0]
        since = (now or now_ms()) - int(hours * 3600 * 1000)
        # tuple() copies the deque atomically: readers run in worker threads while the loop appends.
        pts = [p for p in tuple(self.points.get(entity_id, ())) if p.ts >= since]
        segments: list[list[Point]] = []
        for p in pts:
            if not segments:
                segments.append([p])
                continue
            prev = segments[-1][-1]
            took_off = prev.ground and not p.ground
            if p.ts - prev.ts > FLIGHT_GAP_S * 1000 or took_off:
                segments.append([p])
            else:
                segments[-1].append(p)
        out = []
        current = now or now_ms()
        for seg in segments:
            first, last = seg[0], seg[-1]
            # Landed: on the ground, or last seen low and not heard from since (a finished segment).
            landed = last.ground or (last.alt <= TERMINAL_ALT_M and current - last.ts > FLIGHT_GAP_S * 1000)
            out.append(
                {
                    "start": first.ts,
                    "end": last.ts,
                    "origin": self._terminal(first, kind, outbound=True),
                    "landing": self._terminal(last, kind) if landed else None,
                    "points": [p.row() for p in seg],
                }
            )
        return out

    def track(self, entity_id: str, hours: float = HISTORY_H) -> dict:
        if entity_id not in self.points:
            raise KeyError(entity_id)
        flights = self.flights(entity_id, hours)
        return {
            "id": entity_id,
            "label": self.labels.get(entity_id),
            "hours": hours,
            "first_seen": self.points[entity_id][0].ts if self.points[entity_id] else None,
            "points": sum(len(f["points"]) for f in flights),
            "columns": ["ts", "lon", "lat", "alt_m", "spd_kn", "hdg", "on_ground"],
            "flights": flights,
        }

    # --- persistence ----------------------------------------------------------------------

    def rebuild(self, record_dir: Path, days: int = 2) -> int:
        """Replay recorded upserts of tracked entities (today and the previous days)."""
        today = datetime.now(UTC).date()
        n = 0
        for back in range(days - 1, -1, -1):
            path = record_dir / f"{(today - timedelta(days=back)).strftime('%Y%m%d')}.jsonl"
            if not path.exists():
                continue
            with path.open(encoding="utf-8") as f:
                for line in f:
                    # Cheap pre-filter before parsing: most lines are other kinds or civil traffic.
                    if '"military":true' not in line or (
                        '"kind":"aircraft"' not in line and '"kind":"vessel"' not in line
                    ):
                        continue
                    msg = json.loads(line)
                    if msg.get("t") == "upsert" and tracked(msg["e"]):
                        self.add(msg["e"])
                        n += 1
        log.info("track store: rebuilt %d positions for %d tracks from recordings", n, len(self.points))
        return n
