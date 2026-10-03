"""Live air-raid alert state per region (no key): ubilling.net.ua/aerialalerts.

The feed republishes the official alert state as {"states": {"<region in Ukrainian>":
{"alertnow": bool, "changed": …}}, "cachedat": "<Kyiv local time>"}. Its `changed` timestamps
are not maintained (most read 1970-01-01), so alert start and end times are taken from
Terrestrial's own polling: every state change is appended to data/live/alert_transitions.jsonl
with the time we observed it, together with heartbeats proving when we were watching. The danger
model's serving path reads that log to cover the hours since the sirens dataset's last daily
refresh; where heartbeats have gaps, the affected predictions are flagged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from history.regions import BY_ALERTS_UA
from live.hub import Hub, now_ms
from pipeline.config import LIVE_DIR

log = logging.getLogger("terrestrial.live")

NAME = "air-alerts"
URL = "https://ubilling.net.ua/aerialalerts/"
POLL_S = 30
HEARTBEAT_S = 300
LOG = LIVE_DIR / "alert_transitions.jsonl"
KYIV = ZoneInfo("Europe/Kyiv")
MAX_FEED_AGE_S = 600  # a feed whose cache is older than this is reported, not trusted


def parse(body: dict) -> tuple[dict[str, bool], datetime]:
    """Region ISO → alert active, and the feed's own cache time (UTC)."""
    states = body.get("states")
    if not isinstance(states, dict) or "cachedat" not in body:
        raise ValueError(f"unexpected alert feed shape: {json.dumps(body)[:200]}")
    unknown = set(states) - set(BY_ALERTS_UA)
    if unknown:
        raise ValueError(f"unmapped alert regions: {sorted(unknown)}")
    cached = datetime.strptime(body["cachedat"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KYIV)
    return {BY_ALERTS_UA[name]: bool(v["alertnow"]) for name, v in states.items()}, cached.astimezone(
        ZoneInfo("UTC")
    )


class AlertLog:
    """Append-only record of observed alert transitions and heartbeats."""

    def __init__(self, path: Path = LOG):
        self.path = path
        self.state: dict[str, bool] = {}
        self.since: dict[str, int] = {}  # iso → ms when we saw the current alert begin
        self.last_heartbeat = 0

    def _write(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def observe(self, states: dict[str, bool], at: int) -> list[str]:
        """Record changes; returns the regions whose state changed."""
        first = not self.state
        if first:
            self._write({"t": "session", "at": at})
        changed = []
        for iso, active in states.items():
            if self.state.get(iso) != active:
                # On the first poll an active alert's start is unknown: we saw it at `at`.
                self._write({"t": "state", "iso": iso, "active": active, "at": at, "first_poll": first})
                changed.append(iso)
                if active:
                    self.since[iso] = at
                else:
                    self.since.pop(iso, None)
            self.state[iso] = active
        if at - self.last_heartbeat >= HEARTBEAT_S * 1000:
            self._write({"t": "heartbeat", "at": at})
            self.last_heartbeat = at
        return changed


def read_log(path: Path = LOG) -> tuple[pd.DataFrame, list[tuple[int, int]]]:
    """Alert intervals observed by the poller, and the time spans it was watching.

    Intervals: iso, start, end (UTC), open (still active at the last observation), start_known
    (False when the alert was already running at the first poll of a session).
    Coverage: (from_ms, to_ms) spans with no gap longer than two heartbeats.
    """
    if not path.exists():
        return pd.DataFrame(columns=["iso", "start", "end", "open", "start_known"]), []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows.sort(key=lambda r: r["at"])
    spans: list[list[int]] = []
    open_at: dict[str, tuple[int, bool]] = {}
    intervals = []
    last_seen = 0
    for r in rows:
        gap = r["at"] - last_seen > 2 * HEARTBEAT_S * 1000 + POLL_S * 1000
        if r["t"] == "session" or not spans or gap:
            # Watching stopped: alerts we thought were running end at our last observation.
            for iso, (start, known) in open_at.items():
                intervals.append(
                    {"iso": iso, "start": start, "end": last_seen, "open": False, "start_known": known}
                )
            open_at.clear()
            spans.append([r["at"], r["at"]])
        spans[-1][1] = r["at"]
        last_seen = r["at"]
        if r["t"] == "state":
            if r["active"] and r["iso"] not in open_at:
                open_at[r["iso"]] = (r["at"], not r["first_poll"])
            elif not r["active"] and r["iso"] in open_at:
                start, known = open_at.pop(r["iso"])
                intervals.append(
                    {"iso": r["iso"], "start": start, "end": r["at"], "open": False, "start_known": known}
                )
    for iso, (start, known) in open_at.items():
        intervals.append({"iso": iso, "start": start, "end": last_seen, "open": True, "start_known": known})
    df = pd.DataFrame(intervals, columns=["iso", "start", "end", "open", "start_known"])
    for c in ("start", "end"):
        df[c] = pd.to_datetime(df[c], unit="ms", utc=True).dt.as_unit("s")
    return df, [(a, b) for a, b in spans]


async def run(hub: Hub, alert_log: AlertLog, on_change=None) -> None:
    hub.source(NAME)
    async with httpx.AsyncClient(timeout=15.0) as http:
        while True:
            try:
                response = await http.get(URL)
                response.raise_for_status()
                states, cached = parse(response.json())
                age = (datetime.now(ZoneInfo("UTC")) - cached).total_seconds()
                if age > MAX_FEED_AGE_S:
                    raise ValueError(f"alert feed cache is {age / 60:.0f} min old")
                at = now_ms()
                changed = alert_log.observe(states, at)
                if changed and on_change:
                    on_change(changed)
                hub.source_ok(NAME, f"{sum(states.values())} of {len(states)} regions under alert")
            except (httpx.HTTPError, ValueError) as exc:
                hub.source_error(NAME, f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(POLL_S)
