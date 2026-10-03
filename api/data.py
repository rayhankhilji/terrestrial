"""Read-side access to data/processed: tables cached in memory, reloaded when files change."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException

from pipeline.config import PROCESSED_DIR, RAW_DIR


class Processed:
    def __init__(self, root: Path = PROCESSED_DIR):
        self.root = root
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _load(self, name: str, loader) -> Any:
        path = self.root / name
        if not path.exists():
            raise HTTPException(
                503,
                f"{name} is missing from {self.root}. Run the pipeline first: "
                "`uv run python -m pipeline.run` (needs GFW_API_TOKEN in .env).",
            )
        mtime = path.stat().st_mtime
        with self._lock:
            cached = self._cache.get(name)
            if cached and cached[0] == mtime:
                return cached[1]
            value = loader(path)
            self._cache[name] = (mtime, value)
            return value

    def table(self, name: str) -> pd.DataFrame:
        return self._load(f"{name}.parquet", pd.read_parquet)

    def json(self, name: str) -> Any:
        return self._load(name, lambda p: json.loads(p.read_text()))

    def optional_json(self, name: str, default: Any) -> Any:
        return self.json(name) if (self.root / name).exists() else default

    def has(self, name: str) -> bool:
        return (self.root / name).exists()


def window() -> dict | None:
    path = RAW_DIR / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else None


def records(df: pd.DataFrame) -> list[dict]:
    """JSON-safe records: timestamps as ISO strings, NaN/NaT as null, numpy arrays as lists."""
    out = []
    for row in df.to_dict(orient="records"):
        clean = {}
        for k, v in row.items():
            if isinstance(v, pd.Timestamp):
                clean[k] = v.isoformat()
            elif hasattr(v, "tolist") and not isinstance(v, str):
                clean[k] = v.tolist()
            elif v is pd.NaT or (isinstance(v, float) and v != v):
                clean[k] = None
            else:
                clean[k] = v
        out.append(clean)
    return out
