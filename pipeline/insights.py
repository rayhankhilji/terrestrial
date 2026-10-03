"""Stage 6 — insights: dark tracks, occupied-port calls, highlights, predictions (§15.5–15.7).

Everything here is derived from the processed tables; nothing new is observed. Every item
carries evidence refs and a provenance tag, and predictions are labelled model estimates.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx
import pandas as pd
import shapely
from shapely.geometry import box, shape

from pipeline import aoi, geo
from pipeline.config import BBOX, KM_PER_NM, PROCESSED_DIR, RAW_DIR, RECENT_DAYS
from pipeline.envelope import vmax_for
from pipeline.io import read_json, read_table, write_json, write_table
from pipeline.score import Tables, score
from pipeline.tracks import dark_track

log = logging.getLogger("terrestrial")

LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_land.geojson"
LAND_CACHE = RAW_DIR / "naturalearth" / "ne_50m_land.geojson"
TREND_STEP_DAYS = 7


# --- dark tracks --------------------------------------------------------------------------


def dark_tracks(t: Tables) -> pd.DataFrame:
    c = t.candidates[t.candidates["consistent"]] if not t.candidates.empty else t.candidates
    if c.empty:
        return pd.DataFrame(
            columns=[
                "gap_id",
                "vessel_id",
                "sar_ids",
                "path",
                "times",
                "length_km",
                "max_leg_speed_kn",
                "n_detections",
            ]
        )
    gaps = t.gaps.set_index("gap_id")
    vmax = t.envelopes.set_index("gap_id")["vmax_kn"]
    rows = []
    for gap_id, group in c.groupby("gap_id"):
        gap = gaps.loc[gap_id]
        gap = gap.copy()
        gap["gap_id"] = gap_id
        track = dark_track(gap, group, float(vmax.loc[gap_id]))
        if track is None:
            continue
        rows.append(
            {
                "gap_id": gap_id,
                "vessel_id": gap["vessel_id"],
                "sar_ids": track.sar_ids,
                "path": [list(p) for p in track.points],
                "times": [ts.isoformat() for ts in track.times],
                "length_km": track.length_km,
                "max_leg_speed_kn": track.max_leg_speed_kn,
                "n_detections": len(track.sar_ids),
            }
        )
    return pd.DataFrame(rows)


# --- occupied-port calls --------------------------------------------------------------------


def occupied_calls(t: Tables, window_end: pd.Timestamp, days: int = RECENT_DAYS) -> pd.DataFrame:
    """Every piece of evidence that a hull called at an occupied port in the last `days`."""
    since = window_end - pd.Timedelta(days=days)
    out = []
    v = t.port_visits
    for r in v[v["occupied_aoi"] & (v["start"] >= since)].itertuples(index=False):
        out.append(
            {
                "vessel_id": r.vessel_id, "aoi": r.aoi, "kind": "ais_port_visit", "provenance": "observed",
                "ts": r.start, "evidence_ref": f"visit:{r.visit_id}", "lon": r.lon, "lat": r.lat,
                "detail": f"AIS port visit ({r.duration_h:.0f} h, GFW confidence {r.confidence})",
            }
        )  # fmt: skip
    c = t.candidates
    if not c.empty:
        for r in c[c["consistent"] & c["occupied_aoi"] & (c["ts"] >= since)].itertuples(index=False):
            out.append(
                {
                    "vessel_id": r.vessel_id, "aoi": r.in_aoi, "kind": "covert_sar", "provenance": "inferred",
                    "ts": r.ts, "evidence_ref": f"sar:{r.sar_id}|gap:{r.gap_id}", "lon": r.lon, "lat": r.lat,
                    "detail": f"Unmatched radar detection during an AIS gap (1 of {r.gap_consistent_candidates} consistent candidates)",
                }
            )  # fmt: skip
    g = t.gaps[t.gaps["start"] >= since]
    for r in g.itertuples(index=False):
        ends = [("off", r.off_lon, r.off_lat, r.start)]
        if not r.open:
            ends.append(("on", r.on_lon, r.on_lat, r.end))
        for what, lon, lat, ts in ends:
            name, km = aoi.nearest_occupied(lon, lat)
            if km[0] <= 50:
                out.append(
                    {
                        "vessel_id": r.vessel_id, "aoi": name[0], "kind": f"gap_{what}_near", "provenance": "observed",
                        "ts": ts, "evidence_ref": f"gap:{r.gap_id}", "lon": lon, "lat": lat,
                        "detail": f"AIS switched {what} {km[0]:.0f} km from occupied {name[0]}",
                    }
                )  # fmt: skip
    cols = ["vessel_id", "aoi", "kind", "provenance", "ts", "evidence_ref", "lon", "lat", "detail"]
    return pd.DataFrame(out, columns=cols).sort_values("ts", ascending=False).reset_index(drop=True)


# --- predictions ------------------------------------------------------------------------------


def _land() -> shapely.Geometry:
    if not LAND_CACHE.exists():
        LAND_CACHE.parent.mkdir(parents=True, exist_ok=True)
        response = httpx.get(LAND_URL, timeout=120, follow_redirects=True)
        response.raise_for_status()
        LAND_CACHE.write_bytes(response.content)
    features = read_json(LAND_CACHE)["features"]
    region = box(*BBOX).buffer(3)
    parts = [shape(f["geometry"]) for f in features]
    return shapely.union_all([p.intersection(region) for p in parts if p.intersects(region)])


def dark_now(t: Tables, window_end: pd.Timestamp) -> list[dict]:
    """For hulls whose latest gap is still open: where can they be at the end of the window?"""
    open_gaps = t.gaps[t.gaps["open"]]
    if open_gaps.empty:
        return []
    land = _land()
    sea = box(*BBOX).difference(land)
    vtype = t.vessels.set_index("vessel_id")["vessel_type"].to_dict()
    history = (
        pd.concat(
            [
                t.port_visits.loc[t.port_visits["aoi"].notna(), ["vessel_id", "aoi"]],
                t.candidates.loc[
                    t.candidates["consistent"] & t.candidates["in_aoi"].notna(), ["vessel_id", "in_aoi"]
                ].rename(columns={"in_aoi": "aoi"})
                if not t.candidates.empty
                else pd.DataFrame(columns=["vessel_id", "aoi"]),
            ]
        )
        .groupby(["vessel_id", "aoi"])
        .size()
    )
    out = []
    for g in open_gaps.sort_values("start").groupby("vessel_id").tail(1).itertuples(index=False):
        hours = (window_end - g.start) / pd.Timedelta(hours=1)
        if hours <= 0:
            continue
        v = vmax_for(vtype.get(g.vessel_id))
        reach = v * hours * KM_PER_NM
        disk = geo.circle(g.off_lon, g.off_lat, min(reach, 2500.0), 96)
        reachable = disk.intersection(sea)
        weights = {}
        for a in aoi.AOIS:
            d = float(geo.distance_km(g.off_lon, g.off_lat, a.lon, a.lat))
            if d <= reach:
                prior = 1 + int(history.get((g.vessel_id, a.name), 0))
                weights[a.name] = prior * (1 - d / reach) + 1e-6
        total = sum(weights.values()) or 1.0
        out.append(
            {
                "vessel_id": g.vessel_id,
                "gap_id": g.gap_id,
                "dark_since": g.start.isoformat(),
                "hours_dark": round(hours, 1),
                "vmax_kn": v,
                "reach_km": round(reach, 1),
                "off": [g.off_lon, g.off_lat],
                "reachable_wkt": reachable.simplify(0.02).wkt,
                "port_likelihood": sorted(
                    (
                        {"aoi": k, "p": round(w / total, 3), "occupied_ua": aoi.AOI_BY_NAME[k].occupied_ua}
                        for k, w in weights.items()
                    ),
                    key=lambda x: -x["p"],
                ),
                "method": "model estimate: sea area reachable at max speed since AIS-off; port weights = (1 + this hull's past calls there) × proximity",
            }
        )
    return out


def risk_trends(
    t: Tables, start: pd.Timestamp, end: pd.Timestamp, vessel_ids: list[str]
) -> dict[str, list[dict]]:
    """Risk as of each week of the window (heuristic score using only evidence known by then)."""
    if not vessel_ids:
        return {}
    sub = Tables(
        vessels=t.vessels[t.vessels["vessel_id"].isin(vessel_ids)],
        identities=t.identities,
        gaps=t.gaps,
        envelopes=t.envelopes,
        candidates=t.candidates,
        encounters=t.encounters,
        port_visits=t.port_visits,
        sanctions=t.sanctions,
    )
    out: dict[str, list[dict]] = {vid: [] for vid in vessel_ids}
    as_of = start + pd.Timedelta(days=TREND_STEP_DAYS)
    steps = []
    while as_of < end:
        steps.append(as_of)
        as_of += pd.Timedelta(days=TREND_STEP_DAYS)
    steps.append(end)
    for step in steps:
        scores, _ = score(sub, as_of=step)
        for r in scores.itertuples(index=False):
            out[r.vessel_id].append({"date": step.date().isoformat(), "risk": int(r.risk)})
    return out


def next_dark_window(t: Tables, window_end: pd.Timestamp) -> dict[str, dict]:
    """Hulls with ≥3 gaps: median spacing between switch-offs → expected next one (model estimate)."""
    out = {}
    for vid, g in t.gaps.sort_values("start").groupby("vessel_id"):
        if len(g) < 3:
            continue
        spacing = g["start"].diff().dropna() / pd.Timedelta(days=1)
        median = float(spacing.median())
        q1, q3 = float(spacing.quantile(0.25)), float(spacing.quantile(0.75))
        last = g["start"].iloc[-1]
        expected = last + pd.Timedelta(days=median)
        out[vid] = {
            "n_gaps": len(g),
            "median_spacing_days": round(median, 1),
            "iqr_days": [round(q1, 1), round(q3, 1)],
            "last_switch_off": last.isoformat(),
            "expected_next": expected.isoformat(),
            "overdue": bool(expected < window_end),
            "method": "model estimate: median interval between this hull's AIS switch-offs",
        }
    return out


# --- highlights ---------------------------------------------------------------------------------


def highlights(t: Tables, scores: pd.DataFrame, calls: pd.DataFrame, tracks: pd.DataFrame) -> list[dict]:
    names = scores.set_index("vessel_id")["name"].to_dict()
    listed = set(scores.loc[scores["listed"], "vessel_id"])
    env = t.envelopes.set_index("gap_id")
    out: list[dict] = []

    def name(vid):
        return names.get(vid) or vid

    # 1. Probable covert port calls, most specific first.
    c = t.candidates
    if not c.empty:
        occ = c[c["consistent"] & c["occupied_aoi"]].copy()
        if not occ.empty:
            occ["area"] = occ["gap_id"].map(env["area_km2"])
            occ = occ.sort_values(["gap_consistent_candidates", "area"]).drop_duplicates("vessel_id")
            for r in occ.head(8).itertuples(index=False):
                out.append(
                    {
                        "id": f"covert:{r.vessel_id}:{r.sar_id}", "kind": "covert_port_call", "severity": "high",
                        "title": f"{name(r.vessel_id)}: probable covert call at {r.in_aoi}",
                        "body": f"AIS was off; an unmatched radar detection inside occupied {r.in_aoi} at {r.ts:%d %b %H:00}Z fits its reachable window "
                        f"(1 of {r.gap_consistent_candidates} consistent candidates, envelope {r.area:,.0f} km²).",
                        "vessel_ids": [r.vessel_id], "evidence": [f"sar:{r.sar_id}", f"gap:{r.gap_id}"],
                        "gap_id": r.gap_id, "lon": r.lon, "lat": r.lat, "ts": r.ts.isoformat(), "provenance": "inferred",
                    }
                )  # fmt: skip
    # 2. Impossible gaps (spoofing / identity swap indicator)
    imp = t.envelopes[t.envelopes["impossible"]].merge(t.gaps, on=["gap_id", "vessel_id"])
    for r in imp.sort_values("implied_speed_kn", ascending=False).head(5).itertuples(index=False):
        out.append(
            {
                "id": f"impossible:{r.gap_id}", "kind": "impossible_gap", "severity": "warn",
                "title": f"{name(r.vessel_id)}: physically impossible AIS gap",
                "body": f"Reappeared {r.distance_km:,.0f} km away after {r.duration_h:.0f} h ({r.implied_speed_kn:.0f} kn implied, max {r.vmax_kn:.1f} kn). "
                "Consistent with position spoofing or two hulls sharing one identity.",
                "vessel_ids": [r.vessel_id], "evidence": [f"gap:{r.gap_id}"], "gap_id": r.gap_id,
                "lon": r.on_lon, "lat": r.on_lat, "ts": r.start.isoformat(), "provenance": "inferred",
            }
        )  # fmt: skip
    # 3. Ship-to-ship meetings with listed hulls
    e = t.encounters
    if not e.empty:
        sts = e[e["other_vessel_id"].isin(listed) & ~e["vessel_id"].isin(listed)].sort_values(
            "start", ascending=False
        )
        for r in sts.drop_duplicates("vessel_id").head(6).itertuples(index=False):
            near = aoi.nearest_occupied(r.lon, r.lat)
            out.append(
                {
                    "id": f"sts:{r.enc_id}", "kind": "sts_with_listed", "severity": "high",
                    "title": f"{name(r.vessel_id)} met listed {name(r.other_vessel_id)}",
                    "body": f"Ship-to-ship encounter on {r.start:%d %b} ({r.duration_h:.0f} h), {near[1][0]:.0f} km from occupied {near[0][0]}.",
                    "vessel_ids": [r.vessel_id, r.other_vessel_id], "evidence": [f"enc:{r.enc_id}"],
                    "lon": r.lon, "lat": r.lat, "ts": r.start.isoformat(), "provenance": "observed",
                }
            )  # fmt: skip
    # 4. Dark tracks spanning several detections
    if not tracks.empty:
        for r in tracks.sort_values("n_detections", ascending=False).head(4).itertuples(index=False):
            if r.n_detections < 2:
                continue
            out.append(
                {
                    "id": f"track:{r.gap_id}", "kind": "dark_track", "severity": "warn",
                    "title": f"{name(r.vessel_id)}: dark track through {r.n_detections} radar detections",
                    "body": f"A feasible route of {r.length_km:,.0f} km links AIS-off to AIS-on through {r.n_detections} unmatched detections "
                    f"(fastest leg {r.max_leg_speed_kn:.1f} kn).",
                    "vessel_ids": [r.vessel_id], "evidence": [f"gap:{r.gap_id}", *[f"sar:{s}" for s in r.sar_ids]],
                    "gap_id": r.gap_id, "lon": r.path[1][0], "lat": r.path[1][1], "ts": r.times[0], "provenance": "inferred",
                }
            )  # fmt: skip
    # 5. The operational answer, as one card
    if not calls.empty:
        hulls = calls["vessel_id"].nunique()
        inferred = calls[calls["provenance"] == "inferred"]["vessel_id"].nunique()
        out.insert(
            0,
            {
                "id": "occupied-calls-summary", "kind": "summary", "severity": "high",
                "title": f"{hulls} hulls with evidence of occupied-port calls in the last {RECENT_DAYS} days",
                "body": f"{inferred} of them rest on inferred evidence (radar during an AIS gap); the rest on AIS port visits or switch-offs near occupied ports.",
                "vessel_ids": calls["vessel_id"].unique().tolist()[:50], "evidence": [], "provenance": "mixed",
            },
        )  # fmt: skip
    return out


# --- stage ----------------------------------------------------------------------------------------


def stage(start: date, end: date, args) -> None:
    t = Tables.load()
    window_start = pd.Timestamp(start, tz="UTC")
    window_end = pd.Timestamp(end, tz="UTC")
    scores = read_table("scores")

    tracks = dark_tracks(t)
    write_table(tracks, "dark_tracks", required=False)
    calls = occupied_calls(t, window_end)
    write_table(calls, "occupied_calls", required=False)
    log.info(
        "  %d dark tracks; %d occupied-port-call evidence items across %d hulls",
        len(tracks),
        len(calls),
        calls["vessel_id"].nunique(),
    )

    top = scores[scores["risk"] > 0].head(150)["vessel_id"].tolist()
    predictions = {
        "dark_now": dark_now(t, window_end),
        "risk_trend": risk_trends(t, window_start, window_end, top),
        "next_dark_window": next_dark_window(t, window_end),
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
    }
    write_json(PROCESSED_DIR / "predictions.json", predictions)
    found = highlights(t, scores, calls, tracks)
    write_json(PROCESSED_DIR / "highlights.json", found)
    log.info(
        "  predictions: %d hulls dark at window end, %d risk trends, %d dark-window forecasts; %d highlights",
        len(predictions["dark_now"]), len(predictions["risk_trend"]), len(predictions["next_dark_window"]), len(found),
    )  # fmt: skip
