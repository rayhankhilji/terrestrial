"""GNSS interference from ADS-B navigation accuracy (gpsjam-style), M7.

Aircraft report how accurate their satellite position is (NACp). When GNSS is jammed or
spoofed, many aircraft in one area report degraded accuracy at the same time. Every aircraft
record the ADS-B poller receives (civil traffic included, for this statistic only; civil
aircraft are never plotted) is binned into CELL_DEG cells. A cell's interference level is the
share of distinct aircraft seen in the last WINDOW_S whose NACp was below BAD_NACP, using
gpsjam.org's thresholds (low < 2 %, medium 2–10 %, high > 10 %).

Excluded, because their accuracy fields say nothing about GNSS: ADS-B version 0 transponders
(no NACp semantics), MLAT and TIS-B positions, and records without NACp. A cell is published
only with at least MIN_AIRCRAFT aircraft.

This is evidence *consistent with* interference, not proof: one aircraft with a faulty
receiver can colour a sparsely flown cell, which is why the counts are shown.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field

from live.hub import Hub, now_ms

NAME = "gnss"
CELL_DEG = 0.5
WINDOW_S = 30 * 60
BAD_NACP = 8
MIN_AIRCRAFT = 3
PUBLISH_S = 30
LEVELS = ((0.10, "high"), (0.02, "medium"), (0.0, "low"))


def level(frac: float) -> str:
    return next(name for limit, name in LEVELS if frac > limit or limit == 0.0)


@dataclass
class Cell:
    i: int
    j: int
    seen: dict[str, tuple[int, bool, int]] = field(default_factory=dict)  # hex → (ts ms, bad, nacp)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.j * CELL_DEG, self.i * CELL_DEG, (self.j + 1) * CELL_DEG, (self.i + 1) * CELL_DEG


class GnssGrid:
    def __init__(self):
        self.cells: dict[tuple[int, int], Cell] = {}
        self.published: set[str] = set()

    def observe(self, ac: dict, received_at: float) -> None:
        """Raw aircraft record (readsb JSON) from any ADS-B poll."""
        nacp, lat, lon = ac.get("nac_p"), ac.get("lat"), ac.get("lon")
        if nacp is None or lat is None or lon is None or not ac.get("hex"):
            return
        derived = set(ac.get("mlat") or ()) | set(ac.get("tisb") or ())
        if (
            (ac.get("version") or 0) < 1
            or "lat" in derived
            or str(ac.get("type", "")).startswith(("mlat", "tisb"))
        ):
            return
        if ac.get("alt_baro") == "ground":
            return  # surface multipath and parked aircraft are not interference
        ts = int((received_at - float(ac.get("seen_pos") or 0.0)) * 1000)
        key = (math.floor(lat / CELL_DEG), math.floor(lon / CELL_DEG))
        cell = self.cells.get(key)
        if cell is None:
            cell = self.cells[key] = Cell(*key)
        cell.seen[ac["hex"]] = (ts, nacp < BAD_NACP, int(nacp))

    def summary(self, now: int | None = None) -> list[dict]:
        now = now or now_ms()
        out = []
        for key, cell in list(self.cells.items()):
            cell.seen = {h: v for h, v in cell.seen.items() if now - v[0] <= WINDOW_S * 1000}
            if not cell.seen:
                del self.cells[key]
                continue
            n = len(cell.seen)
            if n < MIN_AIRCRAFT:
                continue
            bad = sum(1 for _, b, _ in cell.seen.values() if b)
            frac = bad / n
            out.append({"cell": cell, "n": n, "bad": bad, "frac": frac, "level": level(frac)})
        return out

    def entities(self, now: int | None = None) -> list[dict]:
        now = now or now_ms()
        out = []
        for s in self.summary(now):
            cell: Cell = s["cell"]
            w, so, e, n = cell.bounds
            out.append(
                {
                    "id": f"gnss:{cell.i}:{cell.j}",
                    "kind": "gnss",
                    "label": f"GNSS {s['level']} ({s['bad']}/{s['n']} aircraft degraded)",
                    "lon": (w + e) / 2,
                    "lat": (so + n) / 2,
                    "ts": max(v[0] for v in cell.seen.values()),
                    "src": NAME,
                    "prov": "inferred",
                    "props": {
                        "aircraft": s["n"],
                        "degraded": s["bad"],
                        "frac": round(s["frac"], 3),
                        "level": s["level"],
                        "polygon": [[w, so], [e, so], [e, n], [w, n], [w, so]],
                        "window_min": WINDOW_S // 60,
                        "rule": f"share of aircraft with NACp < {BAD_NACP} (ADS-B v1+, no MLAT/TIS-B, airborne)",
                    },
                }
            )
        return out

    def degraded_near(self, lon: float, lat: float) -> dict | None:
        """The cell summary at a position, if published-worthy."""
        cell = self.cells.get((math.floor(lat / CELL_DEG), math.floor(lon / CELL_DEG)))
        if cell is None or len(cell.seen) < MIN_AIRCRAFT:
            return None
        bad = sum(1 for _, b, _ in cell.seen.values() if b)
        frac = bad / len(cell.seen)
        return {"frac": frac, "level": level(frac), "n": len(cell.seen)}


async def run(hub: Hub, grid: GnssGrid) -> None:
    hub.source(NAME)
    while True:
        await asyncio.sleep(PUBLISH_S)
        ents = grid.entities()
        ids = {e["id"] for e in ents}
        for e in ents:
            hub.upsert(e)
        hub.remove(sorted(grid.published - ids))
        grid.published = ids
        high = sum(1 for e in ents if e["props"]["level"] == "high")
        hub.source_ok(NAME, f"{len(ents)} cells with ≥{MIN_AIRCRAFT} aircraft; {high} high interference")
