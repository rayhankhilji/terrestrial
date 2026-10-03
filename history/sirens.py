"""Air-raid alerts per region, 2022 → yesterday.

Source: Vadimkin/ukrainian-air-raid-sirens-dataset, `volunteer_data_en.csv` (region-level alerts
collected from the official alert channels since 25 Feb 2022, updated daily). Columns:
region, started_at, finished_at, naive. `naive` = the end time was not observed and was
estimated by the dataset; kept as a column so features can use or exclude it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from history.regions import BY_SIRENS, HISTORY_RAW
from pipeline.config import HISTORY_DIR
from pipeline.io import write_table
from reference.cache import cached

log = logging.getLogger("terrestrial")

URL = "https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/main/datasets/volunteer_data_en.csv"
COLUMNS = ["region", "started_at", "finished_at", "naive"]


def normalise(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    if list(raw.columns) != COLUMNS:
        raise ValueError(f"sirens dataset columns changed: {list(raw.columns)}")
    unknown = set(raw["region"]) - set(BY_SIRENS)
    if unknown:
        raise ValueError(f"unmapped sirens regions: {sorted(unknown)}")
    out = pd.DataFrame(
        {
            "iso": raw["region"].map(BY_SIRENS),
            "start": pd.to_datetime(raw["started_at"], utc=True).dt.as_unit("s"),
            "end": pd.to_datetime(raw["finished_at"], utc=True).dt.as_unit("s"),
            "naive": raw["naive"].astype(str).str.lower() == "true",
        }
    )
    out = out[out["end"] > out["start"]].sort_values(["iso", "start"]).reset_index(drop=True)
    out["minutes"] = (out["end"] - out["start"]).dt.total_seconds() / 60
    return out


def fetch(refresh: bool = False) -> pd.DataFrame:
    path = cached(
        URL, "volunteer_data_en.csv", refresh=refresh, max_age_days=0.5, base=HISTORY_RAW / "sirens"
    )
    alerts = normalise(path)
    write_table(alerts, "alerts", directory=HISTORY_DIR)
    log.info("  alerts: %d rows, %s → %s", len(alerts), alerts["start"].min(), alerts["start"].max())
    return alerts
