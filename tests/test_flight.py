"""Flight destination model: segmentation and labels, train/serve feature parity, time-respecting
priors (no leakage), candidates, winds aloft and predicted paths, on real archived flights.

`adsb_archive_sample.csv.gz` holds every position of four military aircraft on 2 Oct 2026 (see
fixtures/README.md); `ourairports_flight_sample.csv` the airfields around them.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from live.milclass import classify
from live.tracks import AirfieldIndex
from predict.flight import features as F
from predict.flight.flights import endurance_table, segment
from predict.flight.serve import (
    FlightModel,
    Predictor,
    great_circle,
    parse_winds,
    predicted_path,
    pressure_level,
    tailwind,
)
from predict.flight.train import build
from reference.aircraftdb import load_from as load_register
from reference.airfields import parse as parse_airfields
from reference.icao_ranges import load_from as load_ranges

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def fields():
    return parse_airfields(FIX / "history" / "ourairports_flight_sample.csv")


@pytest.fixture(scope="module")
def flights(fields):
    pos = pd.read_csv(FIX / "history" / "adsb_archive_sample.csv.gz")
    pos["ts"] = pd.to_datetime(pos["ts"], utc=True, format="ISO8601")
    return segment(pos, AirfieldIndex(fields))


@pytest.fixture(scope="module")
def classifier():
    register = load_register(FIX / "reference" / "basic_ac_db_sample.json.gz")
    ranges = load_ranges(FIX / "reference" / "flags.js")
    return lambda ac: classify(ac, register, ranges)


def by(flights, callsign):
    return [f for f in flights if f.callsign == callsign]


def test_c17_landing_short_of_coverage_is_labelled_at_the_field_ahead(flights):
    # Coverage ends 8 km east of Ramstein at 480 m, without a reported track, right over a
    # hospital helipad; the label must be the runway ahead, not the helipad below.
    last = by(flights, "RCH153")[-1]
    assert last.landing == "ETAR"
    assert last.points["track"].iloc[-1] != last.points["track"].iloc[-1]  # NaN: track not reported


def test_local_circuits_return_to_base(flights):
    tutor = by(flights, "UAU967")
    assert len(tutor) == 3 and all(f.origin == "EGDM" and f.landing == "EGDM" for f in tutor)


def test_helicopter_and_multileg_transport(flights):
    assert {f.landing for f in by(flights, "GASTN41")} == {"LKKB"}
    legs = [(f.origin, f.landing) for f in by(flights, "GAF148")]
    assert ("ETNW", "EYKA") in legs and ("EYKA", "EDDN") in legs


def test_endurance_table_has_a_global_default(flights):
    table = endurance_table(flights)
    assert table["*"] > 60 and all(v > 0 for v in table.values())


def test_kinematics_are_causal_and_sane(flights):
    fl = by(flights, "RCH153")[-1]
    pts = fl.points
    ts = pts["ts"].astype("int64").to_numpy() / 1e9
    arr = [pts[c].to_numpy(dtype=float) for c in ("lat", "lon", "alt_m", "gs_kn", "track")]
    i = len(ts) - 1
    kin = F.kinematics(ts, *arr, i)
    assert kin["vrate_fpm"] < -300  # on approach
    assert 200 <= kin["track"] <= 330  # heading west towards Ramstein, derived from positions
    # Causal: appending future points must not change the state at i.
    longer = [np.append(a, a[-1] + 1) for a in arr]
    assert F.kinematics(np.append(ts, ts[-1] + 60), *longer, i) == kin


def test_priors_never_contain_the_flight_being_predicted(flights, fields, classifier):
    endurance = endurance_table(flights)
    rows, priors = build(flights, F.Fields(fields), classifier, endurance)
    tutor = rows[(rows["hex"] == "400ee1") & (rows["cand"] == "EGDM")]
    tutor_ids = sorted(tutor.groupby("flight")["hex_prior_n"].max().items())
    # The Tutor's three flights: the first knows no earlier landing of this airframe, the second one, the third two.
    assert [n for _, n in tutor_ids] == [0, 1, 2]
    assert sum(priors.by_hex["400ee1"].values()) == 3  # serving starts from every flight
    assert set(rows["y"]) == {0, 1}


def test_truth_is_usually_among_candidates(flights, fields, classifier):
    rows, _ = build(flights, F.Fields(fields), classifier, endurance_table(flights))
    assert rows.groupby("snap")["y"].max().mean() > 0.8


def test_winds_aloft_fixture_and_geometry():
    w = parse_winds(json.loads((FIX / "live" / "openmeteo_winds_aloft.json").read_text()))
    assert set(w) == {925, 850, 700, 500, 300, 250} and len(w[250][0]) == 24
    assert pressure_level(0) == 925 and pressure_level(10_500) == 250 and pressure_level(5_600) == 500
    assert tailwind(50, 270, 90) == pytest.approx(50)  # westerly wind, flying east
    assert tailwind(50, 270, 270) == pytest.approx(-50)
    gc = great_circle(7.6, 49.4, 27.9, 43.2, 10)
    assert gc[0] == pytest.approx([7.6, 49.4]) and gc[-1] == pytest.approx([27.9, 43.2])


def test_predicted_path_descends_to_the_field(fields):
    etar = next(a for a in fields if a.ident == "ETAR")
    path = predicted_path(8.5, 49.6, 9000, etar, 70.0, 300)
    assert path[0][2] == 9000 or path[0][2] < 9000  # starts at or below cruise
    assert path[-1][2] == pytest.approx(etar.elevation_m, abs=1)
    assert all(b[3] > a[3] for a, b in zip(path, path[1:], strict=False))


def test_live_prediction_uses_the_training_features(flights, fields, classifier):
    """Train a tiny ranker on the sample, then predict from a live-format flight (track store
    rows) and check the C-17's approach ranks Ramstein highly."""
    endurance = endurance_table(flights)
    ff = F.Fields(fields)
    rows, priors = build(flights, ff, classifier, endurance)
    model = HistGradientBoostingClassifier(max_iter=50, random_state=0).fit(rows[F.FEATURES], rows["y"])
    fm = FlightModel("test", {"beats_baselines": None}, model, endurance, priors)
    pred = Predictor(fm, ff, {a.ident: a for a in fields})
    fl = by(flights, "RCH153")[-1]
    cut = fl.points.iloc[: len(fl.points) - 40]  # ~3 minutes before coverage ends
    live_rows = [
        [
            int(r.ts.value // 1_000_000),
            r.lon,
            r.lat,
            r.alt_m,
            r.gs_kn,
            None if pd.isna(r.track) else r.track,
            r.ground,
        ]
        for r in cut.itertuples()
    ]
    entity = {
        "id": "aircraft:ae4d66",
        "props": {
            "icao24": "ae4d66",
            "type": "C17",
            "callsign": "RCH153",
            "role": "airlift",
            "airframe": "fixed_wing",
            "state_code": "us",
        },
    }
    p = pred.predict(entity, {"points": live_rows, "origin": None}, live_rows[-1][0] + 1000, paths=True)
    assert p is not None
    assert "ETAR" in [d["ident"] for d in p.destinations]
    assert sum(d["p"] for d in p.destinations) <= 1.0001
    assert all(d["path"][-1][:2] == pytest.approx([d["lon"], d["lat"]], abs=1e-3) for d in p.destinations)
