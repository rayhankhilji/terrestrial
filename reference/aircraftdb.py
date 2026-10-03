"""Military airframe register from the ADS-B Exchange basic aircraft database.

`basic-ac-db.json.gz` (ADS-B Exchange, refreshed daily, free download) has one JSON object per
line: icao, reg, icaotype, year, manufacturer, model, ownop, faa_pia, faa_ladd, short_type, mil.
We keep the airframes flagged `mil` (≈23k) and learn type → role from their model names.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from reference.cache import cached
from reference.roles import LearnedType, learn_type_roles

URL = "https://downloads.adsbexchange.com/downloads/basic-ac-db.json.gz"


@dataclass(frozen=True)
class Airframe:
    icao: str
    reg: str | None
    icaotype: str | None
    model: str | None
    ownop: str | None
    short_type: str | None


@dataclass
class Register:
    airframes: dict[str, Airframe]  # lower-case hex → airframe (military only)
    type_roles: dict[str, LearnedType]

    def get(self, hex_address: str) -> Airframe | None:
        return self.airframes.get(hex_address.lower().lstrip("~"))


def _clean(value):
    return value.strip() or None if isinstance(value, str) else value


def load_from(path: Path) -> Register:
    airframes: dict[str, Airframe] = {}
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("mil"):
                continue
            icao = row["icao"].lower()
            airframes[icao] = Airframe(
                icao=icao,
                reg=_clean(row.get("reg")),
                icaotype=_clean(row.get("icaotype")),
                model=_clean(row.get("model")),
                ownop=_clean(row.get("ownop")),
                short_type=_clean(row.get("short_type")),
            )
    if not airframes:
        raise ValueError(f"no military airframes in {path}; has the database format changed?")
    roles = learn_type_roles((a.icaotype, a.model) for a in airframes.values())
    return Register(airframes, roles)


@lru_cache(maxsize=1)
def register(refresh: bool = False) -> Register:
    return load_from(cached(URL, "basic-ac-db.json.gz", refresh=refresh))
