<p align="center">
  <img src="web/public/favicon.svg" width="84" alt="Terrestrial mark: nested rounded triangles drawn as topographic contour lines" />
</p>

<h1 align="center">terrestrial</h1>

<p align="center">
  <b>The war's public signals on one live map: what is coming, where, and how sure we are.</b><br/>
  Live military air, air threats in flight, the front line, danger forecasts, GPS jamming, imaging satellites and dark vessels: fused from open sources, every number traceable to its evidence.<br/>
  Built for the European Defence Tech Hackathon, London, October 2026.
</p>

---

## What it does

| | |
|---|---|
| **Air threats in flight** | The Air Force of Ukraine's public channel and a long-running monitor are read every 30 s: each report becomes a placed threat (jet drone, Shahed, cruise or ballistic missile, glide bombs) with its position, heading and the original post. The overnight tally adds launch directions (Orel, Kursk, Millerovo, Hvardiiske…) and impact counts. |
| **Danger forecast** | A gradient-boosted model trained on every air-raid alert since 2022, news-reported attacks, weather and moon phase gives each region the probability of a new alert in the current 6-hour block. It beats climatology and persistence on 2026, and its model card says so. |
| **Military air picture** | Only military aircraft and naval vessels (ADS-B / AIS), each with why it is classed military, its state and role, its full flight history, and **where it will land**: a ranker trained on archived military flights (69 % first-guess, 81 % top-3 on held-out days, vs 53 % for the best simple baseline), re-routed live with winds aloft. |
| **Mission nets** | Craft sharing a mission (tanker rendezvous, ISR orbits, formations) grouped by operator. |
| **Priority craft** | A transparent threat / intelligence score for every craft, each point explained. |
| **Front line** | DeepStateMap's occupied territory, the front (~1,100 km), attack directions, estimated Russian unit positions and the airfields Russia operates from. |
| **GPS jamming** | gpsjam-style cells from the accuracy every aircraft reports. |
| **Space** | 80+ radar and optical imaging satellites tracked live, and when the next one can look at any place. |
| **Situation brief** | The picture as numbered facts, always; with a Featherless key, an open-weight LLM writes a SITREP that must cite them. |
| **3D battlefield** | Camera presets: globe, theatre, a low battlefield view over terrain and real buildings (reported danger points get light beams, lit buildings and pinned cards) and a chase camera behind any aircraft at its altitude. |
| **Maritime, same map** | Naval vessels sit in the one picture; the Dark vessels panel adds the original challenge: AIS gaps, reachability ellipses, unmatched radar detections, occupied-port calls, sanctions, a versioned TuringDB network. Merchant shipping is a layer. |

**Honesty is a feature.** Observed facts are bone-white and solid; model estimates and inferences are teal and dashed everywhere; threats are rust. Russian military aviation does not broadcast, so it appears only through Air Force reports, alerts and news. Submarines are never plotted. Fuel is never observed. Nothing is "confirmed".

## Quickstart

Requirements: Python 3.11 with [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
cp .env.example .env     # then add the keys you have (see below); everything else needs none
make setup               # uv sync + npm install
make live                # live server on :8001
make web                 # the app on http://localhost:5173
```

Optional:

```bash
make history             # fetch 2022→now history (alerts, VIINA, weather) for the danger model
uv run python -m predict.strike.train          # train the danger models
uv run python -m history.adsb_archive --days 7 # archived military flights
uv run python -m predict.flight.train          # train the landing model (the live server hot-reloads it)
make api                 # maritime API on :8000 (after make pipeline, needs a GFW token)
make test                # 128 tests on saved real responses
```

### Keys

Put them in `.env` in the project folder (never in chat, never in git), then restart `make live`.

| Key | What it unlocks | Get it |
|---|---|---|
| `FEATHERLESS_API_KEY` | AI SITREPs and vessel briefs (open-weight LLM) | featherless.ai |
| `TYPESAFE_API_KEY` | Jev: news → structured signals, net mission labels | docs.typesafe.ai |
| `GFW_API_TOKEN` | Maritime mode (dark vessels) | globalfishingwatch.org/our-apis/tokens |
| `AISSTREAM_API_KEY` | Live naval AIS | aisstream.io |
| `FIRMS_MAP_KEY` | Thermal anomalies (strikes, fires) | firms.modaps.eosdis.nasa.gov |
| `KAGGLE_USERNAME`, `KAGGLE_KEY` | Training data and GPU notebooks | kaggle.com → Settings → Create New Token |
| `HF_TOKEN` | `make publish`: models to Hugging Face | huggingface.co/settings/tokens (write) |

The **Data streams** panel shows which streams are working and which are waiting for a key.

### Hosted demo (Vercel)

The hosted site has no Python server, so it replays a **real recording** of the live system in the
browser (the WebSocket stream and every REST answer, timestamps shifted to now, labelled
"RECORDED" with the capture time). The recording is deployed, never committed.

```bash
make live                    # in one terminal
make capture MIN=12          # record 12 minutes into web/public/rec
make deploy                  # VITE_RECORDED=1 build → vercel deploy --prebuilt --prod
```

A build with `VITE_LIVE_URL=https://…` talks to a remote live server instead.

### Demo without network

```bash
make demo-cut DAY=20261003   # the busiest hours of a recorded day → data/demo/demo.jsonl
make demo                    # replay at 10× (run make web alongside)
```

### Publish the models

```bash
uv run python -m predict.publish --dry-run   # write the Hugging Face model cards locally
make publish                                 # private repos <you>/terrestrial-<model>; --public to share
```

## Data (open sources only)

Air: adsb.fi, adsb.lol (ODbL; airplanes.live and ADS-B Exchange carry the same community feeds but their APIs need approval or payment), ADS-B Exchange aircraft database, adsb.lol `globe_history` archive. Threats: Air Force of Ukraine and war_monitor public Telegram posts, the official air-raid alert map, DeepStateMap.Live. History: Vadimkin air-raid sirens dataset, VIINA 2.0. Space: CelesTrak. News: Kyiv Independent, Ukrainska Pravda, Ukrinform, GDELT. Reference: OurAirports, GeoNames (CC BY 4.0), Wikidata, Natural Earth, geoBoundaries. Weather: Open-Meteo (CC BY 4.0). Maritime: Global Fishing Watch, OpenSanctions (non-commercial, attribution).

## Layout

```
live/       live server: sources, hub, classifiers, nets, threat, GNSS, satellites, Telegram, SITREP, AI
predict/    trained models: strike (danger forecast), flight (destination), publishing
history/    historical data lake: alerts, VIINA, weather, DeepState, adsb.lol archive
reference/  airfields, aircraft register, ICAO ranges, gazetteer, land mask
pipeline/   maritime pipeline: fetch → normalise → envelope → match → score → insights → brief
graph/ api/ maritime graph (TuringDB) and API
web/        Vite + React + TypeScript: MapLibre globe + deck.gl, the whole UI (src/lib/recording.ts: hosted replay)
docs/       pitch notes (docs/pitch.md), methodology
tests/      pytest on real saved responses (tests/fixtures/README.md lists every source)
CLAUDE.md   the build spec and every decision taken since
```

## Licence

Code: [MIT](LICENSE). Data stays under its providers' licences; see the attribution line in the app.
