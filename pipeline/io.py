"""Disk I/O for pipeline stages: parquet tables, JSON blobs, the window manifest.

Every write logs its row count, and `required=True` writes raise on an empty table so a
stage can never hand an empty result to the next one silently (CLAUDE.md §5).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.config import PROCESSED_DIR, RAW_DIR

log = logging.getLogger("terrestrial")


class EmptyStageOutput(RuntimeError):
    pass


def table_path(name: str, directory: Path = PROCESSED_DIR) -> Path:
    return directory / f"{name}.parquet"


def write_table(
    df: pd.DataFrame, name: str, *, required: bool = True, directory: Path = PROCESSED_DIR
) -> Path:
    if required and df.empty:
        raise EmptyStageOutput(f"{name}: produced 0 rows where data was expected")
    path = table_path(name, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    log.info("  wrote %-22s %7d rows", name, len(df))
    return path


def read_table(name: str, directory: Path = PROCESSED_DIR) -> pd.DataFrame:
    path = table_path(name, directory)
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run the pipeline stage that produces it")
    return pd.read_parquet(path)


def write_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str))
    tmp.replace(path)
    return path


def read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing")
    return json.loads(path.read_text())


MANIFEST = RAW_DIR / "manifest.json"


def load_window() -> tuple[date, date] | None:
    """The (start, end) window the raw cache was fetched for, if any."""
    if not MANIFEST.exists():
        return None
    m = read_json(MANIFEST)
    return date.fromisoformat(m["start"]), date.fromisoformat(m["end"])


def save_window(start: date, end: date, bbox: tuple[float, ...]) -> None:
    write_json(MANIFEST, {"start": start.isoformat(), "end": end.isoformat(), "bbox": list(bbox)})
