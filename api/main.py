"""Terrestrial read-only API. Serves processed tables from disk and the TuringDB graph."""

from __future__ import annotations

from fastapi import FastAPI

from pipeline.aoi import AOIS

app = FastAPI(title="Terrestrial API", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/aois")
def aois() -> dict:
    """AOI circles as a GeoJSON FeatureCollection (occupied ports flagged)."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": a.name,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[list(c) for c in a.polygon.exterior.coords]],
                },
                "properties": {
                    "name": a.name,
                    "occupied_ua": a.occupied_ua,
                    "radius_km": a.radius_km,
                    "center": [a.lon, a.lat],
                },
            }
            for a in AOIS
        ],
    }
