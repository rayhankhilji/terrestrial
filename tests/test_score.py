"""Risk score rules (CLAUDE.md §7).

Synthetic, minimal tables exercise each rule and cap; OpenSanctions rows come from the real
verbatim sample fixture.
"""

from pathlib import Path

import pandas as pd
import pytest

from pipeline.config import WEIGHTS as W
from pipeline.opensanctions import normalise as normalise_sanctions
from pipeline.score import Tables, score

T0 = pd.Timestamp("2026-08-01", tz="UTC")
SAMPLE = Path(__file__).parent / "fixtures" / "opensanctions" / "maritime_sample.csv"


def _sanctions():
    return normalise_sanctions(SAMPLE)


def _listed_imo(sanctions, shadow=None, detained_only=False):
    if detained_only:
        rows = sanctions[sanctions["detained"] & ~sanctions["sanctioned"] & sanctions["imo"].notna()]
    else:
        rows = sanctions[sanctions["sanctioned"] & sanctions["imo"].notna()]
        if shadow is not None:
            rows = rows[rows["shadow_fleet"] == shadow]
    return rows.iloc[0]["imo"]


def _gap(gid, vid, start, hours, off=(32.0, 43.0), on=(32.5, 43.2), open_=False):
    return {
        "gap_id": gid, "vessel_id": vid, "start": start, "end": start + pd.Timedelta(hours=hours),
        "duration_h": hours, "off_lon": off[0], "off_lat": off[1], "on_lon": on[0], "on_lat": on[1],
        "distance_km": 45.0, "implied_speed_kn": 1.0, "open": open_,
    }  # fmt: skip


def _tables(vessels, gaps=(), envelopes=(), candidates=(), encounters=(), visits=(), identities=()):
    s = _sanctions()
    return Tables(
        vessels=pd.DataFrame(vessels, columns=["vessel_id", "name", "flag", "imo", "mmsi", "vessel_type"]),
        identities=pd.DataFrame(
            list(identities), columns=["vessel_id", "name", "flag", "mmsi", "imo", "date_from", "date_to"]
        ),
        gaps=pd.DataFrame(list(gaps), columns=list(_gap("x", "x", T0, 1).keys())),
        envelopes=pd.DataFrame(list(envelopes), columns=["gap_id", "impossible"]),
        candidates=pd.DataFrame(
            list(candidates),
            columns=[
                "gap_id",
                "vessel_id",
                "sar_id",
                "ts",
                "consistent",
                "in_aoi",
                "occupied_aoi",
                "gap_consistent_candidates",
            ],
        ),
        encounters=pd.DataFrame(
            list(encounters), columns=["enc_id", "vessel_id", "other_vessel_id", "start"]
        ),
        port_visits=pd.DataFrame(
            list(visits), columns=["visit_id", "vessel_id", "start", "aoi", "occupied_aoi"]
        ),
        sanctions=s,
    )


def _points(breakdown, vid, signal):
    return int(breakdown[(breakdown.vessel_id == vid) & (breakdown.signal == signal)]["points"].sum())


def test_listing_and_gap_cap():
    s = _sanctions()
    imo = _listed_imo(s)
    gaps = [_gap(f"g{i}", "v1", T0 + pd.Timedelta(days=i), 20) for i in range(5)]
    scores, bd = score(_tables([("v1", "ALPHA", "PA", imo, "1", "tanker")], gaps=gaps))
    assert _points(bd, "v1", "sanctioned") == W.sanctioned
    assert _points(bd, "v1", "gap") == W.gap_cap  # 5 gaps, capped at 3 × 10
    assert scores.loc[0, "listed"]


def test_detention_alone_is_not_a_sanction():
    s = _sanctions()
    imo = _listed_imo(s, detained_only=True)
    scores, bd = score(_tables([("v1", "BRAVO", "PA", imo, "1", "cargo")]))
    assert _points(bd, "v1", "sanctioned") == 0
    assert _points(bd, "v1", "psc_detention") == W.psc_detention
    assert not scores.loc[0, "listed"]


