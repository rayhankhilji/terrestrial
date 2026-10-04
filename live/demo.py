"""Cut a demo recording: the busiest stretch of a day's live recording, small enough to replay.

    uv run python -m live.demo --day 20261003                 # busiest 2 h of that day
    uv run python -m live.demo --day 20261003 --start 14:00 --hours 3

Writes data/demo/demo.jsonl (every recorded message in the window, verbatim). `make demo`
replays it with LIVE_REPLAY at 10× speed, with no network needed except basemap tiles.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

from pipeline.config import DATA_DIR, LIVE_DIR

OUT = DATA_DIR / "demo" / "demo.jsonl"
INTERESTING = {"aircraft": 1, "vessel": 1, "net": 20, "airthreat": 30, "region": 2, "news": 5}


def keep(msg: dict) -> bool:
    """What the product shows today: civil traffic recorded before the military-only filter
    (§16.1) is dropped."""
    if msg["t"] != "upsert":
        return True
    e = msg["e"]
    return e["kind"] not in ("aircraft", "vessel") or bool((e.get("props") or {}).get("military"))


def busiest(path, hours: float) -> datetime:
    """Start of the window with the most interesting upserts (weighted by kind)."""
    per_10min: Counter = Counter()
    with path.open(encoding="utf-8") as f:
        for line in f:
            if '"t":"upsert"' not in line:
                continue
            kind = line.split('"kind":"', 1)[-1].split('"', 1)[0]
            w = INTERESTING.get(kind)
            if w and (kind not in ("aircraft", "vessel") or '"military":true' in line):
                per_10min[int(json.loads(line)["at"] // 600_000)] += w
    if not per_10min:
        raise SystemExit(f"{path} has no recorded upserts")
    span = int(hours * 6)
    first, last = min(per_10min), max(per_10min)
    best = max(
        range(first, max(first, last - span) + 1), key=lambda s: sum(per_10min[s + k] for k in range(span))
    )
    return datetime.fromtimestamp(best * 600, UTC)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", required=True, help="recording day YYYYMMDD (data/live/<day>.jsonl)")
    p.add_argument("--start", help="window start HH:MM UTC (default: the busiest window)")
    p.add_argument("--hours", type=float, default=2.0)
    args = p.parse_args()
    path = LIVE_DIR / f"{args.day}.jsonl"
    if not path.exists():
        raise SystemExit(f"no recording at {path}")
    if args.start:
        day = datetime.strptime(args.day, "%Y%m%d").replace(tzinfo=UTC)
        h, m = map(int, args.start.split(":"))
        start = day + timedelta(hours=h, minutes=m)
    else:
        start = busiest(path, args.hours)
    a, b = start.timestamp() * 1000, (start + timedelta(hours=args.hours)).timestamp() * 1000
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open(encoding="utf-8") as src, OUT.open("w", encoding="utf-8") as dst:
        for line in src:
            msg = json.loads(line)
            if a <= msg["at"] < b and keep(msg):
                dst.write(line)
                n += 1
    print(
        f"wrote {n} messages {start:%Y-%m-%d %H:%M}–{start + timedelta(hours=args.hours):%H:%M} UTC to {OUT}"
    )


if __name__ == "__main__":
    main()
