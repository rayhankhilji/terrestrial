"""ICAO 24-bit address → state of registry.

Source: the allocation table in ICAO Annex 10 Vol III, Chapter 9 appendix, as maintained in
tar1090's `flags.js` (wiedehopf/tar1090, MIT). We parse that file rather than re-typing the
table. The state of registry is where the airframe is registered, which for military aircraft
is the operating state (NATO's E-3 fleet is registered in Luxembourg, handled in milclass).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from reference.cache import cached

URL = "https://raw.githubusercontent.com/wiedehopf/tar1090/master/html/flags.js"
ROW = re.compile(
    r'\{\s*start:\s*0x([0-9A-Fa-f]+),\s*end:\s*0x([0-9A-Fa-f]+),\s*country:\s*"([^"]+)",\s*country_code:\s*(?:"([a-z]+)"|null)\s*\}'
)


@dataclass(frozen=True)
class Allocation:
    start: int
    end: int
    country: str
    code: str | None  # ISO 3166-1 alpha-2, lower case


def parse(text: str) -> list[Allocation]:
    rows = [Allocation(int(a, 16), int(b, 16), c, d or None) for a, b, c, d in ROW.findall(text)]
    if len(rows) < 150:
        raise ValueError(f"ICAO range table looks wrong: only {len(rows)} rows parsed")
    rows.sort(key=lambda r: r.start)
    return rows


class Ranges:
    """Blocks overlap (e.g. Bermuda and Guernsey inside the UK catch-all), so the narrowest
    block containing the address wins, which matches the file's specific-first ordering."""

    def __init__(self, rows: list[Allocation]):
        self.rows = rows
        self._memo: dict[int, Allocation | None] = {}

    def lookup(self, hex_address: str) -> Allocation | None:
        try:
            value = int(hex_address.strip().lstrip("~"), 16)
        except ValueError:
            return None
        if value not in self._memo:
            hits = [r for r in self.rows if r.start <= value <= r.end]
            self._memo[value] = min(hits, key=lambda r: r.end - r.start) if hits else None
        return self._memo[value]


def load_from(path: Path) -> Ranges:
    return Ranges(parse(path.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def ranges(refresh: bool = False) -> Ranges:
    return load_from(cached(URL, "icao_ranges_flags.js", refresh=refresh, max_age_days=90))
