"""Military classification of ADS-B aircraft (CLAUDE.md §16.1).

For one readsb-style aircraft record, decide whether it is military and, if so, what it is and
who operates it, keeping every rule that fired as evidence so the UI can show *why*.

- military: readsb `dbFlags & 1`, presence in the ADS-B Exchange military register, or arrival
  through a military-only feed.
- role / airframe: ICAO Doc 8643 table > MDS designation of this airframe's model > type role
  learned from the register > ICAO description letter (see reference/roles.py).
- state: ICAO 24-bit address allocation (state of registry).
- org: NATO for the Luxembourg-registered NATO fleet (LX-N…), else the register's owner/operator,
  else "<state> military".
- callsign family: the leading letters of the callsign (RCH, FORTE, HOMER…), used by the nets
  engine to link aircraft flying under one mission callsign series.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from reference.aircraftdb import Register
from reference.icao_ranges import Ranges
from reference.roles import ICAO_TYPES, airframe_from_description, parse_mds

# ICAO type designators of unmanned aircraft seen on ADS-B (RQ-4, MQ-9, MQ-1C, MQ-4C, TB2, Heron…).
UAV_TYPES = {"Q4", "Q9", "Q1", "Q25", "MQ9", "RQ4", "Q4C", "TB2", "TB3", "HRON", "H450", "AKN"}
FAMILY = re.compile(r"^([A-Z]{2,6})\d")

ORG_PATTERNS = (
    (re.compile(r"\bAIR FORCE\b|\bUSAF\b|\bRAF\b|\bLUFTWAFFE\b"), "Air Force"),
    (re.compile(r"\bNAVY\b|\bNAVAIR\b|\bNAVAL\b"), "Navy"),
    (re.compile(r"\bARMY\b"), "Army"),
    (re.compile(r"\bMARINE CORPS\b|\bUSMC\b"), "Marine Corps"),
    (re.compile(r"\bCOAST GUARD\b"), "Coast Guard"),
    (re.compile(r"\bSPECIAL OPERATIONS\b"), "Special Operations"),
    (re.compile(r"\bCUSTOMS\b|\bBORDER\b"), "Border / Customs"),
    (re.compile(r"\bGOVERNMENT\b|\bROYAL FLIGHT\b|\bAMIRI\b|\bROYAL AIR WING\b"), "Government flight"),
)


@dataclass
class Classification:
    military: bool
    role: str = "unknown"
    airframe: str = "fixed_wing"
    uav: bool = False
    state: str | None = None
    state_code: str | None = None
    org: str | None = None
    callsign_family: str | None = None
    designation: str | None = None
    evidence: list[str] = field(default_factory=list)

    def as_props(self) -> dict:
        return {
            "military": self.military,
            "role": self.role,
            "airframe": self.airframe,
            "uav": self.uav,
            "state": self.state,
            "state_code": self.state_code,
            "org": self.org,
            "callsign_family": self.callsign_family,
            "designation": self.designation,
            "mil_evidence": self.evidence,
        }


def callsign_family(callsign: str | None) -> str | None:
    match = FAMILY.match((callsign or "").strip().upper())
    return match.group(1) if match else None


def _org(state: str | None, reg: str | None, ownop: str | None) -> tuple[str | None, str | None]:
    if reg and reg.upper().startswith("LX-N"):
        return "NATO", "registration LX-N… (NATO AEW&C fleet)"
    if ownop:
        upper = ownop.upper()
        for pattern, branch in ORG_PATTERNS:
            if pattern.search(upper):
                return (f"{state} {branch}" if state else branch), f"operator: {ownop}"
    if state:
        return f"{state} military", None
    return None, None


def classify(ac: dict, register: Register, ranges: Ranges, military_feed: bool = False) -> Classification:
    hex_address = (ac.get("hex") or "").lower().lstrip("~")
    airframe = register.get(hex_address) if hex_address else None
    evidence: list[str] = []
    if (ac.get("dbFlags") or 0) & 1:
        evidence.append("readsb military flag (dbFlags)")
    if airframe is not None:
        evidence.append("in the ADS-B Exchange military register")
    if military_feed:
        evidence.append("reported by a military-only feed")
    c = Classification(military=bool(evidence), evidence=evidence)
    if not c.military:
        return c

    type_code = (ac.get("t") or (airframe.icaotype if airframe else None) or "").upper() or None
    mds = parse_mds(airframe.model if airframe else None) or parse_mds(ac.get("desc"))
    learned = register.type_roles.get(type_code) if type_code else None
    if type_code in ICAO_TYPES:
        c.role, c.airframe = ICAO_TYPES[type_code]
        evidence.append(f"type {type_code} (ICAO Doc 8643)")
        if mds and mds[0] == c.role:  # keep a designation that agrees ("RC-135"), drop lookalikes
            c.designation = mds[2]
    elif mds:
        c.role, frame, c.designation = mds
        c.airframe = frame or c.airframe
        evidence.append(f"designation {mds[2]}")
    elif learned:
        c.role = learned.role
        c.airframe = learned.airframe or c.airframe
        evidence.append(
            f"type {type_code}: {learned.votes}/{learned.parsed} registered airframes are {learned.role}"
        )
    if c.airframe == "fixed_wing":
        described = airframe_from_description(airframe.short_type if airframe else None)
        if described and described != "fixed_wing":
            c.airframe = described
        elif ac.get("category") == "A7":
            c.airframe = "helicopter"
            evidence.append("emitter category A7 (rotorcraft)")
    if type_code in UAV_TYPES or ac.get("category") == "B6" or c.airframe == "uav":
        c.uav, c.airframe = True, "uav"

    allocation = ranges.lookup(hex_address) if hex_address else None
    if allocation is not None and allocation.code:
        c.state, c.state_code = allocation.country, allocation.code
        evidence.append(f"ICAO address block of {allocation.country}")
    reg = ac.get("r") or (airframe.reg if airframe else None)
    c.org, why = _org(c.state, reg, airframe.ownop if airframe else None)
    if why:
        evidence.append(why)
    c.callsign_family = callsign_family(ac.get("flight"))
    return c


class MilClassifier:
    """`classify` bound to loaded reference data."""

    def __init__(self, register: Register, ranges: Ranges):
        self.register = register
        self.ranges = ranges

    @classmethod
    def load(cls) -> MilClassifier:
        from reference.aircraftdb import register
        from reference.icao_ranges import ranges

        return cls(register(), ranges())

    def __call__(self, ac: dict, military_feed: bool = False) -> Classification:
        return classify(ac, self.register, self.ranges, military_feed)