def test_covert_port_call_requires_time_consistent_detection_in_occupied_aoi():
    gaps = [_gap("g1", "v1", T0, 40, off=(32.0, 43.0), on=(33.0, 43.5))]
    cands = [
        ("g1", "v1", "s-date-only", T0 + pd.Timedelta(hours=2), False, "Sevastopol", True, 1),
        ("g1", "v1", "s-sea", T0 + pd.Timedelta(hours=10), True, None, False, 2),
    ]
    _, bd = score(_tables([("v1", "C", "RU", None, "2", "cargo")], gaps=gaps, candidates=cands))
    assert _points(bd, "v1", "verified_dark") == W.verified_dark
    assert _points(bd, "v1", "covert_port_call") == 0  # the occupied-port hit was not time-consistent

    cands.append(("g1", "v1", "s-sev", T0 + pd.Timedelta(hours=20), True, "Sevastopol", True, 2))
    _, bd = score(_tables([("v1", "C", "RU", None, "2", "cargo")], gaps=gaps, candidates=cands))
    assert _points(bd, "v1", "covert_port_call") == W.covert_port_call
    row = bd[bd.signal == "covert_port_call"].iloc[0]
    assert row.provenance == "inferred" and "s-sev" in row.evidence_ref


def test_gap_near_occupied_impossible_and_encounters_with_listed_partner():
    s = _sanctions()
    listed = _listed_imo(s, shadow=True)
    gaps = [
        _gap("g1", "v1", T0, 12, off=(33.40, 44.55), on=(31.0, 43.0))
    ]  # switched off ~10 km from Sevastopol
    encs = [("e1", "v1", "v2", T0), ("e2", "v1", "v3", T0), ("e3", "v1", "v2", T0)]
    _, bd = score(
        _tables(
            [
                ("v1", "D", "RU", None, "3", "tanker"),
                ("v2", "SHADOW", "GA", listed, "4", "tanker"),
                ("v3", "X", "TR", None, "5", "cargo"),
            ],
            gaps=gaps,
            envelopes=[("g1", True)],
            encounters=encs,
        )
    )
    assert _points(bd, "v1", "gap_near_occupied") == W.gap_near_occupied
    assert _points(bd, "v1", "impossible_gap") == W.impossible_gap
    assert _points(bd, "v1", "encounter") == W.encounter_cap
    assert _points(bd, "v1", "encounter_sanctioned") == W.encounter_sanctioned


def test_identity_changes_in_window_and_total_cap():
    s = _sanctions()
    imo = _listed_imo(s)
    gaps = [_gap(f"g{i}", "v1", T0 + pd.Timedelta(days=i), 20, off=(33.45, 44.6)) for i in range(4)]
    idents = [
        (
            "v1",
            "OLD",
            "PA",
            "1",
            imo,
            T0 - pd.Timedelta(days=400),
            T0 - pd.Timedelta(days=200),
        ),  # before window: ignored
        ("v1", "MID", "PA", "1", imo, T0 - pd.Timedelta(days=200), T0 + pd.Timedelta(days=3)),
        ("v1", "NEW", "CM", "1", imo, T0 + pd.Timedelta(days=3), T0 + pd.Timedelta(days=60)),
    ]
    visits = [("p1", "v1", T0 + pd.Timedelta(days=5), "Berdyansk", True)]
    scores, bd = score(
        _tables([("v1", "NEW", "CM", imo, "1", "cargo")], gaps=gaps, identities=idents, visits=visits)
    )
    assert _points(bd, "v1", "identity_change") == W.identity_change_each  # only the in-window change
    assert _points(bd, "v1", "occupied_port_visit") == W.occupied_port_visit
    assert scores.loc[0, "raw_points"] > W.total_cap
    assert scores.loc[0, "risk"] == W.total_cap


@pytest.mark.parametrize("as_of_days", [0, 2])
def test_as_of_ignores_future_evidence(as_of_days):
    gaps = [_gap(f"g{i}", "v1", T0 + pd.Timedelta(days=i), 20) for i in range(3)]
    _, bd = score(
        _tables([("v1", "E", "PA", None, "1", "cargo")], gaps=gaps), as_of=T0 + pd.Timedelta(days=as_of_days)
    )
    assert _points(bd, "v1", "gap") == W.gap_each * (as_of_days + 1)
