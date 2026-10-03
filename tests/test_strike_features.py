"""Air-raid danger features: block accounting and, above all, no leakage from the future.

The leakage test builds features twice, from the full history and from the history as it
existed at a cutoff (alerts not yet started are removed, alerts still running are cut at the
cutoff, VIINA events after the cutoff day are removed), and requires identical features for every
issue time up to the cutoff. That is the train/serve parity rule of CLAUDE.md §16.4.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from history import regions, sirens, viina
from predict.strike import features as F

FIX = Path(__file__).parent / "fixtures" / "history"
ORIGIN = pd.Timestamp("2024-01-01", tz="UTC")


def _geoms():
    return regions.parse_boundaries((FIX / "ukr_adm1_simplified.geojson").read_text())


def _alert(iso, start, end):
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    return {"iso": iso, "start": s, "end": e, "naive": False, "minutes": (e - s).total_seconds() / 60}


def test_alert_minutes_split_across_blocks():
    alerts = pd.DataFrame([_alert("UA-63", "2024-01-01 05:00", "2024-01-01 07:30")])
    minutes, starts, active_end, _ = F.alert_grid(alerts, ["UA-63"], 4, ORIGIN)
    assert minutes[0, 0] == 60 and minutes[0, 1] == 90
    assert starts[0].tolist() == [1, 0, 0, 0]
    assert active_end[0].tolist() == [True, False, False, False]


def test_features_have_no_leakage_from_after_the_issue_time():
    alerts = sirens.normalise(FIX / "sirens_volunteer_sample.csv")
    events = pd.concat(
        [
            viina.normalise_csv(pd.read_csv(FIX / f"viina_1pd_{y}_sample.csv", low_memory=False))
            for y in (2024, 2026)
        ]
    )
    weather = pd.DataFrame(
        columns=["iso", "date", "cloud_cover_mean", "precipitation_sum", "wind_speed_10m_max"]
    )
    origin = pd.Timestamp("2024-01-01", tz="UTC")
    end = pd.Timestamp("2026-09-30", tz="UTC")
    cutoff = pd.Timestamp("2025-06-15 12:00", tz="UTC")

    full = F.build(alerts, events, weather, _geoms(), end, origin=origin, with_target=False)
    known = alerts[alerts["start"] < cutoff].copy()
    known["end"] = known["end"].clip(upper=cutoff)
    seen = events[events["date"] < cutoff.tz_localize(None).normalize()]
    past = F.build(
        known,
        seen,
        weather,
        _geoms(),
        cutoff - pd.Timedelta(hours=F.BLOCK_H),
        origin=origin,
        with_target=False,
    )

    cols = [c for c in F.FEATURES if c not in ("cloud", "precip", "wind")]
    a = full[full["issue"] <= cutoff].set_index(["iso", "issue"])[cols].sort_index()
    b = past.set_index(["iso", "issue"])[cols].sort_index()
    assert len(b) > 1000 and a.index.equals(b.index)
    np.testing.assert_allclose(a.to_numpy(dtype=float), b.to_numpy(dtype=float), rtol=1e-5, atol=1e-3)


def test_targets_describe_the_next_block():
    alerts = pd.DataFrame([_alert("UA-63", "2024-01-01 13:00", "2024-01-01 14:00")])
    events = viina.normalise_csv(pd.read_csv(FIX / "viina_1pd_2026_sample.csv", low_memory=False)).iloc[:0]
    weather = pd.DataFrame(
        columns=["iso", "date", "cloud_cover_mean", "precipitation_sum", "wind_speed_10m_max"]
    )
    df = F.build(alerts, events, weather, _geoms(), pd.Timestamp("2024-01-02", tz="UTC"), origin=ORIGIN)
    kh = df[df["iso"] == "UA-63"].set_index("issue")
    # Issued at 12:00 about 12:00–18:00: the alert starts then.
    assert kh.loc[pd.Timestamp("2024-01-01 12:00", tz="UTC"), "y_new"] == 1
    assert kh.loc[pd.Timestamp("2024-01-01 06:00", tz="UTC"), "y_new"] == 0
    assert kh.loc[pd.Timestamp("2024-01-01 18:00", tz="UTC"), "m_1"] == 60
