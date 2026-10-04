"""Capture the running live server into a static recording the web app can replay by itself.

    uv run python -m live.capture --minutes 15 --out web/public/rec

Hosted builds (Vercel) have no Python server, so the app replays this recording in the
browser (VITE_RECORDED=1): the WebSocket stream exactly as the server sent it, plus the REST
answers the panels ask for. Nothing is synthesised: every byte is a real server response.
The browser shifts timestamps so the recording plays "now" and labels it as a recording with
its true capture time (CLAUDE.md §14, same contract as live/replay.py).

Outputs (under --out):
  meta.json           capture window, server, counts
  snapshot.json       the first WebSocket message (full picture)
  stream.json         [[ms since snapshot, message], ...] for the capture window
  rest/*.json         streams, headlines, airfields, regions, front, model cards
  track/<id>.json     48 h history of every craft seen (trimmed to replay time in the browser)
  predict/<id>.json   [[at, prediction], ...] sampled each minute for every aircraft
  passes.json         imaging-satellite passes on a 1° grid over the theatre
  places.json         gazetteer subset (towns ≥ 5,000 people and airfields) for search
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import websockets

log = logging.getLogger("terrestrial.capture")

REST = {
    "streams": "/live/streams",
    "headlines": "/live/headlines?limit=120",
    "airfields": "/live/airfields",
    "regions": "/live/regions",
    "front": "/live/front",
    "models_strike": "/live/models/strike",
    "models_flight": "/live/models/flight",
}
GRID = [(lon, lat) for lon in range(22, 43) for lat in range(43, 54)]
PREDICT_EVERY_S = 60


def _file(entity_id: str) -> str:
    """Static-file-safe name for an entity id ("aircraft:ae4e16" → "aircraft_ae4e16")."""
    return entity_id.replace(":", "_").replace("/", "_")


def _write(path: Path, data: object) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    path.write_text(raw, encoding="utf-8")
    return len(raw)


def _ids(msg: dict, into: set[str]) -> None:
    if msg.get("t") == "batch":
        for m in msg["m"]:
            _ids(m, into)
    elif msg.get("t") == "upsert" and msg["e"]["kind"] in ("aircraft", "vessel"):
        into.add(msg["e"]["id"])


async def _sample_predictions(http: httpx.AsyncClient, ids: set[str], out: dict[str, list]) -> None:
    for i in sorted(ids):
        if not i.startswith("aircraft:"):
            continue
        r = await http.get(f"/live/predict/{quote(i, safe='')}")
        if r.status_code == 200:
            out.setdefault(i, []).append([int(time.time() * 1000), r.json()])


async def capture(base: str, minutes: float, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url=base, timeout=60) as http:
        ws_url = base.replace("http", "ws", 1) + "/live/ws"
        stream: list[list] = []
        seen: set[str] = set()
        predictions: dict[str, list] = {}
        async with websockets.connect(ws_url, max_size=None) as ws:
            snapshot = json.loads(await ws.recv())
            if snapshot.get("t") != "snapshot":
                raise RuntimeError(f"expected a snapshot first, got {snapshot.get('t')}")
            t0 = int(time.time() * 1000)
            for e in snapshot["entities"]:
                if e["kind"] in ("aircraft", "vessel"):
                    seen.add(e["id"])
            log.info("snapshot: %d entities", len(snapshot["entities"]))
            end = time.monotonic() + minutes * 60
            next_sample = time.monotonic()
            while time.monotonic() < end:
                if time.monotonic() >= next_sample:
                    next_sample += PREDICT_EVERY_S
                    asyncio.create_task(_sample_predictions(http, set(seen), predictions))
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("t") in ("status", "sentinel_hits", "resync"):
                    # status is resent every few seconds; keep one a minute
                    if msg.get("t") != "status" or not stream or stream[-1][0] // 60000 != (int(time.time() * 1000) - t0) // 60000:
                        stream.append([int(time.time() * 1000) - t0, msg])
                    continue
                _ids(msg, seen)
                stream.append([int(time.time() * 1000) - t0, msg])
            t1 = int(time.time() * 1000)
        await asyncio.sleep(2)  # let the last prediction sample land

        sizes = {"snapshot": _write(out / "snapshot.json", snapshot), "stream": _write(out / "stream.json", stream)}
        for name, path in REST.items():
            r = await http.get(path)
            r.raise_for_status()
            sizes[name] = _write(out / "rest" / f"{name}.json", r.json())
        tracks = 0
        for i in sorted(seen):
            r = await http.get(f"/live/track/{quote(i, safe='')}", params={"hours": 48})
            if r.status_code == 200:
                _write(out / "track" / f"{_file(i)}.json", r.json())
                tracks += 1
        for i, rows in predictions.items():
            _write(out / "predict" / f"{_file(i)}.json", rows)
        passes = {}
        for lon, lat in GRID:
            r = await http.get("/live/passes", params={"lon": lon, "lat": lat, "hours": 24})
            r.raise_for_status()
            passes[f"{lon},{lat}"] = r.json()["passes"]
        sizes["passes"] = _write(out / "passes.json", passes)
        sizes["places"] = _write(out / "places.json", _places())
        meta = {
            "captured_from": t0,
            "captured_to": t1,
            "server": base,
            "messages": len(stream),
            "craft": len(seen),
            "tracks": tracks,
            "predicted": len(predictions),
            "grid_deg": 1,
        }
        _write(out / "meta.json", meta)
        log.info("captured %s; bytes %s", meta, sizes)


def _places() -> list[list]:
    """[name, lon, lat, country, population, feature, search keys] for the palette."""
    from reference.gazetteer import gazetteer, norm

    g = gazetteer()
    keys: dict[int, set[str]] = {}
    for name, idx in g.names.items():
        for j in idx:
            keys.setdefault(j, set()).add(name)
    rows = []
    for j, p in enumerate(g.places):
        if p.population < 5000 and p.feature != "AIRB":
            continue
        k = sorted(n for n in keys.get(j, ()) if len(n) <= 40)[:12]
        rows.append([p.name, round(p.lon, 4), round(p.lat, 4), p.country, p.population, p.feature, k or [norm(p.name)]])
    return rows


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://localhost:8001")
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--out", type=Path, default=Path("web/public/rec"))
    a = ap.parse_args()
    asyncio.run(capture(a.base, a.minutes, a.out))


if __name__ == "__main__":
    main()
