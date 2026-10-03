"""Match + dark-track algorithm tests.

The inputs here are synthetic geometry chosen to exercise the rules (like the ellipse tests),
not data: real-data behaviour is covered by tests on saved API fixtures.
"""

import pandas as pd
import pytest

from pipeline import geo
from pipeline.envelope import envelopes
from pipeline.match import match
from pipeline.tracks import dark_track

T0 = pd.Timestamp("2026-08-10 06:00", tz="UTC")
A = (31.0, 43.5)
B = (33.0, 43.5)  # ~161 km east of A
HOURS = 30.0


@pytest.fixture
def gap():
    return pd.DataFrame(
        [
            {
                "gap_id": "g1",
                "vessel_id": "v1",
                "start": T0,
                "end": T0 + pd.Timedelta(hours=HOURS),
                "duration_h": HOURS,
                "off_lon": A[0],
                "off_lat": A[1],
                "on_lon": B[0],
                "on_lat": B[1],
                "open": False,
            }
        ]
    )


@pytest.fixture
def vessels():
    return pd.DataFrame([{"vessel_id": "v1", "vessel_type": "tanker"}])


def _sar(rows):
    df = pd.DataFrame(rows, columns=["sar_id", "ts", "lon", "lat"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["detections"] = 1
    return df


def test_match_separates_consistent_date_only_and_outside(gap, vessels):
    env = envelopes(gap, vessels)
    mid = geo.midpoint(*A, *B)
    far_north = geo.destination(*mid, 0, env.loc[0, "semi_minor_km"] - 20)  # inside, far from A/B
    sar = _sar(
        [
            ("on-route", T0 + pd.Timedelta(hours=15), *mid),  # reachable mid-gap
            ("too-early", T0 + pd.Timedelta(hours=1), *far_north),  # inside, but unreachable 1 h in
            ("before-gap", T0 - pd.Timedelta(hours=4), *mid),  # same day, before AIS went off
            ("outside", T0 + pd.Timedelta(hours=15), 38.0, 43.5),  # outside the ellipse
        ]
    )
    c = match(gap, env, sar).set_index("sar_id")
    assert "outside" not in c.index
    assert bool(c.loc["on-route", "consistent"]) is True
    assert bool(c.loc["too-early", "consistent"]) is False
    assert bool(c.loc["before-gap", "consistent"]) is False
    assert (c["gap_consistent_candidates"] == 1).all()


def test_dark_track_chains_feasible_detections_in_time_order(gap, vessels):
    env = envelopes(gap, vessels)
    p1 = geo.destination(*A, 90, 50)
    p2 = geo.destination(*A, 90, 110)
    sar = _sar(
        [
            ("p2", T0 + pd.Timedelta(hours=20), *p2),
            ("p1", T0 + pd.Timedelta(hours=8), *p1),
        ]
    )
    c = match(gap, env, sar)
    track = dark_track(gap.iloc[0], c[c["consistent"]], env.loc[0, "vmax_kn"])
    assert track is not None
    assert track.sar_ids == ["p1", "p2"]
    assert track.points[0] == pytest.approx(A) and track.points[-1] == pytest.approx(B)
    assert track.max_leg_speed_kn <= env.loc[0, "vmax_kn"] + 1e-6


def test_dark_track_drops_detection_that_breaks_feasibility(gap, vessels):
    env = envelopes(gap, vessels)
    near_b = geo.destination(*B, 270, 5)
    near_a = geo.destination(*A, 90, 5)
    # Near B early, then back near A later: each alone is consistent, together they are not
    # chainable in that order, so the track keeps one detection.
    sar = _sar([("near-b", T0 + pd.Timedelta(hours=12), *near_b), ("near-a", T0 + pd.Timedelta(hours=13), *near_a)])
    c = match(gap, env, sar)
    track = dark_track(gap.iloc[0], c[c["consistent"]], env.loc[0, "vmax_kn"])
    assert track is not None and len(track.sar_ids) == 1
