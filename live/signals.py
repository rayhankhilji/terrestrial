"""News → structured signals with Jev (TypeSafe AI), M7 / CLAUDE.md §16.3.

Every new headline (news wires, GDELT) and every Telegram report our rules could not classify
is queued; with TYPESAFE_API_KEY set, Jev answers typed questions about it in one call:

- event: what kind of event the text reports (choice);
- target: what was targeted (choice);
- russian_strike: is this a Russian strike on Ukraine (yes/no probability);
- region: which Ukrainian region it concerns (choice over the 27 regions + none);
- severity: 1–5 (score).

Answers are attached to the entity as a `signal` annotation with every probability, the
confidence and the model, and logged with the request hash (live/ai.py) as provenance. Jev only
judges the text it is shown; nothing is computed by it. Without a key the queue is not drained
and the status pill says so.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from history.regions import BY_ISO
from live.ai import AIUnavailable, Jev, choice, jev, noul, score
from live.hub import Hub

log = logging.getLogger("terrestrial.live")

NAME = "signals"
QUEUE_MAX = 500
EVENTS = {
    "missile_strike": "Missile strike or missile attack",
    "drone_strike": "Attack drone (Shahed-type or jet drone) strike or attack",
    "glide_bomb": "Guided aerial / glide bomb (KAB) strike",
    "artillery": "Artillery, MLRS or shelling",
    "air_defence": "Air defence engagement or interception",
    "ground_combat": "Ground fighting, advance or withdrawal",
    "naval": "Naval or maritime incident",
    "ukrainian_strike": "Ukrainian strike on Russian or occupied territory",
    "political": "Political, diplomatic or economic news",
    "other": "None of the above",
}
TARGETS = {
    "energy": "Power grid, power plant, substation, gas or fuel infrastructure",
    "residential": "Homes, apartment buildings, civilians",
    "transport": "Rail, bridges, roads, ports, airports",
    "industry": "Factories, enterprises, warehouses",
    "military": "Military units, equipment or facilities",
    "public": "Hospitals, schools, administrative buildings",
    "none": "No target stated or not an attack",
}


def questions() -> dict[str, dict]:
    regions = {iso: r.name for iso, r in BY_ISO.items()} | {"none": "No Ukrainian region named or implied"}
    return {
        "event": choice("What kind of event does this report describe?", EVENTS),
        "target": choice("What was targeted or affected?", TARGETS),
        "russian_strike": noul(
            "Does this report describe a Russian strike or attack on Ukraine that already happened or is in progress?"
        ),
        "region": choice("Which Ukrainian region does this report concern most?", regions),
        "severity": score(
            "How severe is the reported event for civilians and infrastructure?",
            ["no harm", "minor", "moderate", "serious", "mass-casualty or strategic"],
        ),
    }


def state_of(e: dict) -> dict:
    p = e.get("props") or {}
    return {
        "text": p.get("text") or e.get("label"),
        "summary": p.get("summary"),
        "outlet": p.get("outlet") or p.get("channel_name") or e.get("src"),
        "place_named": p.get("place") or (p.get("at_place") or {}).get("name"),
    }


class Signals:
    def __init__(self, client: Jev = jev):
        self.jev = client
        self.queue: deque[str] = deque(maxlen=QUEUE_MAX)
        self.done: set[str] = set()

    def __call__(self, hub: Hub, e: dict) -> None:
        """Hub listener: queue new news and unclassified threat reports."""
        if e["id"] in self.done or e["id"] in self.queue:
            return
        if e["kind"] == "news" or (e["kind"] == "airthreat" and not e["props"].get("weapon")):
            self.queue.append(e["id"])

    async def drain(self, hub: Hub) -> int:
        n = 0
        while self.queue:
            eid = self.queue[0]  # removed only once answered, so a failed call is retried
            e = hub.entities.get(eid)
            if e is None:
                self.queue.popleft()
                continue
            answers = await self.jev.ask(state_of(e), questions())
            self.queue.popleft()
            hub.annotate(eid, "signal", {
                qid: {"value": a.value, "probabilities": a.probabilities, "confidence": a.confidence}
                for qid, a in answers.items()
            } | {"model": next(iter(answers.values())).model, "call": next(iter(answers.values())).call})  # fmt: skip
            self.done.add(eid)
            n += 1
        return n


async def run(hub: Hub, sig: Signals) -> None:
    hub.source(NAME)
    total = 0
    while True:
        if not sig.jev.available:
            hub.source_error(
                NAME, f"{len(sig.queue)} reports waiting; set TYPESAFE_API_KEY to extract signals with Jev"
            )
            await asyncio.sleep(60)
            continue
        try:
            total += await sig.drain(hub)
            hub.source_ok(NAME, f"{total} reports structured by Jev; {len(sig.queue)} queued")
        except AIUnavailable as exc:
            hub.source_error(NAME, f"{exc}; {len(sig.queue)} queued")
            await asyncio.sleep(30)
        await asyncio.sleep(5)
