"""Snapshot × candidate-airfield rows for the destination ranker.

A snapshot is the state of a flight at one moment. Every kinematic input is derived from the
position history up to that moment (`kinematics`), never from fields only one source carries,
so the adsb.lol archive (training) and the live track store (serving) produce identical
features (CLAUDE.md §16.4 train/serve parity).

For each snapshot the candidates are the airfields the aircraft could plausibly still reach:
within its remaining range (ground speed × remaining endurance, where endurance is the learned
95th percentile flight duration of its type), ranked by an "effort" that prefers fields ahead
of it, plus its origin and the fields its airframe, type and callsign family landed at before.
Each row describes one (snapshot, candidate) pair; the ranker scores rows, and probabilities
are the scores normalised within the snapshot.

Priors (hex / type / callsign family → airfield counts) only ever contain flights that ended
before the snapshot's flight began, in training and in serving alike.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from reference.airfields import Airfield
from reference.roles import ROLES

K = 40
K_ANY = 20
K_AHEAD = 30
REACH_MARGIN = 1.3
SNAPSHOT_EVERY_MIN = 5
MIN_REMAINING_MIN = 45
VRATE_WINDOW_S = 120
MIN_DERIVE_S = 30
TURN_WINDOW_S = 15 * 60
FAMILY = re.compile(r"^([A-Z]{2,6})\d")
KIND_CODE = {"large_airport": 3, "medium_airport": 2, "small_airport": 1, "heliport": 0, "seaplane_base": 0}
ROLE_CODE = {r: i for i, r in enumerate(ROLES)}

FEATURES = [
    "dist_km", "bearing_off", "closing_kn", "eta_min", "eta_vs_remaining", "dist_rank", "effort_rank",
    "is_origin", "cand_military", "cand_kind", "same_country", "alt_m", "vrate_fpm", "gs_kn",
    "turn_15", "straightness", "elapsed_min", "elapsed_frac", "origin_known", "rotary", "role",
    "hex_prior", "type_prior", "family_prior", "hex_prior_n", "type_prior_n", "family_prior_n",
]  # fmt: skip


def family(callsign: str | None) -> str | None:
    m = FAMILY.match((callsign or "").strip().upper())
    return m.group(1) if m else None


@dataclass
class Priors:
    """Counts of landings per airframe (hex), ICAO type and callsign family."""

    by_hex: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    by_type: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    by_family: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

    def add(self, hex_: str, type_: str | None, callsign: str | None, landing: str) -> None:
        self.by_hex[hex_][landing] += 1
        if type_:
            self.by_type[type_][landing] += 1
        if fam := family(callsign):
            self.by_family[fam][landing] += 1

    @staticmethod
    def share(table: dict[str, Counter], key: str | None, idents: np.ndarray) -> tuple[np.ndarray, int]:
        c = table.get(key or "")
        if not c:
            return np.zeros(len(idents)), 0
        n = sum(c.values())
        return np.array([c.get(i, 0) / n for i in idents]), n

    def top(self, table: dict[str, Counter], key: str | None, k: int = 3) -> list[str]:
        c = table.get(key or "")
        return [ident for ident, _ in c.most_common(k)] if c else []

    def to_dict(self) -> dict:
        return {
            name: {k: dict(v) for k, v in getattr(self, name).items()}
            for name in ("by_hex", "by_type", "by_family")
        }

    @classmethod
    def from_dict(cls, d: dict) -> Priors:
        p = cls()
        for name in ("by_hex", "by_type", "by_family"):
            for k, v in d.get(name, {}).items():
                getattr(p, name)[k] = Counter(v)
        return p


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
        self.country = np.array([(a.country or "").upper() for a in self.items])

    def geometry(self, lon: float, lat: float) -> tuple[np.ndarray, np.ndarray]:
        """Distance (km) and initial bearing (deg) from a point to every airfield."""
        lo, la = np.radians(lon), np.radians(lat)
        dlon = self.lon - lo
        h = np.sin((self.lat - la) / 2) ** 2 + np.cos(la) * np.cos(self.lat) * np.sin(dlon / 2) ** 2
        dist = 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
        y = np.sin(dlon) * np.cos(self.lat)
        x = np.cos(la) * np.sin(self.lat) - np.sin(la) * np.cos(self.lat) * np.cos(dlon)
        return dist, (np.degrees(np.arctan2(y, x)) + 360) % 360


def _hav_km(lat1, lon1, lat2, lon2):
    la1, la2 = np.radians(lat1), np.radians(lat2)
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def kinematics(ts: np.ndarray, lat: np.ndarray, lon: np.ndarray, alt: np.ndarray, gs: np.ndarray,
               trk: np.ndarray, i: int) -> dict:  # fmt: skip
    """State at index i of a flight's arrays (ts in seconds), from history up to i only."""
    t = ts[i]
    j = int(np.searchsorted(ts, t - VRATE_WINDOW_S))
    dt = max(t - ts[j], 1.0)
    vrate = (alt[i] - alt[j]) / 0.3048 / (dt / 60) if i > j else 0.0
    k = int(np.searchsorted(ts, t - TURN_WINDOW_S))
    tr = trk[k : i + 1]
    tr = tr[~np.isnan(tr)]
    turn = float(np.abs((np.diff(tr) + 180) % 360 - 180).sum()) if len(tr) > 1 else 0.0
    path = float(_hav_km(lat[k:i], lon[k:i], lat[k + 1 : i + 1], lon[k + 1 : i + 1]).sum()) if i > k else 0.0
    disp = float(_hav_km(lat[k], lon[k], lat[i], lon[i]))
    # Ground speed: reported, unless the positions disagree. MLAT-derived speeds are often wrong
    # (two feeds reported 245 and 479 kn for the same C-17 at FL350), so on a straight segment of
    # at least MIN_DERIVE_S the speed implied by the positions wins when they differ by > 35 %.
    g = gs[i]
    wt = trk[j : i + 1]
    wt = wt[~np.isnan(wt)]
    straight = len(wt) < 2 or float(np.abs((np.diff(wt) + 180) % 360 - 180).sum()) < 45
    derived = (
        float(_hav_km(lat[j], lon[j], lat[i], lon[i])) / (dt / 3600) / 1.852
        if t - ts[j] >= MIN_DERIVE_S
        else None
    )
    if np.isnan(g):
        g = (
            derived
            if derived is not None
            else (path / max((t - ts[k]) / 3600, 1e-6) / 1.852 if i > k else 0.0)
        )
    elif derived is not None and straight and derived > 50 and abs(g - derived) > 0.35 * derived:
        g = derived
    tk = trk[i]
    if np.isnan(tk) and i > 0:
        y = np.sin(np.radians(lon[i] - lon[i - 1])) * np.cos(np.radians(lat[i]))
        x = np.cos(np.radians(lat[i - 1])) * np.sin(np.radians(lat[i])) - np.sin(
            np.radians(lat[i - 1])
        ) * np.cos(np.radians(lat[i])) * np.cos(np.radians(lon[i] - lon[i - 1]))
        tk = (np.degrees(np.arctan2(y, x)) + 360) % 360
    return {
        "lat": float(lat[i]),
        "lon": float(lon[i]),
        "alt_m": float(alt[i]),
        "gs_kn": float(g),
        "track": float(0.0 if np.isnan(tk) else tk),
        "vrate_fpm": float(np.clip(vrate, -8000, 8000)),
        "turn_15": min(turn, 2000.0),
        "straightness": disp / path if path > 1 else 1.0,
    }


