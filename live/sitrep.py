"""Situation report every EVERY_S (M7): the picture in numbered facts, plus AI prose when keyed.

`facts` is deterministic: it reads the hub (air-threat reports, alerts, danger forecast, front
line, craft priorities, nets, GNSS interference, headlines, re-routes) and states each finding
as one sentence with the entity ids behind it. It always works, without any key.

With FEATHERLESS_API_KEY set, an open-weight LLM turns the facts into a short SITREP that must
cite them ([F3]); prose citing unknown facts, or none, is rejected and the previous accepted
SITREP stays (pipeline/featherless.py). The UI labels the prose as AI-generated with its model.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter

from live.hub import Hub, now_ms
from pipeline import featherless

log = logging.getLogger("terrestrial.live")

NAME = "sitrep"
EVERY_S = 600
WINDOW_MS = 60 * 60 * 1000
TASK = (
    "Write a SITREP of at most 6 short sentences for the next hours, most urgent first: the air "
    "threat picture over Ukraine, where risk is elevated, notable military air activity, and anything "
    "that changed. Plain text, no headings, no bullet points."
)


def facts(hub: Hub, now: int | None = None) -> list[dict]:
    now = now or now_ms()
    out: list[dict] = []

    def add(text: str, refs: list[str] | None = None, kind: str = "info") -> None:
        out.append({"id": f"F{len(out) + 1}", "text": text, "refs": refs or [], "kind": kind})

    threats = [e for e in hub.of_kind("airthreat") if not e["props"].get("tally")]
    recent = [e for e in threats if now - e["ts"] <= WINDOW_MS]
    if recent:
        by_weapon = Counter(e["props"]["weapon_label"] or "unclassified target" for e in recent)
        regions = Counter(e["props"]["region_name"] for e in recent if e["props"]["region_name"])
        what = ", ".join(f"{n} × {w}" for w, n in by_weapon.most_common())
        where = ", ".join(r for r, _ in regions.most_common(5))
        add(
            f"In the last hour {len(recent)} air-threat reports were posted ({what}), mostly over {where or 'unplaced areas'}.",
            [e["id"] for e in recent][:20],
            "threat",
        )
        heading = Counter(e["props"]["to_place"]["name"] for e in recent if e["props"].get("to_place"))
        if heading:
            add(
                "Reported headings: "
                + ", ".join(f"{n} towards {p}" for p, n in heading.most_common(5))
                + ".",
                [e["id"] for e in recent if e["props"].get("to_place")][:20],
                "threat",
            )
    tallies = sorted((e for e in hub.of_kind("airthreat") if e["props"].get("tally")), key=lambda e: -e["ts"])
    if tallies:
        t = tallies[0]["props"]["tally"]
        launch = ", ".join(a["name"] for a in t["launch_areas"])
        hits = f"; impacts reported at {t['hit_locations']} locations" if t.get("hit_locations") else ""
        add(
            f"Latest Air Force tally: {t['attacked'] or 'unknown number of'} targets launched, {t['downed']} downed or suppressed{hits}; launch areas {launch or 'not stated'}.",
            [tallies[0]["id"]],
            "threat",
        )

    regions = hub.of_kind("region")
    alerting = [r for r in regions if r["props"].get("alert_active")]
    if regions:
        add(
            f"{len(alerting)} of {len(regions)} regions are under an air-raid alert now"
            + (f": {', '.join(r['label'] for r in alerting[:8])}." if alerting else "."),
            [r["id"] for r in alerting],
            "alert",
        )
        high = sorted(
            (r for r in regions if (r["props"].get("p_new") or 0) >= 0.5), key=lambda r: -r["props"]["p_new"]
        )
        if high:
            add(
                "Model forecast, new alert in the current 6-hour block: "
                + ", ".join(f"{r['label']} {r['props']['p_new']:.0%}" for r in high[:6])
                + ".",
                [r["id"] for r in high[:6]],
                "forecast",
            )

    front = hub.entities.get("front:deepstate")
    if front and front["props"].get("update"):
        add(f"DeepState's latest front-line update: {front['props']['update']}", [front["id"]], "front")

    craft = sorted(
        (
            e
            for e in [*hub.of_kind("aircraft"), *hub.of_kind("vessel")]
            if (e["props"].get("threat") or {}).get("priority", 0) >= 15
        ),
        key=lambda e: -e["props"]["threat"]["priority"],
    )
    for e in craft[:4]:
        reasons = [r["reason"] for r in e["props"]["threat"]["reasons"] if r["points"] > 0][:3]
        add(
            f"{e['label']} ({e['props'].get('state') or 'unattributed'}, {e['props'].get('role', 'unknown role')}) has priority {e['props']['threat']['priority']}: {'; '.join(reasons)}.",
            [e["id"]],
            "craft",
        )
    nets = [n for n in hub.of_kind("net") if n["props"].get("mission") not in (None, "unknown", "training")]
    if nets:
        add(
            "Active mission nets: "
            + "; ".join(
                f"{n['props']['mission'].replace('_', ' ')} with {len(n['props']['members'])} craft ({'/'.join(s.upper() for s in n['props']['states'])})"
                for n in nets[:4]
            )
            + ".",
            [n["id"] for n in nets[:4]],
            "craft",
        )
    jam = [g for g in hub.of_kind("gnss") if g["props"]["level"] == "high"]
    if jam:
        add(
            f"GNSS interference is high in {len(jam)} cells, centred near "
            + ", ".join(f"{g['lat']:.1f}°N {g['lon']:.1f}°E" for g in jam[:4])
            + ".",
            [g["id"] for g in jam[:6]],
            "gnss",
        )
    reroutes = [a for a in hub.alerts if a.get("kind") == "reroute" and now - a.get("ts", 0) <= WINDOW_MS]
    if reroutes:
        add(
            f"{len(reroutes)} military aircraft likely re-routed in the last hour: "
            + "; ".join(a["title"] for a in reroutes[:3])
            + ".",
            [i for a in reroutes[:3] for i in a.get("entities", [])],
            "craft",
        )
    news = sorted(
        (
            e
            for e in hub.of_kind("news")
            if e["props"].get("strike_related") and now - e["ts"] <= 3 * WINDOW_MS
        ),
        key=lambda e: -e["ts"],
    )
    for e in news[:3]:
        add(f'{e["props"].get("outlet", "News")}: "{e["label"]}"', [e["id"]], "news")
    return out


class Sitrep:
    def __init__(self):
        self.current: dict | None = None
        self.prose: dict | None = None

    def build(self, hub: Hub) -> dict:
        fs = facts(hub)
        self.current = {"at": now_ms(), "facts": fs, "prose": self.prose}
        return self.current

    def write(self) -> dict | None:
        if not self.current or not featherless.featherless_key() or len(self.current["facts"]) < 2:
            return None
        table = {f["id"]: f["text"] for f in self.current["facts"]}
        p = featherless.complete(TASK, table)
        self.prose = {
            "text": p.text,
            "cited": p.cited,
            "model": p.model,
            "at": int(p.at * 1000),
            "facts_at": self.current["at"],
        }
        self.current["prose"] = self.prose
        return self.prose

    def entity(self) -> dict:
        c = self.current or {"at": now_ms(), "facts": [], "prose": None}
        return {
            "id": "sitrep:current",
            "kind": "sitrep",
            "label": "Situation report",
            "lon": 31.2,
            "lat": 49.0,
            "ts": c["at"],
            "src": NAME,
            "prov": "inferred",
            "props": c,
        }


async def run(hub: Hub, rep: Sitrep) -> None:
    hub.source(NAME)
    last_prose = 0.0
    while True:
        rep.build(hub)
        hub.upsert(rep.entity())
        detail = f"{len(rep.current['facts'])} facts"
        if featherless.featherless_key() and time.monotonic() - last_prose >= EVERY_S:
            try:
                await asyncio.to_thread(rep.write)
                last_prose = time.monotonic()
                hub.upsert(rep.entity())
                detail += f"; AI summary by {rep.prose['model']}"
            except featherless.FeatherlessError as exc:
                hub.source_error(NAME, f"{detail}; AI summary rejected: {exc}")
                await asyncio.sleep(60)
                continue
        elif not featherless.featherless_key():
            detail += "; AI summary off (set FEATHERLESS_API_KEY)"
        hub.source_ok(NAME, detail)
        await asyncio.sleep(60)
