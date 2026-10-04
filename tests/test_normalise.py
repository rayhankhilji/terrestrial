"""GFW normalisation against the official client's recorded response fixtures
(tests/fixtures/gfw_client/, from GlobalFishingWatch/gfw-api-python-client)."""

import json
from pathlib import Path

import pandas as pd
import pytest

from pipeline import normalise as N
from pipeline.config import GFW_SAR_DATASET

FIX = Path(__file__).parent / "fixtures" / "gfw_client"


def _event():
    return json.loads((FIX / "event_item.json").read_text())


def test_gap_numbers_given_as_strings_are_parsed():
    g = N.gaps([_event()]).iloc[0]
    assert g["gap_id"] == "3ca9b73aee21fbf278a636709e0f8f03"
    assert g["on_lat"] == pytest.approx(-14.4101933333) and g["distance_km"] == pytest.approx(
        55.5109, rel=1e-4
    )
    assert g["open"]  # the fixture's end is "": an open gap
    assert g["duration_h"] == pytest.approx(15.45)


def test_encounter_loitering_port_visit():
    e = _event()
    enc = N.encounters([e]).iloc[0]
    assert enc["other_vessel_id"] == "2d971d23e-e7a1-1b04-5298-a53e51a17ac5" and enc[
        "median_distance_km"
    ] == pytest.approx(0.034)
    lo = N.loitering([e]).iloc[0]
    assert lo["duration_h"] == pytest.approx(287.96, rel=1e-3)
    pv = N.port_visits([e]).iloc[0]
    assert pv["aoi"] is None and not pv["occupied_aoi"]  # Northern Ireland is no Black Sea port


def test_missing_required_field_raises():
    e = _event()
    del e["gap"]["offPosition"]
    with pytest.raises(N.SchemaError, match="offPosition"):
        N.gaps([e])


def test_identities_and_vessels():
    body = {"entries": [json.loads((FIX / "vessel_item.json").read_text())]}
    idents, types = N.identities([body])
    assert set(idents["flag"]) >= {"RUS", "PLW"} and idents["imo"].eq("9076260").all()
    assert types["da1cd7e1b-b8d0-539c-6581-2b3df8d0a6af"]["vessel_type"] == "carrier"
    v = N.vessels({"gaps": [_event()]}, idents, types)
    assert {"126570026-6217-2a10-f383-a42f66f78ea7", "2d971d23e-e7a1-1b04-5298-a53e51a17ac5"} <= set(
        v["vessel_id"]
    )


def test_sar_rows():
    item = json.loads((FIX / "fourwings_report_item.json").read_text())
    df = N.sar([{"entries": [{GFW_SAR_DATASET: [item]}]}])
    assert (
        len(df) == 1
        and df.iloc[0]["ts"] == pd.Timestamp("2022-01-13 04:00", tz="UTC")
        and df.iloc[0]["detections"] == 1
    )
    with pytest.raises(N.SchemaError):
        N.sar([{"entries": [{"public-global-fishing-effort:v3.0": [item]}]}])
