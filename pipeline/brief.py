"""Stage 7 — brief: AI analyst briefs for the highest-risk vessels (CLAUDE.md §15.4).

For each vessel with risk ≥ BRIEF_MIN_RISK (at most BRIEF_MAX_VESSELS), its dossier is turned
into numbered facts (identity, every score reason with its evidence, gaps, encounters, candidate
detections, occupied-port calls) and Featherless writes a short brief that must cite them.
Briefs citing unknown facts, or none, are rejected and not stored. Results are cached in
data/processed/briefs.json keyed by vessel id, with the model, timestamp and cited fact ids.
Skipped (with a log line) when FEATHERLESS_API_KEY is not set.
"""

from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd

from pipeline import featherless
from pipeline.config import BRIEF_MAX_VESSELS, BRIEF_MIN_RISK, PROCESSED_DIR, featherless_key
from pipeline.io import read_table, write_json

log = logging.getLogger("terrestrial")

TASK = (
    "Write a brief of 3–5 sentences for an analyst deciding whether this vessel warrants attention: "
    "what it did, why it scores as it does, and what would confirm or refute the concern."
)


def vessel_facts(vid: str, t: dict[str, pd.DataFrame]) -> dict[str, str]:
    facts: dict[str, str] = {}

    def add(text: str) -> None:
        facts[f"F{len(facts) + 1}"] = text

    v = t["vessels"][t["vessels"]["vessel_id"] == vid].iloc[0]
    s = t["scores"][t["scores"]["vessel_id"] == vid].iloc[0]
    add(
        f"Vessel {v['name'] or 'unnamed'} (flag {v['flag'] or 'unknown'}, IMO {v['imo'] or 'unknown'}, MMSI {v['mmsi'] or 'unknown'}, type {v['vessel_type']})."
    )
    add(f"Heuristic risk score {int(s['risk'])} of 100.")
    for r in t["score_breakdown"][t["score_breakdown"]["vessel_id"] == vid].itertuples():
        add(f"+{int(r.points)} points: {r.reason} ({r.provenance}, source {r.source}).")
    for g in t["gaps"][t["gaps"]["vessel_id"] == vid].itertuples():
        state = "still dark" if g.open else f"reappeared after {g.duration_h:.0f} h"
        add(f"AIS gap starting {g.start:%d %b %H:%M} UTC at {g.off_lat:.2f}N {g.off_lon:.2f}E, {state}.")
    for e in t["encounters"][t["encounters"]["vessel_id"] == vid].itertuples():
        add(
            f"Encounter with {e.other_name or e.other_vessel_id} on {e.start:%d %b} at {e.lat:.2f}N {e.lon:.2f}E."
        )
    c = t["candidates"][(t["candidates"]["vessel_id"] == vid) & t["candidates"]["consistent"]]
    for r in c.itertuples():
        where = f" inside {r.in_aoi}" if r.in_aoi else ""
        add(
            f"Unmatched radar detection {r.sar_id} at {r.ts:%d %b %H:%M} UTC consistent with the dark track{where}."
        )
    return facts


def run() -> None:
    if not featherless_key():
        log.info("  FEATHERLESS_API_KEY not set: briefs skipped")
        return
    names = ["vessels", "scores", "score_breakdown", "gaps", "encounters", "candidates"]
    t = {n: read_table(n) for n in names}
    top = (
        t["scores"][t["scores"]["risk"] >= BRIEF_MIN_RISK]
        .sort_values("risk", ascending=False)
        .head(BRIEF_MAX_VESSELS)
    )
    briefs, rejected = {}, 0
    for vid in top["vessel_id"]:
        facts = vessel_facts(vid, t)
        try:
            p = featherless.complete(TASK, facts, max_tokens=400)
        except featherless.FeatherlessError as exc:
            log.warning("  brief for %s rejected: %s", vid, exc)
            rejected += 1
            continue
        briefs[vid] = {
            "text": p.text,
            "cited": p.cited,
            "facts": facts,
            "model": p.model,
            "generated_at": int(p.at),
        }
        time.sleep(0.5)
    write_json(PROCESSED_DIR / "briefs.json", briefs)
    log.info("  %d briefs written, %d rejected", len(briefs), rejected)


def stage(start: date, end: date, args) -> None:
    run()
