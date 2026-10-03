"""Violent events per region and day from VIINA 2.0 (Zhukov & Ayers, Harvard/Georgetown).

`event_1pd_latest_<year>.zip`: news reports geocoded to populated places, classified by a BERT
model into actor and tactic, de-duplicated to one event per place, day and type. Binary `_b`
columns use thresholds chosen by the authors to maximise out-of-sample F1. Updated every few
days; the files live in Git LFS and are fetched through media.githubusercontent.com.

We keep date, region, place, coordinates, actor flags and the air-war tactics. Cite: Zhukov &
Ayers (2023), VIINA 2.0.

Label completeness differs by year (measured on the 2026-09-29 release), so features may only
use labels that are complete over the training period:
- complete every year: a_rus (Russian actor), t_airstrike, t_aad (air defence), t_artillery;
- t_uav: missing for 45–73 % of rows until Oct 2024;
- a_rus_init / a_ukr_init (initiator): missing before 2025.
Missing labels are kept as missing (`<NA>`), never silently turned into 0, and `air_attack`
is defined from complete labels only. The corpus also shrinks from ~172k events (2022) to ~45k
(2025) as sources change, so counts are compared within a period, not across years.
"""

from __future__ import annotations

import logging
import zipfile
from datetime import date

import pandas as pd

from history.regions import BY_VIINA, HISTORY_RAW
from pipeline.config import HISTORY_DIR
from pipeline.io import write_table
from reference.cache import cached

log = logging.getLogger("terrestrial")

URL = "https://media.githubusercontent.com/media/zhukovyuri/VIINA/main/Data/event_1pd_latest_{year}.zip"
FIRST_YEAR = 2022
FLAGS = {
    "a_rus_b": "rus_actor",
    "a_rus_init_b": "rus_initiated",
    "a_ukr_init_b": "ukr_initiated",
    "t_airstrike_b": "airstrike",
    "t_uav_b": "uav",
    "t_aad_b": "air_defence",
    "t_artillery_b": "artillery",
    "t_civcas_b": "civ_casualties",
    "t_property_b": "property_damage",
}
KEEP = [
    "event_id_1pd",
    "date",
    "ADM1_NAME",
    "asciiname",
    "longitude",
    "latitude",
    "GEO_PRECISION",
    "n_reports",
    *FLAGS,
]


def normalise_csv(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in KEEP if c not in frame.columns]
    if missing:
        raise ValueError(f"VIINA columns missing: {missing}")
    df = frame[KEEP].copy()
    df = df[df["ADM1_NAME"].notna() & (df["ADM1_NAME"] != "")]
    unknown = set(df["ADM1_NAME"]) - set(BY_VIINA)
    if unknown:
        raise ValueError(f"unmapped VIINA regions: {sorted(unknown)}")
    out = pd.DataFrame(
        {
            "event_id": df["event_id_1pd"].astype(str),
            "date": pd.to_datetime(df["date"].astype(str), format="%Y%m%d"),
            "iso": df["ADM1_NAME"].map(BY_VIINA),
            "place": df["asciiname"],
            "lon": df["longitude"].astype(float),
            "lat": df["latitude"].astype(float),
            "precision": df["GEO_PRECISION"],
            "reports": df["n_reports"].astype(int),
        }
    )
    for src, dst in FLAGS.items():
        out[dst] = pd.array(pd.to_numeric(df[src], errors="coerce").to_numpy(), dtype="boolean")
    # Air attack: an air strike or an air-defence engagement (incoming missiles/drones) reported
    # in the region. Both labels are complete in every year, so the series is comparable.
    out["air_attack"] = (out["airstrike"].fillna(False) | out["air_defence"].fillna(False)).astype(bool)
    return out


def fetch(refresh: bool = False, today: date | None = None) -> pd.DataFrame:
    frames = []
    last_year = (today or date.today()).year
    for year in range(FIRST_YEAR, last_year + 1):
        # Past years are frozen; the current year is refreshed every day.
        age = 1.0 if year == last_year else 365.0
        path = cached(
            URL.format(year=year),
            f"event_1pd_latest_{year}.zip",
            refresh=refresh and year == last_year,
            max_age_days=age,
            base=HISTORY_RAW / "viina",
        )
        with zipfile.ZipFile(path) as z:
            (name,) = [n for n in z.namelist() if n.endswith(".csv")]
            with z.open(name) as f:
                frames.append(normalise_csv(pd.read_csv(f, low_memory=False)))
    events = pd.concat(frames, ignore_index=True).sort_values(["date", "iso"]).reset_index(drop=True)
    write_table(events, "viina_events", directory=HISTORY_DIR)
    log.info(
        "  VIINA: %d events (%d air attacks), %s → %s",
        len(events), int(events["air_attack"].sum()), events["date"].min().date(), events["date"].max().date(),
    )  # fmt: skip
    return events
