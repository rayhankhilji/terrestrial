"""Naval classification of AIS vessels (CLAUDE.md §16.1).

Most warships do not transmit AIS; those that do are recognisable by:
- ITU-R M.1371 ship type 35 ("engaged in military operations") or 55 ("law enforcement");
- a navy name prefix (USS, HMS, TCG…);
- the flag state, from the MMSI's Maritime Identification Digits.

Every rule that fired is kept as evidence. Submarines are never inferred from AIS.
"""

from __future__ import annotations

import re

from reference.mids import Mids

# Ship-name prefixes of navies and naval auxiliaries that operate in or transit to the theatre.
NAVY_PREFIX = re.compile(
    r"^(USS|USNS|HMS|RFA|HMCS|TCG|ORP|NRP|ITS|ESPS|HDMS|HNLMS|HNOMS|HSWMS|FGS|NMS|BNS|HS|SNS|ROKS)\s",
    re.IGNORECASE,
)
TYPE_ROLES = {35: "warship", 55: "law_enforcement"}


def classify(props: dict, mmsi: str, mids: Mids) -> dict:
    """Naval fields to merge into a vessel's props."""
    evidence: list[str] = []
    role = None
    code = props.get("type_code")
    if code in TYPE_ROLES:
        role = TYPE_ROLES[code]
        evidence.append(
            f"AIS ship type {code} ({'military operations' if code == 35 else 'law enforcement'})"
        )
    name = props.get("name") or ""
    prefix = NAVY_PREFIX.match(name)
    if prefix:
        role = role or "warship"
        evidence.append(f"navy name prefix {prefix.group(1).upper()}")
    flag = mids.flag(mmsi)
    return {
        "military": role is not None,
        "naval_role": role,
        "state": flag.country if flag else None,
        "state_code": flag.code if flag else None,
        "org": f"{flag.country} Navy" if role == "warship" and flag else None,
        "mil_evidence": evidence,
    }
