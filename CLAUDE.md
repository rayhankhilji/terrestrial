# TERRESTRIAL — Black Sea dark-vessel detection (EDTH London, Challenge 1)

This file is the single source of truth for the build. Read all of it before writing code.
§15 and §16 record decisions taken after the original brief; where they conflict with an
earlier section, the later section wins (§16 > §15 > §1–14).

## 1. What we're building

An OSINT pipeline + web app that **detects** vessels hiding their movements in the Black Sea, **verifies** where they actually went using satellite radar, and **tracks** them as a connected network over time.

Core idea ("bridge the gap"): when a ship switches off AIS, we know where it went dark, where it reappeared, and how long it was gone. That defines an ellipse of everywhere it could physically have been. Satellite radar (SAR) detections inside that ellipse, during that time, that match no AIS-broadcasting ship = evidence of where the dark ship actually was. Headline finding: **probable covert port calls at occupied Ukrainian ports.**

Operational question (from the challenge brief): *which hulls have called at occupied ports in the last 30 days?* Grain looted from occupied Sevastopol and Berdyansk is shipped with AIS off; ships switch back on mid-sea reporting departure ports far from occupied territory. Shadow-fleet tankers loiter off Novorossiysk waiting for ship-to-ship transfers.

Primary target: Challenge 1 (Dark Vessels, OSINT). Secondary: the graph layer runs on TuringDB with per-day commits, which may also qualify for Challenge 4 (TuringDB). Core detection must work without the graph layer.

Hackathon scope. One demo, real data, works offline.

## 2. Non-goals

- No live streaming AIS ingestion (stretch only, see §11).
- No auth, no users, no deployment. Runs on a laptop.
- No ML model training. We use Global Fishing Watch's existing SAR detections.
- No raw satellite image processing.
- No claims of certainty in the UI. Everything is "candidate" / "likely" with a visible evidence chain.

## 3. Stack

| Layer | Choice |
|---|---|
| Pipeline + API | Python 3.11, `uv`, `httpx`, `pandas`, `shapely`, `pyproj`, `FastAPI`, `uvicorn`, `pytest` |
| Graph | TuringDB Community server (Docker image `turingdbai/turingdb:3.0`, or native `turingdb` on arm64 macOS / Linux), OpenCypher over its HTTP API (§15.1) |
| Frontend | Vite + React + TypeScript, `maplibre-gl` (globe projection), `deck.gl` via `@deck.gl/maplibre`, `react-force-graph-2d` |
| Basemap | CARTO Dark Matter style: `https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json` (no token) |
| AI | Featherless.ai, OpenAI-compatible chat completions over `httpx` (§15.4) |
| Storage | Raw API responses cached as JSON in `data/raw/`; processed tables as parquet in `data/processed/` |

## 4. Data sources

All sources must be cached to disk on first fetch. The demo must run with no network except basemap tiles.

**Global Fishing Watch API v3** (primary). Base URL `https://gateway.api.globalfishingwatch.org/v3/`, header `Authorization: Bearer $GFW_API_TOKEN`. Token: register at https://globalfishingwatch.org/our-apis/tokens, put in `.env` as `GFW_API_TOKEN`. Licence: non-commercial, **attribution required** — show "Data: Global Fishing Watch" in the UI footer.

| Dataset id | Use |
|---|---|
| `public-global-gaps-events:latest` | AIS-off events (all are intentional gaps) — the core signal |
| `public-global-encounters-events:latest` | Ship-to-ship meetings (STS transfers) |
| `public-global-loitering-events:latest` | Loitering |
| `public-global-port-visits-events:latest` | Port visits |
| `public-global-sar-presence:latest` (4Wings API) | SAR vessel detections; filter `matched='false'` for detections with no AIS match. Data lags ~5 days. |
| Vessels API | Identity history (name / flag / MMSI / IMO over time), vessel type, registry info where present |

