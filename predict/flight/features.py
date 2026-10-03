"""Snapshot × candidate-airfield rows for the destination ranker.

A snapshot is the state of a flight at one moment (position, altitude, speed, track, climb rate,
time airborne). For each snapshot the candidates are the airfields the aircraft could plausibly
still reach: within its remaining range (speed × remaining endurance, where endurance is the
learned 95th percentile flight duration of its type), ranked by an "effort" that prefers fields
ahead of it, plus its origin and the fields its type and callsign family land at most. Each row
describes one (snapshot, candidate) pair; the ranker scores rows, and probabilities are the
scores normalised within the snapshot.

Priors (type → airfield, callsign family → airfield) are counted on training flights only.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from predict.flight.flights import Flight
from reference.airfields import Airfield

K = 40
SNAPSHOT_EVERY_MIN = 5
MIN_REMAINING_MIN = 30
FAMILY = re.compile(r"^([A-Z]{2,6})\d")
KIND_CODE = {"large_airport": 3, "medium_airport": 2, "small_airport": 1, "heliport": 0, "seaplane_base": 0}

FEATURES = [
    "dist_km", "bearing_off", "closing_kn", "eta_min", "eta_vs_remaining", "dist_rank", "effort_rank",
    "is_origin", "cand_military", "cand_kind", "same_country", "alt_m", "vrate_fpm", "gs_kn",
    "elapsed_min", "elapsed_frac", "type_prior", "family_prior", "type_prior_n", "family_prior_n",
]  # fmt: skip


def family(callsign: str | None) -> str | None:
    m = FAMILY.match((callsign or "").strip().upper())
    return m.group(1) if m else None


@dataclass
class Priors:
    by_type: dict[str, Counter] = field(default_factory=dict)
    by_family: dict[str, Counter] = field(default_factory=dict)

    @classmethod
    def count(cls, flights: list[Flight]) -> Priors:
        t: dict[str, Counter] = defaultdict(Counter)
        f: dict[str, Counter] = defaultdict(Counter)
        for fl in flights:
            if fl.landing:
                if fl.type:
                    t[fl.type][fl.landing] += 1
                if fam := family(fl.callsign):
                    f[fam][fl.landing] += 1
        return cls(dict(t), dict(f))

    def share(self, table: dict[str, Counter], key: str | None, ident: str) -> tuple[float, int]:
        c = table.get(key or "")
        if not c:
            return 0.0, 0
        n = sum(c.values())
        return c[ident] / n, n


class Fields:
    """Vectorised airfield arrays for candidate search."""

    def __init__(self, airfields: list[Airfield] | tuple[Airfield, ...]):
        self.items = [a for a in airfields if a.kind in KIND_CODE]
        self.ident = np.array([a.ident for a in self.items])
        self.index = {a.ident: i for i, a in enumerate(self.items)}
        self.lon = np.radians([a.lon for a in self.items])
        self.lat = np.radians([a.lat for a in self.items])
        self.military = np.array([a.military for a in self.items])
        self.kind = np.array([KIND_CODE[a.kind] for a in self.items])
        self.country = np.array([a.country for a in self.items])

    def geometry(self, lon: float, lat: float) -> tuple[np.ndarray, np.ndarray]:
        """Distance (km) and initial bearing (deg) from a point to every airfield."""
        lo, la = np.radians(lon), np.radians(lat)
        dlon = self.lon - lo
        h = np.sin((self.lat - la) / 2) ** 2 + np.cos(la) * np.cos(self.lat) * np.sin(dlon / 2) ** 2
        dist = 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
        y = np.sin(dlon) * np.cos(self.lat)
        x = np.cos(la) * np.sin(self.lat) - np.sin(la) * np.cos(self.lat) * np.cos(dlon)
        return dist, (np.degrees(np.arctan2(y, x)) + 360) % 360


def candidates(
    fields: Fields, lon: float, lat: float, track: float, gs_kn: float, remaining_min: float,
    rotary: bool, extra: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:  # fmt: skip
    dist, brg = fields.geometry(lon, lat)
    off = np.abs((brg - track + 180) % 360 - 180)
    reach = gs_kn * 1.852 * max(remaining_min, MIN_REMAINING_MIN) / 60 + 50
    ok = dist <= reach
    if not rotary:
        ok &= fields.kind > 0  # fixed-wing aircraft do not land at heliports
    effort = dist * (1 + off / 60)
    idx = np.where(ok)[0]
    idx = idx[np.argsort(effort[idx])][:K]
    for ident in extra:
        j = fields.index.get(ident)
        if j is not None and j not in idx:
            idx = np.append(idx, j)
    return idx, dist, off


def snapshot_rows(
    fields: Fields, priors: Priors, state: dict, idx: np.ndarray, dist: np.ndarray, off: np.ndarray,
) -> pd.DataFrame:  # fmt: skip
    gs = max(float(state["gs_kn"] or 0), 1.0)
    d = dist[idx]
    o = off[idx]
    eta = d / (gs * 1.852) * 60
    tp = [priors.share(priors.by_type, state["type"], fields.ident[j]) for j in idx]
    fp = [priors.share(priors.by_family, state["family"], fields.ident[j]) for j in idx]
    effort = d * (1 + o / 60)
    return pd.DataFrame(
        {
            "cand": fields.ident[idx],
            "dist_km": d,
            "bearing_off": o,
            "closing_kn": gs * np.cos(np.radians(o)),
            "eta_min": eta,
            "eta_vs_remaining": eta / max(state["remaining_min"], 1.0),
            "dist_rank": d.argsort().argsort(),
            "effort_rank": effort.argsort().argsort(),
            "is_origin": (fields.ident[idx] == (state["origin"] or "")).astype(int),
            "cand_military": fields.military[idx].astype(int),
            "cand_kind": fields.kind[idx],
            "same_country": (fields.country[idx] == (state["country"] or "")).astype(int),
            "alt_m": state["alt_m"],
            "vrate_fpm": state["vrate_fpm"] if state["vrate_fpm"] is not None else np.nan,
            "gs_kn": gs,
            "elapsed_min": state["elapsed_min"],
            "elapsed_frac": state["elapsed_min"] / max(state["endurance_min"], 1.0),
            "type_prior": [x[0] for x in tp],
            "family_prior": [x[0] for x in fp],
            "type_prior_n": [x[1] for x in tp],
            "family_prior_n": [x[1] for x in fp],
        }
    )


def flight_snapshots(fl: Flight, every_min: int = SNAPSHOT_EVERY_MIN, skip_final_min: int = 5):
    """Snapshot rows of a flight every `every_min` minutes after take-off, excluding the last
    `skip_final_min` minutes (by then the destination is obvious)."""
    pts = fl.points
    t0 = pts["ts"].iloc[0]
    end = pts["ts"].iloc[-1] - pd.Timedelta(minutes=skip_final_min)
    times = pd.date_range(t0 + pd.Timedelta(minutes=every_min), end, freq=f"{every_min}min")
    if len(times) == 0:
        return []
    pos = np.searchsorted(pts["ts"].to_numpy(), times.to_numpy(), side="right") - 1
    return [pts.iloc[p] for p in pos if p >= 0]
