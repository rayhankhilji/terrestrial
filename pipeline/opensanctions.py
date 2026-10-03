"""OpenSanctions maritime collection: download and normalise (CLAUDE.md §4, §15.2)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx
import pandas as pd

from pipeline.config import (
    DETENTION_TOPIC,
    OPENSANCTIONS_INDEX_URL,
    OPENSANCTIONS_MARITIME_URL,
    RAW_DIR,
    SANCTION_TOPICS,
)
from pipeline.io import write_json

log = logging.getLogger("terrestrial")

RAW = RAW_DIR / "opensanctions"
CSV_PATH = RAW / "maritime.csv"
INDEX_PATH = RAW / "index.json"
EXPECTED_COLUMNS = {
    "type",
    "caption",
    "imo",
    "risk",
    "countries",
    "flag",
    "mmsi",
    "id",
    "url",
    "datasets",
    "aliases",
}
IMO_RE = re.compile(r"^IMO(\d{7})$")


def download(refresh: bool = False) -> Path:
    if CSV_PATH.exists() and not refresh:
        log.info("  opensanctions: cached %s", CSV_PATH.name)
        return CSV_PATH
    RAW.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as http:
        index = http.get(OPENSANCTIONS_INDEX_URL)
        index.raise_for_status()
        write_json(INDEX_PATH, index.json())
        response = http.get(OPENSANCTIONS_MARITIME_URL)
        response.raise_for_status()
    CSV_PATH.write_bytes(response.content)
    log.info("  opensanctions: downloaded %s (%.1f MB)", CSV_PATH.name, len(response.content) / 1e6)
    return CSV_PATH


def _split(value: str) -> list[str]:
    return [v for v in (value or "").split(";") if v]


def normalise(csv_path: Path = CSV_PATH) -> pd.DataFrame:
    """One row per listed vessel with parsed IMO/MMSI, topics and derived flags."""
    raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    missing = EXPECTED_COLUMNS - set(raw.columns)
    if missing:
        raise ValueError(f"OpenSanctions maritime.csv schema changed; missing columns {sorted(missing)}")

    vessels = raw[raw["type"] == "VESSEL"]
    rows = []
    for r in vessels.itertuples(index=False):
        imo_match = IMO_RE.match(r.imo) if r.imo else None
        if r.imo and not imo_match:
            raise ValueError(f"unexpected IMO format {r.imo!r} for {r.id}")
        topics = _split(r.risk)
        rows.append(
            {
                "os_id": r.id,
                "name": r.caption,
                "imo": imo_match.group(1) if imo_match else None,
                "mmsis": _split(r.mmsi),
                "flag": r.flag or None,
                "countries": _split(r.countries),
                "topics": topics,
                "datasets": _split(r.datasets),
                "aliases": _split(r.aliases),
                "url": r.url,
                "sanctioned": any(t in SANCTION_TOPICS for t in topics),
                "shadow_fleet": "mare.shadow" in topics,
                "detained": DETENTION_TOPIC in topics,
            }
        )
    df = pd.DataFrame(rows)
    log.info(
        "  opensanctions: %d vessels (%d sanctions-relevant, %d shadow fleet, %d detained)",
        len(df),
        df["sanctioned"].sum(),
        df["shadow_fleet"].sum(),
        df["detained"].sum(),
    )
    return df
