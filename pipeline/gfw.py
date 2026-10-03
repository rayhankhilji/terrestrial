"""Global Fishing Watch API v3 client (httpx). Request side only: every response body is
returned untouched so `fetch` can cache it verbatim and `normalise` parses from disk.

Contracts (from GFW's API docs and official client):
- Events:  POST events?limit&offset, JSON body {datasets, startDate, endDate (exclusive), geometry}
           → {entries, limit, offset, nextOffset, total}
- 4Wings:  POST 4wings/report?spatial-resolution&temporal-resolution&datasets[0]&filters[0]
           &date-range&format=JSON, JSON body {geojson}
           → {entries: [{"<dataset:version>": [rows]}]}
- Vessels: GET vessels?ids[0..n]&datasets[0]&registries-info-data&includes[0] → {entries}
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx

from pipeline.config import (
    BBOX,
    GFW_BASE_URL,
    GFW_IDENTITY_DATASET,
    GFW_PAGE_SIZE,
    GFW_SAR_DATASET,
    gfw_token,
)

log = logging.getLogger("terrestrial")

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 6


def bbox_polygon(bbox: tuple[float, float, float, float] = BBOX) -> dict:
    min_lon, min_lat, max_lon, max_lat = bbox
    ring = [
        [min_lon, min_lat],
        [max_lon, min_lat],
        [max_lon, max_lat],
        [min_lon, max_lat],
        [min_lon, min_lat],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


class GFWError(RuntimeError):
    pass


class GFWClient:
    def __init__(self, token: str | None = None, timeout: float = 120.0):
        self._http = httpx.Client(
            base_url=GFW_BASE_URL,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "Authorization": f"Bearer {token or gfw_token()}",
                "User-Agent": "terrestrial/0.1 (+https://github.com/rayhankhilji/terrestrial)",
            },
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise GFWError(f"{method} {path}: network error {exc}") from exc
                wait = 2**attempt
            else:
                if response.status_code < 400:
                    body = response.json()
                    if not isinstance(body, dict):
                        raise GFWError(f"{method} {path}: expected a JSON object, got {type(body)}")
                    return body
                if response.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    raise GFWError(
                        f"{method} {path} → HTTP {response.status_code}: {response.text[:500]}"
                    )
                wait = float(response.headers.get("Retry-After") or 2**attempt)
            log.warning("  GFW %s %s: retry %d in %.0fs", method, path, attempt, wait)
            time.sleep(wait)
        raise AssertionError("unreachable")

    def event_pages(self, dataset: str, start: date, end: date) -> Iterator[dict]:
        """Yield raw response pages for one events dataset over [start, end)."""
        body = {
            "datasets": [dataset],
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "geometry": bbox_polygon(),
        }
        offset = 0
        while True:
            page = self._request(
                "POST", "events", params={"limit": GFW_PAGE_SIZE, "offset": offset}, json=body
            )
            if "entries" not in page:
                raise GFWError(f"events {dataset}: response has no 'entries': {list(page)}")
            yield page
            next_offset = page.get("nextOffset")
            if not page["entries"] or next_offset in (None, offset):
                return
            offset = int(next_offset)

    def sar_report(self, start: date, end: date) -> dict:
        """Unmatched SAR detections (no AIS match) over [start, end), hourly, ~0.01° cells."""
        params = {
            "spatial-resolution": "HIGH",
            "temporal-resolution": "HOURLY",
            "datasets[0]": GFW_SAR_DATASET,
            "filters[0]": "matched='false'",
            "date-range": f"{start.isoformat()},{end.isoformat()}",
            "format": "JSON",
        }
        return self._request("POST", "4wings/report", params=params, json={"geojson": bbox_polygon()})

    def vessels(self, ids: list[str]) -> dict:
        """Identity records (self-reported history + registry) for a batch of vessel ids."""
        params: dict[str, str] = {
            "datasets[0]": GFW_IDENTITY_DATASET,
            "registries-info-data": "ALL",
            "includes[0]": "POTENTIAL_RELATED_SELF_REPORTED_INFO",
        }
        for i, vessel_id in enumerate(ids):
            params[f"ids[{i}]"] = vessel_id
        return self._request("GET", "vessels", params=params)