Caveats you must handle:
- GFW does not expose raw AIS tracks, only events. Do not try to rebuild tracks.
- GFW gap events require ≥ 12 h duration **and a start ≥ 50 nm from shore**. Near-shore switch-offs are invisible to this dataset; say so in the UI instead of implying full coverage.
- SAR coverage depends on satellite passes. Absence of a detection proves nothing; never score it.
- **Verify every field name against a real saved response before writing parsing code.** Save one real response per endpoint to `tests/fixtures/` and parse from those in tests. Do not guess schemas.
- Paginate (`limit`/`offset`) and respect rate limits. Fetch once, then read from cache.
- Use `httpx` directly against the REST API. Don't mix in the official Python client.

**OpenSanctions** — maritime collection `https://data.opensanctions.org/datasets/latest/maritime/maritime.csv` (§15.2). Columns: `type, caption, imo, risk, countries, flag, mmsi, id, url, datasets, aliases`. Keep `type == "VESSEL"`. Non-commercial use, attribution required.

**Static areas of interest (AOIs)** — hardcode in `pipeline/aoi.py` as point + 15 km radius:

| Name | Lat | Lon | occupied_ua |
|---|---|---|---|
| Sevastopol | 44.61 | 33.52 | true |
| Feodosia | 45.03 | 35.40 | true |
| Kerch | 45.35 | 36.47 | true |
| Berdyansk | 46.75 | 36.79 | true |
| Mariupol | 47.10 | 37.55 | true |
| Port Kavkaz | 45.33 | 36.67 | false |
| Novorossiysk | 44.72 | 37.79 | false |

Coordinates are approximate port centres. Fine for 15 km radii.

**Region and time window.** Black Sea + Azov bbox: lon 27.0–42.0, lat 40.5–47.5. Default window: 90 days ending (today − 5 days). Both are config values in `pipeline/config.py`.

## 5. Pipeline

Single entrypoint: `uv run python -m pipeline.run` (flags: `--days`, `--refresh` to bypass cache, `--no-graph`, `--only <stage>`). Stages run in order. Each stage reads the previous stage's output from disk.

1. **fetch** — pull all datasets in §4 for bbox + window → `data/raw/`. Collect every vessel id seen in events, fetch identities. Download the OpenSanctions CSV.
2. **normalise** → `data/processed/`: `vessels.parquet`, `identities.parquet`, `gaps.parquet`, `encounters.parquet`, `loitering.parquet`, `port_visits.parquet`, `sar.parquet` (unmatched only), `sanctions.parquet`.
3. **envelope** — for each gap compute the reachability ellipse (§6) → `gap_envelopes.parquet` (gap_id, polygon WKT, vmax_kn, impossible flag).
4. **match** — for each gap, find unmatched SAR detections inside its ellipse whose date falls within [gap start date, gap end date] inclusive → `candidates.parquet` (gap_id, sar_id, in_aoi name or null, plus the §15.5 time-consistency fields).
5. **score** — per-vessel risk score with a breakdown (§7) → `scores.parquet`, `score_breakdown.parquet`.
6. **insights** — dark tracks, predictions, highlights, occupied-port calls (§15.5–15.7).
7. **brief** — AI analyst briefs (§15.4). Skipped with `--no-ai` or when no key is configured.
8. **graph** — build and load the TuringDB graph with per-day commits (§8). Skipped with `--no-graph`.

Each stage logs counts (rows in, rows out). If a stage outputs zero rows where data is expected, raise. Don't continue silently.

## 6. Reachability ellipse (the core algorithm)

Given gap off-position A, on-position B, duration T hours, max speed v (knots):

