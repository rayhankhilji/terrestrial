"""Replay a live recording (data/live/YYYYMMDD.jsonl) through the hub, for offline demos.

Messages are re-emitted with their original spacing (divided by `speed`). Timestamps are
shifted so the recording plays "now"; each entity keeps its true observation time in
`orig_ts`, and /live/status reports mode=replay so the UI can say so prominently.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from live.hub import Hub, now_ms

log = logging.getLogger("terrestrial.live")
NAME = "replay"


async def run(hub: Hub, path: Path, speed: float = 1.0) -> None:
    if not path.exists():
        hub.source_error(NAME, f"recording {path} not found")
        return

    def messages():
        # Streamed line by line: recordings can be hundreds of MB.
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    first = next(messages(), None)
    if first is None:
        hub.source_error(NAME, f"recording {path} is empty")
        return
    while True:
        start_wall = now_ms()
        first_at = first["at"]
        offset = start_wall - first_at
        hub.source_ok(NAME, f"replaying {path.name} ×{speed:g}")
        for msg in messages():
            due = start_wall + (msg["at"] - first_at) / speed
            delay = (due - now_ms()) / 1000
            if delay > 0:
                await asyncio.sleep(delay)
            kind = msg["t"]
            if kind == "upsert":
                e = dict(msg["e"])
                e["orig_ts"] = e.get("ts")
                if e.get("ts"):
                    e["ts"] = int(e["ts"] + offset)
                hub.upsert(e)
            elif kind == "relate":
                hub.relate(dict(msg["r"]))
            elif kind == "alert":
                hub.alert({**msg["a"], "ts": now_ms()})
        hub.relations.clear()
