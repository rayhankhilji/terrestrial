"""Live military air picture from open ADS-B aggregators (no key): adsb.fi and adsb.lol.

Only military aircraft are kept (CLAUDE.md §16.1): every record is classified by
live/milclass.py at ingest and civil traffic is dropped there, so it never reaches the hub.

adsb.fi tolerates ~1 request/s, so it is polled round-robin every ADSB_FI_INTERVAL_S over two
250 nm circles covering the Black Sea plus its military feed. adsb.lol rate-limits harder and
only supplies its military feed every 20 s. Positions carry `seen_pos` (seconds since the
position was received), so `ts` is the true observation time and the hub reports real
source-to-screen latency; the browser dead-reckons aircraft between updates.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from live.hub import Hub
from live.milclass import Classification, MilClassifier
from pipeline.config import MIL_BBOX

log = logging.getLogger("terrestrial.live")

ADSB_FI = "https://opendata.adsb.fi/api/v2"
ADSB_LOL = "https://api.adsb.lol/v2"
USER_AGENT = "terrestrial/0.1 (+https://github.com/rayhankhilji/terrestrial)"
# Two 250 nm circles centred so that together they cover lon 27–42, lat 40.5–47.5.
POINTS = ((44.0, 30.6, 250), (44.0, 38.2, 250))
ADSB_FI_INTERVAL_S = 1.1
ADSB_LOL_INTERVAL_S = 20.0
MAX_SEEN_S = 60
FT_TO_M = 0.3048


def _in_area(lat: float, lon: float) -> bool:
    min_lon, min_lat, max_lon, max_lat = MIL_BBOX
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def to_entity(ac: dict, received_at: float, mil: Classification) -> dict | None:
    """Hub entity for a military aircraft; None for civil traffic or records without a position."""
    lat, lon = ac.get("lat"), ac.get("lon")
    if not mil.military or lat is None or lon is None or not ac.get("hex"):
        return None
    alt_baro = ac.get("alt_baro")
    on_ground = alt_baro == "ground"
    alt_ft = ac.get("alt_geom") if isinstance(ac.get("alt_geom"), (int, float)) else alt_baro
    alt_m = 0.0 if on_ground or not isinstance(alt_ft, (int, float)) else alt_ft * FT_TO_M
    type_code = (ac.get("t") or "").upper()
    callsign = (ac.get("flight") or "").strip()
    seen = float(ac.get("seen_pos") or 0.0)
    return {
        "id": f"aircraft:{ac['hex']}",
        "kind": "aircraft",
        "label": callsign or ac.get("r") or ac["hex"].upper(),
        "lon": float(lon),
        "lat": float(lat),
        "alt": round(alt_m, 1),
        "hdg": ac.get("track") if ac.get("track") is not None else ac.get("true_heading"),
        "spd": ac.get("gs"),
        "ts": int((received_at - seen) * 1000),
        "src": "ads-b",
        "prov": "observed",
        "props": {
            "icao24": ac["hex"],
            "callsign": callsign or None,
            "registration": ac.get("r"),
            "type": type_code or None,
            **mil.as_props(),
            "on_ground": on_ground,
            "squawk": ac.get("squawk"),
            "emergency": ac.get("emergency") if ac.get("emergency") not in (None, "none") else None,
            "vrate_fpm": ac.get("baro_rate") or ac.get("geom_rate"),
            # Position integrity (NIC) and accuracy (NACp): inputs to GPS-interference detection.
            "nic": ac.get("nic"),
            "nac_p": ac.get("nac_p"),
        },
    }


async def _poll(
    hub: Hub, http: httpx.AsyncClient, classify: MilClassifier, source: str, url: str, military: bool
) -> int:
    response = await http.get(url)
    response.raise_for_status()
    received_at = time.time()
    body = response.json()
    count = 0
    # readsb-style APIs differ only in the list key: "ac" (adsb.lol) or "aircraft" (adsb.fi).
    for ac in body.get("ac") or body.get("aircraft") or []:
        if float(ac.get("seen_pos") or 0) > MAX_SEEN_S:
            continue
        entity = to_entity(ac, received_at, classify(ac, military))
        if entity is None or not _in_area(entity["lat"], entity["lon"]):
            continue
        entity["src"] = source
        hub.upsert(entity)
        count += 1
    return count


async def _schedule(
    hub: Hub,
    http: httpx.AsyncClient,
    classify: MilClassifier,
    name: str,
    requests: list[tuple[str, bool]],
    interval_s: float,
) -> None:
    """Round-robin over `requests` (url, military-only feed), one request per interval."""
    hub.source(name)
    backoff = 0.0
    i = 0
    while True:
        started = time.monotonic()
        url, military = requests[i % len(requests)]
        try:
            n = await _poll(hub, http, classify, name, url, military)
            hub.source_ok(
                name, f"{n} military aircraft in last response ({'mil feed' if military else 'Black Sea'})"
            )
            backoff = 0.0
            i += 1
        except (httpx.HTTPError, ValueError) as exc:
            backoff = min(60.0, max(5.0, backoff * 2))
            hub.source_error(
                name, f"{type(exc).__name__}: {str(exc).splitlines()[0]}; retrying in {backoff:.0f}s"
            )
        await asyncio.sleep(max(0.0, interval_s - (time.monotonic() - started)) + backoff)


async def run(hub: Hub) -> None:
    hub.source("adsb.fi")
    hub.source("adsb.lol")
    classify = await asyncio.to_thread(MilClassifier.load)
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT}) as http:
        # The point queries return all traffic; they catch military airframes that the /mil
        # feeds miss (e.g. register-only matches). Civil records are dropped at ingest.
        fi = [(f"{ADSB_FI}/lat/{lat}/lon/{lon}/dist/{r}", False) for lat, lon, r in POINTS]
        fi.append((f"{ADSB_FI}/mil", True))
        await asyncio.gather(
            _schedule(hub, http, classify, "adsb.fi", fi, ADSB_FI_INTERVAL_S),
            _schedule(hub, http, classify, "adsb.lol", [(f"{ADSB_LOL}/mil", True)], ADSB_LOL_INTERVAL_S),
        )
