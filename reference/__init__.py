"""Reference ontology for the military picture (CLAUDE.md §16).

Static, slowly changing open datasets that turn a raw ADS-B / AIS message into a typed entity:
who operates it (state / organisation), what it is (role, helicopter, UAV) and where it can
land (airfields). Each loader downloads once into data/raw/reference/ and re-downloads only
when the cached copy is older than REFERENCE_MAX_AGE_DAYS (cache first).
"""
