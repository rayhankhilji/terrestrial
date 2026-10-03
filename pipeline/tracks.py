"""Dark tracks: follow a hull across its AIS gap through time-consistent SAR detections (§15.5).

For one gap, candidates are ordered by their feasible time window. We look for the longest
chain A → P₁ → … → Pₖ → B such that every leg can be flown at the gap's max speed, carrying
the earliest feasible arrival time forward (earliest-arrival dynamic programming). Ties are
broken by the shortest total path. The result re-identifies the hull at B as the one that
went dark at A and gives the analyst a concrete, checkable route; it remains a candidate
reconstruction, not an observed track.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from pipeline import geo
from pipeline.config import KM_PER_NM
from pipeline.match import HOUR_NS, ns


@dataclass(frozen=True)
class DarkTrack:
    gap_id: str
    sar_ids: list[str]
    points: list[tuple[float, float]]  # (lon, lat) including A and B
    times: list[pd.Timestamp]  # earliest feasible time at each point, A and B included
    length_km: float
    max_leg_speed_kn: float


def dark_track(gap, candidates: pd.DataFrame, vmax_kn: float) -> DarkTrack | None:
    """Longest feasible chain through `candidates` (time-consistent rows of one gap)."""
    c = candidates.sort_values(["earliest", "latest"]).reset_index(drop=True)
    if c.empty:
        return None
    speed = vmax_kn * KM_PER_NM  # km/h
    lon, lat = c["lon"].to_numpy(), c["lat"].to_numpy()
    earliest = ns(c["earliest"])
    latest = ns(c["latest"])
    n = len(c)
    # pairwise distances between candidates
    d = geo.distance_km_many(lon[:, None], lat[:, None], lon[None, :], lat[None, :])
    d_from_a = c["dist_from_off_km"].to_numpy()
    d_to_b = c["dist_to_on_km"].to_numpy()

    best_len = np.zeros(n, dtype=int)
    best_time = np.zeros(n, dtype="int64")
    best_dist = np.full(n, np.inf)
    parent = np.full(n, -1)
    t_off, t_on = ns(gap.start), ns(gap.end)

    for i in range(n):
        # start the chain at A
        arrive = max(earliest[i], t_off + _hours(d_from_a[i] / speed))
        if arrive <= latest[i]:
            best_len[i], best_time[i], best_dist[i] = 1, arrive, d_from_a[i]
        for j in range(i):
            if best_len[j] == 0:
                continue
            arrive = max(earliest[i], best_time[j] + _hours(d[j, i] / speed))
            if arrive > latest[i]:
                continue
            length, dist = best_len[j] + 1, best_dist[j] + d[j, i]
            if length > best_len[i] or (length == best_len[i] and dist < best_dist[i]):
                best_len[i], best_time[i], best_dist[i], parent[i] = length, arrive, dist, j

    # the chain must still reach B in time
    feasible = [
        i for i in range(n) if best_len[i] > 0 and best_time[i] + _hours(d_to_b[i] / speed) <= t_on
    ]
    if not feasible:
        return None
    end = max(feasible, key=lambda i: (best_len[i], -(best_dist[i] + d_to_b[i])))
    chain = []
    i = end
    while i != -1:
        chain.append(i)
        i = parent[i]
    chain.reverse()

    points = [(gap.off_lon, gap.off_lat), *[(lon[i], lat[i]) for i in chain], (gap.on_lon, gap.on_lat)]
    times = [gap.start, *[pd.Timestamp(int(best_time[i]), tz="UTC") for i in chain], gap.end]
    legs = [geo.distance_km(*points[k], *points[k + 1]) for k in range(len(points) - 1)]
    leg_speeds = [
        legs[k] / max((times[k + 1] - times[k]) / pd.Timedelta(hours=1), 1e-9) / KM_PER_NM
        for k in range(len(legs))
    ]
    return DarkTrack(
        gap_id=gap.gap_id,
        sar_ids=[c.loc[i, "sar_id"] for i in chain],
        points=[(float(x), float(y)) for x, y in points],
        times=times,
        length_km=float(sum(legs)),
        max_leg_speed_kn=float(max(leg_speeds)),
    )


def _hours(h: float) -> int:
    return int(round(h * HOUR_NS))
