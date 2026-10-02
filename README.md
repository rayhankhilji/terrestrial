<p align="center">
  <img src="web/public/favicon.svg" width="88" alt="Terrestrial logo: a reachability ellipse with its two foci and a radar detection inside" />
</p>

<h1 align="center">Terrestrial</h1>

<p align="center">
  <b>Bridging the AIS gap.</b> Open-source dark-vessel detection, verification and tracking for the Black Sea.<br/>
  Built for the European Defence Tech Hackathon, London, October 2026 (Challenge 1: Dark Vessels).
</p>

---

> **Status: under active development during the hackathon.** Everything below marked ✅ works today; the rest is being built in the open, commit by commit.

## The problem

Ships moving looted Ukrainian grain out of occupied Sevastopol and Berdyansk, and shadow-fleet tankers loitering off Novorossiysk for ship-to-ship transfers, switch off their AIS transponders. AIS is cooperative: on its own it only says what a ship wants you to know. Ukrainian analysts need to answer, from open sources only: **which hulls have called at occupied ports in the last 30 days?**

## The idea

When a ship goes dark we still know three things: where it switched off (**A**), where it switched back on (**B**), and how long it was gone (**T**). At a maximum speed **v**, every place it could have been satisfies

```
dist(A, P) + dist(P, B) ≤ v · T
```

which is an ellipse with foci A and B. ✅ Satellite radar (Sentinel-1 SAR) sees hulls whether or not they transmit. A radar detection **inside that ellipse, during the gap, that matches no AIS-broadcasting ship** is evidence of where the dark ship actually went. When that detection sits inside an occupied port, it is a **probable covert port call**.

Terrestrial then goes further:

- **Tracks the ship across the gap.** Time-consistent radar detections are chained into a feasible dark track A → P₁ → … → B, re-identifying the hull when it reappears.
- **Scores every vessel** with a transparent, additive heuristic. Each point links to the evidence behind it.
- **Tracks the network over time.** A versioned TuringDB graph with one commit per day lets you scrub the timeline and see which ships, ports and meetings joined the network, and when.
- **Answers the operational question directly** with an "occupied-port calls (30 days)" view, ranked highlights, AI analyst briefs that are only allowed to cite facts in the dossier, and predictive estimates (where a currently-dark ship can be now).

Everything is phrased as *candidate*, *likely* or *consistent with*. Nothing is "confirmed".

## Data (open sources only)

| Source | Used for |
|---|---|
| [Global Fishing Watch API v3](https://globalfishingwatch.org/our-apis/) | AIS-off (gap) events, encounters, loitering, port visits, vessel identity history, Sentinel-1 SAR detections already matched against AIS |
| [OpenSanctions](https://www.opensanctions.org/datasets/maritime/) maritime collection | Sanctioned and shadow-fleet vessels (OFAC, EU, UK, Ukraine War & Sanctions, …) |

Both are free for non-commercial use with attribution: *Data: Global Fishing Watch, OpenSanctions.*

## Quickstart

Requirements: Python 3.11 with [uv](https://docs.astral.sh/uv/), Node 20+, Docker (for the TuringDB graph server).

```bash
cp .env.example .env            # add GFW_API_TOKEN (and FEATHERLESS_API_KEY for AI briefs)
make setup                      # uv sync + npm install
make graph-up                   # TuringDB on :6666 (visualiser on :8080)
make pipeline                   # fetch → normalise → envelope → match → score → insights → graph
make api                        # FastAPI on :8000
make web                        # UI on http://localhost:5173
```

Run the tests with `make test`. After the first fetch, everything runs offline except basemap tiles.

## Repository layout

```
pipeline/   fetch, normalise, reachability envelopes, SAR matching, scoring, insights
graph/      TuringDB client, versioned graph build, network queries
api/        FastAPI, read-only over data/processed and TuringDB
web/        Vite + React + TypeScript: map / 3D globe, dossier, network, timeline
tests/      pytest, with fixtures saved from real API responses
CLAUDE.md   the build spec: the single source of truth for scope and rules
```

## Licence

Code: [MIT](LICENSE). Data stays under its providers' licences (Global Fishing Watch and OpenSanctions: non-commercial, attribution required).
