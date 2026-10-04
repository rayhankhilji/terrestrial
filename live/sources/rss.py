"""Ukrainian news wires (RSS, no key): The Kyiv Independent, Ukrainska Pravda (English) and
Ukrinform. Every headline becomes a `news` report; headlines that name a Ukrainian or Russian
place (gazetteer, English names, towns of ≥ MIN_POP) are placed there, others are listed
without a position rather than guessed. The article link is always kept."""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import time
from email.utils import parsedate_to_datetime

import httpx

from live.hub import Hub, now_ms
from reference.gazetteer import Gazetteer, gazetteer

log = logging.getLogger("terrestrial.live")

NAME = "news-wires"
FEEDS = {
    "Kyiv Independent": "https://kyivindependent.com/news-archive/rss/",
    "Ukrainska Pravda": "https://www.pravda.com.ua/eng/rss/",
    "Ukrinform": "https://www.ukrinform.net/rss/block-lastnews",
}
POLL_S = 120
MAX_AGE_MS = 24 * 3600 * 1000
MIN_POP = 5_000
ITEM = re.compile(r"<item>(.*?)</item>", re.S)
FIELD = {k: re.compile(rf"<{k}[^>]*>(.*?)</{k}>", re.S) for k in ("title", "link", "description", "pubDate")}
CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
PROPER = re.compile(r"\b([A-Z][a-z’'\-]+(?:[ \-][A-Z][a-z’'\-]+)?)")
NOT_PLACES = {
    "Russia",
    "Ukraine",
    "Russian",
    "Ukrainian",
    "Kremlin",
    "President",
    "General",
    "Defense",
    "Air",
    "Force",
}
STRIKE_WORDS = re.compile(
    r"\b(attack|strike|struck|drone|missile|shell|explosion|bomb|hit|killed|injured|downed|KAB|Shahed)\w*",
    re.I,
)


def _text(raw: str) -> str:
    raw = CDATA.sub(r"\1", raw)
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def parse_feed(xml: str, source: str) -> list[dict]:
    out = []
    for item in ITEM.findall(xml):
        f = {k: (m.group(1) if (m := rx.search(item)) else "") for k, rx in FIELD.items()}
        if not f["title"] or not f["pubDate"]:
            continue
        out.append(
            {
                "source": source,
                "title": _text(f["title"]),
                "url": _text(f["link"]),
                "summary": _text(f["description"])[:400],
                "at": int(parsedate_to_datetime(_text(f["pubDate"])).timestamp() * 1000),
            }
        )
    return out


def place_of(item: dict, g: Gazetteer) -> dict | None:
    """The first proper noun in the headline that is a sizeable Ukrainian/Russian place."""
    for m in PROPER.finditer(item["title"]):
        name = m.group(1)
        if name in NOT_PLACES:
            continue
        for candidate in (name, name.split()[0]):
            if p := g.lookup(candidate, min_population=MIN_POP):
                return {
                    "name": p.name,
                    "lon": p.lon,
                    "lat": p.lat,
                    "country": p.country,
                    "as_written": candidate,
                }
    return None


def entity(item: dict, g: Gazetteer) -> dict | None:
    place = place_of(item, g)
    if place is None:
        return None
    key = hashlib.sha1(item["url"].encode()).hexdigest()[:12]
    return {
        "id": f"news:wire:{key}",
        "kind": "news",
        "label": item["title"],
        "lon": place["lon"],
        "lat": place["lat"],
        "ts": item["at"],
        "src": NAME,
        "prov": "observed",
        "props": {
            "place": place["name"],
            "country": place["country"],
            "url": item["url"],
            "outlet": item["source"],
            "summary": item["summary"],
            "strike_related": bool(STRIKE_WORDS.search(item["title"])),
            "placed_by": f"headline names {place['as_written']}",
        },
    }


class Wires:
    def __init__(self):
        self.g: Gazetteer | None = None
        self.headlines: list[dict] = []  # every recent item, placed or not, newest first

    def ingest(self, xml: str, source: str) -> list[dict]:
        items = parse_feed(xml, source)
        known = {h["url"] for h in self.headlines}
        self.headlines = sorted(
            [*self.headlines, *[i for i in items if i["url"] not in known]], key=lambda h: -h["at"]
        )[:300]
        return [e for i in items if now_ms() - i["at"] <= MAX_AGE_MS and (e := entity(i, self.g))]


async def run(hub: Hub, wires: Wires) -> None:
    hub.source(NAME)
    wires.g = await asyncio.to_thread(gazetteer)
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": "Mozilla/5.0 (terrestrial OSINT)"}
    ) as http:
        while True:
            started, placed, errors = time.monotonic(), 0, []
            for source, url in FEEDS.items():
                try:
                    r = await http.get(url, follow_redirects=True)
                    r.raise_for_status()
                    for e in wires.ingest(r.text, source):
                        hub.upsert(e)
                        placed += 1
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(f"{source}: {type(exc).__name__}")
            if errors:
                hub.source_error(NAME, "; ".join(errors))
            else:
                hub.source_ok(NAME, f"{len(wires.headlines)} headlines, {placed} placed on the map")
            await asyncio.sleep(max(5.0, POLL_S - (time.monotonic() - started)))