- Max distance travelled D = v × T × 1.852 km.
- Every reachable point P satisfies dist(A,P) + dist(P,B) ≤ D. That is an ellipse with foci A and B.
- Semi-major a = D/2. Half focal distance c = dist(A,B)/2. Semi-minor b = √(a² − c²).
- Compute in a local azimuthal-equidistant projection centred on the midpoint of A and B (pyproj). Rotate the ellipse to the A→B bearing, sample 64 points, reproject to WGS84, and return a shapely Polygon.
- If a < c, the ship could not have covered A→B in T at speed v. Set `impossible = true`. This is a spoofing/identity indicator. Use a 5 km circle around B as the polygon so matching still runs.

v by vessel type: tanker 15 kn, cargo/bulk 14 kn, everything else 15 kn, then ×1.1 buffer. These constants live in config.

Required unit tests:
- A == B gives a circle of radius D/2.
- A known A, B, T produces an ellipse that contains the midpoint and excludes a point at distance D+1 km.
- The impossible case sets the flag.

## 7. Risk score

Transparent and additive, capped at 100. Every point awarded is stored as a breakdown row (`reason`, `points`, `evidence_ref`) so the UI can show exactly why.

| Signal | Points |
|---|---|
| Vessel IMO/MMSI on OpenSanctions with a `sanction` or `mare.shadow` topic | +30 |
| Each AIS gap | +10 (cap 30) |
| Gap starting or ending within 50 km of an `occupied_ua` AOI | +20 (once) |
| **Unmatched SAR detection inside a gap ellipse during the gap** ("verified dark") | +25 (once) |
| …and that detection is inside an `occupied_ua` AOI ("probable covert port call") | +20 (once) |
| Impossible gap (§6) | +15 (once) |
| Each encounter | +10 (cap 20) |
| Encounter counterpart is sanctioned | +15 (once) |
| Port visit within an `occupied_ua` AOI | +20 (once) |
| Each flag or name change in the identity history within the window | +5 (cap 15) |
| Port-state-control detention record on OpenSanctions (`mare.detained`, not a sanction) | +5 (once) |

The weights are judgement calls for the demo. Keep them in config and say so in the UI ("heuristic score").

## 8. Graph layer (TuringDB)

Setup:
- `docker compose up -d turingdb` (any platform) or native `turingdb -demon` on arm64 macOS / Linux. REST on `http://localhost:6666`, visualiser on `http://localhost:8080`.
- Official skill: `npx skills add https://github.com/turing-db/turingdb-skills`. Cypher subset notes: variable-length paths use a postfix quantifier (`-[e]->{1,3}`), not `[*1..3]`; aggregates are `count`/`avg` only; `UNWIND` takes literal lists only; separate node and edge `CREATE`s inside one change need a `COMMIT` between them.
- **Check every Cypher feature you rely on** with a tiny test query before building on it. If something isn't supported, expand hops explicitly instead.

Model:

```
Nodes
  Vessel {vessel_id, imo, mmsi, name, flag, vessel_type, sanctioned, risk}
  Port {name, lat, lon, occupied_ua}            // AOIs from §4
  Gap {gap_id, start, end, duration_h, off_lat, off_lon, on_lat, on_lon, impossible, day}
  Encounter {enc_id, start, end, lat, lon, day}
  SarDetection {sar_id, date, lat, lon}
  Sanction {source, program}
Edges
  (Vessel)-[:HAD_GAP]->(Gap)
  (Gap)-[:NEAR]->(Port)                          // within 50 km
  (Gap)-[:CANDIDATE]->(SarDetection)
  (SarDetection)-[:AT]->(Port)                   // if inside AOI
  (Vessel)-[:IN_ENCOUNTER]->(Encounter)
  (Vessel)-[:VISITED {start, end}]->(Port)
  (Vessel)-[:LISTED]->(Sanction)
```

Every edge carries `provenance` (`observed` | `inferred`) and `source` (§15.6).

