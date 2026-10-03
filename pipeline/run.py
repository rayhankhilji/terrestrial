"""Pipeline entrypoint: `uv run python -m pipeline.run [--days N] [--refresh] [--only STAGE]`.

Stages run in order and each reads the previous stage's output from disk (CLAUDE.md §5).
The window is pinned by data/raw/manifest.json once fetched, so re-runs (and the offline
demo) see exactly the same data unless --days/--start/--refresh is given.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
import time
from datetime import date, timedelta

from pipeline.config import DEFAULT_DAYS, default_window
from pipeline.io import load_window

log = logging.getLogger("terrestrial")

# (stage name, module path). Each module exposes stage(start, end, args).
STAGES: list[tuple[str, str]] = [
    ("fetch", "pipeline.fetch"),
    ("normalise", "pipeline.normalise"),
    ("envelope", "pipeline.envelope"),
    ("match", "pipeline.match"),
    ("score", "pipeline.score"),
    ("insights", "pipeline.insights"),
    ("brief", "pipeline.brief"),
    ("graph", "graph.build"),
]


def resolve_window(args: argparse.Namespace) -> tuple[date, date]:
    if args.start:
        start = date.fromisoformat(args.start)
        return start, start + timedelta(days=args.days or DEFAULT_DAYS)
    pinned = load_window()
    if pinned and not args.days and not args.refresh:
        return pinned
    return default_window(args.days or DEFAULT_DAYS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.run", description=__doc__)
    parser.add_argument("--days", type=int, help=f"window length in days (default {DEFAULT_DAYS})")
    parser.add_argument("--start", help="window start date YYYY-MM-DD (default: end = today - 5 days)")
    parser.add_argument("--refresh", action="store_true", help="bypass the raw cache and re-fetch")
    parser.add_argument("--only", choices=[s for s, _ in STAGES], action="append", help="run only these stages")
    parser.add_argument("--from", dest="from_stage", choices=[s for s, _ in STAGES], help="start at this stage")
    parser.add_argument("--no-graph", action="store_true", help="skip the TuringDB graph stage")
    parser.add_argument("--no-ai", action="store_true", help="skip AI briefs")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    start, end = resolve_window(args)
    log.info("Terrestrial pipeline: window %s → %s (end exclusive)", start, end)

    names = [s for s, _ in STAGES]
    selected = names
    if args.only:
        selected = [s for s in names if s in args.only]
    elif args.from_stage:
        selected = names[names.index(args.from_stage) :]
    if args.no_graph:
        selected = [s for s in selected if s != "graph"]
    if args.no_ai:
        selected = [s for s in selected if s != "brief"]

    for name, module_path in STAGES:
        if name not in selected:
            continue
        t0 = time.perf_counter()
        log.info("▶ %s", name)
        importlib.import_module(module_path).stage(start, end, args)
        log.info("✓ %s (%.1fs)", name, time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