@dataclass
class Airframe:
    """What the ranker knows about the aircraft, independent of time."""

    hex: str
    type: str | None
    callsign: str | None
    role: str
    rotary: bool
    country: str | None  # ISO alpha-2, upper case
    endurance_min: float


def candidates(fields: Fields, lon: float, lat: float, track: float, gs_kn: float, remaining_min: float,
               rotary: bool, extra: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:  # fmt: skip
    dist, brg = fields.geometry(lon, lat)
    off = np.abs((brg - track + 180) % 360 - 180)
    # Learned endurance underestimates range (flights leave coverage), so allow a margin.
    reach = max(gs_kn, 120.0) * 1.852 * max(REACH_MARGIN * remaining_min, MIN_REMAINING_MIN) / 60 + 50
    ok = dist <= reach
    if not rotary:
        ok &= fields.kind > 0  # fixed-wing aircraft do not land at heliports
    effort = dist * (1 + off / 60)
    # Military flights mostly use military or larger fields: K of those, plus the nearest few of
    # any kind (dense European airspace has dozens of small strips within a few minutes).
    major = np.where(ok & (fields.military | (fields.kind >= 2)))[0]
    anyk = np.where(ok)[0]
    idx = major[np.argsort(effort[major])][:K]
    idx = np.union1d(idx, anyk[np.argsort(effort[anyk])][:K_ANY])
    # …and long-haul: major fields closest to the current great-circle heading (cross-track km).
    ahead = major[off[major] < 45]
    xtrack = dist[ahead] * np.sin(np.radians(off[ahead]))
    idx = np.union1d(idx, ahead[np.argsort(xtrack)][:K_AHEAD])
    far = major[(dist[major] > 300) & (off[major] < 20)]
    idx = np.union1d(idx, far[np.argsort(off[far])][:K_AHEAD])
    for ident in extra:
        j = fields.index.get(ident)
        if j is not None and j not in idx:
            idx = np.append(idx, j)
    return idx, dist, off


def snapshot_rows(fields: Fields, priors: Priors, ac: Airframe, kin: dict, elapsed_min: float,
                  origin: str | None) -> pd.DataFrame:  # fmt: skip
    """Candidate rows for one snapshot (columns: cand + FEATURES)."""
    remaining = max(ac.endurance_min - elapsed_min, 0.3 * ac.endurance_min)
    extra = [origin] if origin else []
    fam = family(ac.callsign)
    extra += (
        priors.top(priors.by_hex, ac.hex)
        + priors.top(priors.by_type, ac.type)
        + priors.top(priors.by_family, fam)
    )
    idx, dist, off = candidates(
        fields, kin["lon"], kin["lat"], kin["track"], kin["gs_kn"], remaining, ac.rotary, extra
    )
    gs = max(kin["gs_kn"], 1.0)
    d, o = dist[idx], off[idx]
    idents = fields.ident[idx]
    eta = d / (gs * 1.852) * 60
    effort = d * (1 + o / 60)
    hp, hn = priors.share(priors.by_hex, ac.hex, idents)
    tp, tn = priors.share(priors.by_type, ac.type, idents)
    fp, fn = priors.share(priors.by_family, fam, idents)
    return pd.DataFrame(
        {
            "cand": idents,
            "dist_km": d,
            "bearing_off": o,
            "closing_kn": gs * np.cos(np.radians(o)),
            "eta_min": eta,
            "eta_vs_remaining": eta / max(remaining, 1.0),
            "dist_rank": d.argsort().argsort(),
            "effort_rank": effort.argsort().argsort(),
            "is_origin": (idents == (origin or "")).astype(int),
            "cand_military": fields.military[idx].astype(int),
            "cand_kind": fields.kind[idx],
            "same_country": (fields.country[idx] == (ac.country or "?")).astype(int),
            "alt_m": kin["alt_m"],
            "vrate_fpm": kin["vrate_fpm"],
            "gs_kn": gs,
            "turn_15": kin["turn_15"],
            "straightness": kin["straightness"],
            "elapsed_min": elapsed_min,
            "elapsed_frac": elapsed_min / max(ac.endurance_min, 1.0),
            "origin_known": int(origin is not None),
            "rotary": int(ac.rotary),
            "role": ROLE_CODE.get(ac.role, ROLE_CODE["unknown"]),
            "hex_prior": hp,
            "type_prior": tp,
            "family_prior": fp,
            "hex_prior_n": hn,
            "type_prior_n": tn,
            "family_prior_n": fn,
        }
    )


def snapshot_indices(
    ts: np.ndarray, every_min: int = SNAPSHOT_EVERY_MIN, skip_final_min: int = 5
) -> list[int]:
    """Indices of the last position at each `every_min` mark after take-off, excluding the final
    `skip_final_min` minutes (by then the destination is obvious)."""
    if len(ts) < 2:
        return []
    marks = np.arange(ts[0] + every_min * 60, ts[-1] - skip_final_min * 60, every_min * 60)
    pos = np.searchsorted(ts, marks, side="right") - 1
    return sorted(set(int(p) for p in pos if p >= 0))