Loading with versioning:
1. **Base commit**: Vessels, Ports and Sanctions. Write a JSONL in the TuringDB import format (node and relationship ids must each start at 0 and increase by 1 with no gaps), copy it to the server's `data/` directory, then `LOAD JSONL 'base.jsonl' AS terrestrial`.
2. **One commit per day** in the window: `CHANGE NEW` → that day's Gap/Encounter/SarDetection nodes → `COMMIT` → `MATCH` existing nodes then `CREATE` the edges → `CHANGE SUBMIT`.
3. Record `{date: commit_hash}` in `data/processed/commits.json` (hashes from `CALL db.history()`).

Queries the API needs (each in `graph/queries.py` with a test):
- **network(vessel, hops≤3, as_of)**: check out the commit for `as_of`, then return vessels reachable through shared encounters, shared ports, or shared candidate detections, with paths.
- **shadow_cluster**: vessels within 2 encounter-hops of any sanctioned vessel.
- **diff(date1, date2)**: run network/cluster at both commits and set-diff the nodes and edges in Python. Returns what appeared between the two dates.

## 9. API (FastAPI, `api/main.py`)

Read-only, serves from `data/processed/` plus TuringDB. Coordinates are GeoJSON `[lon, lat]`.

| Route | Returns |
|---|---|
| `GET /api/meta` | Window, dataset freshness, counts, attribution, coverage caveats |
| `GET /api/vessels?min_risk=0` | Ranked list: id, name, flag, imo, risk, top 3 reasons |
| `GET /api/vessels/{id}` | Dossier: identity history, all events, score breakdown, candidate detections, sanctions, dark tracks, predictions, brief |
| `GET /api/events?type=gap\|encounter\|loitering\|port_visit&from&to` | GeoJSON FeatureCollection |
| `GET /api/sar?from&to` | GeoJSON of unmatched SAR detections |
| `GET /api/gaps/{gap_id}/envelope` | GeoJSON: ellipse polygon, A, B, candidate detections, dark track |
| `GET /api/occupied-calls?days=30` | Vessels with evidence of occupied-port calls in the last N days of the window |
| `GET /api/highlights` | Ranked key findings, each with evidence refs |
| `GET /api/predictions` | Currently-dark vessels with reachability now + destination likelihoods |
| `GET /api/network/{vessel_id}?hops=2&as_of=YYYY-MM-DD` | `{nodes, links}` for the force graph |
| `GET /api/network/diff?from&to` | `{added_nodes, added_links}` |
| `GET /api/aois` | GeoJSON of AOI circles |

Every route gets a pytest smoke test against the processed fixtures.

## 10. Frontend (`web/`)

One screen, dark theme:
- **Left sidebar**: vessel list ranked by risk, with a coloured risk badge and top reason. Search by name/IMO/MMSI. Quick filters including "occupied-port calls (30 d)".
- **Centre map**: 2D map / 3D globe toggle. AOI circles (occupied ports in red), gap A→B arcs, unmatched SAR dots (small, grey), encounters (amber), loitering, port visits. Selecting a vessel highlights its events. Clicking a gap draws its ellipse plus candidate detections and the reconstructed dark track, which is the "money shot".
- **Bottom timeline**: date slider + play over the window with an event-density histogram. The map filters to events up to that date, and the network panel uses `as_of` = slider date.
- **Right drawer (vessel dossier)**: identity history table, score breakdown (each reason → clickable evidence that pans the map to it), sanctions with pivot links, AI brief, prediction, and a network panel (`react-force-graph-2d`) with a "show what changed since…" diff toggle.
- **Highlights**: key findings feed; each card flies the map to its evidence.
- **Footer**: "Data: Global Fishing Watch, OpenSanctions. Heuristic risk score — candidate findings, not conclusions."

No component library. Plain CSS with variables. Make it readable on a projector: large type, high contrast.

## 11. Build order

Each phase ends with something that works. Don't start a phase until the previous one passes its check.

