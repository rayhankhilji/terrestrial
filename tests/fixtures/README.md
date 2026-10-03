# Test fixtures

Every file here is a real response (or a verbatim subset of rows from a real response),
saved from the live source. Nothing is hand-written.

| File | Source | Licence |
|---|---|---|
| `opensanctions/maritime_sample.csv` | Rows copied verbatim from OpenSanctions `maritime.csv` | CC BY-NC 4.0, © OpenSanctions |
| `gfw/*` | Global Fishing Watch API v3 responses for the Black Sea window, trimmed to a few entries | CC BY-NC 4.0, © Global Fishing Watch |
| `live/adsbfi_point.json` | adsb.fi `/v2/lat/44.0/lon/30.6/dist/250` response | ODbL, © adsb.fi contributors |
| `live/adsblol_mil_subset.json` | adsb.lol `/v2/mil` response, first 15 aircraft kept | ODbL, © adsb.lol contributors |
| `live/gdelt_export_sample.tsv` | Verbatim rows of GDELT 2.0 export `20261003051500`: every theatre row plus 60 others | GDELT terms: free use with citation |
| `reference/basic_ac_db_sample.json.gz` | Verbatim lines of ADS-B Exchange `basic-ac-db.json.gz`: the airframes in the two live fixtures plus a few of each common military type | ADS-B Exchange free database download |
| `reference/flags.js` | wiedehopf/tar1090 `html/flags.js` (ICAO 24-bit address allocation table) | MIT |
| `reference/mids.json` | michaeljfazio/MIDs `mids.json` (ITU Maritime Identification Digits) | Apache-2.0 |
| `reference/airports_sample.csv` | Verbatim rows of OurAirports `airports.csv` (Saky, Belbek, RAF Benson, Belfast Intl, Heathrow…) | Public domain |
| `live/wikidata_facilities_subset.json` | Rows from the cached Wikidata SPARQL result for theatre facilities (Black Sea ports, refineries, bases) | CC0, Wikidata |
