"""Land polygons (Natural Earth 1:10m land, public domain), clipped to the military area.

Used where administrative shapes are not enough: the simplified ADM1 boundaries include parts
of the Sea of Azov and coastal waters, so "Ukrainian territory" for the front line is
intersected with land.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from shapely.geometry import box, shape
from shapely.ops import unary_union

from pipeline.config import MIL_BBOX
from reference.cache import cached

URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson"


def parse(path: Path, bbox=MIL_BBOX):
    area = box(*bbox)
    parts = []
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        g = shape(f["geometry"])
        if g.intersects(area):
            parts.append(g.intersection(area))
    if not parts:
        raise ValueError(f"no land in {bbox} in {path}; has the file changed?")
    return unary_union(parts)


@lru_cache(maxsize=1)
def land(refresh: bool = False):
    return parse(cached(URL, "ne_10m_land.geojson", refresh=refresh, max_age_days=365))
