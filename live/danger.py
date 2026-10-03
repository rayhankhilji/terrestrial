"""Danger zones in the live picture: per-region air-raid alert state and model forecasts.

One `region` entity per Ukrainian region carries the live alert state (from the alert poller)
and the latest forecast of the danger models (predict/strike/serve.py), with its drivers and any
degradation flags. Forecasts are recomputed every RECOMPUTE_S in a worker thread; the history
tables they read are refreshed in the background when stale (sirens daily, VIINA every few days).
"""

from __future__ import annotations

import asyncio
import logging
import time

from shapely.geometry import mapping

from history import sirens, viina
from history.regions import BY_ISO, boundaries
from live.hub import Hub, now_ms
from live.sources.alerts import AlertLog
from predict.strike import serve

log = logging.getLogger("terrestrial.live")

NAME = "danger-model"
RECOMPUTE_S = 300
HISTORY_REFRESH_S = 6 * 3600


def regions_geojson() -> dict:
    geoms = boundaries()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": iso,
                "properties": {"iso": iso, "name": BY_ISO[iso].name},
                "geometry": mapping(g.geometry),
            }
            for iso, g in sorted(geoms.items())
        ],
    }


def model_cards() -> dict:
    return serve.cards()


class DangerZones:
    def __init__(self, alert_log: AlertLog):
        self.alert_log = alert_log
        self.forecast: dict | None = None
        self.service: serve.DangerService | None = None
        self.history_at = 0.0

    def _entity(self, iso: str) -> dict:
        g = boundaries()[iso]
        region = (self.forecast or {}).get("regions", {}).get(iso, {})
        active = self.alert_log.state.get(iso)
        since = self.alert_log.since.get(iso)
        f = self.forecast or {}
        return {
            "id": f"region:{iso}",
            "kind": "region",
            "label": BY_ISO[iso].name,
            "lon": g.lon,
            "lat": g.lat,
            "ts": now_ms(),
            "src": NAME,
            "prov": "inferred",
            "props": {
                "iso": iso,
                "alert_active": active,
                "alert_since": since,
                "modelled": bool(region),
                "p_new": region.get("p_new"),
                "p_active": region.get("p_active"),
                "drivers": region.get("drivers", []),
                "valid_from": f.get("valid_from"),
                "valid_to": f.get("valid_to"),
                "issued": f.get("issued"),
                "degraded": f.get("degraded", []),
                "models": f.get("models", {}),
            },
        }

    def publish(self, hub: Hub, isos=None) -> None:
        for iso in isos or sorted(BY_ISO):
            hub.upsert(self._entity(iso))

    def alert_changed(self, hub: Hub, isos: list[str]) -> None:
        self.publish(hub, isos)

    def _refresh_history(self) -> None:
        if time.time() - self.history_at < HISTORY_REFRESH_S:
            return
        sirens.fetch()
        viina.fetch()
        self.history_at = time.time()

    def _compute(self) -> dict:
        self._refresh_history()
        if self.service is None:
            self.service = serve.DangerService()
        if not self.service.available:
            raise RuntimeError("no trained danger model: run `uv run python -m predict.strike.train`")
        return self.service.compute()

    async def run(self, hub: Hub) -> None:
        hub.source(NAME)
        self.publish(hub)  # regions with live alert state even before the first forecast
        while True:
            started = time.monotonic()
            self.forecast = await asyncio.to_thread(self._compute)
            self.publish(hub)
            f = self.forecast
            detail = f"forecast {f['valid_from'][11:16]}–{f['valid_to'][11:16]} UTC in {time.monotonic() - started:.0f}s"
            if f["degraded"]:
                hub.source_error(NAME, f"{detail}; degraded: {f['degraded'][0]}")
            else:
                hub.source_ok(NAME, detail)
            await asyncio.sleep(RECOMPUTE_S)
