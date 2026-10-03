"""Live service: `uv run uvicorn live.server:app --port 8001` (the web app proxies /live).

WebSocket /live/ws sends one `snapshot`, then `batch` messages flushed every FLUSH_MS with
every upsert / relation / alert since the last flush, plus a `status` heartbeat. Sources run
as background tasks on their own schedules; nothing is fetched on request.

Set LIVE_REPLAY=data/live/YYYYMMDD.jsonl to replay a recording instead of polling sources
(for demos without network); LIVE_REPLAY_SPEED speeds it up.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from live import danger, nets, replay
from live.correlate import Correlator
from live.hub import Hub, now_ms
from live.sentinels import SentinelEngine
from live.sources import adsb, ais, firms, gdelt, weather, wikidata
from live.sources import alerts as air_alerts
from live.tracks import AirfieldIndex, TrackStore
from pipeline.config import LIVE_DIR
from reference.airfields import airfields

log = logging.getLogger("terrestrial.live")

FLUSH_MS = 50
STATUS_EVERY_S = 2.0
MAX_BATCH = 2000

REPLAY = os.environ.get("LIVE_REPLAY", "").strip()

hub = Hub(record_dir=None if REPLAY else LIVE_DIR)
sentinels = SentinelEngine()
tracks = TrackStore()
net_engine = nets.NetsEngine(tracks)
alert_log = air_alerts.AlertLog()
danger_zones = danger.DangerZones(alert_log)


async def supervise(name: str, run) -> None:
    """Run a source forever: a crash is logged with its traceback, shown on the source's
    status pill, and the source restarts. Nothing fails silently."""
    while True:
        try:
            await run(hub)
            return  # the source finished on purpose (e.g. disabled: no key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("live source %s crashed", name)
            hub.source_error(name, f"crashed: {type(exc).__name__}: {exc}; restarting in 10s")
            await asyncio.sleep(10)


async def _expire_loop() -> None:
    while True:
        await asyncio.sleep(5)
        hub.expire()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    facilities = await asyncio.to_thread(wikidata.load_into, hub)
    tracks.airfields = AirfieldIndex(await asyncio.to_thread(airfields))
    if not REPLAY:
        # Before any source starts, so the rebuild never races live appends.
        await asyncio.to_thread(tracks.rebuild, LIVE_DIR)
    hub.listeners.append(tracks)
    hub.listeners.append(Correlator(facilities))
    hub.listeners.append(sentinels)
    if REPLAY:
        speed = float(os.environ.get("LIVE_REPLAY_SPEED", "1"))
        tasks = [asyncio.create_task(supervise("replay", lambda h: replay.run(h, Path(REPLAY), speed)))]
    else:
        tasks = [
            asyncio.create_task(supervise(src.__name__.rsplit(".", 1)[-1], src.run))
            for src in (adsb, weather, gdelt, ais, firms)
        ]
    tasks.append(asyncio.create_task(_expire_loop()))
    tasks.append(asyncio.create_task(nets.run(hub, net_engine)))
    if not REPLAY:
        tasks.append(
            asyncio.create_task(
                supervise(
                    air_alerts.NAME,
                    lambda h: air_alerts.run(h, alert_log, lambda isos: danger_zones.alert_changed(h, isos)),
                )
            )
        )
        tasks.append(asyncio.create_task(supervise("danger-model", lambda h: danger_zones.run(h))))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Terrestrial live", lifespan=lifespan)


@app.get("/live/status")
def status() -> dict:
    return {
        "mode": "replay" if REPLAY else "live",
        "sources": hub.status(),
        "entities": len(hub.entities),
        "relations": len(hub.relations),
        "server_time": now_ms(),
    }


@app.get("/live/airfields")
def list_airfields(military_only: bool = False) -> list[dict]:
    """Airfields in the military area (OurAirports): static reference, served from cache."""
    return [asdict(a) for a in airfields() if a.military or not military_only]


@app.get("/live/track/{entity_id:path}")
def track(entity_id: str, hours: float = 48) -> dict:
    """Full observed history of a military aircraft or naval vessel, split into flights."""
    try:
        return tracks.track(entity_id, hours=max(0.1, min(hours, 48)))
    except KeyError as exc:
        raise HTTPException(404, f"no track history for {entity_id}") from exc


@app.get("/live/regions")
def regions_geojson() -> dict:
    """Region boundaries (geoBoundaries ADM1) for the danger-zone choropleth: static reference."""
    return danger.regions_geojson()


@app.get("/live/models/strike")
def strike_model_cards() -> dict:
    """Model cards of the danger models, verbatim (training data, split, scores vs baselines)."""
    return danger.model_cards()


@app.get("/live/snapshot")
def snapshot() -> dict:
    return hub.snapshot()


@app.get("/live/sentinels")
def list_sentinels() -> list[dict]:
    return sentinels.list()


@app.put("/live/sentinels/{sentinel_id}")
def put_sentinel(sentinel_id: str, sentinel: dict = Body(...)) -> dict:
    if sentinel.get("id") != sentinel_id:
        raise HTTPException(400, "sentinel id in body must match the URL")
    try:
        saved = sentinels.put(sentinel)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    hub.publish({"t": "sentinels", "sentinels": sentinels.list()})
    return saved


@app.delete("/live/sentinels/{sentinel_id}")
def delete_sentinel(sentinel_id: str) -> dict:
    try:
        sentinels.delete(sentinel_id)
    except KeyError as exc:
        raise HTTPException(404, f"no sentinel {sentinel_id}") from exc
    hub.publish({"t": "sentinels", "sentinels": sentinels.list()})
    return {"deleted": sentinel_id}


@app.websocket("/live/ws")
async def stream(ws: WebSocket) -> None:
    await ws.accept()
    queue = hub.subscribe()
    try:
        await ws.send_json(
            {**hub.snapshot(), "mode": "replay" if REPLAY else "live", "sentinels": sentinels.list()}
        )
        last_status = 0.0
        loop = asyncio.get_running_loop()
        while True:
            batch: list[dict] = []
            with suppress(TimeoutError):
                batch.append(await asyncio.wait_for(queue.get(), timeout=STATUS_EVERY_S))
            deadline = loop.time() + FLUSH_MS / 1000
            while len(batch) < MAX_BATCH and (remaining := deadline - loop.time()) > 0:
                try:
                    batch.append(await asyncio.wait_for(queue.get(), timeout=remaining))
                except TimeoutError:
                    break
            if any(m["t"] == "resync" for m in batch):
                await ws.send_json(
                    {**hub.snapshot(), "mode": "replay" if REPLAY else "live", "sentinels": sentinels.list()}
                )
                continue
            if batch:
                await ws.send_json({"t": "batch", "m": batch, "srv": now_ms()})
            if loop.time() - last_status >= STATUS_EVERY_S:
                await ws.send_json(
                    {"t": "status", "sources": hub.status(), "srv": now_ms(), "entities": len(hub.entities)}
                )
                last_status = loop.time()
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(queue)
