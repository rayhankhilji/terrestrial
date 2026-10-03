"""Aircraft role and airframe class.

Three sources, in order of precedence:

0. **ICAO Doc 8643 type designators** listed in ICAO_TYPES below (widely used non-US military
   types). Listing a type there is an explicit decision, so it wins: model strings of these
   types ("A-400M", "CN-235") look like MDS designations but are not, and their US-registered
   examples (e.g. Coast Guard HC-144 = CN35) do not represent the type's role elsewhere.
1. **US/NATO Mission Design Series** parsed from the model name ("Boeing KC-135R" → K = tanker,
   "Sikorsky UH-60A" → U = utility + H = helicopter, "RQ-4B" → R = reconnaissance + Q = UAV).
   Letters follow DoD 4120.15-L (Model Designation of Military Aerospace Vehicles).
2. **Type designator → role learned from the aircraft database**: for each ICAO type code,
   the majority MDS role across all airframes of that type whose model name parses. This is
   how a bare `K35R` or `H60` in the live feed gets a role with no hand-written table.

Airframe class falls back to the ICAO description code (first letter H = helicopter,
T = tilt-rotor) when no MDS vehicle letter is available.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

ROLES = (
    "isr", "electronic", "tanker", "airlift", "fighter", "strike", "bomber", "maritime_patrol",
    "sar", "utility", "vip", "trainer", "multi_mission", "weather", "unknown",
)  # fmt: skip

# DoD 4120.15-L mission letters (modified mission or basic mission).
MISSION = {
    "A": "strike", "B": "bomber", "C": "airlift", "E": "electronic", "F": "fighter", "H": "sar",
    "K": "tanker", "M": "multi_mission", "O": "isr", "P": "maritime_patrol", "R": "isr",
    "S": "maritime_patrol", "T": "trainer", "U": "utility", "V": "vip", "W": "weather",
}  # fmt: skip
# Vehicle-type letters (last letter when present).
VEHICLE = {"H": "helicopter", "Q": "uav", "V": "tiltrotor"}
STATUS = set("GJNYZ")  # status prefixes (grounded, special test, permanent…): not a mission
# Manufacturer model codes that look like MDS designations but are not ("BD-700", "PC-12",
# "Su-34", "An-26"…). Russian and Soviet designations are covered by ICAO_TYPES instead.
NOT_MDS = {
    "AN", "AS", "ATR", "AW", "BAE", "BD", "BK", "BN", "CL", "CN", "CRJ", "DA", "DC", "DHC", "EC", "EMB",
    "ERJ", "HS", "IL", "KA", "MBB", "MD", "MI", "MIG", "PA", "PC", "PZL", "SA", "SC", "SF", "SR", "SU",
    "TB", "TU", "YAK",
}  # fmt: skip

MDS = re.compile(r"(?<![A-Z0-9])([A-Z]{1,4})-(\d{1,3})[A-Z]?\b")
# A variant suffix after a Soviet/Chinese designation ("Tu-134 A-3", "Tu-154 B-2", "Y-8 F-200W")
# is not an MDS designation.
VARIANT_OF = re.compile(r"\b(TU|AN|IL|YAK|Y|MI|KA|SU|MIG|BE)-?\d+[A-Z]*[\s-]*$")

# ICAO Doc 8643 designators of common non-US military types → (role, airframe).
ICAO_TYPES: dict[str, tuple[str, str]] = {
    "EUFI": ("fighter", "fixed_wing"), "TOR": ("strike", "fixed_wing"), "RFAL": ("fighter", "fixed_wing"),
    "MIR2": ("fighter", "fixed_wing"), "GRIF": ("fighter", "fixed_wing"), "F35": ("fighter", "fixed_wing"),
    "A400": ("airlift", "fixed_wing"), "C295": ("airlift", "fixed_wing"), "CN35": ("airlift", "fixed_wing"),
    "C27J": ("airlift", "fixed_wing"), "MRTT": ("tanker", "fixed_wing"), "A139": ("utility", "helicopter"),
    "E3TF": ("electronic", "fixed_wing"), "E3CF": ("electronic", "fixed_wing"), "E737": ("electronic", "fixed_wing"),
    "SB39": ("electronic", "fixed_wing"), "R135": ("isr", "fixed_wing"), "P8": ("maritime_patrol", "fixed_wing"),
    "P3": ("maritime_patrol", "fixed_wing"), "ATLA": ("maritime_patrol", "fixed_wing"),
    "Q4": ("isr", "uav"), "Q9": ("isr", "uav"), "Q1": ("isr", "uav"), "HRON": ("isr", "uav"),
    "NH90": ("utility", "helicopter"), "EH10": ("utility", "helicopter"), "AS32": ("utility", "helicopter"),
    "EC25": ("utility", "helicopter"), "A169": ("utility", "helicopter"), "LYNX": ("maritime_patrol", "helicopter"),
    "TIGR": ("strike", "helicopter"), "HAWK": ("trainer", "fixed_wing"), "PC21": ("trainer", "fixed_wing"),
    "PC9": ("trainer", "fixed_wing"), "M346": ("trainer", "fixed_wing"), "L39": ("trainer", "fixed_wing"),
    "TEX2": ("trainer", "fixed_wing"), "G115": ("trainer", "fixed_wing"), "TUCA": ("trainer", "fixed_wing"),
}  # fmt: skip


def parse_mds(model: str | None) -> tuple[str, str | None, str] | None:
    """(role, airframe or None, designation) from a model name, or None if it has no MDS."""
    if not model:
        return None
    text = model.upper()
    for match in MDS.finditer(text):
        letters, number = match.group(1), match.group(2)
        if letters in NOT_MDS or VARIANT_OF.search(text[: match.start()]):
            continue
        if len(letters) == 1 and letters in VEHICLE and letters != "H":
            # "V-22": a vehicle-type letter with no mission letter.
            return "unknown", VEHICLE[letters], f"{letters}-{number}"
        vehicle = VEHICLE.get(letters[-1]) if len(letters) > 1 else None
        mission = letters[:-1] if vehicle else letters
        mission = mission.lstrip("".join(STATUS)) or mission
        if not mission or mission[0] not in MISSION:
            continue
        return MISSION[mission[0]], vehicle, f"{letters}-{number}"
    return None


def airframe_from_description(short_type: str | None) -> str | None:
    """ICAO aircraft description (e.g. 'H2T', 'L4J'): first letter is the airframe class."""
    if not short_type:
        return None
    return {"H": "helicopter", "T": "tiltrotor", "R": "tiltrotor", "G": "gyrocopter"}.get(
        short_type[0], "fixed_wing"
    )


MIN_VOTES = 3


@dataclass(frozen=True)
class LearnedType:
    role: str
    airframe: str | None
    votes: int  # airframes agreeing with `role`
    parsed: int  # airframes of this type whose model name parsed


def learn_type_roles(rows) -> dict[str, LearnedType]:
    """ICAO type → majority MDS role and airframe across airframes whose model name parses.

    Types with fewer than MIN_VOTES agreeing airframes, or without a strict majority, are left
    out (too little evidence or no consensus).
    """
    roles: dict[str, Counter] = {}
    frames: dict[str, Counter] = {}
    for icaotype, model in rows:
        parsed = parse_mds(model)
        if icaotype and parsed:
            roles.setdefault(icaotype, Counter())[parsed[0]] += 1
            if parsed[1]:
                frames.setdefault(icaotype, Counter())[parsed[1]] += 1
    table = {}
    for icaotype, counter in roles.items():
        role, n = counter.most_common(1)[0]
        if n < MIN_VOTES or n * 2 <= sum(counter.values()):
            continue
        frame = frames.get(icaotype)
        airframe = frame.most_common(1)[0][0] if frame and frame.most_common(1)[0][1] * 2 >= n else None
        table[icaotype] = LearnedType(role, airframe, n, sum(counter.values()))
    return table
