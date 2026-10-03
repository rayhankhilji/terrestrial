"""Live air-raid alert poller (real saved feed response) and the serving merge/coverage logic."""

import json
from pathlib import Path

import pandas as pd

from history.regions import BY_ISO
from live.sources.alerts import AlertLog, parse, read_log
from predict.strike.serve import merge_intervals, unwatched_minutes

FIX = Path(__file__).parent / "fixtures" / "live"
T0 = 1_791_000_000_000  # ms


def test_feed_parses_to_region_states_with_utc_cache_time():
    states, cached = parse(json.loads((FIX / "aerialalerts_ubilling.json").read_text()))
    assert len(states) == 26 and set(states) <= set(BY_ISO)
    assert sum(states.values()) == 8
    assert str(cached.tzinfo) == "UTC" and cached.hour == 13  # 16:55 Kyiv summer time


def test_log_reconstructs_intervals_and_watching_spans(tmp_path):
    log = AlertLog(tmp_path / "t.jsonl")
    log.observe({"UA-63": True, "UA-30": False}, T0)  # Kharkiv already under alert at first poll
    log.observe({"UA-63": False, "UA-30": True}, T0 + 60_000)
    log.observe({"UA-63": False, "UA-30": False}, T0 + 120_000)
    intervals, spans = read_log(tmp_path / "t.jsonl")
    kh = intervals[intervals["iso"] == "UA-63"].iloc[0]
    ky = intervals[intervals["iso"] == "UA-30"].iloc[0]
    assert not kh["start_known"] and ky["start_known"]
    assert (ky["end"] - ky["start"]).total_seconds() == 60
    assert spans == [(T0, T0 + 120_000)]


def test_unobserved_gap_closes_open_alerts_and_splits_spans(tmp_path):
    log = AlertLog(tmp_path / "t.jsonl")
    log.observe({"UA-63": True}, T0)
    later = AlertLog(tmp_path / "t.jsonl")  # a restart two hours later
    later.observe({"UA-63": False}, T0 + 2 * 3600_000)
    intervals, spans = read_log(tmp_path / "t.jsonl")
    assert len(spans) == 2
    (row,) = intervals.itertuples()
    assert row.end == pd.Timestamp(T0, unit="ms", tz="UTC")  # ended at our last observation, not invented
    gap = unwatched_minutes(
        spans, pd.Timestamp(T0, unit="ms", tz="UTC"), pd.Timestamp(T0 + 2 * 3600_000, unit="ms", tz="UTC")
    )
    assert 119 <= gap <= 120


def test_merge_intervals_unions_overlaps_per_region():
    ts = lambda m: pd.Timestamp("2026-10-03", tz="UTC") + pd.Timedelta(minutes=m)  # noqa: E731
    df = pd.DataFrame(
        [
            ("UA-63", ts(0), ts(30)),
            ("UA-63", ts(20), ts(50)),
            ("UA-63", ts(60), ts(70)),
            ("UA-30", ts(0), ts(10)),
        ],
        columns=["iso", "start", "end"],
    )
    out = merge_intervals(df)
    assert out[out["iso"] == "UA-63"]["minutes"].tolist() == [50.0, 10.0]
    assert len(out[out["iso"] == "UA-30"]) == 1
