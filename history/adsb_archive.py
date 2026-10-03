"""Military flight traces from the adsb.lol daily archive (globe_history), streamed, never stored.

Each daily release (github.com/adsblol/globe_history_<year>, ODbL) is a tar split into ~2 GB
parts holding readsb output, including `traces/<xx>/trace_full_<hex>.json` (gzip) for every
aircraft seen that day:

    {"icao": hex, "r": reg, "t": type, "desc": …, "dbFlags": int, "timestamp": t0,
     "trace": [[dt, lat, lon, alt_baro|"ground", gs, track, flags, vrate, aircraft|null, …], …]}

We stream the parts as one tar, keep only aircraft with the readsb military flag (dbFlags & 1)
that were inside MIL_BBOX at least once, and write one compact parquet per day to
data/history/adsb/<date>.parquet (a few MB instead of ~4 GB). Resumable per day.

    uv run python -m history.adsb_archive --days 7          # the last 7 published days
    uv run python -m history.adsb_archive --date 2026-10-02
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import io
import json
import logging
import tarfile
import time
from datetime import date, timedelta

import httpx
import pandas as pd

from pipeline.config import HISTORY_DIR, MIL_BBOX
from reference.cache import USER_AGENT

log = logging.getLogger("terrestrial")

RELEASES = "https://api.github.com/repos/adsblol/globe_history_{year}/releases/tags/v{day:%Y.%m.%d}-planes-readsb-prod-0"
OUT = HISTORY_DIR / "adsb"
FT_TO_M = 0.3048
COLUMNS = [
    "hex",
    "ts",
    "lat",
    "lon",
    "alt_m",
    "gs_kn",
    "track",
    "vrate_fpm",
    "ground",
    "callsign",
    "reg",
    "type",
]


class Concatenated(io.RawIOBase):
    """Read several HTTP streams back to back as one file (the split tar parts)."""

    def __init__(self, client: httpx.Client, urls: list[str]):
        self.client = client
        self.urls = list(urls)
        self.chunks = None
        self.buffer = b""
        self.read_bytes = 0
        self._ctx = None

    def _next_part(self) -> bool:
        if self._ctx is not None:
            self._ctx.__exit__(None, None, None)
        if not self.urls:
            return False
        self._ctx = self.client.stream("GET", self.urls.pop(0), follow_redirects=True, timeout=120.0)
        response = self._ctx.__enter__()
        response.raise_for_status()
        self.chunks = response.iter_bytes(1 << 20)
        return True

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        while not self.buffer:
            if self.chunks is None and not self._next_part():
                return 0
            try:
                self.buffer = next(self.chunks)
            except StopIteration:
                self.chunks = None
                if not self._next_part():
                    return 0
        n = min(len(b), len(self.buffer))
        b[:n] = self.buffer[:n]
        self.buffer = self.buffer[n:]
        self.read_bytes += n
        return n


def parse_trace(blob: bytes) -> pd.DataFrame | None:
    """Rows for one aircraft's day, or None if it is not military or never in the area."""
    with contextlib.suppress(OSError):  # members may be stored uncompressed
        blob = gzip.decompress(blob)
    d = json.loads(blob)
    if not (d.get("dbFlags") or 0) & 1 or not d.get("trace"):
        return None
    min_lon, min_lat, max_lon, max_lat = MIL_BBOX
    t0 = float(d["timestamp"])
    rows = []
    callsign = None
    for p in d["trace"]:
        dt, lat, lon, alt, gs, track = p[0], p[1], p[2], p[3], p[4], p[5]
        vrate = p[7] if len(p) > 7 else None
        details = p[8] if len(p) > 8 else None
        if isinstance(details, dict) and details.get("flight"):
            callsign = details["flight"].strip() or callsign
        ground = alt == "ground"
        rows.append(
            (
                d["icao"],
                t0 + dt,
                lat,
                lon,
                0.0 if ground or alt is None else alt * FT_TO_M,
                gs,
                track,
                vrate,
                ground,
                callsign,
            )
        )
    df = pd.DataFrame(rows, columns=COLUMNS[:10])
    inside = df["lon"].between(min_lon, max_lon) & df["lat"].between(min_lat, max_lat)
    if not inside.any():
        return None
    df["reg"] = d.get("r")
    df["type"] = d.get("t")
    return df


def release_assets(day: date, client: httpx.Client) -> list[str]:
    response = client.get(
        RELEASES.format(year=day.year, day=day), headers={"Accept": "application/vnd.github+json"}
    )
    if response.status_code == 404:
        raise FileNotFoundError(f"no adsb.lol archive published for {day}")
    response.raise_for_status()
    parts = sorted(
        (a["name"], a["browser_download_url"]) for a in response.json()["assets"] if ".tar." in a["name"]
    )
    if not parts:
        raise FileNotFoundError(f"adsb.lol release for {day} has no tar parts")
    return [url for _, url in parts]


def extract_day(day: date, refresh: bool = False) -> pd.DataFrame:
    out = OUT / f"{day.isoformat()}.parquet"
    if out.exists() and not refresh:
        return pd.read_parquet(out)
    started = time.monotonic()
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        stream = Concatenated(client, release_assets(day, client))
        frames, seen = [], 0
        with tarfile.open(fileobj=io.BufferedReader(stream, 1 << 20), mode="r|") as tar:
            for member in tar:
                if not member.isfile() or "/trace_full_" not in member.name:
                    continue
                seen += 1
                df = parse_trace(tar.extractfile(member).read())
                if df is not None:
                    frames.append(df)
                if seen % 20000 == 0:
                    log.info(
                        "  %s: %d traces read, %d military kept, %.1f GB streamed",
                        day,
                        seen,
                        len(frames),
                        stream.read_bytes / 1e9,
                    )
    if not frames:
        raise RuntimeError(f"no military traces found for {day}: has the archive format changed?")
    result = pd.concat(frames, ignore_index=True)
    result["ts"] = pd.to_datetime(result["ts"], unit="s", utc=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    result.to_parquet(tmp, index=False)
    tmp.replace(out)
    log.info(
        "  %s: %d military aircraft, %d positions from %d traces in %.0f s",
        day,
        result["hex"].nunique(),
        len(result),
        seen,
        time.monotonic() - started,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--days", type=int, default=1, help="the last N published days")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    days = [args.date] if args.date else [date.today() - timedelta(days=k) for k in range(1, args.days + 1)]
    for day in days:
        try:
            extract_day(day, args.refresh)
        except FileNotFoundError as exc:
            log.warning("  %s", exc)


if __name__ == "__main__":
    main()
