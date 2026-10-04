"""Live intelligence feeds on real saved responses: gazetteer, Air Force Telegram reports, news
wires, imaging-satellite passes, the stream registry and AI-prose citation checks."""

import json
import time
from pathlib import Path

import pytest

from live.hub import Hub
from live.registry import STREAMS, status
from live.satellites import CATALOG, parse_elements, passes, positions, solar_elevation
from live.sitrep import facts
from live.sources import rss, telegram
from pipeline.featherless import FeatherlessError, validate
from reference.gazetteer import build

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def gaz():
    return build({"UA": FIX / "reference" / "geonames_UA.txt", "RU": FIX / "reference" / "geonames_RU.txt"})


@pytest.fixture(scope="module")
def regions():
    from history.regions import parse_boundaries

    return {
        i: (r.lon, r.lat)
        for i, r in parse_boundaries((FIX / "history" / "ukr_adm1_simplified.geojson").read_text()).items()
    }


def test_gazetteer_resolves_inflections_and_ambiguity(gaz):
    assert gaz.lookup("Кременчук").name == "Kremenchuk"  # not Yampil, which lists it as an alias
    assert gaz.lookup("Ізюма").name == "Izyum"  # genitive
    assert gaz.lookup("Одесу").name == "Odesa"  # accusative
    assert gaz.lookup("Орел").country == "RU"  # Oryol, not a 142-person Ukrainian village
    assert gaz.lookup("Чорнобиль").name == "Chornobyl"  # not a Kyiv district sharing the name
    assert gaz.lookup("Нічогоподібного") is None


def _posts(name, channel):
    return telegram.parse_page((FIX / "live" / name).read_text(encoding="utf-8"), channel)


def test_air_force_reports_are_parsed_and_placed(gaz, regions):
    posts = {p.id: p for p in _posts("telegram_kpszsu.html", "kpszsu")}
    assert len(posts) == 20 and all(p.at > 0 for p in posts.values())
    t = telegram.parse_post(posts["kpszsu/82678"], gaz)  # 🏍 jet drone over Dnipropetrovsk, course Kremenchuk
    assert (t.weapon, t.region, t.to_place["name"]) == ("jet_uav", "UA-12", "Kremenchuk")
    e = telegram.entity(t, regions)
    assert (
        e["props"]["url"] == "https://t.me/kpszsu/82678" and e["props"]["text"] == posts["kpszsu/82678"].text
    )
    kab = telegram.parse_post(posts["kpszsu/82670"], gaz)  # 💣 KABs on Donetsk region
    assert (kab.weapon, kab.region, kab.to_place) == ("glide_bomb", "UA-14", None)
    reservoir = telegram.parse_post(posts["kpszsu/82668"], gaz)  # "Київського водосховища" is not a town
    assert reservoir.to_place is None


def test_overnight_tally_with_launch_areas(gaz):
    post = next(p for p in _posts("telegram_kpszsu.html", "kpszsu") if p.id == "kpszsu/82681")
    t = telegram.parse_post(post, gaz)
    assert t.tally["attacked"] == 135 and t.tally["downed"] == 125
    assert t.tally["hit_locations"] == 5 and t.tally["debris_locations"] == 5
    names = [a["name"] for a in t.tally["launch_areas"]]
    assert {"Kursk", "Millerovo", "Primorsko-Akhtarsk", "Hvardiyske"} <= set(names)


def test_monitor_positions_and_unplaceable_status_posts(gaz, regions):
    posts = {p.id: p for p in _posts("telegram_war_monitor.html", "war_monitor")}
    nizhyn = telegram.parse_post(posts["war_monitor/47258"], gaz)  # 1х реактив повз Ніжин
    assert (nizhyn.weapon, nizhyn.count, nizhyn.at_place["name"]) == ("jet_uav", 1, "Nizhyn")
    status_only = telegram.parse_post(posts["war_monitor/47257"], gaz)  # "search until all-clear"
    assert telegram.entity(status_only, regions) is None  # kept as text, never guessed onto the map


def test_news_wires_parse_and_place_only_named_towns(gaz):
    items = rss.parse_feed((FIX / "live" / "rss_ukrinform.xml").read_text(encoding="utf-8"), "Ukrinform")
    assert len(items) == 30 and all(i["url"].startswith("https://") and i["at"] > 0 for i in items)
    izium = next(i for i in items if i["title"].startswith("Izium"))
    e = rss.entity(izium, gaz)
    assert e["props"]["place"] == "Izyum" and e["props"]["strike_related"]
    toll = next(i for i in items if "casualty toll" in i["title"])
    assert rss.entity(toll, gaz) is None  # names no town: listed, not placed


def test_satellite_positions_and_passes():
    rows = json.loads((FIX / "live" / "celestrak_sentinel1.json").read_text())
    sats = parse_elements(rows, CATALOG[0])
    assert {s.name for s in sats} == {"SENTINEL-1A", "SENTINEL-1C", "SENTINEL-1D"}  # 1B is dead
    epoch = time.mktime(time.strptime(rows[0]["EPOCH"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    lon, lat, alt, ok = positions(sats, [epoch])
    assert ok.all() and ((alt > 680) & (alt < 730)).all() and (abs(lat) <= 82).all()
    p = passes(sats, 33.52, 44.61, epoch, hours=24)  # Sevastopol
    assert p and all(x["end"] > x["start"] and x["closest_km"] <= 350 for x in p)
    # Sentinel-1 is in a dawn–dusk orbit: every pass is near 06:00 or 18:00 local solar time.
    for x in p:
        solar_h = (time.gmtime(x["start"] / 1000).tm_hour + 33.52 / 15) % 24
        assert min(abs(solar_h - 6), abs(solar_h - 18)) < 2


def test_solar_elevation_sign():
    noon = time.mktime((2026, 6, 21, 10, 0, 0, 0, 0, 0)) - time.timezone  # 10:00 UTC ≈ noon in Kyiv
    assert solar_elevation(30.5, 50.45, noon) > 50
    assert solar_elevation(30.5, 50.45, noon + 12 * 3600) < -10


def test_registry_reports_keys_and_health(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    hub = Hub()
    hub.source_ok("adsb.fi", "22 aircraft")
    rows = {r["id"]: r for r in status(hub)}
    assert len(rows) == len(STREAMS)
    assert rows["adsb-fi"]["state"] == "ok"
    assert rows["jev"]["state"] == "needs-key"


def test_ai_prose_must_cite_real_facts():
    table = {"F1": "a", "F2": "b", "F3": "c"}
    assert validate("Drones over Kyiv [F1]. Risk high [F2, F3].", table) == ["F1", "F2", "F3"]
    with pytest.raises(FeatherlessError):
        validate("Something [F9] and [F1].", table)
    with pytest.raises(FeatherlessError):
        validate("No citations at all.", table)


def test_sitrep_facts_from_parsed_reports(gaz, regions):
    hub = Hub()
    for p in _posts("telegram_kpszsu.html", "kpszsu"):
        if e := telegram.entity(telegram.parse_post(p, gaz), regions):
            hub.upsert(e)
    latest = max(e["ts"] for e in hub.of_kind("airthreat"))
    fs = facts(hub, now=latest + 60_000)
    assert fs[0]["id"] == "F1" and fs[0]["kind"] == "threat" and fs[0]["refs"]
    assert any("tally" in f["text"].lower() for f in fs)
