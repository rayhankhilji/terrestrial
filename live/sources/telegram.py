"""Live air-threat reports from public Telegram channels (no key, no account).

Telegram serves a public web preview of open channels at https://t.me/s/<channel>. We poll:

- `kpszsu`: the Air Force of the Armed Forces of Ukraine. Minute-level threat tracking
  ("🏍 Реактивний БпЛА на Дніпропетровщині, курс Кременчук" = a jet-powered drone over
  Dnipropetrovsk region heading for Kremenchuk) and the morning tally of the night's attack
  (how many drones/missiles, from which launch directions, how many were downed).
- `war_monitor`: a long-running volunteer monitoring channel with finer positions
  ("🅿️1х реактив повз Ніжин" = one jet drone passing Nizhyn).

Each post is parsed by transparent rules (no AI) into an `airthreat` entity: weapon class,
count, region, the place it was reported at or passing, and the place it is heading for, all
geocoded with the GeoNames gazetteer. The original Ukrainian text and a link to the post are
always kept: the structured fields are our reading of it, the post is the source. Parsing
misses (posts we cannot place) are kept as text-only reports, never guessed onto the map.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from history.regions import BY_ISO, boundaries
from live.hub import Hub, now_ms
from reference.gazetteer import Gazetteer, gazetteer

log = logging.getLogger("terrestrial.live")

NAME = "telegram"
CHANNELS = {"kpszsu": "Air Force of Ukraine", "war_monitor": "monitor (volunteer)"}
POLL_S = 30
TTL_MS = 45 * 60 * 1000  # a threat track is stale after 45 minutes
MIN_POP = 300  # villages smaller than this are too ambiguous to place from a bare name
POST = re.compile(r'(?=<div class="tgme_widget_message_wrap)')
POST_ID = re.compile(r'data-post="([^"]+)"')
POST_TIME = re.compile(r'<time datetime="([^"]+)"')
POST_TEXT = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)

WEAPONS = (  # (class, English label, patterns) — first match wins, most specific first
    ("ballistic", "ballistic missile", ("балісти",)),
    ("cruise_missile", "cruise missile", ("крилат", "мгкр", "калібр", "кр ")),
    ("missile", "missile", ("ракет", "🚀")),
    ("glide_bomb", "guided glide bombs (KAB)", ("каб", "💣")),
    ("jet_uav", "jet-powered attack drone", ("реактив", "🏍")),
    ("uav", "attack drone (Shahed-type)", ("бпла", "шахед", "🛵", "🅿")),
)

# Region stems (any case ending) → ISO 3166-2 code.
REGION_STEMS = {
    "вінниччин": "UA-05", "волин": "UA-07", "луганщин": "UA-09", "дніпропетровщин": "UA-12",
    "дніпровщин": "UA-12", "донеччин": "UA-14", "житомирщин": "UA-18", "закарпатт": "UA-21",
    "запоріжж": "UA-23", "прикарпатт": "UA-26", "івано-франківщин": "UA-26", "київщин": "UA-32",
    "кіровоградщин": "UA-35", "крим": "UA-43", "львівщин": "UA-46", "миколаївщин": "UA-48",
    "одещин": "UA-51", "полтавщин": "UA-53", "рівненщин": "UA-56", "сумщин": "UA-59",
    "тернопільщин": "UA-61", "харківщин": "UA-63", "херсонщин": "UA-65", "хмельниччин": "UA-68",
    "черкащин": "UA-71", "чернігівщин": "UA-74", "буковин": "UA-77", "чернівеччин": "UA-77",
}  # fmt: skip
REGION = re.compile(r"\b(" + "|".join(sorted(REGION_STEMS, key=len, reverse=True)) + r")\w*", re.I)
CITY_HEADER = re.compile(r"^\W*(київ|харків|одеса|дніпро|запоріжжя|суми|миколаїв|херсон|чернігів)\s*:", re.I)
COUNT = re.compile(r"(\d+)\s*[хx]\b", re.I)
PLACE = r"(?:н\.\s?п\.\s?|м\.\s?|смт\.?\s?|с\.\s?)?([А-ЯІЇЄҐ][\w'’ʼ\-]+(?:[ \-][А-ЯІЇЄҐ][\w'’ʼ\-]+)?)"
TARGET = re.compile(r"(?:курсом|курс|вектор|в напрямку|у напрямку|напрямок|на)\s+(?:на\s+)?" + PLACE)
PASSING = re.compile(r"(?:повз|біля|в районі|над|від)\s+" + PLACE)
DIRECTIONS = re.compile(r"із напрямків?:\s*(.+?)(?:\.|\n|$)", re.S)
TALLY = re.compile(r"збито/подавлено\s+(\d+)", re.I)
ATTACKED = re.compile(r"атакував\s+(\d+)\s+(?:ударними\s+)?(?:бпла|ракет)", re.I)
NOT_PLACES = {"Київ", "Київщині", "Київщина"}  # handled as region/city context, not as targets
ADJECTIVE = re.compile(r"(ського|ської|ський|ська|цького|цької|ному|ної)$")
# War Monitor's terse "1х реактив Буча" / "1х Троєщина": the place right after the count.
BARE = re.compile(r"\d+\s*[хx]\s*(?:реактив\w*\s+|мгкр\s+|бпла\s+|шахед\w*\s+)?" + PLACE, re.I)
HITS = re.compile(r"влучання[^.]*?на\s+(\d+)\s+локац", re.I)
DEBRIS = re.compile(r"падіння збитих[^.]*?на\s+(\d+)\s+локац", re.I)
LAUNCH_NOISE = re.compile(r"\b(ТОТ|АР|рф|РФ)\b")


@dataclass
class Post:
    channel: str
    id: str  # "kpszsu/82678"
    at: int  # ms
    text: str


@dataclass
class Threat:
    post: Post
    weapon: str | None = None
    weapon_label: str | None = None
    count: int | None = None
    region: str | None = None  # ISO
    at_place: dict | None = None  # {name, lon, lat}
    to_place: dict | None = None
    tally: dict | None = None
    summary: str = ""
    notes: list[str] = field(default_factory=list)


def parse_page(page: str, channel: str) -> list[Post]:
    out = []
    for chunk in POST.split(page)[1:]:
        pid, t, m = POST_ID.search(chunk), POST_TIME.search(chunk), POST_TEXT.search(chunk)
        if not (pid and t and m):
            continue
        text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", m.group(1)))).strip()
        out.append(
            Post(channel, pid.group(1), int(datetime.fromisoformat(t.group(1)).timestamp() * 1000), text)
        )
    return out


def _place(g: Gazetteer, name: str) -> dict | None:
    if name in NOT_PLACES:
        return None
    p = g.lookup(name, min_population=MIN_POP, countries=("UA",))
    return {"name": p.name, "lon": p.lon, "lat": p.lat, "as_written": name} if p else None


def parse_post(post: Post, g: Gazetteer) -> Threat:
    t = Threat(post)
    text = post.text
    low = text.lower()
    for cls, label, pats in WEAPONS:
        if any(p in low for p in pats):
            t.weapon, t.weapon_label = cls, label
            break
    if m := COUNT.search(text):
        t.count = int(m.group(1))
    if m := REGION.search(text):
        t.region = REGION_STEMS[m.group(1).lower()]
    elif m := CITY_HEADER.search(text):
        city = _place(g, m.group(1).capitalize()) if m.group(1).lower() != "київ" else None
        t.region = "UA-30" if m.group(1).lower() == "київ" else None
        t.at_place = city
    if m := TALLY.search(text):
        attacked = ATTACKED.search(text)
        dirs = DIRECTIONS.search(text)
        launch = []
        for name in re.split(r"[,;]|\s+та\s+|\s+і\s+", dirs.group(1)) if dirs else []:
            # "ТОТ АР Крим – Гвардійське": the named airfield after the dash is the launch site.
            name = LAUNCH_NOISE.sub(" ", name.split("–")[-1].split(" - ")[0]).strip(' –-«»"“”')
            if name and (p := g.lookup(name, min_population=1_000)):
                launch.append(
                    {"name": p.name, "lon": p.lon, "lat": p.lat, "country": p.country, "as_written": name}
                )
        t.tally = {
            "downed": int(m.group(1)),
            "attacked": int(attacked.group(1)) if attacked else None,
            "launch_areas": launch,
            "hit_locations": int(hits.group(1)) if (hits := HITS.search(text)) else None,
            "debris_locations": int(debris.group(1)) if (debris := DEBRIS.search(text)) else None,
        }
        t.region = None
        t.summary = f"Overnight attack: {t.tally['attacked'] or '?'} launched, {t.tally['downed']} downed or suppressed (Air Force tally)"
        return t
    if m := PASSING.search(text):
        t.at_place = _place(g, m.group(1)) or t.at_place
    if t.at_place is None and (m := BARE.search(text)):
        t.at_place = _place(g, m.group(1))
    for m in TARGET.finditer(text):
        name = m.group(1)
        if REGION.match(name) or name in NOT_PLACES or ADJECTIVE.search(name.lower()):
            continue
        if p := _place(g, name):
            t.to_place = p
            break
    if t.weapon is None and t.count:
        t.weapon, t.weapon_label = "unknown", "aerial target"  # monitors count targets before typing them
    if t.weapon is None and t.tally is None:
        t.notes.append("no weapon class recognised")
    where = BY_ISO[t.region].name if t.region in BY_ISO else None
    parts = [f"{t.count}× " if t.count else "", t.weapon_label or "report"]
    if t.at_place:
        parts.append(f" near {t.at_place['name']}")
    elif where:
        parts.append(f" over {where}")
    if t.to_place:
        parts.append(f", heading for {t.to_place['name']}")
    t.summary = "".join(parts)
    return t


def entity(t: Threat, region_points: dict[str, tuple[float, float]]) -> dict | None:
    """Hub entity for a parsed report, positioned at the reported place, else the region; None
    if it cannot be placed at all (it stays in the report list, not on the map)."""
    if t.tally:
        anchor = (31.2, 49.0)  # national report: shown at the centre of Ukraine
    elif t.at_place:
        anchor = (t.at_place["lon"], t.at_place["lat"])
    elif t.region in region_points:
        anchor = region_points[t.region]
    elif t.to_place:
        anchor = (t.to_place["lon"], t.to_place["lat"])
    else:
        return None
    channel, num = t.post.id.split("/", 1)
    return {
        "id": f"airthreat:{t.post.id}",
        "kind": "airthreat",
        "label": t.summary,
        "lon": anchor[0],
        "lat": anchor[1],
        "ts": t.post.at,
        "src": NAME,
        "prov": "observed",
        "props": {
            "weapon": t.weapon,
            "weapon_label": t.weapon_label,
            "count": t.count,
            "region": t.region,
            "region_name": BY_ISO[t.region].name if t.region in BY_ISO else None,
            "at_place": t.at_place,
            "to_place": t.to_place,
            "tally": t.tally,
            "text": t.post.text,
            "channel": channel,
            "channel_name": CHANNELS.get(channel, channel),
            "url": f"https://t.me/{channel}/{num}",
            "placed_by": "reported place" if t.at_place else "region centre" if t.region else "target",
            "notes": t.notes,
        },
    }


class Channels:
    def __init__(self):
        self.seen: dict[str, int] = {}
        self.g: Gazetteer | None = None
        self.region_points: dict[str, tuple[float, float]] = {}

    def setup(self) -> None:
        self.g = gazetteer()
        self.region_points = {iso: (r.lon, r.lat) for iso, r in boundaries().items()}

    def ingest(self, page: str, channel: str) -> list[dict]:
        out = []
        for post in parse_page(page, channel):
            if post.id in self.seen:
                continue
            self.seen[post.id] = post.at
            if e := entity(parse_post(post, self.g), self.region_points):
                out.append(e)
        return out


async def run(hub: Hub, ch: Channels | None = None) -> None:
    ch = ch or Channels()
    hub.source(NAME)
    await asyncio.to_thread(ch.setup)
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": "Mozilla/5.0 (terrestrial OSINT)"}
    ) as http:
        while True:
            started = time.monotonic()
            placed = 0
            for channel in CHANNELS:
                try:
                    r = await http.get(f"https://t.me/s/{channel}", follow_redirects=True)
                    r.raise_for_status()
                    ents = ch.ingest(r.text, channel)
                    for e in ents:
                        if now_ms() - e["ts"] <= TTL_MS or e["props"]["tally"]:
                            hub.upsert(e)
                            placed += 1
                except httpx.HTTPError as exc:
                    hub.source_error(NAME, f"{channel}: {type(exc).__name__}: {exc}")
                await asyncio.sleep(1)
            stale = [
                e["id"]
                for e in hub.of_kind("airthreat")
                if now_ms() - e["ts"] > TTL_MS and not e["props"]["tally"]
            ]
            hub.remove(stale)
            hub.source_ok(
                NAME, f"{len(hub.of_kind('airthreat'))} live threat reports from {len(CHANNELS)} channels"
            )
            await asyncio.sleep(max(1.0, POLL_S - (time.monotonic() - started)))
