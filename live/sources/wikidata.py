"""Reference ontology from Wikidata: ports, refineries, air and naval bases in the theatre.

Wikidata gives every facility a stable identifier (Q-id) and links onward to other open
knowledge, so live observations (fires, news, aircraft, vessels) can be tied to named
infrastructure. Fetched once and cached under data/raw/wikidata/ (cache first).
"""

from __future__ import annotations

import logging

import httpx

from live.hub import Hub, now_ms
from pipeline.config import RAW_DIR, THEATRE_BBOX
from pipeline.io import read_json, write_json

log = logging.getLogger("terrestrial.live")

NAME = "wikidata"
CACHE = RAW_DIR / "wikidata" / "facilities.json"
ENDPOINT = "https://query.wikidata.org/sparql"
TYPES = {
    "Q44782": "port",
    "Q15310171": "port",
    "Q12353044": "refinery",
    "Q695850": "airbase",
    "Q1324633": "naval base",
}


def _query() -> str:
    min_lon, min_lat, max_lon, max_lat = THEATRE_BBOX
    values = " ".join(f"wd:{q}" for q in TYPES)
    return f"""
SELECT ?item ?itemLabel ?type ?coord (SAMPLE(?countryLabel) AS ?country) WHERE {{
  VALUES ?type {{ {values} }}
  ?item wdt:P31 ?type .
  SERVICE wikibase:box {{
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerSouthWest "Point({min_lon} {min_lat})"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerNorthEast "Point({max_lon} {max_lat})"^^geo:wktLiteral .
  }}
  OPTIONAL {{ ?item wdt:P17 ?c . ?c rdfs:label ?countryLabel . FILTER(LANG(?countryLabel) = "en") }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,uk,ru" . }}
}} GROUP BY ?item ?itemLabel ?type ?coord"""


def fetch(refresh: bool = False) -> list[dict]:
    if CACHE.exists() and not refresh:
        return read_json(CACHE)
    response = httpx.get(
        ENDPOINT,
        params={"query": _query()},
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "terrestrial/0.1 (https://github.com/rayhankhilji/terrestrial)",
        },
        timeout=120.0,
    )
    response.raise_for_status()
    facilities: dict[str, dict] = {}
    for b in response.json()["results"]["bindings"]:
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        lon_s, lat_s = b["coord"]["value"].removeprefix("Point(").removesuffix(")").split()
        label = b["itemLabel"]["value"]
        if label == qid:  # no label in en/uk/ru
            continue
        facilities.setdefault(
            qid,
            {
                "qid": qid,
                "name": label,
                "type": TYPES[b["type"]["value"].rsplit("/", 1)[-1]],
                "lon": float(lon_s),
                "lat": float(lat_s),
                "country": b.get("country", {}).get("value"),
            },
        )
    rows = sorted(facilities.values(), key=lambda f: (f["type"], f["name"]))
    if not rows:
        raise ValueError("Wikidata returned no facilities for the theatre")
    write_json(CACHE, rows)
    return rows


def to_entity(f: dict) -> dict:
    return {
        "id": f"facility:{f['qid']}",
        "kind": "facility",
        "label": f["name"],
        "lon": f["lon"],
        "lat": f["lat"],
        "ts": now_ms(),
        "src": NAME,
        "prov": "observed",
        "props": {
            "qid": f["qid"],
            "type": f["type"],
            "country": f["country"],
            "url": f"https://www.wikidata.org/wiki/{f['qid']}",
        },
    }


def load_into(hub: Hub) -> list[dict]:
    hub.source(NAME)
    try:
        facilities = fetch()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        hub.source_error(NAME, f"{type(exc).__name__}: {exc}")
        return []
    for f in facilities:
        hub.upsert(to_entity(f))
    hub.source_ok(NAME, f"{len(facilities)} facilities (cached reference ontology)")
    return facilities
