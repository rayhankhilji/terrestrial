"""OpenSanctions maritime normalisation against a real verbatim sample."""

from pathlib import Path

from pipeline.opensanctions import normalise

SAMPLE = Path(__file__).parent / "fixtures" / "opensanctions" / "maritime_sample.csv"


def test_only_vessels_are_kept_and_imos_parsed():
    df = normalise(SAMPLE)
    assert len(df) > 0
    assert df["os_id"].is_unique
    imos = df["imo"].dropna()
    assert imos.str.fullmatch(r"\d{7}").all()


def test_topic_flags():
    df = normalise(SAMPLE)
    assert df["shadow_fleet"].any()
    assert df.loc[df["shadow_fleet"], "sanctioned"].all()  # shadow fleet counts as sanctions-relevant
    detained_only = df[df["topics"].apply(lambda t: list(t) == ["mare.detained", "reg.warn"])]
    assert not detained_only.empty
    assert (~detained_only["sanctioned"]).all() and detained_only["detained"].all()


def test_multi_valued_mmsi_is_split():
    df = normalise(SAMPLE)
    assert (df["mmsis"].apply(len) > 1).any()
    assert all(m.isdigit() for ms in df["mmsis"] for m in ms)
