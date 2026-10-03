"""Stage 5 — score: transparent, additive, capped heuristic risk per vessel (CLAUDE.md §7).

Every point awarded is one breakdown row: (signal, reason, points, evidence_ref, provenance,
source), so the UI can show exactly why a hull ranks where it does. Weights live in
config.ScoreWeights and are judgement calls, labelled "heuristic" everywhere they appear.

`score(tables, as_of)` only uses evidence that existed by `as_of`, which gives the risk trend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from pipeline import aoi
from pipeline.config import NEAR_AOI_KM, WEIGHTS
from pipeline.io import read_table, write_table

log = logging.getLogger("terrestrial")

W = WEIGHTS


@dataclass
class Tables:
    vessels: pd.DataFrame
    identities: pd.DataFrame
    gaps: pd.DataFrame
    envelopes: pd.DataFrame
    candidates: pd.DataFrame
    encounters: pd.DataFrame
    port_visits: pd.DataFrame
    sanctions: pd.DataFrame

    @classmethod
    def load(cls) -> Tables:
        return cls(
            vessels=read_table("vessels"),
            identities=read_table("identities"),
            gaps=read_table("gaps"),
            envelopes=read_table("gap_envelopes"),
            candidates=read_table("candidates"),
            encounters=read_table("encounters"),
            port_visits=read_table("port_visits"),
            sanctions=read_table("sanctions"),
        )


def sanctions_index(sanctions: pd.DataFrame) -> tuple[dict[str, dict], dict[str, dict]]:
    by_imo, by_mmsi = {}, {}
    for r in sanctions.itertuples(index=False):
        entry = r._asdict()
        if r.imo:
            by_imo[r.imo] = entry
        for m in r.mmsis:
            by_mmsi[str(m)] = entry
    return by_imo, by_mmsi


def listing_for(vessel, by_imo: dict, by_mmsi: dict) -> tuple[dict, str] | None:
    """OpenSanctions entry for a vessel: IMO match first (strong), MMSI second (weaker)."""
    if vessel.imo and vessel.imo in by_imo:
        return by_imo[vessel.imo], "IMO"
    if vessel.mmsi and str(vessel.mmsi) in by_mmsi:
        return by_mmsi[str(vessel.mmsi)], "MMSI"
    return None


def _fmt_day(ts: pd.Timestamp) -> str:
    return ts.strftime("%d %b %Y")


def score(t: Tables, as_of: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (scores, breakdown). Events after `as_of` are ignored."""
    gaps, cands, encs, visits = t.gaps, t.candidates, t.encounters, t.port_visits
    if as_of is not None:
        gaps = gaps[gaps["start"] <= as_of]
        cands = cands[cands["ts"] <= as_of]
        encs = encs[encs["start"] <= as_of]
        visits = visits[visits["start"] <= as_of]
    impossible = set(t.envelopes.loc[t.envelopes["impossible"], "gap_id"])
    by_imo, by_mmsi = sanctions_index(t.sanctions)
    vessel_by_id = {v.vessel_id: v for v in t.vessels.itertuples(index=False)}
    listed_ids = {vid for vid, v in vessel_by_id.items() if (hit := listing_for(v, by_imo, by_mmsi)) and hit[0]["sanctioned"]}

    rows: list[dict] = []

    def award(vid, signal, reason, points, ref, prov="observed", source="GFW events"):
        rows.append(
            {"vessel_id": vid, "signal": signal, "reason": reason, "points": points, "evidence_ref": ref, "provenance": prov, "source": source}
        )

    for vid, v in vessel_by_id.items():
        # 1. OpenSanctions listing
        hit = listing_for(v, by_imo, by_mmsi)
        if hit:
            entry, on = hit
            topics = ", ".join(entry["topics"])
            if entry["sanctioned"]:
                award(vid, "sanctioned", f"Listed on OpenSanctions ({topics}), matched on {on}", W.sanctioned, f"os:{entry['os_id']}", source="OpenSanctions")
            if entry["detained"]:
                award(vid, "psc_detention", f"Port-state-control detention record (not a sanction), matched on {on}", W.psc_detention, f"os:{entry['os_id']}", source="OpenSanctions")

        # 2–6. AIS gaps and what happened inside them
        vg = gaps[gaps["vessel_id"] == vid].sort_values("start")
        for k, g in enumerate(vg.itertuples(index=False)):
            if k * W.gap_each >= W.gap_cap:
                break
            hours = f"{g.duration_h:.0f} h" if not g.open else "still open"
            award(vid, "gap", f"AIS switched off {_fmt_day(g.start)} ({hours})", W.gap_each, f"gap:{g.gap_id}")
        near = None
        for g in vg.itertuples(index=False):
            ends = [(g.off_lon, g.off_lat, "switched off")]
            if not g.open:
                ends.append((g.on_lon, g.on_lat, "switched on"))
            for lon, lat, what in ends:
                name, km = aoi.nearest_occupied(lon, lat)
                if km[0] <= NEAR_AOI_KM:
                    near = (g, name[0], km[0], what)
                    break
            if near:
                break
        if near:
            g, name, km, what = near
            award(vid, "gap_near_occupied", f"AIS {what} {km:.0f} km from occupied {name}", W.gap_near_occupied, f"gap:{g.gap_id}")
        vc = cands[(cands["vessel_id"] == vid) & cands["consistent"]].sort_values(["gap_consistent_candidates", "ts"])
        if not vc.empty:
            c = vc.iloc[0]
            award(
                vid, "verified_dark",
                f"Unmatched radar detection consistent with the hull during its gap ({c['ts'].strftime('%d %b %H:00')}Z, 1 of {c['gap_consistent_candidates']} candidates)",
                W.verified_dark, f"sar:{c['sar_id']}|gap:{c['gap_id']}", prov="inferred", source="GFW SAR + Terrestrial",
            )  # fmt: skip
            occ = vc[vc["occupied_aoi"]]
            if not occ.empty:
                c = occ.iloc[0]
                award(
                    vid, "covert_port_call",
                    f"Probable covert port call: radar detection inside occupied {c['in_aoi']} during the gap ({c['ts'].strftime('%d %b %H:00')}Z)",
                    W.covert_port_call, f"sar:{c['sar_id']}|gap:{c['gap_id']}", prov="inferred", source="GFW SAR + Terrestrial",
                )  # fmt: skip
        imp = [g for g in vg.itertuples(index=False) if g.gap_id in impossible]
        if imp:
            g = imp[0]
            award(
                vid, "impossible_gap",
                f"Physically impossible gap: {g.distance_km:.0f} km in {g.duration_h:.0f} h needs > max speed (spoofing or identity swap indicator)",
                W.impossible_gap, f"gap:{g.gap_id}", prov="inferred", source="Terrestrial",
            )  # fmt: skip

        # 7–8. Encounters (ship-to-ship meetings)
        ve = encs[encs["vessel_id"] == vid].sort_values("start")
        for k, e in enumerate(ve.itertuples(index=False)):
            if k * W.encounter_each >= W.encounter_cap:
                break
            other = vessel_by_id.get(e.other_vessel_id)
            who = (other.name if other is not None and other.name else e.other_vessel_id) or "unknown vessel"
            award(vid, "encounter", f"Ship-to-ship encounter with {who} on {_fmt_day(e.start)}", W.encounter_each, f"enc:{e.enc_id}")
        listed_partner = ve[ve["other_vessel_id"].isin(listed_ids)]
        if not listed_partner.empty:
            e = listed_partner.iloc[0]
            other = vessel_by_id.get(e["other_vessel_id"])
            award(vid, "encounter_sanctioned", f"Met a sanctions-listed vessel ({other.name if other is not None else e['other_vessel_id']})", W.encounter_sanctioned, f"enc:{e['enc_id']}")

        # 9. Port visits inside occupied AOIs
        vv = visits[(visits["vessel_id"] == vid) & visits["occupied_aoi"]].sort_values("start")
        if not vv.empty:
            p = vv.iloc[0]
            award(vid, "occupied_port_visit", f"AIS port visit at occupied {p['aoi']} on {_fmt_day(p['start'])}", W.occupied_port_visit, f"visit:{p['visit_id']}")

        # 10. Identity changes (name / flag) inside the window
        vi = t.identities[t.identities["vessel_id"] == vid].sort_values("date_from")
        changes = []
        prev = None
        for r in vi.itertuples(index=False):
            if prev is not None and (r.name != prev.name or r.flag != prev.flag):
                changes.append((prev, r))
            prev = r
        window_start = t.gaps["start"].min() if not t.gaps.empty else None
        if window_start is not None:
            changes = [(a, b) for a, b in changes if b.date_from >= window_start and (as_of is None or b.date_from <= as_of)]
        for k, (a, b) in enumerate(changes):
            if k * W.identity_change_each >= W.identity_change_cap:
                break
            what = []
            if a.name != b.name:
                what.append(f"name {a.name} → {b.name}")
            if a.flag != b.flag:
                what.append(f"flag {a.flag} → {b.flag}")
            award(vid, "identity_change", f"Identity change on {_fmt_day(b.date_from)}: {', '.join(what)}", W.identity_change_each, f"identity:{vid}:{b.date_from.isoformat()}", source="GFW identity")

    breakdown = pd.DataFrame(rows, columns=["vessel_id", "signal", "reason", "points", "evidence_ref", "provenance", "source"])
    totals = breakdown.groupby("vessel_id")["points"].sum() if not breakdown.empty else pd.Series(dtype=int)
    scores = t.vessels[["vessel_id", "name", "flag", "imo", "mmsi", "vessel_type"]].copy()
    scores["raw_points"] = scores["vessel_id"].map(totals).fillna(0).astype(int)
    scores["risk"] = scores["raw_points"].clip(upper=W.total_cap)
    scores["listed"] = scores["vessel_id"].isin(listed_ids)
    scores = scores.sort_values(["risk", "raw_points"], ascending=False).reset_index(drop=True)
    scores["rank"] = scores.index + 1
    return scores, breakdown


def stage(start: date, end: date, args) -> None:
    t = Tables.load()
    log.info("  in: %d vessels, %d gaps, %d candidates, %d encounters", len(t.vessels), len(t.gaps), len(t.candidates), len(t.encounters))
    scores, breakdown = score(t)
    write_table(scores, "scores")
    write_table(breakdown, "score_breakdown")
    top = scores.head(10)
    for r in top.itertuples(index=False):
        log.info("    %3d  %-28s %s", r.risk, (r.name or r.vessel_id)[:28], r.flag or "")
