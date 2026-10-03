"""Flights from position histories: segmentation, take-off and landing airfields.

Works on any table with hex, ts (UTC), lat, lon, alt_m, gs_kn, track, vrate_fpm, ground: the
adsb.lol archive extracts (history/adsb_archive.py) and the live track store alike, so training
and serving segment flights identically.

A flight is a run of positions with no gap longer than GAP_MIN that starts after the aircraft
leaves the ground. It *landed* if it ends on the ground, or ends low (≤ TERMINAL_ALT_M) and
descending within AIRFIELD_KM of an airfield, and that airfield is its label. Flights that end
high or far from any airfield left receiver coverage: their destination is unknown (censored),
they are kept for endurance statistics but never used as labels.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from live.tracks import TERMINAL_ALT_M, AirfieldIndex

GAP_MIN = 20
AIRFIELD_KM = 6.0
MIN_POINTS = 20
MIN_DURATION_MIN = 10


@dataclass
class Flight:
    hex: str
    callsign: str | None
    type: str | None
    reg: str | None
    points: pd.DataFrame  # ts, lat, lon, alt_m, gs_kn, track, vrate_fpm, ground
    origin: str | None  # airfield ident
    landing: str | None  # airfield ident (None = censored)

    @property
    def start(self) -> pd.Timestamp:
        return self.points["ts"].iloc[0]

    @property
    def end(self) -> pd.Timestamp:
        return self.points["ts"].iloc[-1]

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


def _text(value) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _bearing(a, b) -> float | None:
    """Initial bearing a → b in degrees, None if the points coincide."""
    la1, la2 = np.radians(a.lat), np.radians(b.lat)
    dlon = np.radians(b.lon - a.lon)
    y = np.sin(dlon) * np.cos(la2)
    x = np.cos(la1) * np.sin(la2) - np.sin(la1) * np.cos(la2) * np.cos(dlon)
    if abs(x) < 1e-12 and abs(y) < 1e-12:
        return None
    return float((np.degrees(np.arctan2(y, x)) + 360) % 360)


def _heading(pts: pd.DataFrame, at_end: bool) -> float | None:
    """Reported track at a flight's end (or start), else the bearing over the last (first)
    positions: some military transponders omit track on approach."""
    row = pts.iloc[-1] if at_end else pts.iloc[0]
    if not pd.isna(row.track):
        return float(row.track)
    ends = pts.tail(6) if at_end else pts.head(6)
    return _bearing(ends.iloc[0], ends.iloc[-1]) if len(ends) > 1 else None


def _terminal(pts: pd.DataFrame, airfields: AirfieldIndex, descending: bool, outbound: bool) -> str | None:
    row = pts.iloc[0] if outbound else pts.iloc[-1]
    low = bool(row.ground) or (row.alt_m <= TERMINAL_ALT_M and descending)
    if not low:
        return None
    heading = _heading(pts, at_end=not outbound)
    if heading is not None and outbound:
        heading = (heading + 180) % 360
    hit = airfields.landing_site(
        row.lon, row.lat, within_km=AIRFIELD_KM, ground=bool(row.ground), heading=heading
    )
    return hit[0].ident if hit else None


def segment(positions: pd.DataFrame, airfields: AirfieldIndex) -> list[Flight]:
    out: list[Flight] = []
    for hex_, g in positions.sort_values(["hex", "ts"]).groupby("hex", sort=False):
        g = g.reset_index(drop=True)
        gap = g["ts"].diff().dt.total_seconds().fillna(0) > GAP_MIN * 60
        took_off = g["ground"].shift(fill_value=True) & ~g["ground"]
        piece = (gap | took_off).cumsum()
        for _, seg in g.groupby(piece):
            air = seg[~seg["ground"]]
            if len(air) < MIN_POINTS:
                continue
            # Keep the ground point that ends the flight (touchdown), if any.
            last_ground = seg[seg["ground"] & (seg["ts"] > air["ts"].iloc[-1])].head(1)
            pts = pd.concat([air, last_ground]).reset_index(drop=True)
            if (pts["ts"].iloc[-1] - pts["ts"].iloc[0]).total_seconds() < MIN_DURATION_MIN * 60:
                continue
            tail = pts.tail(6)
            descending = bool(
                (tail["alt_m"].diff().fillna(0) <= 30).all()
                and tail["alt_m"].iloc[0] >= tail["alt_m"].iloc[-1]
            )
            climbing = bool(pts.head(6)["alt_m"].iloc[-1] >= pts.head(6)["alt_m"].iloc[0])
            callsign = (
                next((c for c in map(_text, seg["callsign"]) if c), None) if "callsign" in seg else None
            )
            out.append(
                Flight(
                    hex=str(hex_),
                    callsign=callsign,
                    type=_text(seg["type"].iloc[0]) if "type" in seg else None,
                    reg=_text(seg["reg"].iloc[0]) if "reg" in seg else None,
                    points=pts,
                    origin=_terminal(pts, airfields, descending=climbing, outbound=True),
                    landing=_terminal(pts, airfields, descending=descending, outbound=False),
                )
            )
    return out


def endurance_table(flights: list[Flight], quantile: float = 0.95) -> dict[str, float]:
    """Per ICAO type, the `quantile` of flight duration (minutes); key '*' is all types."""
    df = pd.DataFrame({"type": [f.type for f in flights], "minutes": [f.minutes for f in flights]})
    table = {"*": float(np.quantile(df["minutes"], quantile))} if len(df) else {"*": 300.0}
    for t, g in df.dropna().groupby("type"):
        if len(g) >= 5:
            table[str(t)] = float(np.quantile(g["minutes"], quantile))
    return table