| # | Phase | Done when |
|---|---|---|
| 0 | Repo scaffold, `.env.example`, `uv` project, `web/` Vite app, `pytest` runs | `uv run pytest` and `npm run dev` both work |
| 1 | fetch + normalise for gaps, SAR, vessels, sanctions (§5 steps 1–2) | Parquet files exist with non-zero rows; fixtures saved |
| 2 | envelope + match (§6) | Unit tests pass; ≥1 gap with a candidate SAR detection, printed to console |
| 3 | score (§7) + API vessels/dossier/envelope routes | `/api/vessels` returns a ranked list with breakdowns |
| 4 | Frontend: list, map, gap ellipse on click, dossier | Click top vessel → its gap → ellipse + detections render |
| 5 | Encounters, loitering, port visits added through all layers | Visible on map; feeding the score |
| 6 | TuringDB graph + per-day commits + network/diff routes + network panel | Timeline slider changes the network; diff shows new links |
| 7 | Insights: dark tracks, occupied-port calls, highlights, predictions, AI briefs, 3D globe | Each visible in the UI with its evidence chain |
| 8 | Demo polish: pick demo vessel, preload cache, rehearse | Full demo runs with wifi off (except basemap) |

Stretch only, after phase 8: live AIS from aisstream.io for spoofing checks (position jumps, positions over land).

**Feature freeze: Sunday 09:00.** After that, only bug fixes and demo prep.

## 12. Commands

```
cp .env.example .env                       # add GFW_API_TOKEN (+ FEATHERLESS_API_KEY for briefs)
uv sync
docker compose up -d turingdb              # graph server (REST :6666, visualiser :8080)
uv run python -m pipeline.run --days 90    # full pipeline incl. graph
uv run python -m pipeline.run --no-graph   # core only
uv run uvicorn api.main:app --reload --port 8000
cd web && npm install && npm run dev       # proxies /api to :8000
docker compose down                        # stop the graph server
uv run pytest
```

## 13. Directory layout

```
CLAUDE.md  README.md  LICENSE  .env.example  pyproject.toml  docker-compose.yml  Makefile
pipeline/   config.py aoi.py geo.py gfw.py opensanctions.py fetch.py normalise.py envelope.py
            match.py score.py tracks.py insights.py brief.py featherless.py io.py run.py
graph/      client.py build.py queries.py
api/        main.py
web/        (Vite React TS app)
tests/      fixtures/  test_envelope.py test_score.py test_graph.py test_api.py ...
docs/       methodology, demo script, screenshots
data/       raw/ processed/ turing/        (gitignored)
```

## 14. Rules for the agent

- **Never fabricate data.** No mock vessels, no invented events, no placeholder coordinates in the running app. Test fixtures must be real saved API responses.
- **Fail loudly.** Missing token, empty response or schema mismatch → raise with a clear message. No silent fallbacks, no swallowed exceptions.
- **Cache first.** Never hit an external API at request time from the web app. The API only reads from disk and TuringDB.
- **One way to do each thing.** One HTTP client (`httpx`), one geometry library (`shapely` + `pyproj`), one state approach in React.
- **Small, surgical changes.** Keep each module single-purpose. Don't refactor working phases while building new ones.
- **Language in the UI and API is probabilistic**: "candidate", "likely", "consistent with". Never "confirmed".
- **Secrets only in `.env`.** Never commit `.env` or `data/`.
- When unsure about a GFW field or a TuringDB Cypher feature, check it against the docs or a live test query before writing code that depends on it.
- Commit early and often with clear messages; the repo is public.

## 15. Decisions taken after the original brief

### 15.1 TuringDB transport
`turingdb` on PyPI ships wheels only for arm64 macOS and Linux, so it cannot be installed on Intel Macs. The official SDK's default (`json`) backend is a thin wrapper over one HTTP endpoint (`POST /query`, Cypher in the body, `graph` / `change` (hex) / `commit` as query params). `graph/client.py` reimplements exactly that contract with `httpx`, so the same code runs on every platform and we keep a single HTTP client. The server runs from the official Docker image (`docker-compose.yml`) or natively where the wheel exists. Time travel = `LOAD COMMIT '<hash>'` then the `commit` param, mirroring the SDK's `checkout(commit=...)`.

