"""Live AIS from aisstream.io (free key, WebSocket push, sub-second).

Each position report becomes a `vessel` entity; static reports (name, IMO, type, destination)
are merged into the same entity. Vessels are screened against OpenSanctions on arrival
(IMO first, then MMSI) so a listed hull lights up the moment it transmits in the box.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import websockets

from live import navclass
from live.hub import Hub, now_ms
from pipeline import opensanctions
from pipeline.config import BBOX, optional_key
from reference.mids import Mids
from reference.mids import mids as load_mids

log = logging.getLogger("terrestrial.live")

NAME = "aisstream"
URL = "wss://stream.aisstream.io/v0/stream"
MESSAGE_TYPES = [
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "ShipStaticData",
]

# ITU-R M.1371 ship-and-cargo type codes, coarse groups.
SHIP_TYPES = {
    range(30, 31): "fishing",
    range(60, 70): "passenger",
    range(70, 80): "cargo",
    range(80, 90): "tanker",
}


def ship_type(code: int | None) -> str | None:
    if code is None:
        return None
    for codes, label in SHIP_TYPES.items():
        if code in codes:
            return label
    return "other"


def parse_time(text: str | None) -> int:
    # e.g. "2026-10-03 06:12:01.123456789 +0000 UTC"
    if not text:
        return now_ms()
    stamp = text.replace(" UTC", "").strip()
    date_part, time_part, tz = stamp.split(" ")
    if "." in time_part:
        whole, frac = time_part.split(".")
        time_part = f"{whole}.{frac[:6]}"
    return int(datetime.fromisoformat(f"{date_part}T{time_part}{tz[:3]}:{tz[3:]}").timestamp() * 1000)


class SanctionsIndex:
    def __init__(self) -> None:
        self.by_imo: dict[str, dict] = {}
        self.by_mmsi: dict[str, dict] = {}
        try:
            df = opensanctions.normalise()
        except FileNotFoundError:
            log.warning(
                "OpenSanctions cache missing; run `python -m pipeline.run --only fetch` for live screening"
            )
            return
        for r in df.itertuples(index=False):
            entry = {
                "name": r.name,
                "url": r.url,
                "topics": list(r.topics),
                "datasets": list(r.datasets),
                "sanctioned": bool(r.sanctioned),
            }
            if r.imo:
                self.by_imo[r.imo] = entry
            for m in r.mmsis:
                self.by_mmsi[m] = entry

    def match(self, imo: str | None, mmsi: str) -> tuple[dict, str] | None:
        if imo and imo in self.by_imo:
            return self.by_imo[imo], "imo"
        if mmsi in self.by_mmsi:
            return self.by_mmsi[mmsi], "mmsi"
        return None


def handle(hub: Hub, message: dict, sanctions: SanctionsIndex, mids: Mids) -> None:
    kind = message.get("MessageType")
    meta = message.get("MetaData") or {}
    mmsi = str(meta.get("MMSI") or "")
    if not mmsi:
        return
    eid = f"vessel:{mmsi}"
    current = dict(hub.entities.get(eid) or {})
    props = dict(current.get("props") or {"mmsi": mmsi})
    body = (message.get("Message") or {}).get(kind) or {}

    if kind == "ShipStaticData":
        imo = str(body.get("ImoNumber") or "") or None
        dim = body.get("Dimension") or {}
        props.update(
            {
                "name": (body.get("Name") or "").strip() or props.get("name"),
                "imo": imo if imo and imo != "0" else props.get("imo"),
                "callsign": (body.get("CallSign") or "").strip() or None,
                "ship_type": ship_type(body.get("Type")),
                "type_code": body.get("Type"),
                "destination": (body.get("Destination") or "").strip() or None,
                "length_m": (dim.get("A") or 0) + (dim.get("B") or 0) or None,
            }
        )
        if "lon" not in current:  # no position yet: keep the static data for later
            hub.entities[eid] = {**current, "id": eid, "props": props}
            return
    else:
        lat, lon = body.get("Latitude"), body.get("Longitude")
        if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
            return
        heading = body.get("TrueHeading")
        current.update(
            {
                "lon": float(lon),
                "lat": float(lat),
                "hdg": heading if heading is not None and heading != 511 else body.get("Cog"),
                "spd": body.get("Sog"),
                "ts": parse_time(meta.get("time_utc")),
            }
        )
        props["nav_status"] = body.get("NavigationalStatus")

    props.setdefault("name", (meta.get("ShipName") or "").strip() or None)
    # Naval vessels belong to the military picture; merchant traffic feeds Maritime mode only.
    props.update(navclass.classify(props, mmsi, mids))
    hit = sanctions.match(props.get("imo"), mmsi)
    props["sanctions"] = {**hit[0], "matched_on": hit[1]} if hit else None
    hub.upsert(
        {
            **current,
            "id": eid,
            "kind": "vessel",
            "label": props.get("name") or f"MMSI {mmsi}",
            "src": NAME,
            "prov": "observed",
            "props": props,
            "ts": current.get("ts", now_ms()),
        }
    )


async def run(hub: Hub) -> None:
    key = optional_key("AISSTREAM_API_KEY")
    if not key:
        hub.source(NAME).state = "disabled"
        hub.source(NAME).detail = "set AISSTREAM_API_KEY in .env (free at aisstream.io)"
        return
    sanctions = SanctionsIndex()
    mids = await asyncio.to_thread(load_mids)
    min_lon, min_lat, max_lon, max_lat = BBOX
    subscription = {
        "APIKey": key,
        "BoundingBoxes": [[[min_lat, min_lon], [max_lat, max_lon]]],
        "FilterMessageTypes": MESSAGE_TYPES,
    }
    backoff = 2.0
    while True:
        try:
            async with websockets.connect(URL, open_timeout=15, ping_interval=20) as ws:
                await ws.send(json.dumps(subscription))
                hub.source_ok(NAME, "connected; streaming Black Sea AIS")
                backoff = 2.0
                async for raw in ws:
                    message = json.loads(raw)
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    handle(hub, message, sanctions, mids)
                    hub.source(NAME).last_ok = now_ms()
        except (OSError, websockets.WebSocketException, RuntimeError, ValueError) as exc:
            hub.source_error(NAME, f"{type(exc).__name__}: {exc}; reconnecting in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)
