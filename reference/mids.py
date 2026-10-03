"""MMSI Maritime Identification Digits → flag state.

The first three digits of a ship-station MMSI are its MID (ITU-R M.585, Table of Maritime
Identification Digits). Mapping from michaeljfazio/MIDs (Apache-2.0), derived from the ITU table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from reference.cache import cached

URL = "https://raw.githubusercontent.com/michaeljfazio/MIDs/master/mids.json"


@dataclass(frozen=True)
class Flag:
    code: str  # ISO 3166-1 alpha-2, lower case
    country: str


class Mids:
    def __init__(self, table: dict[str, Flag]):
        self.table = table

    def flag(self, mmsi: str | int | None) -> Flag | None:
        text = str(mmsi or "")
        # Ship stations have 9-digit MMSIs starting 2–7; other formats (coast stations,
        # SAR aircraft, AtoN) carry the MID elsewhere and are not ships.
        if len(text) != 9 or not text.isdigit() or text[0] not in "234567":
            return None
        return self.table.get(text[:3])


def load_from(path: Path) -> Mids:
    raw = json.loads(path.read_text(encoding="utf-8"))
    table = {mid: Flag(v[0].lower(), v[3]) for mid, v in raw.items() if v and v[0]}
    if len(table) < 200:
        raise ValueError(f"MID table looks wrong: {len(table)} entries")
    return Mids(table)


@lru_cache(maxsize=1)
def mids(refresh: bool = False) -> Mids:
    return load_from(cached(URL, "mids.json", refresh=refresh, max_age_days=180))
