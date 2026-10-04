"""Every data stream Terrestrial uses, with live health (M7 "Streams" panel).

One entry per stream that is actually implemented: who provides it, what it is, how often it
updates, whether it needs a key, its licence, and the hub source whose status reports its
health. Offline datasets report the age of their local copy. Counts shown in the UI are of
streams that are working right now, never of streams we merely intend to add.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from live.hub import Hub
from pipeline.config import HISTORY_DIR, REFERENCE_DIR


@dataclass(frozen=True)
class Stream:
    id: str
    name: str
    group: str  # air | sea | space | conflict | news | weather | reference | history | model
    provider: str
    what: str
    cadence: str
    licence: str
    source: str | None = None  # hub source name reporting live health
    key: str | None = None  # env var that must be set
    path: str | None = None  # local file/dir whose age is reported (offline datasets)
    max_age_h: float | None = None


STREAMS = (
    Stream("adsb-fi", "ADS-B regional + military feed", "air", "adsb.fi", "Aircraft positions over the Black Sea and the global military feed", "~1 s", "ODbL", "adsb.fi"),
    Stream("adsb-lol", "ADS-B military feed", "air", "adsb.lol", "Global military aircraft positions", "20 s", "ODbL", "adsb.lol"),
    Stream("gnss", "GNSS interference", "air", "Terrestrial (from ADS-B)", "Share of aircraft per cell reporting degraded GPS accuracy", "30 s", "derived", "gnss"),
    Stream("tracks", "Flight history and nets", "air", "Terrestrial (from ADS-B)", "48 h tracks per craft, shared-mission nets", "10 s", "derived", "nets"),
    Stream("ais", "AIS ship positions", "sea", "aisstream.io", "Naval and law-enforcement vessels (live AIS)", "real time", "aisstream terms", "aisstream", "AISSTREAM_API_KEY"),
    Stream("gfw", "Global Fishing Watch events", "sea", "Global Fishing Watch", "AIS gaps, encounters, loitering, port visits, unmatched SAR detections", "daily (5-day lag)", "CC BY-NC 4.0", None, "GFW_API_TOKEN"),
    Stream("celestrak", "Imaging satellites", "space", "CelesTrak", "Orbits of radar and optical imaging satellites, pass windows", "elements every 12 h, positions 10 s", "public", "celestrak"),
    Stream("firms", "Thermal anomalies", "space", "NASA FIRMS", "VIIRS / MODIS fire detections (strikes, fires)", "~3 h", "NASA open data", "firms", "FIRMS_MAP_KEY"),
    Stream("air-alerts", "Air-raid alerts", "conflict", "ubilling.net.ua (official alert map mirror)", "Live alert state of every Ukrainian region", "30 s", "public", "air-alerts"),
    Stream("telegram", "Air Force threat tracking", "conflict", "Air Force of Ukraine, war_monitor (Telegram)", "Drones, missiles and glide bombs in flight: region, position, heading; overnight tallies", "30 s", "public posts", "telegram"),
    Stream("deepstate", "Front line", "conflict", "DeepStateMap.Live", "Occupied territory, front line, attack directions, unit positions, Russian airfields", "30 min", "DeepState terms, attribution", "deepstate"),
    Stream("danger", "Danger forecast", "conflict", "Terrestrial model", "Probability of a new air-raid alert per region, next 6 h", "5 min", "derived", "danger-model"),
    Stream("gdelt", "GDELT events", "news", "GDELT 2.0", "Geolocated force/coercion events from world news", "15 min", "GDELT terms", "gdelt"),
    Stream("wires", "Ukrainian news wires", "news", "Kyiv Independent, Ukrainska Pravda, Ukrinform", "Headlines, placed when they name a town", "2 min", "publisher terms (headlines + links)", "news-wires"),
    Stream("weather", "Weather and sea state", "weather", "Open-Meteo", "Conditions and 48 h forecast at ports; winds aloft for flight ETAs", "15 min", "CC BY 4.0", "open-meteo"),
    Stream("wikidata", "Facilities", "reference", "Wikidata", "Air and naval bases, ports, refineries in the theatre", "cached", "CC0", "wikidata"),
    Stream("ourairports", "Airfields", "reference", "OurAirports", "Every airfield in the theatre; military flagged", "weekly", "public domain", None, None, f"{REFERENCE_DIR}/ourairports_airports.csv", 24 * 8),
    Stream("aircraft-db", "Military aircraft register", "reference", "ADS-B Exchange", "Hex → type, registration, operator", "weekly", "ADSBx free database", None, None, f"{REFERENCE_DIR}/basic-ac-db.json.gz", 24 * 8),
    Stream("geonames", "Gazetteer", "reference", "GeoNames", "Ukrainian/Russian/English place names for geocoding reports", "quarterly", "CC BY 4.0", None, None, f"{REFERENCE_DIR}/geonames", 24 * 100),
    Stream("sirens-history", "Air-raid alert history", "history", "Vadimkin/ukrainian-air-raid-sirens-dataset", "Every alert since Feb 2022 (training data)", "daily", "MIT", None, None, f"{HISTORY_DIR}/alerts.parquet", 48),
    Stream("viina", "VIINA conflict events", "history", "VIINA 2.0 (Zhukov)", "News-derived geolocated events since 2022", "weekly", "cite on use", None, None, f"{HISTORY_DIR}/viina_events.parquet", 24 * 10),
    Stream("adsb-archive", "Military flight archive", "history", "adsb.lol globe_history", "Daily traces of military aircraft (training data)", "daily", "ODbL", None, None, f"{HISTORY_DIR}/adsb", 24 * 3),
    Stream("flight-model", "Flight destination model", "model", "Terrestrial model", "Where each military aircraft will land, re-routed live", "10 s", "derived", "flight-model"),
    Stream("jev", "Jev decision model", "model", "TypeSafe AI", "Typed judgements: news → structured signals, net missions, alert triage", "on demand", "TypeSafe terms", None, "TYPESAFE_API_KEY"),
    Stream("featherless", "Analyst summaries", "model", "Featherless (open-weight LLM)", "SITREP every 10 minutes citing the facts it used", "10 min", "model licence", "sitrep", "FEATHERLESS_API_KEY"),
)  # fmt: skip


def _age_h(path: str) -> float | None:
    p = Path(path)
    if not p.exists():
        return None
    mtime = (
        max((f.stat().st_mtime for f in p.iterdir()), default=p.stat().st_mtime)
        if p.is_dir()
        else p.stat().st_mtime
    )
    return (time.time() - mtime) / 3600


def status(hub: Hub) -> list[dict]:
    out = []
    for s in STREAMS:
        row = asdict(s)
        state, detail = "unknown", ""
        if s.key and not os.environ.get(s.key):
            state, detail = "needs-key", f"set {s.key} in .env"
        elif s.source and s.source in hub.sources:
            src = hub.sources[s.source].as_dict()
            state, detail = src["state"], src.get("detail", "")
            row["last_ok"] = src.get("last_ok")
        elif s.path:
            age = _age_h(s.path)
            if age is None:
                state, detail = "missing", "not downloaded yet"
            else:
                state = "ok" if age <= (s.max_age_h or 1e9) else "stale"
                detail = f"local copy {age:.0f} h old" if age < 72 else f"local copy {age / 24:.0f} days old"
        elif s.source:
            state, detail = "starting", ""
        row["state"], row["detail"] = state, detail
        out.append(row)
    return out
