"""Place-name lookup for Ukrainian, Russian and English text (GeoNames, CC BY 4.0).

Built from the GeoNames country dumps for Ukraine, Russia and Belarus: populated places (P)
and airfields (S.AIRB), with every alternate name (Ukrainian, Russian, transliterations).
Names are ambiguous (a village may list "Kremenchuk" among its alternate names), so a lookup
returns the most populous match, preferring Ukraine unless a foreign match is >10× larger, and the caller decides whether the match
is good enough (e.g. a minimum population for words that are also common nouns).

Ukrainian inflects place names; `variants` produces the nominative candidates for the common
locative / genitive / accusative endings seen in alert channels ("на Сумщині", "курс на Сміла",
"в районі Ізюма").
"""

from __future__ import annotations

import math
import re
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pipeline.config import REFERENCE_DIR
from reference.cache import cached

URL = "https://download.geonames.org/export/dump/{cc}.zip"
COUNTRIES = ("UA", "RU", "BY")
MIN_POP = {"UA": 0, "RU": 5000, "BY": 5000}  # Russia/Belarus: towns and airfields only
COUNTRY_RANK = {"UA": 0.0, "RU": 1.0, "BY": 1.0}
APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "ё": "е", "Ё": "Е"})


@dataclass(frozen=True)
class Place:
    geonameid: int
    name: str  # GeoNames primary (English) name
    lon: float
    lat: float
    country: str
    admin1: str
    feature: str  # e.g. PPL, PPLA, PPLC, AIRB
    population: int


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(APOSTROPHES).strip().lower())


def variants(word: str) -> list[str]:
    """Nominative candidates for an inflected Ukrainian/Russian place name."""
    w = norm(word)
    out = [w]
    endings = [
        ("ому", ""), ("ові", ""), ("еві", ""), ("ою", "а"), ("ею", "я"), ("ом", ""), ("ем", ""),
        ("ах", "и"), ("ях", "і"), ("і", "а"), ("і", ""), ("у", ""), ("у", "а"), ("ю", "я"), ("а", ""),
        ("я", "ь"), ("ї", "я"), ("и", "а"), ("е", "о"), ("еві", "ів"), ("ова", "ів"), ("єва", "їв"),
        ("ева", "ев"), ("ова", "ов"), ("ого", "е"), ("ого", "ий"), ("ому", "е"), ("ої", "а"),
    ]  # fmt: skip
    for end, repl in endings:
        if len(w) > len(end) + 2 and w.endswith(end):
            out.append(w[: -len(end)] + repl)
    return list(dict.fromkeys(out))


class Gazetteer:
    def __init__(self, places: list[Place], names: dict[str, list[int]]):
        self.places = places
        self.names = names

    def lookup(
        self, word: str, min_population: int = 0, countries: tuple[str, ...] = COUNTRIES
    ) -> Place | None:
        best = None
        for v in variants(word):
            for i in self.names.get(v, ()):
                p = self.places[i]
                if p.country not in countries or (p.population < min_population and p.feature != "AIRB"):
                    continue
                # Ukraine wins ties, but a far larger foreign place wins (each rank costs 10× size).
                # Districts of a city (PPLX) borrow the city's names: rank them 30× smaller.
                key = (
                    COUNTRY_RANK[p.country]
                    + (1.5 if p.feature == "PPLX" else 0)
                    - math.log10(p.population + 1)
                )
                if best is None or key < best[0]:
                    best = (key, p)
            if best is not None:
                return best[1]  # the least-inflected variant that matches wins
        return None


def parse(path: Path, cc: str) -> list[tuple[Place, set[str]]]:
    """(place, all its names) for populated places and airfields in one GeoNames dump."""
    out = []
    zipped = path.suffix == ".zip"
    with zipfile.ZipFile(path).open(f"{cc}.txt") if zipped else path.open("rb") as f:
        for raw in f:
            cols = raw.decode("utf-8").rstrip("\n").split("\t")
            if len(cols) < 15:
                continue
            fclass, fcode = cols[6], cols[7]
            if not (fclass == "P" or fcode == "AIRB"):
                continue
            pop = int(cols[14] or 0)
            if fclass == "P" and pop < MIN_POP[cc]:
                continue
            place = Place(
                int(cols[0]), cols[1], float(cols[5]), float(cols[4]), cols[8], cols[10], fcode, pop
            )
            out.append((place, {cols[1], cols[2], *[n for n in cols[3].split(",") if n]}))
    return out


def build(paths: dict[str, Path]) -> Gazetteer:
    places: list[Place] = []
    names: dict[str, list[int]] = {}
    for cc, path in paths.items():
        for place, alts in parse(path, cc):
            idx = len(places)
            places.append(place)
            for n in alts:
                names.setdefault(norm(n), []).append(idx)
    if not places:
        raise ValueError("gazetteer is empty: check the GeoNames dumps")
    return Gazetteer(places, names)


@lru_cache(maxsize=1)
def gazetteer() -> Gazetteer:
    paths = {
        cc: cached(URL.format(cc=cc), f"{cc}.zip", max_age_days=90, base=REFERENCE_DIR / "geonames")
        for cc in COUNTRIES
    }
    return build(paths)
