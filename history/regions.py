"""Ukraine's 27 first-level regions, the join key for every historical dataset.

Canonical id: ISO 3166-2:UA code (UA-05 …). Boundaries: geoBoundaries gbOpen UKR ADM1
(simplified; CC BY 4.0, wmgeolab). Each dataset spells regions its own way, so every spelling
is mapped explicitly here; tests assert that every name in the real samples maps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from pipeline.config import RAW_DIR
from reference.cache import cached

URL = "https://github.com/wmgeolab/geoBoundaries/raw/main/releaseData/gbOpen/UKR/ADM1/geoBoundaries-UKR-ADM1_simplified.geojson"
HISTORY_RAW = RAW_DIR / "history"


@dataclass(frozen=True)
class Region:
    iso: str
    name: str
    sirens: str | None  # Vadimkin/ukrainian-air-raid-sirens-dataset (English)
    viina: str | None  # VIINA ADM1_NAME
    alerts_ua: str | None  # Ukrainian name used by the live alert feed


# iso, English name, sirens dataset, VIINA ADM1_NAME, live alert feed (Ukrainian)
REGIONS: tuple[Region, ...] = tuple(
    Region(*row)
    for row in (
        ("UA-05", "Vinnytsia", "Vinnytska oblast", "Vinnytsya", "Вінницька область"),
        ("UA-07", "Volyn", "Volynska oblast", "Volyn", "Волинська область"),
        ("UA-09", "Luhansk", "Luhanska oblast", "Luhans'k", "Луганська область"),
        ("UA-12", "Dnipropetrovsk", "Dnipropetrovska oblast", "Dnipropetrovs'k", "Дніпропетровська область"),
        ("UA-14", "Donetsk", "Donetska oblast", "Donets'k", "Донецька область"),
        ("UA-18", "Zhytomyr", "Zhytomyrska oblast", "Zhytomyr", "Житомирська область"),
        ("UA-21", "Zakarpattia", "Zakarpatska oblast", "Transcarpathia", "Закарпатська область"),
        ("UA-23", "Zaporizhzhia", "Zaporizka oblast", "Zaporizhzhya", "Запорізька область"),
        (
            "UA-26",
            "Ivano-Frankivsk",
            "Ivano-Frankivska oblast",
            "Ivano-Frankivs'k",
            "Івано-Франківська область",
        ),
        ("UA-30", "Kyiv City", "Kyiv City", "Kiev City", "м. Київ"),
        ("UA-32", "Kyiv", "Kyivska oblast", "Kiev", "Київська область"),
        ("UA-35", "Kirovohrad", "Kirovohradska oblast", "Kirovohrad", "Кіровоградська область"),
        ("UA-40", "Sevastopol", None, "Sevastopol'", "Севастополь"),
        ("UA-43", "Crimea", None, "Crimea", None),
        ("UA-46", "Lviv", "Lvivska oblast", "L'viv", "Львівська область"),
        ("UA-48", "Mykolaiv", "Mykolaivska oblast", "Mykolayiv", "Миколаївська область"),
        ("UA-51", "Odesa", "Odeska oblast", "Odessa", "Одеська область"),
        ("UA-53", "Poltava", "Poltavska oblast", "Poltava", "Полтавська область"),
        ("UA-56", "Rivne", "Rivnenska oblast", "Rivne", "Рівненська область"),
        ("UA-59", "Sumy", "Sumska oblast", "Sumy", "Сумська область"),
        ("UA-61", "Ternopil", "Ternopilska oblast", "Ternopil'", "Тернопільська область"),
        ("UA-63", "Kharkiv", "Kharkivska oblast", "Kharkiv", "Харківська область"),
        ("UA-65", "Kherson", "Khersonska oblast", "Kherson", "Херсонська область"),
        ("UA-68", "Khmelnytskyi", "Khmelnytska oblast", "Khmel'nyts'kyy", "Хмельницька область"),
        ("UA-71", "Cherkasy", "Cherkaska oblast", "Cherkasy", "Черкаська область"),
        ("UA-74", "Chernihiv", "Chernihivska oblast", "Chernihiv", "Чернігівська область"),
        ("UA-77", "Chernivtsi", "Chernivetska oblast", "Chernivtsi", "Чернівецька область"),
    )
)
BY_ISO = {r.iso: r for r in REGIONS}
BY_SIRENS = {r.sirens: r.iso for r in REGIONS if r.sirens}
BY_VIINA = {r.viina: r.iso for r in REGIONS if r.viina}
BY_ALERTS_UA = {r.alerts_ua: r.iso for r in REGIONS if r.alerts_ua}


@dataclass(frozen=True)
class RegionGeometry:
    iso: str
    geometry: BaseGeometry
    lon: float  # representative point (inside the polygon)
    lat: float


def parse_boundaries(text: str) -> dict[str, RegionGeometry]:
    out = {}
    for f in json.loads(text)["features"]:
        iso = f["properties"]["shapeISO"]
        if iso not in BY_ISO:
            raise ValueError(f"unknown region {iso} ({f['properties'].get('shapeName')}) in boundaries")
        geom = shape(f["geometry"])
        p = geom.representative_point()
        out[iso] = RegionGeometry(iso, geom, round(p.x, 4), round(p.y, 4))
    missing = set(BY_ISO) - set(out)
    if missing:
        raise ValueError(f"boundaries are missing regions: {sorted(missing)}")
    return out


@lru_cache(maxsize=1)
def boundaries(refresh: bool = False) -> dict[str, RegionGeometry]:
    path = cached(
        URL, "ukr_adm1_simplified.geojson", refresh=refresh, max_age_days=365, base=HISTORY_RAW / "regions"
    )
    return parse_boundaries(path.read_text(encoding="utf-8"))


def neighbours(geoms: dict[str, RegionGeometry]) -> dict[str, list[str]]:
    """Regions sharing a border (buffered slightly: simplified boundaries leave slivers)."""
    out: dict[str, list[str]] = {}
    for a, ga in geoms.items():
        grown = ga.geometry.buffer(0.02)
        out[a] = sorted(b for b, gb in geoms.items() if b != a and grown.intersects(gb.geometry))
    return out
