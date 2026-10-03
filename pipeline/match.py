"""Stage 4 — match: unmatched SAR detections that could be the dark vessel (CLAUDE.md §5.4, §15.5).

Base rule (spec): a detection is a candidate for a gap when it lies inside the gap's
reachability ellipse and its date falls within [gap start date, gap end date] inclusive.

Refinement (§15.5): each detection carries the hour of the satellite pass, so we also test
time consistency. The ship must be able to reach P from A after switching off, and still
reach B by switching on:
    t >= t_off + dist(A,P)/v      and      t <= t_on - dist(P,B)/v
for some t inside the detection's hour. `consistent` candidates are the ones the score and
dark tracks use; the rest stay visible as weaker, date-only candidates.

Every candidate also records how many consistent candidates its gap has, so the UI can say
"1 of N" instead of overstating a match inside a very large ellipse.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
import shapely
from shapely import wkt as shapely_wkt

from pipeline import aoi, geo
from pipeline.config import KM_PER_NM
from pipeline.io import read_table, write_table

log = logging.getLogger("terrestrial")

HOUR_NS = 3_600_000_000_000


def ns(values) -> np.ndarray | int:
    """UTC timestamps (tz-aware Series/Timestamp) as int64 nanoseconds since the epoch."""
    if isinstance(values, pd.Timestamp):
        return values.tz_convert("UTC").as_unit("ns").value
    # pandas 3 parses to microsecond resolution by default; pin nanoseconds explicitly.
    return pd.to_datetime(values, utc=True).dt.as_unit("ns").astype("int64").to_numpy()


def from_ns(values) -> pd.Series:
    return pd.to_datetime(pd.Series(values, dtype="int64"), unit="ns", utc=True)


def match(gaps: pd.DataFrame, envelopes: pd.DataFrame, sar: pd.DataFrame) -> pd.DataFrame:
    """Candidate (gap, detection) pairs with time-consistency fields."""
    if sar.empty or envelopes.empty:
        return _empty()
    g = gaps.merge(envelopes[["gap_id", "polygon_wkt", "vmax_kn", "impossible"]], on="gap_id", how="inner")
    points = shapely.points(sar["lon"].to_numpy(), sar["lat"].to_numpy())
    tree = shapely.STRtree(points)
    sar_ns = ns(sar["ts"])
    sar_day_ns = ns(sar["ts"].dt.normalize())
    rows = []
    for gap in g.itertuples(index=False):
        polygon = shapely_wkt.loads(gap.polygon_wkt)
        idx = tree.query(polygon, predicate="contains")
        if idx.size == 0:
            continue
        days = sar_day_ns[idx]
        idx = idx[(days >= ns(gap.start.normalize())) & (days <= ns(gap.end.normalize()))]
        if idx.size == 0:
            continue
        hit = sar.iloc[idx]
        speed_kmh = gap.vmax_kn * KM_PER_NM
        d_ap = geo.distance_km_many(gap.off_lon, gap.off_lat, hit["lon"].to_numpy(), hit["lat"].to_numpy())
        d_pb = geo.distance_km_many(hit["lon"].to_numpy(), hit["lat"].to_numpy(), gap.on_lon, gap.on_lat)
        t_det = sar_ns[idx]
        earliest = np.maximum(t_det, ns(gap.start) + (d_ap / speed_kmh * HOUR_NS).astype("int64"))
        latest = np.minimum(t_det + HOUR_NS, ns(gap.end) - (d_pb / speed_kmh * HOUR_NS).astype("int64"))
        consistent = earliest <= latest
        rows.append(
            pd.DataFrame(
                {
                    "gap_id": gap.gap_id,
                    "vessel_id": gap.vessel_id,
                    "sar_id": hit["sar_id"].to_numpy(),
                    "ts": hit["ts"].to_numpy(),
                    "lon": hit["lon"].to_numpy(),
                    "lat": hit["lat"].to_numpy(),
                    "detections": hit["detections"].to_numpy(),
                    "dist_from_off_km": d_ap,
                    "dist_to_on_km": d_pb,
                    "consistent": consistent,
                    "earliest": from_ns(np.where(consistent, earliest, np.iinfo("int64").min)),
                    "latest": from_ns(np.where(consistent, latest, np.iinfo("int64").min)),
                    "in_aoi": aoi.containing(hit["lon"].to_numpy(), hit["lat"].to_numpy()),
                }
            )
        )
    if not rows:
        return _empty()
    out = pd.concat(rows, ignore_index=True)
    out["in_aoi"] = out["in_aoi"].astype(object).where(out["in_aoi"].notna(), None)
    out["occupied_aoi"] = out["in_aoi"].map(lambda n: bool(n) and aoi.AOI_BY_NAME[n].occupied_ua)
    n_consistent = out[out["consistent"]].groupby("gap_id").size()
    out["gap_consistent_candidates"] = out["gap_id"].map(n_consistent).fillna(0).astype(int)
    return out


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "gap_id", "vessel_id", "sar_id", "ts", "lon", "lat", "detections", "dist_from_off_km",
            "dist_to_on_km", "consistent", "earliest", "latest", "in_aoi", "occupied_aoi",
            "gap_consistent_candidates",
        ]
    )  # fmt: skip


def stage(start: date, end: date, args) -> None:
    gaps = read_table("gaps")
    closed = gaps[~gaps["open"]]
    envelopes = read_table("gap_envelopes")
    sar = read_table("sar")
    log.info("  in: %d closed gaps, %d envelopes, %d SAR detections", len(closed), len(envelopes), len(sar))
    candidates = match(closed, envelopes, sar)
    # Zero candidates is a legitimate outcome (SAR may not have imaged any gap), so not required;
    # but say so loudly.
    if candidates.empty:
        log.warning("  no SAR detection falls inside any gap envelope during its gap")
    write_table(candidates, "candidates", required=False)
    if not candidates.empty:
        c = candidates[candidates["consistent"]]
        log.info(
            "  %d candidates (%d time-consistent) across %d gaps; %d inside occupied-port AOIs",
            len(candidates), len(c), candidates["gap_id"].nunique(), int(c["occupied_aoi"].sum()),
        )  # fmt: skip
        best = c.sort_values("gap_consistent_candidates").head(5)
        for r in best.itertuples(index=False):
            log.info(
                "    gap %s ↔ SAR %s at %s (%s) — 1 of %d consistent",
                r.gap_id, r.sar_id, r.ts, r.in_aoi or "open sea", r.gap_consistent_candidates,
            )  # fmt: skip
