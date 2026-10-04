"""Stage 2 — normalise: cached GFW / OpenSanctions responses → parquet tables (CLAUDE.md §5).

Field names follow GFW API v3 as recorded in the official client's response fixtures
(github.com/GlobalFishingWatch/gfw-api-python-client, tests/fixtures; copies in
tests/fixtures/gfw_client/). GFW returns some numbers as strings (gap.onPosition, distances),
so every numeric field goes through `_num`. Any missing required field raises with the event id:
a schema change must stop the pipeline, not silently drop rows.

Outputs (data/processed/):
  vessels      vessel_id, name, flag, imo, mmsi, vessel_type
  identities   vessel_id, name, flag, mmsi, imo, callsign, date_from, date_to, source
  gaps         gap_id, vessel_id, start, end, duration_h, off_lon, off_lat, on_lon, on_lat,
               distance_km, implied_speed_kn, open, intentional
  encounters   enc_id, vessel_id, other_vessel_id, start, end, lon, lat, median_distance_km,
               median_speed_kn, duration_h
  loitering    loit_id, vessel_id, start, end, lon, lat, duration_h, distance_km, avg_speed_kn
  port_visits  visit_id, vessel_id, start, end, lon, lat, port, port_flag, aoi, occupied_aoi
  sar          sar_id, ts, lon, lat, detections                (unmatched detections only)
  sanctions    OpenSanctions maritime vessels (pipeline/opensanctions.py)
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date

import numpy as np
import pandas as pd

from pipeline import aoi, opensanctions
from pipeline.config import GFW_DATASETS, GFW_SAR_DATASET, RAW_DIR
from pipeline.fetch import event_pages, window_dir
from pipeline.io import read_json, write_table

log = logging.getLogger("terrestrial")


class SchemaError(ValueError):
    pass


def _num(value, what: str, eid: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{what} is not a number ({value!r}) in event {eid}") from exc


def _req(obj: dict, key: str, eid: str):
    if key not in obj or obj[key] is None:
        raise SchemaError(f"event {eid} has no {key!r}; has the GFW schema changed? keys: {sorted(obj)}")
    return obj[key]


def _ts(value) -> pd.Timestamp | None:
    return pd.Timestamp(value).tz_convert("UTC") if value else None


def _vessel(e: dict, eid: str) -> dict:
    v = _req(e, "vessel", eid)
    return {"vessel_id": _req(v, "id", eid), "name": v.get("name"), "flag": v.get("flag"),
            "mmsi": v.get("ssvid"), "vessel_type": (v.get("type") or "other").lower()}  # fmt: skip


def gaps(entries: list[dict]) -> pd.DataFrame:
    rows = []
    for e in entries:
        eid = _req(e, "id", "?")
        g = _req(e, "gap", eid)
        off = _req(g, "offPosition", eid)
        on = g.get("onPosition")
        start, end = _ts(_req(e, "start", eid)), _ts(e.get("end"))
        is_open = end is None or not on
        rows.append(
            {
                "gap_id": eid,
                "vessel_id": _vessel(e, eid)["vessel_id"],
                "start": start,
                "end": end,
                "duration_h": _num(g["durationHours"], "gap.durationHours", eid) if g.get("durationHours") is not None else ((end - start).total_seconds() / 3600 if end else np.nan),
                "off_lon": _num(off["lon"], "gap.offPosition.lon", eid),
                "off_lat": _num(off["lat"], "gap.offPosition.lat", eid),
                "on_lon": _num(on["lon"], "gap.onPosition.lon", eid) if on else np.nan,
                "on_lat": _num(on["lat"], "gap.onPosition.lat", eid) if on else np.nan,
                "distance_km": _num(g["distanceKm"], "gap.distanceKm", eid) if g.get("distanceKm") is not None else np.nan,
                "implied_speed_kn": _num(g["impliedSpeedKnots"], "gap.impliedSpeedKnots", eid) if g.get("impliedSpeedKnots") is not None else np.nan,
                "open": bool(is_open),
                "intentional": bool(g.get("intentionalDisabling", True)),
            }
        )  # fmt: skip
    return pd.DataFrame(rows)


def encounters(entries: list[dict]) -> pd.DataFrame:
    rows = []
    for e in entries:
        eid = _req(e, "id", "?")
        enc = _req(e, "encounter", eid)
        other = _req(enc, "vessel", eid)
        pos = _req(e, "position", eid)
        start, end = _ts(_req(e, "start", eid)), _ts(e.get("end"))
        rows.append(
            {
                "enc_id": eid,
                "vessel_id": _vessel(e, eid)["vessel_id"],
                "other_vessel_id": _req(other, "id", eid),
                "other_name": other.get("name"),
                "start": start,
                "end": end,
                "lon": _num(pos["lon"], "position.lon", eid),
                "lat": _num(pos["lat"], "position.lat", eid),
                "median_distance_km": _num(enc.get("medianDistanceKilometers", np.nan), "encounter.medianDistanceKilometers", eid),
                "median_speed_kn": _num(enc.get("medianSpeedKnots", np.nan), "encounter.medianSpeedKnots", eid),
                "duration_h": (end - start).total_seconds() / 3600 if end else np.nan,
            }
        )  # fmt: skip
    return pd.DataFrame(rows)


def loitering(entries: list[dict]) -> pd.DataFrame:
    rows = []
    for e in entries:
        eid = _req(e, "id", "?")
        lo = _req(e, "loitering", eid)
        pos = _req(e, "position", eid)
        rows.append(
            {
                "loit_id": eid,
                "vessel_id": _vessel(e, eid)["vessel_id"],
                "start": _ts(_req(e, "start", eid)),
                "end": _ts(e.get("end")),
                "lon": _num(pos["lon"], "position.lon", eid),
                "lat": _num(pos["lat"], "position.lat", eid),
                "duration_h": _num(lo.get("totalTimeHours", np.nan), "loitering.totalTimeHours", eid),
                "distance_km": _num(lo.get("totalDistanceKm", np.nan), "loitering.totalDistanceKm", eid),
                "avg_speed_kn": _num(lo.get("averageSpeedKnots", np.nan), "loitering.averageSpeedKnots", eid),
            }
        )  # fmt: skip
    return pd.DataFrame(rows)


def port_visits(entries: list[dict]) -> pd.DataFrame:
    rows = []
    for e in entries:
        eid = _req(e, "id", "?")
        pv = _req(e, "port_visit", eid)
        anchorage = pv.get("intermediateAnchorage") or pv.get("startAnchorage") or {}
        pos = e.get("position") or anchorage
        lon, lat = (
            _num(_req(pos, "lon", eid), "position.lon", eid),
            _num(_req(pos, "lat", eid), "position.lat", eid),
        )
        area = aoi.containing(lon, lat)[0]
        rows.append(
            {
                "visit_id": eid,
                "vessel_id": _vessel(e, eid)["vessel_id"],
                "start": _ts(_req(e, "start", eid)),
                "end": _ts(e.get("end")),
                "lon": lon,
                "lat": lat,
                "port": anchorage.get("name"),
                "port_flag": anchorage.get("flag"),
                "confidence": pv.get("confidence"),
                "aoi": area,
                "occupied_aoi": bool(area and aoi.AOI_BY_NAME[area].occupied_ua),
            }
        )
    return pd.DataFrame(rows)


def sar(reports: list[dict]) -> pd.DataFrame:
    """4Wings report bodies: {entries: [{"<dataset:version>": [{date, lat, lon, detections}]}]}."""
    rows = []
    for body in reports:
        for entry in _req(body, "entries", "4wings report"):
            for dataset, items in entry.items():
                if not dataset.startswith(GFW_SAR_DATASET.split(":")[0]):
                    raise SchemaError(f"4Wings entry for unexpected dataset {dataset}")
                for r in items:
                    ts = pd.Timestamp(_req(r, "date", dataset), tz="UTC")
                    lon, lat = _num(r["lon"], "lon", dataset), _num(r["lat"], "lat", dataset)
                    key = f"{ts.isoformat()}|{lon:.4f}|{lat:.4f}"
                    rows.append(
                        {
                            "sar_id": hashlib.sha1(key.encode()).hexdigest()[:16],
                            "ts": ts,
                            "lon": lon,
                            "lat": lat,
                            "detections": int(_num(r.get("detections", 1), "detections", dataset)),
                        }  # fmt: skip
                    )
    return pd.DataFrame(rows, columns=["sar_id", "ts", "lon", "lat", "detections"]).drop_duplicates("sar_id")


def identities(batches: list[dict]) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Self-reported AIS identities (name / flag / MMSI over time) and the shiptype per vessel id."""
    rows, types = [], {}
    for body in batches:
        for entry in _req(body, "entries", "vessels response"):
            for info in entry.get("combinedSourcesInfo") or []:
                st = info.get("shiptypes") or []
                if st:
                    types[info["vesselId"]] = {
                        "vessel_type": max(st, key=lambda s: s.get("yearTo") or 0)["name"].lower()
                    }
            for s in entry.get("selfReportedInfo") or []:
                rows.append(
                    {
                        "vessel_id": _req(s, "id", "selfReportedInfo"),
                        "name": s.get("shipname"),
                        "flag": s.get("flag"),
                        "mmsi": s.get("ssvid"),
                        "imo": s.get("imo"),
                        "callsign": s.get("callsign"),
                        "date_from": _ts(s.get("transmissionDateFrom")),
                        "date_to": _ts(s.get("transmissionDateTo")),
                        "source": "GFW identity",
                    }
                )
    cols = ["vessel_id", "name", "flag", "mmsi", "imo", "callsign", "date_from", "date_to", "source"]
    return pd.DataFrame(rows, columns=cols), types


