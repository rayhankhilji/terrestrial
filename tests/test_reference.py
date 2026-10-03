"""Reference loaders and the military classifier, on real saved samples (tests/fixtures/reference)."""

import json
from pathlib import Path

import pytest

from live.milclass import callsign_family, classify
from reference.aircraftdb import load_from as load_register
from reference.airfields import parse as parse_airfields
from reference.icao_ranges import load_from as load_ranges
from reference.roles import parse_mds

REF = Path(__file__).parent / "fixtures" / "reference"
LIVE = Path(__file__).parent / "fixtures" / "live"


@pytest.fixture(scope="module")
def register():
    return load_register(REF / "basic_ac_db_sample.json.gz")


@pytest.fixture(scope="module")
def ranges():
    return load_ranges(REF / "flags.js")


def _aircraft(name):
    body = json.loads((LIVE / name).read_text())
    return {a["hex"]: a for a in body.get("ac") or body.get("aircraft")}


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("Boeing KC-135R", ("tanker", None, "KC-135")),
        ("Sikorsky UH-60A Black Hawk", ("utility", "helicopter", "UH-60")),
        ("Northrop Grumman RQ-4B", ("isr", "uav", "RQ-4")),
        ("Bell-Boeing V-22 Osprey", ("unknown", "tiltrotor", "V-22")),
        ("Boeing P-8A Poseidon", ("maritime_patrol", None, "P-8")),
        ("Bombardier BD-700 Global Express", None),  # manufacturer code, not an MDS
        ("G-115 Tutor", None),
        ("Tupolev Tu-134-A-3", None),  # variant suffix of a Soviet type, not an A- designation
        ("Tupolev Tu-154 B-2", None),
        ("Shaanxi Y-8 F-200W", None),
        ("AgustaWestland AW.139 HH-139A", ("sar", "helicopter", "HH-139")),
        (None, None),
    ],
)
def test_mds_parsing(model, expected):
    assert parse_mds(model) == expected


def test_icao_ranges(ranges):
    assert ranges.lookup("ae01d5").country == "United States"
    assert ranges.lookup("43c5dc").code == "gb"
    assert ranges.lookup("4d03c6").country == "Luxembourg"
    assert ranges.lookup("~ae01d5").code == "us"  # non-ICAO (TIS-B) marker is ignored
    assert ranges.lookup("zzzzzz") is None


def test_register_keeps_military_and_learns_type_roles(register):
    assert all(a.icao == a.icao.lower() for a in register.airframes.values())
    assert register.get("4D03C6").reg == "LX-N90448"
    k35r = register.type_roles["K35R"]
    assert k35r.role == "tanker" and k35r.votes >= 3
    assert register.type_roles["H60"].airframe == "helicopter"
    assert register.type_roles["Q4"].airframe == "uav"


def test_classify_military_feed_aircraft(register, ranges):
    ac = _aircraft("adsblol_mil_subset.json")
    rc135 = classify(ac["ae01d5"], register, ranges, military_feed=True)
    assert rc135.military and rc135.role == "isr" and rc135.designation == "RC-135"
    assert rc135.state == "United States" and rc135.org == "United States military"
    assert rc135.callsign_family == "COBRA"
    tanker = classify(ac["ae0504"], register, ranges, military_feed=True)
    assert tanker.role == "tanker" and tanker.callsign_family == "RCH"
    gov = classify(ac["710195"], register, ranges, military_feed=True)
    assert gov.org == "Saudi Arabia Government flight"
    assert any("Government of Saudi Arabia" in e for e in gov.evidence)


def test_civil_traffic_is_not_military(register, ranges):
    ac = _aircraft("adsbfi_point.json")
    military = [h for h, a in ac.items() if classify(a, register, ranges).military]
    assert len(military) < len(ac) // 10
    civil = next(a for h, a in ac.items() if h not in military)
    c = classify(civil, register, ranges)
    assert not c.military and c.evidence == []


def test_nato_fleet_org(register, ranges):
    c = classify({"hex": "4d03c6", "t": "E3TF", "flight": "NATO05"}, register, ranges, military_feed=True)
    assert c.org == "NATO" and c.role == "electronic" and c.state == "Luxembourg"


def test_callsign_family():
    assert callsign_family("RCH913  ") == "RCH"
    assert callsign_family("FORTE10") == "FORTE"
    assert callsign_family("170040") is None
    assert callsign_family(None) is None


def test_airfields_classify_military_by_name_not_history(tmp_path):
    rows = {a.ident: a for a in parse_airfields(REF / "airports_sample.csv")}
    assert rows["UKFI"].military and rows["UKFB"].military  # Saky, Belbek
    assert rows["EGUB"].military  # RAF Benson
    assert not rows["EGAA"].military  # Belfast International: "RAF Aldergrove" only in keywords
    assert not rows["EGLL"].military
    assert rows["UKFI"].elevation_m is not None


def test_overlapping_blocks_prefer_the_narrowest(ranges):
    assert ranges.lookup("43be10").country == "Bermuda"  # inside the UK catch-all block
    assert ranges.lookup("43be10").end - ranges.lookup("43be10").start < 0x1000


def test_naval_classification_from_ship_type_prefix_and_mid():
    from live.navclass import classify as classify_vessel
    from reference.mids import load_from as load_mids

    mids = load_mids(REF / "mids.json")
    warship = classify_vessel({"type_code": 35, "name": "TCG ANADOLU"}, "271000000", mids)
    assert warship["military"] and warship["naval_role"] == "warship"
    assert warship["state"] == "Turkey" and warship["org"] == "Turkey Navy"
    assert len(warship["mil_evidence"]) == 2
    police = classify_vessel({"type_code": 55, "name": "PATROL 1"}, "272000000", mids)
    assert police["naval_role"] == "law_enforcement" and police["state_code"] == "ua"
    merchant = classify_vessel({"type_code": 70, "name": "GRAIN STAR"}, "273000000", mids)
    assert not merchant["military"] and merchant["state"] == "Russia" and merchant["mil_evidence"] == []
    assert mids.flag("002730000") is None  # coast station, not a ship


def test_listed_icao_type_beats_lookalike_designation(register, ranges):
    # Airbus's "A-400M" is not an A- (attack) designation.
    c = classify({"hex": "3b7769", "t": "A400", "desc": "AIRBUS A-400M Atlas"}, register, ranges, True)
    assert c.role == "airlift" and c.designation is None
