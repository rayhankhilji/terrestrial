"""Download-once file cache for reference datasets."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from pipeline.config import REFERENCE_DIR, REFERENCE_MAX_AGE_DAYS

log = logging.getLogger("terrestrial")

USER_AGENT = "terrestrial/0.2 (+https://github.com/rayhankhilji/terrestrial)"


def cached(
    url: str,
    name: str,
    refresh: bool = False,
    max_age_days: float = REFERENCE_MAX_AGE_DAYS,
    base: Path = REFERENCE_DIR,
) -> Path:
    """Path of a local copy of `url`, downloading it if missing, stale or `refresh` is set.

    A failed refresh of a file we already have keeps the old copy and logs a warning; a failed
    first download raises (there is nothing to fall back to).
    """
    path = base / name
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_days * 86400
    if fresh and not refresh:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        with httpx.stream(
            "GET", url, headers={"User-Agent": USER_AGENT}, timeout=120.0, follow_redirects=True
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_bytes(1 << 16):
                    f.write(chunk)
        if tmp.stat().st_size == 0:
            raise ValueError(f"empty download from {url}")
        tmp.replace(path)
        log.info("reference: downloaded %s (%.1f MB)", name, path.stat().st_size / 1e6)
    except (httpx.HTTPError, ValueError) as exc:
        tmp.unlink(missing_ok=True)
        if not path.exists():
            raise RuntimeError(f"could not download reference file {name} from {url}: {exc}") from exc
        log.warning("reference: refresh of %s failed (%s); keeping the cached copy", name, exc)
    return path
