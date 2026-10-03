"""Stage 1 — fetch: pull every source for the window into data/raw/ (cache first).

Layout (one directory per window so re-running with another window never mixes data):
  data/raw/gfw/<start>_<end>/events/<kind>/page_00000.json …   + _complete.json marker
  data/raw/gfw/<start>_<end>/sar/<chunk_start>_<chunk_end>.json
  data/raw/gfw/<start>_<end>/vessels/batch_0000.json …        + requested.json
  data/raw/opensanctions/maritime.csv, index.json
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from pipeline import opensanctions
from pipeline.config import BBOX, GFW_DATASETS, GFW_VESSEL_BATCH, RAW_DIR
from pipeline.gfw import GFWClient
from pipeline.io import read_json, save_window, write_json

log = logging.getLogger("terrestrial")

SAR_CHUNK_DAYS = 15


def window_dir(start: date, end: date) -> Path:
    return RAW_DIR / "gfw" / f"{start.isoformat()}_{end.isoformat()}"


def event_pages(root: Path, kind: str) -> list[dict]:
    folder = root / "events" / kind
    if not (folder / "_complete.json").exists():
        raise FileNotFoundError(f"{folder} is incomplete; run the fetch stage")
    return [read_json(p) for p in sorted(folder.glob("page_*.json"))]


def _fetch_events(
    client: GFWClient, root: Path, kind: str, dataset: str, start: date, end: date, refresh: bool
) -> None:
    folder = root / "events" / kind
    if (folder / "_complete.json").exists() and not refresh:
        log.info("  events %-12s cached", kind)
        return
    for old in folder.glob("page_*.json"):
        old.unlink()
    entries = 0
    total = None
    pages = 0
    for i, page in enumerate(client.event_pages(dataset, start, end)):
        write_json(folder / f"page_{i:05d}.json", page)
        entries += len(page["entries"])
        total = page.get("total", total)
        pages += 1
    write_json(
        folder / "_complete.json", {"dataset": dataset, "pages": pages, "entries": entries, "total": total}
    )
    log.info("  events %-12s %6d entries in %d pages (API total %s)", kind, entries, pages, total)


def _fetch_sar(client: GFWClient, root: Path, start: date, end: date, refresh: bool) -> None:
    folder = root / "sar"
    chunk = start
    while chunk < end:
        chunk_end = min(chunk + timedelta(days=SAR_CHUNK_DAYS), end)
        path = folder / f"{chunk.isoformat()}_{chunk_end.isoformat()}.json"
        if path.exists() and not refresh:
            log.info("  sar %s cached", path.stem)
        else:
            body = client.sar_report(chunk, chunk_end)
            if "entries" not in body:
                raise ValueError(f"4Wings report without 'entries': {list(body)}")
            write_json(path, body)
            rows = sum(len(v) for e in body["entries"] for v in e.values() if isinstance(v, list))
            log.info("  sar %s: %d rows", path.stem, rows)
        chunk = chunk_end


def vessel_ids_from_events(root: Path) -> set[str]:
    """Every vessel id referenced by any cached event (main vessel and encounter counterpart)."""
    ids: set[str] = set()
    for kind in GFW_DATASETS:
        for page in event_pages(root, kind):
            for event in page["entries"]:
                vessel = event.get("vessel") or {}
                if vessel.get("id"):
                    ids.add(vessel["id"])
                other = (event.get("encounter") or {}).get("vessel") or {}
                if other.get("id"):
                    ids.add(other["id"])
    return ids


def _fetch_vessels(client: GFWClient, root: Path, refresh: bool) -> None:
    folder = root / "vessels"
    requested_path = folder / "requested.json"
    requested: set[str] = set() if refresh or not requested_path.exists() else set(read_json(requested_path))
    if refresh:
        for old in folder.glob("batch_*.json"):
            old.unlink()
    wanted = sorted(vessel_ids_from_events(root) - requested)
    if not wanted:
        log.info("  vessels: %d identities cached", len(requested))
        return
    existing = len(list(folder.glob("batch_*.json")))
    for n, i in enumerate(range(0, len(wanted), GFW_VESSEL_BATCH)):
        batch = wanted[i : i + GFW_VESSEL_BATCH]
        body = client.vessels(batch)
        write_json(folder / f"batch_{existing + n:04d}.json", body)
        requested.update(batch)
        write_json(requested_path, sorted(requested))
    log.info("  vessels: fetched %d new identities (%d total)", len(wanted), len(requested))


def run(start: date, end: date, refresh: bool = False) -> Path:
    root = window_dir(start, end)
    opensanctions.download(refresh=refresh)
    client = GFWClient()
    try:
        for kind, dataset in GFW_DATASETS.items():
            _fetch_events(client, root, kind, dataset, start, end, refresh)
        _fetch_sar(client, root, start, end, refresh)
        _fetch_vessels(client, root, refresh)
    finally:
        client.close()
    save_window(start, end, BBOX)
    return root


def stage(start: date, end: date, args) -> None:
    run(start, end, refresh=args.refresh)
