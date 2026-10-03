"""Features and target for the air-raid danger model.

Unit: one region × one 6-hour UTC block (00–06, 06–12, 12–18, 18–24). A prediction is issued at
the end of block b and is about block b+1:

    target  y_active = any air-raid alert active in the region during block b+1
            y_new    = a new alert starts in the region during block b+1 (the warning signal:
                       an alert that was already running does not count)

Every feature is computed from data that exists at the issue time (train/serve parity, §16.4):
- alert history of the region, its neighbours and the whole country up to the end of block b;
- VIINA air attacks only up to VIINA_LAG_DAYS before the issue day (VIINA publishes every few
  days, so a live prediction can never see the last days of events);
- calendar and moon of block b+1 (known in advance);
- weather of the day of block b+1 (observed in training; the forecast in live use).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from history.regions import BY_SIRENS, neighbours
from predict.moon import illumination

BLOCK_H = 6
BLOCKS_PER_DAY = 24 // BLOCK_H
VIINA_LAG_DAYS = 5
START = pd.Timestamp("2022-03-01", tz="UTC")
# Regions with alert data: everything the sirens dataset covers except occupied Luhansk
# (three alerts in four years: the alert system does not operate there).
MODEL_REGIONS = sorted(iso for iso in BY_SIRENS.values() if iso != "UA-09")

FEATURES = [
    "region", "block_of_day", "dow", "month_sin", "month_cos", "moon",
    "m_1", "m_4", "m_28", "m_120", "starts_4", "active_at_issue", "since_last_h",
    "nb_m_1", "nb_m_4", "nat_m_1", "nat_m_4", "nat_regions_1", "nat_regions_4",
    "cloud", "precip", "wind",
    "viina_region_7d", "viina_national_7d", "viina_region_28d",
]  # fmt: skip
CATEGORICAL = ["region"]


def block_index(ts: pd.Series | pd.DatetimeIndex, origin: pd.Timestamp = START) -> np.ndarray:
    return ((pd.to_datetime(ts, utc=True) - origin) // pd.Timedelta(hours=BLOCK_H)).to_numpy()


def alert_grid(alerts: pd.DataFrame, regions: list[str], n_blocks: int, origin: pd.Timestamp = START):
    """Per region and block: alert minutes, alerts started, and minutes of alert still running
    at the block's end (to know whether an alert is active at issue time)."""
    r_index = {iso: i for i, iso in enumerate(regions)}
    minutes = np.zeros((len(regions), n_blocks), dtype=np.float32)
    starts = np.zeros_like(minutes)
    active_end = np.zeros((len(regions), n_blocks), dtype=bool)
    last_end = np.full((len(regions), n_blocks), np.nan, dtype=np.float64)  # hours since origin
    block_s = BLOCK_H * 3600
    sub = alerts[alerts["iso"].isin(r_index)]
    s = ((sub["start"] - origin).dt.total_seconds()).to_numpy()
    e = ((sub["end"] - origin).dt.total_seconds()).to_numpy()
    ri = sub["iso"].map(r_index).to_numpy()
    for r, a, b in zip(ri, s, e, strict=True):
        if b <= 0:
            continue
        a = max(a, 0.0)
        first, last = int(a // block_s), int(min(b, n_blocks * block_s - 1) // block_s)
        if first >= n_blocks:
            continue
        starts[r, first] += 1
        for k in range(first, last + 1):
            lo, hi = k * block_s, (k + 1) * block_s
            minutes[r, k] += (min(b, hi) - max(a, lo)) / 60
            if b >= hi:
                active_end[r, k] = True
        if last < n_blocks:
            last_end[r, last] = np.nanmax([last_end[r, last], b / 3600])
    return minutes, starts, active_end, last_end


def _rolling(x: np.ndarray, w: int) -> np.ndarray:
    """Sum over the last w blocks including the current one (along axis 1)."""
    c = np.cumsum(np.pad(x, ((0, 0), (1, 0))), axis=1, dtype=np.float64)
    out = c[:, 1:] - c[:, np.maximum(np.arange(1, x.shape[1] + 1) - w, 0)]
    return out.astype(np.float32)


def build(
    alerts: pd.DataFrame,
    viina: pd.DataFrame,
    weather: pd.DataFrame,
    geoms: dict,
    end: pd.Timestamp,
    origin: pd.Timestamp = START,
    with_target: bool = True,
) -> pd.DataFrame:
    """One row per (region, issue block b), for issue blocks up to `end`.

    With `with_target`, the last block has no next block observed and is dropped; without it
    (live use), the last row per region is the current forecast.
    """
    regions = MODEL_REGIONS
    n_blocks = int((end - origin) // pd.Timedelta(hours=BLOCK_H)) + 1
    minutes, starts, active_end, last_end = alert_grid(alerts, regions, n_blocks, origin)

    m4, m28, m120 = _rolling(minutes, 4), _rolling(minutes, 28), _rolling(minutes, 120)
    s4 = _rolling(starts, 4)
    # hours since the last alert ended, as of each block's end
    hours_at_end = (np.arange(n_blocks) + 1) * BLOCK_H
    last = pd.DataFrame(last_end.T).ffill().to_numpy().T
    since = np.where(np.isnan(last), 24 * 30, np.clip(hours_at_end[None, :] - last, 0, 24 * 30))
    since = np.where(active_end, 0.0, since)

    nb = neighbours(geoms)
    r_index = {iso: i for i, iso in enumerate(regions)}
    nb_idx = [[r_index[n] for n in nb[iso] if n in r_index] for iso in regions]
    nb_m1 = np.stack([minutes[ix].mean(axis=0) if ix else np.zeros(n_blocks) for ix in nb_idx])
    nb_m4 = np.stack([m4[ix].mean(axis=0) if ix else np.zeros(n_blocks) for ix in nb_idx])
    nat_m1 = minutes.sum(axis=0)
    nat_regions_1 = (minutes > 0).sum(axis=0).astype(np.float32)
    nat_m4 = _rolling(nat_m1[None, :], 4)[0]
    nat_regions_4 = _rolling(nat_regions_1[None, :], 4)[0]

    issue = origin + pd.to_timedelta(np.arange(n_blocks) + 1, unit="h") * BLOCK_H  # end of block b
    target_start = issue
    day = target_start.normalize()

    # VIINA air attacks per region-day, visible only VIINA_LAG_DAYS after the fact.
    va = viina[viina["air_attack"] & viina["iso"].isin(r_index)]
    days = pd.date_range(
        origin.tz_localize(None).normalize() - pd.Timedelta(days=40),
        end.tz_localize(None).normalize(),
        freq="D",
    )
    counts = va.groupby(["iso", "date"]).size().unstack("iso").reindex(index=days, columns=regions).fillna(0)
    lagged = counts.shift(VIINA_LAG_DAYS)
    v7, v28 = lagged.rolling(7, min_periods=1).sum(), lagged.rolling(28, min_periods=1).sum()
    vn7 = v7.sum(axis=1)
    issue_day = (
        issue.tz_localize(None).normalize() if issue.tz is None else issue.tz_convert(None).normalize()
    )

    w = weather.set_index(["iso", "date"])
    rows = []
    rng = slice(0, n_blocks - 1) if with_target else slice(0, n_blocks)
    b_idx = np.arange(n_blocks)[rng]
    moon = illumination(target_start[b_idx] + pd.Timedelta(hours=BLOCK_H / 2))
    for ri, iso in enumerate(regions):
        tday = day[b_idx].tz_convert(None)
        wk = pd.MultiIndex.from_arrays([[iso] * len(b_idx), tday])
        wx = w.reindex(wk)
        frame = pd.DataFrame(
            {
                "region": ri,
                "iso": iso,
                "issue": issue[b_idx],
                "block_of_day": ((b_idx + 1) % BLOCKS_PER_DAY),
                "dow": target_start[b_idx].dayofweek,
                "month_sin": np.sin(2 * np.pi * target_start[b_idx].month / 12),
                "month_cos": np.cos(2 * np.pi * target_start[b_idx].month / 12),
                "moon": moon,
                "m_1": minutes[ri, b_idx],
                "m_4": m4[ri, b_idx],
                "m_28": m28[ri, b_idx],
                "m_120": m120[ri, b_idx],
                "starts_4": s4[ri, b_idx],
                "active_at_issue": active_end[ri, b_idx].astype(np.int8),
                "since_last_h": since[ri, b_idx],
                "nb_m_1": nb_m1[ri, b_idx],
                "nb_m_4": nb_m4[ri, b_idx],
                "nat_m_1": nat_m1[b_idx],
                "nat_m_4": nat_m4[b_idx],
                "nat_regions_1": nat_regions_1[b_idx],
                "nat_regions_4": nat_regions_4[b_idx],
                "cloud": wx["cloud_cover_mean"].to_numpy(),
                "precip": wx["precipitation_sum"].to_numpy(),
                "wind": wx["wind_speed_10m_max"].to_numpy(),
                "viina_region_7d": v7[iso].reindex(issue_day[b_idx]).to_numpy(),
                "viina_national_7d": vn7.reindex(issue_day[b_idx]).to_numpy(),
                "viina_region_28d": v28[iso].reindex(issue_day[b_idx]).to_numpy(),
            }
        )
        if with_target:
            frame["y_active"] = (minutes[ri, b_idx + 1] > 0).astype(np.int8)
            frame["y_new"] = (starts[ri, b_idx + 1] > 0).astype(np.int8)
        rows.append(frame)
    return pd.concat(rows, ignore_index=True)
