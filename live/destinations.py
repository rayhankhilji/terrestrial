"""Destination predictions in the live picture (M6).

Every EVERY_S seconds, each airborne military aircraft with a current flight in the track store
gets a compact `pred` annotation (most likely landing airfield, probability, ETA, alternatives,
on-station flag, endurance estimate). The selected aircraft's full prediction, with paths and
winds aloft, is served on request by /live/predict/{id}. Landings the track store observes are
added to the priors as they happen. A confident prediction that changes its airfield raises a
`reroute` alert.
"""

from __future__ import annotations

import asyncio
import logging
import time

from live.hub import Hub, now_ms
from live.tracks import TrackStore
from predict.flight import features as F
from predict.flight.serve import FlightModel, Predictor, Winds
from reference.airfields import airfields

log = logging.getLogger("terrestrial.live")

NAME = "flight-model"
EVERY_S = 10
REROUTE_P = 0.35
CONFIRM_TICKS = 3
REROUTE_COOLDOWN_MS = 15 * 60 * 1000
RELOAD_CHECK_S = 120


class Destinations:
    def __init__(self, store: TrackStore):
        self.store = store
        self.predictor: Predictor | None = None
        self.settled: dict[str, dict] = {}
        self.pending: dict[str, tuple[str, int]] = {}
        self.last_alert: dict[str, int] = {}

    def load(self) -> bool:
        model = FlightModel.load()
        if model is None:
            return False
        fields = airfields()
        self.predictor = Predictor(model, F.Fields(fields), {a.ident: a for a in fields}, Winds())
        return True

    def current_flight(self, eid: str, now: int) -> dict | None:
        flights = self.store.flights(eid, hours=24, now=now)
        return flights[-1] if flights else None

    def predict(self, e: dict, winds: bool = True, paths: bool = True) -> dict | None:
        if self.predictor is None:
            return None
        now = now_ms()
        flight = self.current_flight(e["id"], now)
        if flight is None:
            return None
        pred = self.predictor.predict(e, flight, now, winds=winds, paths=paths)
        if pred is None:
            return None
        return {
            **pred.__dict__,
            "model": {
                "version": self.predictor.model.version,
                "beats_baselines": self.predictor.model.card["beats_baselines"],
            },
        }

    def _confirm(self, eid: str, compact: dict, now: int) -> dict | None:
        """The previously settled prediction if the most likely field has *settled* on a new one:
        the new field must lead for CONFIRM_TICKS updates in a row with p ≥ REROUTE_P, and an
        aircraft raises at most one re-route per REROUTE_COOLDOWN_MS. Two near-equal fields
        swapping the lead every few seconds is uncertainty, not a re-route."""
        settled = self.settled.get(eid)
        if settled is None or compact["p"] < REROUTE_P:
            if settled is None and compact["p"] >= REROUTE_P:
                self.settled[eid] = compact
            self.pending.pop(eid, None)
            return None
        if compact["dest"] == settled["dest"]:
            self.settled[eid] = compact
            self.pending.pop(eid, None)
            return None
        dest, ticks = self.pending.get(eid, (None, 0))
        ticks = ticks + 1 if dest == compact["dest"] else 1
        self.pending[eid] = (compact["dest"], ticks)
        if (
            ticks < CONFIRM_TICKS
            or eid in self.last_alert
            and now - self.last_alert[eid] < REROUTE_COOLDOWN_MS
        ):
            return None
        self.settled[eid] = compact
        self.pending.pop(eid, None)
        self.last_alert[eid] = now
        return settled

    async def update(self, hub: Hub) -> int:
        """Runs on the event loop (hub writes are not thread-safe), yielding between aircraft."""
        if self.predictor is None:
            return 0
        now = now_ms()
        n = learned = 0
        for e in list(hub.of_kind("aircraft")):
            await asyncio.sleep(0)
            props = e.get("props") or {}
            if not props.get("military"):
                continue
            flights = self.store.flights(e["id"], hours=24, now=now)
            for fl in flights[:-1] + ([flights[-1]] if flights and flights[-1]["landing"] else []):
                if fl["landing"]:
                    hex_ = (props.get("icao24") or e["id"].split(":", 1)[-1]).lower()
                    learned += self.predictor.learn(
                        hex_, props.get("type"), props.get("callsign"), fl["landing"]["ident"], fl["end"]
                    )
            pred = self.predictor.predict(e, flights[-1], now) if flights else None
            if pred is None:
                hub.annotate(e["id"], "pred", None)
                continue
            compact = pred.compact()
            hub.annotate(e["id"], "pred", compact)
            n += 1
            before = self._confirm(e["id"], compact, now)
            if before is not None:
                hub.annotate(e["id"], "reroute_at", now)
                hub.alert(
                    {
                        "id": f"reroute:{e['id']}:{now}",
                        "kind": "reroute",
                        "severity": "info",
                        "title": f"{e.get('label')} likely re-routed to {compact['dest_icao'] or compact['dest']}",
                        "body": f"Most likely landing changed from {before.get('dest_icao') or before['dest']} "
                        f"({before['p']:.0%}) to {compact['dest_name']} ({compact['p']:.0%}), ETA ~{compact['eta_min']:.0f} min. Model estimate.",
                        "entities": [e["id"]],
                        "lon": e["lon"],
                        "lat": e["lat"],
                    }
                )
        if learned:
            log.info("flight model: learned %d observed landings", learned)
        return n


async def run(hub: Hub, dest: Destinations) -> None:
    loaded = await asyncio.to_thread(dest.load)
    if not loaded:
        hub.source_error(NAME, "no trained flight model: run uv run python -m predict.flight.train")
        return
    hub.source_ok(NAME, f"model {dest.predictor.model.version}")
    checked = time.monotonic()
    while True:
        started = time.monotonic()
        if started - checked > RELOAD_CHECK_S:
            checked = started
            if FlightModel.latest_version() != dest.predictor.model.version:
                learned = dest.predictor.learned
                await asyncio.to_thread(dest.load)
                dest.predictor.learned = set()  # replay live landings into the new priors
                log.info(
                    "flight model: reloaded %s (%d live landings to re-learn)",
                    dest.predictor.model.version,
                    len(learned),
                )
        n = await dest.update(hub)
        hub.source_ok(NAME, f"{n} aircraft predicted in {time.monotonic() - started:.1f}s")
        await asyncio.sleep(EVERY_S)