### 15.2 OpenSanctions file
`targets.simple.csv` is 464 MB; the maritime collection (`maritime.csv`, ~5 MB) is OpenSanctions' own vessel-screening export with the same upstream lists (OFAC, EU, UK, UA War & Sanctions, Black Sea MoU…), plus MMSI and risk topics. We use it. `sanction` and `mare.shadow` topics count as sanctions-relevant listing; `mare.detained` (port-state detentions) is shown separately and scored lightly.

### 15.3 SAR time resolution
The 4Wings report is requested at HOURLY temporal resolution so each unmatched detection carries the hour of the satellite pass, which enables §15.5.

### 15.4 AI briefs (Featherless)
The team uses Featherless.ai (`https://api.featherless.ai/v1`, OpenAI-compatible, key `FEATHERLESS_API_KEY`, model in config). Briefs are generated **in the pipeline** for the top-ranked vessels and cached in `data/processed/briefs.json`, so the demo stays offline and the cache-first rule holds. The prompt contains only the vessel's dossier JSON; the model must cite fact ids, and the pipeline rejects a brief that cites an id not present in the dossier. The UI labels briefs as AI-generated with model name and timestamp.

### 15.5 Dark tracks (tracking across the gap)
Beyond the date-window match of §5.4, each candidate is checked for time consistency using the detection hour: dist(A,P) ≤ v·(t_det − t_off) and dist(P,B) ≤ v·(t_on − t_det). Time-consistent candidates are chained into the longest feasible path A → P₁ → … → B (consecutive legs must be feasible at v), giving a reconstructed dark track and re-identifying the hull at B as the one that went dark at A. Identity changes across the gap (name/flag/MMSI from the identity history) are flagged.

### 15.6 Ontology and provenance
Entity types follow the FollowTheMoney (FtM) vocabulary used by OpenSanctions where one exists (Vessel, Organization, Sanction). Every fact carries `source` (GFW events, GFW SAR, GFW identity, OpenSanctions, Terrestrial inference) and `provenance` (`observed` vs `inferred`). Inferred facts list the observed facts they were derived from. Pivot links (OpenSanctions profile, GFW vessel viewer, IMO lookups) let analysts verify outside the tool.

### 15.7 Insights
- **Occupied-port calls (N days)**: per vessel, the union of (a) AIS port visits in occupied AOIs, (b) probable covert port calls (time-consistent SAR candidates in occupied AOIs), (c) gaps starting/ending within 50 km of occupied AOIs — each tagged observed/inferred.
- **Highlights**: deterministic, ranked findings computed from the tables, each with evidence refs.
- **Predictions** (model estimates, labelled as such): for vessels whose latest gap is still open, the reachable area now (clipped to sea) and a likelihood over AOIs from reachability and the vessel's own history; per-vessel risk trend over the window; dark-activity density surface.

## 16. Terrestrial v2: military picture and predictive engine

The product was redirected (Oct 2026): a predictive defence picture in support of Ukraine.
Dark vessels (§1–15) remain as the **Maritime** sub-sector, one mode of the same app.

### 16.1 Scope
- The globe shows **military only**: military aircraft, helicopters, UAVs, naval and law-enforcement
  vessels, military airfields and naval/submarine bases. Civil traffic is dropped at ingest.
- **Nets**: shared-mission groups of military entities, inferred from co-movement, rendezvous,
  callsign families and common origin; filterable by state / organisation; several per state.
- **Flight history and prediction**: full observed path per craft, a continuously re-routed
  forecast to landing with destination probabilities, ETA and an endurance estimate.
- **Threat ranking** of craft (transparent breakdown, heuristic weights in config) and a
  **strike-risk / danger-zone model** trained on 2022–2026 history.
- Model training is now in scope (supersedes the §2 non-goal). Models are small, tabular,
  calibrated and evaluated against baselines; each ships a model card shown in the UI.

