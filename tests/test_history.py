"""Historical datasets: region join keys and normalisers, on verbatim samples of the real files."""

import json
from pathlib import Path

import pandas as pd
import pytest

from history import regions, sirens, viina, weather

FIX = Path(__file__).parent / "fixtures" / "history"


def test_boundaries_cover_every_region_with_an_interior_point():
    geoms = regions.parse_boundaries((FIX / "ukr_adm1_simplified.geojson").read_text())
    assert set(geoms) == set(regions.BY_ISO)
    for g in geoms.values():
        assert g.geometry.contains(g.geometry.representative_point())
    nb = regions.neighbours(geoms)
    assert "UA-32" in nb["UA-30"]  # Kyiv City sits inside Kyiv oblast
    assert "UA-63" in nb["UA-59"] and "UA-46" not in nb["UA-63"]  # Sumy–Kharkiv border; Lviv is far


def test_sirens_sample_normalises_with_every_region_mapped():
    alerts = sirens.normalise(FIX / "sirens_volunteer_sample.csv")
    assert alerts["iso"].notna().all() and alerts["iso"].isin(regions.BY_ISO).all()
    assert (alerts["end"] > alerts["start"]).all() and (alerts["minutes"] > 0).all()
    assert str(alerts["start"].dt.tz) == "UTC"


def test_sirens_reject_unknown_region(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "region,started_at,finished_at,naive\nAtlantis oblast,2024-01-01 00:00:00+00:00,2024-01-01 01:00:00+00:00,False\n"
    )
    with pytest.raises(ValueError, match="unmapped"):
        sirens.normalise(bad)


@pytest.mark.parametrize("year", [2024, 2026])
def test_viina_sample_keeps_missing_labels_missing(year):
    raw = pd.read_csv(FIX / f"viina_1pd_{year}_sample.csv", low_memory=False)
    events = viina.normalise_csv(raw)
    assert events["iso"].isin(regions.BY_ISO).all()
    kept = raw[raw["ADM1_NAME"].notna()]  # events without a region are dropped
    assert len(events) == len(kept)
    # Missing source labels stay missing instead of becoming False.
    assert events["uav"].isna().sum() == kept["t_uav_b"].isna().sum()
    expected = (kept["t_airstrike_b"].fillna(0) + kept["t_aad_b"].fillna(0) > 0).to_numpy()
    assert (events["air_attack"].to_numpy() == expected).all()


def test_weather_archive_parse():
    payload = json.loads((FIX / "openmeteo_archive_sample.json").read_text())
    df = weather.parse(payload, ["UA-30", "UA-63"])
    assert list(df["iso"].unique()) == ["UA-30", "UA-63"]
    assert len(df) == 6 and df[weather.VARIABLES].notna().all().all()
    with pytest.raises(ValueError):
        weather.parse(payload, ["UA-30"])
