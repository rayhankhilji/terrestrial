"""API smoke tests."""

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_aois_geojson():
    body = client.get("/api/aois").json()
    assert body["type"] == "FeatureCollection"
    names = {f["properties"]["name"] for f in body["features"]}
    assert {"Sevastopol", "Berdyansk", "Novorossiysk"} <= names
    sev = next(f for f in body["features"] if f["id"] == "Sevastopol")
    assert sev["properties"]["occupied_ua"] is True
    ring = sev["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]  # closed ring