### 16.2 Observability limits (state them in the UI; never fill the gaps with invented data)
- Russian military aircraft essentially never broadcast ADS-B. Russian air operations enter the
  system through Ukrainian Air Force launch reports (missile-attack dataset), air-raid alerts and
  news-derived events, not through tracks.
- Submarines are never plotted as positions; only their bases.
- Fuel is never observed; endurance is a type-level estimate, labelled as such.
- A carrier is a landing candidate only if it is an observed live entity.
- The live air picture is "aircraft that broadcast", mostly NATO and partner ISR, tankers, airlift.

### 16.3 AI
- **Jev** (TypeSafe AI, `POST https://api.typesafe.ai/v1/systemone`, `TYPESAFE_API_KEY`,
  model `jev-latest`) is the fast decision layer: typed questions (noul / choice / score) over
  state we prepare. Code computes every number; Jev only judges (it is weak at arithmetic and
  dates). Uses: news → structured signals, free-text parsing of datasets, net mission labels,
  borderline net edges, alert triage, gated destination re-ranking, maritime entity resolution.
- **Featherless** (open-weight LLM) writes prose (SITREPs, briefs) citing fact ids, validated as §15.4.
- The live server may call AI at runtime only through `live/ai.py`: input-hash cache, per-minute
  budget, every answer stored with its probability/confidence and model as provenance.

### 16.4 Training data and models
- Train/serve parity: a model feature is allowed only if it is available live with the same lag.
- Time-split validation only (no random splits); report Brier skill / AUC (strike) and top-k
  accuracy (destination) against simple baselines; if a model does not beat them, the UI says so.
- Historical sources are fetched by `history/` into `data/raw/history/` and normalised to
  `data/history/`; each is tested on a small saved real sample.

### 16.5 Live intelligence feeds (Oct 2026)
- **Air threats**: public Telegram web previews (`t.me/s/kpszsu`, `t.me/s/war_monitor`) parsed by
  transparent rules (`live/sources/telegram.py`); every report keeps its original text and link.
  A report is placed only where the post places it (reported position, region centre, or the
  target town); unplaceable status posts stay text-only. Headings are reported courses, never
  predicted impacts.
- **Geocoding**: GeoNames UA/RU/BY (`reference/gazetteer.py`), Ukrainian inflection variants,
  ambiguity resolved by population with a Ukraine bias (a foreign place must be >10× larger).
  News headlines are placed only when they name a town of ≥ 5,000 people.
- **Satellites**: a pass is an *opportunity*, never an acquisition; commercial SAR is tasked.
- **SITREP**: facts are deterministic and always available; AI prose must cite them (§15.4).
- **Registry**: the Streams panel counts only streams working now.

### 16.6 Interface grammar (brand identity v1)
- Brand palette only: ink (green-black), bone, slate teal, ochre, rust. Wordmark: lower-case
  "terrestrial" in Newsreader; mark: nested rounded triangles as contour lines (`Logo.tsx`).
- Observed = bone, solid; model estimate or inference = teal + dashed tag; threat = rust;
  caution = amber; ochre = brand and the front line only. Satellite imagery is colour-graded
  toward the palette.
- Area layers (danger forecast, alerts, occupied territory, front) are native MapLibre layers so
  they drape on terrain and order under labels; point/path/3D layers are deck.gl.
- 3D battlefield lighting of buildings uses only real OSM footprints near a reported point.

### 16.7 One picture, hosted replay (Oct 2026)
- Military and Maritime are one picture (no mode toggle): naval vessels always, merchant
  shipping as a layer, dark vessels as a panel.
- Hosted builds (Vercel, `VITE_RECORDED=1`) replay a real capture of the live server
  (`live/capture.py`) in the browser with timestamps shifted to now, labelled RECORDED with the
  capture time. Captures are deployed, never committed (`web/public/rec/` is gitignored).