def vessels(events: dict[str, list[dict]], idents: pd.DataFrame, types: dict[str, dict]) -> pd.DataFrame:
    """One row per vessel id seen in any event, identity from its latest self-reported record."""
    seen: dict[str, dict] = {}
    for entries in events.values():
        for e in entries:
            v = _vessel(e, e.get("id", "?"))
            seen.setdefault(v["vessel_id"], v)
            other = (e.get("encounter") or {}).get("vessel")
            if other and other.get("id"):
                seen.setdefault(other["id"], {"vessel_id": other["id"], "name": other.get("name"), "flag": other.get("flag"),
                                              "mmsi": other.get("ssvid"), "vessel_type": (other.get("type") or "other").lower()})  # fmt: skip
    latest = idents.sort_values("date_to").groupby("vessel_id").last() if not idents.empty else pd.DataFrame()
    rows = []
    for vid, v in seen.items():
        row = {**v, "imo": None, **types.get(vid, {})}
        if vid in latest.index:
            li = latest.loc[vid]
            row.update({k: li[k] or row.get(k) for k in ("name", "flag", "mmsi", "imo")})
        rows.append(row)
    return pd.DataFrame(rows, columns=["vessel_id", "name", "flag", "imo", "mmsi", "vessel_type"])


def run(start: date, end: date) -> None:
    root = window_dir(start, end)
    events = {kind: [e for page in event_pages(root, kind) for e in page["entries"]] for kind in GFW_DATASETS}
    tables = {
        "gaps": gaps(events["gaps"]),
        "encounters": encounters(events["encounters"]),
        "loitering": loitering(events["loitering"]),
        "port_visits": port_visits(events["port_visits"]),
        "sar": sar([read_json(p) for p in sorted((root / "sar").glob("*.json"))]),
    }
    idents, types = identities([read_json(p) for p in sorted((root / "vessels").glob("batch_*.json"))])
    tables["identities"] = idents
    tables["vessels"] = vessels(events, idents, types)
    tables["sanctions"] = opensanctions.normalise(RAW_DIR / "opensanctions" / "maritime.csv")
    for name, df in tables.items():
        log.info("  %-12s %6d rows", name, len(df))
        write_table(df, name, required=name in ("gaps", "vessels", "sanctions"))


def stage(start: date, end: date, args) -> None:
    run(start, end)
