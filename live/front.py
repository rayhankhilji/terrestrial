"""The front line in the live picture (DeepStateMap.Live, refreshed every REFRESH_S).

One small `front` entity carries the snapshot time, DeepState's latest update text and counts;
the geometry (occupied territory, grey zone, front line, attack directions, estimated Russian
unit positions, airfields Russia operates from) is served as GeoJSON by /live/front, because
it is too large to push through the entity stream. The last good snapshot is kept on disk so
a restart without network still shows the front, labelled with its age.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math

import httpx
from shapely.ops import unary_union

from history import deepstate
from history.regions import boundaries
from live.hub import Hub, now_ms
from pipeline.config import LIVE_DIR
from reference.cache import USER_AGENT
from reference.land import land

log = logging.getLogger("terrestrial.live")

NAME = "deepstate"
REFRESH_S = 30 * 60
CACHE = LIVE_DIR / "deepstate_last.json"


class FrontLine:
    def __init__(self):
        self.front: deepstate.Front | None = None
        self.geojson: dict | None = None
        self.update_text: str | None = None
        self.update_at: str | None = None
        self.ukraine = None
        self.land = None

    def load(self, payload: dict) -> None:
        if self.ukraine is None:
            self.ukraine = unary_union([g.geometry for g in boundaries().values()])
            self.land = land()
        self.front = deepstate.parse(payload, self.ukraine, self.land)
        self.geojson = self.front.geojson()

    def entity(self) -> dict:
        f = self.front
        c = f.occupied.centroid
        km2 = f.occupied.area * 111.32**2 * math.cos(math.radians(c.y))
        return {
            "id": "front:deepstate",
            "kind": "front",
            "label": "Front line (DeepStateMap)",
            "lon": c.x,
            "lat": c.y,
            "ts": now_ms(),
            "src": NAME,
            "prov": "observed",
            "props": {
                "snapshot": f.snapshot_id,
                "datetime": f.datetime,
                "occupied_km2": round(km2, -2),
                "front_km": round(f.front.length * 111.32 * math.cos(math.radians(c.y))),
                "attack_directions": len(f.attack_directions),
                "units": len(f.units),
                "airfields": len(f.airfields),
                "update": self.update_text,
                "update_at": self.update_at,
            },
        }

    def refresh(self) -> None:
        with httpx.Client(timeout=60.0, headers={"User-Agent": USER_AGENT}) as http:
            payload = deepstate.fetch_last(http)
            index = deepstate.fetch_history_index(http)
        self.load(payload)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        latest = max(index, key=lambda e: e.get("createdAt") or "") if index else {}
        self.update_text = deepstate.describe_update(latest) or None
        self.update_at = latest.get("createdAt")

    def load_cached(self) -> bool:
        if not CACHE.exists():
            return False
        self.load(json.loads(CACHE.read_text(encoding="utf-8")))
        return True


async def run(hub: Hub, line: FrontLine) -> None:
    hub.source(NAME)
    if await asyncio.to_thread(line.load_cached):
        hub.upsert(line.entity())
    while True:
        try:
            await asyncio.to_thread(line.refresh)
            hub.upsert(line.entity())
            p = line.entity()["props"]
            hub.source_ok(
                NAME,
                f"snapshot {p['datetime']}: {p['occupied_km2']:,.0f} km² occupied, front ~{p['front_km']} km",
            )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            hub.source_error(
                NAME, f"{type(exc).__name__}: {exc}; keeping {'cached snapshot' if line.front else 'nothing'}"
            )
        await asyncio.sleep(REFRESH_S)
