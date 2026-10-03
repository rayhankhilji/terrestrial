"""Central configuration: region, time window, physics constants, score weights, paths.

Every tunable number in the pipeline lives here so the UI and docs can state exactly
what was used. Score weights are heuristic judgement calls, not calibrated values.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- Paths -------------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("TERRESTRIAL_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
TURING_DIR = DATA_DIR / "turing"  # mounted as the TuringDB server's -turing-dir

# --- Region and window -------------------------------------------------------------------

# Black Sea + Sea of Azov: (min_lon, min_lat, max_lon, max_lat)
BBOX: tuple[float, float, float, float] = (27.0, 40.5, 42.0, 47.5)

DEFAULT_DAYS = 90
# GFW SAR detections lag roughly five days behind real time.
DATA_LAG_DAYS = 5
# The operational question asks about the most recent 30 days of the window.
RECENT_DAYS = 30


def default_window(days: int = DEFAULT_DAYS, today: date | None = None) -> tuple[date, date]:
    """Return (start, end) with `end` exclusive: `days` days ending today - DATA_LAG_DAYS."""
    end = (today or date.today()) - timedelta(days=DATA_LAG_DAYS)
    return end - timedelta(days=days), end


# --- Physics -----------------------------------------------------------------------------

KM_PER_NM = 1.852

# Max sustained speed (knots) by broad vessel class, before the buffer below.
VMAX_KN: dict[str, float] = {"tanker": 15.0, "cargo": 14.0, "default": 15.0}
SPEED_BUFFER = 1.1
ELLIPSE_POINTS = 64
# Polygon used for matching when a gap is physically impossible (§6).
IMPOSSIBLE_RADIUS_KM = 5.0

# --- Geography ---------------------------------------------------------------------------

AOI_RADIUS_KM = 15.0
NEAR_AOI_KM = 50.0

# --- Scoring (heuristic) -----------------------------------------------------------------


@dataclass(frozen=True)
class ScoreWeights:
    sanctioned: int = 30
    gap_each: int = 10
    gap_cap: int = 30
    gap_near_occupied: int = 20
    verified_dark: int = 25
    covert_port_call: int = 20
    impossible_gap: int = 15
    encounter_each: int = 10
    encounter_cap: int = 20
    encounter_sanctioned: int = 15
    occupied_port_visit: int = 20
    identity_change_each: int = 5
    identity_change_cap: int = 15
    psc_detention: int = 5
    total_cap: int = 100

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


WEIGHTS = ScoreWeights()


@dataclass(frozen=True)
class ThreatWeights:
    """Live craft ranking (CLAUDE.md §16.1, M7): heuristic, transparent, every point explained.

    Two axes: `threat` (to Ukraine: who operates it, what it can do, where it is going) and
    `intel` (how significant it is to watch: missions, anomalies). Priority = threat +
    INTEL_SHARE × intel, capped at 100."""

    hostile_state: int = 40
    strike_role: int = 15
    isr_role: int = 8
    uav: int = 8
    near_ukraine_50km: int = 20
    near_ukraine_150km: int = 12
    near_ukraine_300km: int = 6
    near_front_50km: int = 12
    near_front_150km: int = 6
    over_occupied: int = 10
    inbound_to_ukraine: int = 10
    destination_near_ukraine: int = 5
    emergency_squawk: int = 25
    no_callsign: int = 5
    own_gnss_degraded: int = 8
    gnss_interference_area: int = 6
    isr_near_ukraine: int = 15
    mission_net: int = 10
    on_station: int = 8
    reroute_recent: int = 6
    naval_warship: int = 10
    cap: int = 100
    intel_share: float = 0.5

    def as_dict(self) -> dict:
        return asdict(self)


THREAT = ThreatWeights()
# ICAO address-block states treated as hostile to Ukraine (lower-case ISO alpha-2).
HOSTILE_STATES = frozenset({"ru", "by", "ir", "kp"})


# OpenSanctions risk topics that count as a sanctions-relevant listing.
SANCTION_TOPICS = frozenset({"sanction", "mare.shadow"})
DETENTION_TOPIC = "mare.detained"

# --- Global Fishing Watch ----------------------------------------------------------------

GFW_BASE_URL = "https://gateway.api.globalfishingwatch.org/v3/"
GFW_DATASETS = {
    "gaps": "public-global-gaps-events:latest",
    "encounters": "public-global-encounters-events:latest",
    "loitering": "public-global-loitering-events:latest",
    "port_visits": "public-global-port-visits-events:latest",
}
GFW_SAR_DATASET = "public-global-sar-presence:latest"
GFW_IDENTITY_DATASET = "public-global-vessel-identity:latest"
GFW_PAGE_SIZE = 500
GFW_VESSEL_BATCH = 40


def gfw_token() -> str:
    token = os.environ.get("GFW_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "GFW_API_TOKEN is not set. Register at https://globalfishingwatch.org/our-apis/tokens "
            "and put GFW_API_TOKEN=... in .env (see .env.example)."
        )
    return token


# --- OpenSanctions -----------------------------------------------------------------------

OPENSANCTIONS_MARITIME_URL = "https://data.opensanctions.org/datasets/latest/maritime/maritime.csv"
OPENSANCTIONS_INDEX_URL = "https://data.opensanctions.org/datasets/latest/maritime/index.json"

# --- TuringDB ----------------------------------------------------------------------------

TURINGDB_URL = os.environ.get("TURINGDB_URL", "http://localhost:6666").rstrip("/")
GRAPH_NAME = "terrestrial"

# --- Featherless (AI briefs) -------------------------------------------------------------

FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"
FEATHERLESS_DEFAULT_MODEL = "Qwen/Qwen3-32B"
BRIEF_MIN_RISK = 40
BRIEF_MAX_VESSELS = 30


def featherless_key() -> str | None:
    key = os.environ.get("FEATHERLESS_API_KEY", "").strip()
    return key or None


def featherless_model() -> str:
    return os.environ.get("FEATHERLESS_MODEL", "").strip() or FEATHERLESS_DEFAULT_MODEL


# --- Live layer ---------------------------------------------------------------------------

LIVE_DIR = DATA_DIR / "live"  # recordings for replay
# Wider "theatre" box for live air picture and news: Black Sea, Ukraine, south-west Russia.
THEATRE_BBOX: tuple[float, float, float, float] = (22.0, 40.0, 45.0, 53.0)
# Military picture (§16): Europe, the Black Sea and the eastern Mediterranean. The /mil ADS-B
# feeds are global and small, so the wider box costs nothing and keeps NATO ISR/tanker tracks
# from the UK, Baltic and Mediterranean in view.
MIL_BBOX: tuple[float, float, float, float] = (-12.0, 30.0, 60.0, 72.0)
REFERENCE_DIR = RAW_DIR / "reference"
HISTORY_DIR = DATA_DIR / "history"  # normalised historical tables (predictive layer, §16.4)
MODELS_DIR = DATA_DIR / "models"
REFERENCE_MAX_AGE_DAYS = 7


def optional_key(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None
