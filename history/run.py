"""Fetch and normalise every historical dataset: `uv run python -m history.run [--refresh] [--only NAME]`."""

from __future__ import annotations

import argparse
import logging
import time

from history import sirens, viina, weather

SOURCES = {"sirens": sirens.fetch, "viina": viina.fetch, "weather": weather.fetch}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="re-download even if the cache is fresh")
    parser.add_argument("--only", choices=sorted(SOURCES), action="append")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for name in args.only or SOURCES:
        started = time.monotonic()
        logging.info("history: %s", name)
        SOURCES[name](refresh=args.refresh)
        logging.info("history: %s done in %.1fs", name, time.monotonic() - started)


if __name__ == "__main__":
    main()
