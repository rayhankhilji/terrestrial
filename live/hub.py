"""The live hub: an in-memory ontology of entities and relations, fanned out to subscribers.

Sources call `upsert` / `relate` / `alert`; every WebSocket client owns a queue that the hub
appends to, and the server flushes each queue in small batches (~100 ms) so updates reach
the browser well under a second after the source produced them.

Entity shape (plain dicts, JSON-ready):
  id      "aircraft:4b1805", "vessel:273...", "fire:...", "news:...", "facility:Q123"
  kind    aircraft | vessel | fire | news | facility | station
  label   human-readable name
  lon, lat, alt (metres, optional), hdg (deg), spd (knots)
  ts      source observation time, epoch ms
  rx      hub receive time, epoch ms
  src     source name (adsb.lol, aisstream, firms, gdelt, open-meteo, wikidata)
  prov    observed | inferred
  props   kind-specific attributes
Relations: {id, rel, a, b, prov, src, why, ts} — `why` lists the facts an inferred link rests on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("terrestrial.live")

# Seconds after which an entity of this kind is dropped if not refreshed.
TTL_S = {
    "aircraft": 90,
    "vessel": 30 * 60,
    "fire": 36 * 3600,
    "news": 24 * 3600,
    "station": 3 * 3600,
    "facility": None,
}
QUEUE_LIMIT = 20_000


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class SourceStatus:
    name: str
    state: str = "starting"  # starting | ok | error | disabled
    detail: str = ""
    last_ok: int | None = None
    messages: int = 0
    rate: deque = field(default_factory=lambda: deque(maxlen=600))  # rx timestamps
    lag_ms: deque = field(default_factory=lambda: deque(maxlen=500))  # rx - ts samples

    def as_dict(self) -> dict[str, Any]:
        cutoff = now_ms() - 60_000
        per_min = sum(1 for t in self.rate if t >= cutoff)
        lags = sorted(self.lag_ms)
        return {
            "name": self.name,
            "state": self.state,
            "detail": self.detail,
            "last_ok": self.last_ok,
            "messages": self.messages,
            "per_min": per_min,
            "lag_p50_ms": lags[len(lags) // 2] if lags else None,
        }


class Hub:
    def __init__(self, record_dir: Path | None = None):
        self.entities: dict[str, dict] = {}
        self.kinds: dict[str, set[str]] = {}
        self.relations: dict[str, dict] = {}
        self.alerts: deque[dict] = deque(maxlen=500)
        self.sources: dict[str, SourceStatus] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._record_dir = record_dir
        self._record_file = None
        self._record_day = None
        self.listeners: list = []  # callables(entity) — rule engines, correlators

    # --- sources -------------------------------------------------------------------------

    def source(self, name: str) -> SourceStatus:
        if name not in self.sources:
            self.sources[name] = SourceStatus(name)
        return self.sources[name]

    def source_ok(self, name: str, detail: str = "") -> None:
        s = self.source(name)
        s.state, s.detail, s.last_ok = "ok", detail, now_ms()

    def source_error(self, name: str, detail: str) -> None:
        s = self.source(name)
        s.state, s.detail = "error", detail[:300]
        log.warning("live source %s: %s", name, detail[:300])

    # --- writes --------------------------------------------------------------------------

    def upsert(self, entity: dict) -> None:
        entity["rx"] = now_ms()
        previous = self.entities.get(entity["id"])
        if previous is not None and previous.get("ts", 0) > entity.get("ts", 0):
            return  # out-of-order sample
        if previous is not None and _same_state(previous, entity):
            previous["rx"] = entity["rx"]
            return
        self.entities[entity["id"]] = entity
        self.kinds.setdefault(entity["kind"], set()).add(entity["id"])
        status = self.source(entity["src"])
        status.messages += 1
        status.rate.append(entity["rx"])
        if entity.get("ts"):
            status.lag_ms.append(max(0, entity["rx"] - entity["ts"]))
        self._publish({"t": "upsert", "e": entity})
        for listener in self.listeners:
            listener(self, entity)

    def relate(self, relation: dict) -> None:
        relation.setdefault("ts", now_ms())
        if relation["id"] in self.relations:
            return
        self.relations[relation["id"]] = relation
        self._publish({"t": "relate", "r": relation})

    def alert(self, alert: dict) -> None:
        alert.setdefault("ts", now_ms())
        self.alerts.appendleft(alert)
        self._publish({"t": "alert", "a": alert})

    def expire(self) -> None:
        now = now_ms()
        dead = [
            eid
            for eid, e in self.entities.items()
            if (ttl := TTL_S.get(e["kind"])) is not None and now - e["rx"] > ttl * 1000
        ]
        for eid in dead:
            self.kinds.get(self.entities[eid]["kind"], set()).discard(eid)
            del self.entities[eid]
        if dead:
            dead_set = set(dead)
            for rid in [
                r for r, rel in self.relations.items() if rel["a"] in dead_set or rel["b"] in dead_set
            ]:
                del self.relations[rid]
            self._publish({"t": "remove", "ids": dead})

    # --- fan-out -------------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def snapshot(self) -> dict:
        return {
            "t": "snapshot",
            "entities": list(self.entities.values()),
            "relations": list(self.relations.values()),
            "alerts": list(self.alerts)[:100],
            "status": self.status(),
            "server_time": now_ms(),
        }

    def status(self) -> list[dict]:
        return [s.as_dict() for s in self.sources.values()]

    def of_kind(self, kind: str) -> list[dict]:
        return [self.entities[i] for i in self.kinds.get(kind, ())]

    def publish(self, message: dict) -> None:
        self._publish(message)

    def _publish(self, message: dict) -> None:
        for q in list(self._subscribers):
            if q.full():
                # A stalled client must not grow memory without bound: drop its backlog
                # and tell it to resync from a fresh snapshot.
                while not q.empty():
                    q.get_nowait()
                q.put_nowait({"t": "resync"})
                continue
            q.put_nowait(message)
        self._record(message)

    def _record(self, message: dict) -> None:
        if self._record_dir is None or message["t"] in ("remove", "sentinel_hits"):
            return
        day = time.strftime("%Y%m%d", time.gmtime())
        if day != self._record_day:
            if self._record_file:
                self._record_file.close()
            self._record_dir.mkdir(parents=True, exist_ok=True)
            self._record_file = open(self._record_dir / f"{day}.jsonl", "a", encoding="utf-8")  # noqa: SIM115
            self._record_day = day
        self._record_file.write(json.dumps({"at": now_ms(), **message}, separators=(",", ":")) + "\n")
        self._record_file.flush()


def _same_state(a: dict, b: dict) -> bool:
    keys = ("lon", "lat", "alt", "hdg", "spd", "label", "props")
    return all(a.get(k) == b.get(k) for k in keys)
