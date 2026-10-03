"""Terrestrial read-only API (CLAUDE.md §9).

Serves data/processed (pipeline output) and the TuringDB graph. It never calls an external
API: everything here was fetched and computed by the pipeline beforehand.
Coordinates are GeoJSON [lon, lat].
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping

from api.data import Processed, records, window
from pipeline.aoi import AOIS
from pipeline.config import NEAR_AOI_KM, RECENT_DAYS, WEIGHTS

app = FastAPI(title="Terrestrial API", version="0.2.0")
data = Processed()

ATTRIBUTION = (
    "Data: Global Fishing Watch, OpenSanctions. Heuristic risk score — candidate findings, not conclusions."
)
CAVEATS = [
    "GFW gap events need ≥ 12 h and a start ≥ 50 nm from shore: AIS switch-offs close to the coast are not in this dataset.",
    "SAR coverage depends on Sentinel-1 passes. Absence of a radar detection proves nothing and is never scored.",
    "A radar detection inside a reachability envelope is consistent with the dark hull, not proof of it; the dossier shows how many rival candidates exist.",
    "Risk weights are heuristic judgement calls (see /api/meta weights).",
]


def _feature(geometry: dict, props: dict, fid: str | None = None) -> dict:
    f = {"type": "Feature", "geometry": geometry, "properties": props}
    if fid is not None:
        f["id"] = fid
    return f


def _between(df: pd.DataFrame, col: str, start: date | None, end: date | None) -> pd.DataFrame:
    if start:
        df = df[df[col] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df[col] < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)]
    return df


# --- basics --------------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/aois")
def aois() -> dict:
    """AOI circles as a GeoJSON FeatureCollection (occupied ports flagged)."""
    return {
        "type": "FeatureCollection",
        "features": [
            _feature(
                {"type": "Polygon", "coordinates": [[list(c) for c in a.polygon.exterior.coords]]},
                {
                    "name": a.name,
                    "occupied_ua": a.occupied_ua,
                    "radius_km": a.radius_km,
                    "center": [a.lon, a.lat],
                },
                a.name,
            )
            for a in AOIS
        ],
    }


@app.get("/api/meta")
def meta() -> dict:
    counts = {}
    for name in (
        "vessels",
        "gaps",
        "encounters",
        "loitering",
        "port_visits",
        "sar",
        "candidates",
        "dark_tracks",
        "occupied_calls",
    ):
        if data.has(f"{name}.parquet"):
            counts[name] = len(data.table(name))
    return {
        "window": window(),
        "recent_days": RECENT_DAYS,
        "near_aoi_km": NEAR_AOI_KM,
        "counts": counts,
        "weights": WEIGHTS.as_dict(),
        "attribution": ATTRIBUTION,
        "caveats": CAVEATS,
        "pipeline_ready": data.has("scores.parquet"),
    }


# --- vessels -------------------------------------------------------------------------------


@app.get("/api/vessels")
def vessels(min_risk: int = 0, limit: int = Query(500, le=5000)) -> list[dict]:
    """Vessels ranked by heuristic risk, with their top three reasons."""
    scores = data.table("scores")
    breakdown = data.table("score_breakdown")
    calls = (
        data.table("occupied_calls")
        if data.has("occupied_calls.parquet")
        else pd.DataFrame(columns=["vessel_id"])
    )
    dark = {p["vessel_id"] for p in data.optional_json("predictions.json", {}).get("dark_now", [])}
    called = set(calls["vessel_id"])
    top = (
        breakdown.sort_values("points", ascending=False)
        .groupby("vessel_id")
        .head(3)
        .groupby("vessel_id")[["reason", "points", "signal"]]
        .apply(lambda g: g.to_dict(orient="records"))
        .to_dict()
    )
    rows = scores[scores["risk"] >= min_risk].head(limit)
    out = records(rows)
    for r in out:
        r["reasons"] = top.get(r["vessel_id"], [])
        r["occupied_call_recent"] = r["vessel_id"] in called
        r["dark_now"] = r["vessel_id"] in dark
    return out


def _vessel_rows(name: str, vessel_id: str, col: str = "vessel_id") -> list[dict]:
    if not data.has(f"{name}.parquet"):
        return []
    df = data.table(name)
    return records(df[df[col] == vessel_id])


@app.get("/api/vessels/{vessel_id}")
def dossier(vessel_id: str) -> dict:
    """Everything known about one hull, with the evidence behind each point of its score."""
    scores = data.table("scores")
    row = scores[scores["vessel_id"] == vessel_id]
    if row.empty:
        raise HTTPException(404, f"no vessel {vessel_id}")
    vessel = records(row)[0]
    envelopes = data.table("gap_envelopes").set_index("gap_id")
    gaps = _vessel_rows("gaps", vessel_id)
    for g in gaps:
        if g["gap_id"] in envelopes.index:
            e = envelopes.loc[g["gap_id"]]
            g.update(
                {
                    "impossible": bool(e["impossible"]),
                    "vmax_kn": float(e["vmax_kn"]),
                    "area_km2": float(e["area_km2"]),
                }
            )
    encounters = _vessel_rows("encounters", vessel_id)
    names = scores.set_index("vessel_id")["name"].to_dict()
    for e in encounters:
        e["other_name"] = names.get(e["other_vessel_id"])
    predictions = data.optional_json("predictions.json", {})
    briefs = data.optional_json("briefs.json", {})
    sanctions = []
    if vessel.get("imo") or vessel.get("mmsi"):
        s = data.table("sanctions")
        hit = s[(s["imo"] == vessel.get("imo")) & s["imo"].notna()] if vessel.get("imo") else s.iloc[0:0]
        if hit.empty and vessel.get("mmsi"):
            hit = s[s["mmsis"].apply(lambda ms: str(vessel["mmsi"]) in list(ms))]
        sanctions = records(hit)
    return {
        "vessel": vessel,
        "identities": _vessel_rows("identities", vessel_id),
        "breakdown": records(
            data.table("score_breakdown")
            .query("vessel_id == @vessel_id")
            .sort_values("points", ascending=False)
        ),
        "gaps": gaps,
        "candidates": _vessel_rows("candidates", vessel_id),
        "dark_tracks": _vessel_rows("dark_tracks", vessel_id),
        "encounters": encounters,
        "loitering": _vessel_rows("loitering", vessel_id),
        "port_visits": _vessel_rows("port_visits", vessel_id),
        "occupied_calls": _vessel_rows("occupied_calls", vessel_id),
        "sanctions": sanctions,
        "risk_trend": predictions.get("risk_trend", {}).get(vessel_id, []),
        "dark_now": next((p for p in predictions.get("dark_now", []) if p["vessel_id"] == vessel_id), None),
        "next_dark_window": predictions.get("next_dark_window", {}).get(vessel_id),
        "brief": briefs.get(vessel_id),
        "pivots": _pivots(vessel),
        "caveats": CAVEATS,
    }


def _pivots(v: dict) -> list[dict]:
    links = [
        {
            "label": "Global Fishing Watch vessel viewer",
            "url": f"https://globalfishingwatch.org/map/vessel/{v['vessel_id']}",
        }
    ]
    if v.get("mmsi"):
        links.append(
            {
                "label": "MarineTraffic (MMSI)",
                "url": f"https://www.marinetraffic.com/en/ais/details/ships/mmsi:{v['mmsi']}",
            }
        )
    if v.get("imo"):
        links.append(
            {
                "label": "OpenSanctions search (IMO)",
                "url": f"https://www.opensanctions.org/search/?q=IMO{v['imo']}",
            }
        )
    return links


# --- map layers ----------------------------------------------------------------------------


@app.get("/api/events")
def events(
    type: str = Query(..., pattern="^(gap|encounter|loitering|port_visit)$"),
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    vessel_id: str | None = None,
) -> dict:
    table = {"gap": "gaps", "encounter": "encounters", "loitering": "loitering", "port_visit": "port_visits"}[
        type
    ]
    df = _between(data.table(table), "start", start, end)
    if vessel_id:
        df = df[df["vessel_id"] == vessel_id]
    features = []
    for r in records(df):
        if type == "gap":
            geometry = (
                {
                    "type": "LineString",
                    "coordinates": [[r["off_lon"], r["off_lat"]], [r["on_lon"], r["on_lat"]]],
                }
                if not r["open"]
                else {"type": "Point", "coordinates": [r["off_lon"], r["off_lat"]]}
            )
            fid = r["gap_id"]
        else:
            geometry = {"type": "Point", "coordinates": [r["lon"], r["lat"]]}
            fid = r.get("enc_id") or r.get("loit_id") or r.get("visit_id")
        features.append(_feature(geometry, {**r, "event_type": type}, fid))
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/sar")
def sar(start: date | None = Query(None, alias="from"), end: date | None = Query(None, alias="to")) -> dict:
    """Unmatched SAR detections (no AIS match), as compact GeoJSON points."""
    df = _between(data.table("sar"), "ts", start, end)
    return {
        "type": "FeatureCollection",
        "features": [
            _feature(
                {"type": "Point", "coordinates": [r.lon, r.lat]},
                {"ts": r.ts.isoformat(), "detections": int(r.detections)},
                r.sar_id,
            )
            for r in df.itertuples(index=False)
        ],
    }


@app.get("/api/gaps/{gap_id}/envelope")
def envelope(gap_id: str) -> dict:
    """The money shot: reachability ellipse, A and B, candidate detections and the dark track."""
    envelopes = data.table("gap_envelopes")
    e = envelopes[envelopes["gap_id"] == gap_id]
    if e.empty:
        raise HTTPException(404, f"no envelope for gap {gap_id} (open gaps have none)")
    e = e.iloc[0]
    gap = data.table("gaps").set_index("gap_id").loc[gap_id]
    features = [
        _feature(
            mapping(shapely_wkt.loads(e["polygon_wkt"])),
            {
                "role": "envelope", "vmax_kn": float(e["vmax_kn"]), "max_distance_km": float(e["max_distance_km"]),
                "area_km2": float(e["area_km2"]), "impossible": bool(e["impossible"]), "speed_class": e["speed_class"],
            },
        ),
        _feature({"type": "Point", "coordinates": [gap["off_lon"], gap["off_lat"]]}, {"role": "ais_off", "ts": gap["start"].isoformat()}),
        _feature({"type": "Point", "coordinates": [gap["on_lon"], gap["on_lat"]]}, {"role": "ais_on", "ts": gap["end"].isoformat()}),
    ]  # fmt: skip
    cands = data.table("candidates")
    for r in records(cands[cands["gap_id"] == gap_id]):
        features.append(
            _feature(
                {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                {"role": "candidate", **r},
                r["sar_id"],
            )
        )
    if data.has("dark_tracks.parquet"):
        tracks = data.table("dark_tracks")
        for r in records(tracks[tracks["gap_id"] == gap_id]):
            features.append(
                _feature({"type": "LineString", "coordinates": r["path"]}, {"role": "dark_track", **r})
            )
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "gap_id": gap_id,
            "vessel_id": gap["vessel_id"],
            "duration_h": float(gap["duration_h"]),
        },
    }


# --- insights ------------------------------------------------------------------------------


@app.get("/api/occupied-calls")
def occupied_calls(days: int = Query(RECENT_DAYS, ge=1, le=365)) -> dict:
    """The operational question: which hulls show evidence of calls at occupied ports recently?"""
    calls = data.table("occupied_calls")
    end = pd.Timestamp(window()["end"], tz="UTC") if window() else calls["ts"].max()
    recent = calls[calls["ts"] >= end - pd.Timedelta(days=days)]
    scores = data.table("scores").set_index("vessel_id")
    hulls = []
    for vid, g in recent.groupby("vessel_id"):
        s = scores.loc[vid] if vid in scores.index else None
        hulls.append(
            {
                "vessel_id": vid,
                "name": None if s is None else s["name"],
                "flag": None if s is None else s["flag"],
                "imo": None if s is None else s["imo"],
                "risk": None if s is None else int(s["risk"]),
                "listed": False if s is None else bool(s["listed"]),
                "ports": sorted(g["aoi"].dropna().unique().tolist()),
                "observed": int((g["provenance"] == "observed").sum()),
                "inferred": int((g["provenance"] == "inferred").sum()),
                "evidence": records(g),
            }
        )
    hulls.sort(key=lambda h: (-(h["risk"] or 0), h["name"] or ""))
    return {"days": days, "window_end": end.isoformat(), "hulls": hulls}


@app.get("/api/highlights")
def highlights() -> list[dict]:
    return data.json("highlights.json")


@app.get("/api/predictions")
def predictions() -> dict:
    return data.json("predictions.json")
