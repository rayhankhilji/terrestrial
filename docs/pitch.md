# Terrestrial: pitch notes

For the 3-minute pitch and 2-minute Q&A (EDTH London, October 2026). Every number below comes
from the running system or its model cards; where it is a live reading it says when.

## One sentence

**Terrestrial turns the war's public signals (Air Force threat reports, air-raid alerts, the
front line, military flights, satellites and ships) into one live map that shows what is
coming, where, and how sure we are.**

The guide's form: *We help Ukrainian air-watch teams and analysts see every reported air threat,
the front and the sky on one live map, with a forecast of where danger comes next, so they can
act minutes earlier, proven by a live system fusing 19 working open data streams and a danger
model that beats "same as last time" by 19%.*

## The hook (first 15 seconds)

> "At 12:25 UTC today, the Ukrainian Air Force posted 17 air-threat reports in one hour: jet
> drones, Shaheds, missiles, heading for Kherson, Pereiaslav, Kremenchuk. Ten regions were under
> alert. That information is public, but it is scattered across Telegram posts, alert apps and
> maps. Nobody puts it on one picture with a forecast. We did."

(Those are the live numbers on screen this morning; quote whatever the brief shows on the day.)

## Problem

- **Situation:** the picture of an attack is public but fragmented: Air Force Telegram posts,
  the alert map, DeepStateMap, flight trackers, satellite catalogues, news wires. Each is one app.
- **Problem:** no single view, no forecast, no traceability. A report says "drone over Sumy region
  heading for Romny"; nobody draws it, ties it to the alert, or says where the next one lands.
- **Impact:** minutes lost per decision, and analysts who cannot check where a claim came from.

## Solution: how it works (3 steps)

1. **Read:** 25 open streams (19 working without keys), polled every 1–30 s: ADS-B military
   aircraft, the Air Force's own threat posts, the alert map, DeepState's front line, imaging
   satellites, news wires, AIS ships and GFW dark-vessel events.
2. **Fuse and predict:** rules turn posts into placed threats with headings; trained models
   forecast which regions get the next alert and where each military aircraft will land.
3. **Show it honestly:** one 3D map. Observed is solid; model estimates are teal and dashed. Every
   fact links to its original post.

## Why us / the advantage

- **We read the Air Force's own reporting, live.** Russian military aircraft do not broadcast, so
  flight trackers cannot see the attack. The threat reports can, and we place them on the map.
- **Models that publish their own scorecards.** Each model is tested on 2026 data it never saw,
  compared against simple shortcuts, and says so in the app if it loses.
- **Traceable, not "trust me".** Every number cites its source; the AI summary is rejected if it
  cites a fact that does not exist.
- **Open source and cheap to run:** one laptop, no paid data, MIT licence.

## Traction (what is proven, today)

| | |
|---|---|
| Danger model (new alert per region, next 6 h) | test 2026: ROC-AUC **0.86**; Brier **0.150** vs 0.185 persistence and 0.199 climatology (**19% / 25% better**) |
| Landing-site model (military aircraft) | held-out days: **69%** first guess, **81%** in top 3, vs **53%** for the best simple rule |
| Live streams | **19 of 25** working with no keys (the rest wait for keys) |
| Coverage | 5-day archive: **40–60 different military aircraft a day** near Ukraine (Poland, Romania, Black Sea) |
| Tests | 128 tests on real saved responses |

## Next steps / the ask (fill in yours)

The guide wants: amount or resource + what it achieves + by when. A candidate:

> "We want **one pilot with a Ukrainian air-watch or civil-defence team** and an **introduction
> to Brave1**, to put alerts on their phones in Ukrainian within 60 days."

- 30 days: push alerts (Telegram bot) and a Ukrainian-language interface.
- 60 days: pilot with one team; export to the map tools they already use (CoT/ATAK).
- 90 days: a finer-grained strike forecast (town level) trained on Kaggle GPUs, published on
  Hugging Face.

---

## The tech stack, and why

| Layer | What | Why this |
|---|---|---|
| Ingest + API | Python 3.11, FastAPI, httpx, WebSocket | One language for data, models and server; async polling of 25 streams on a laptop |
| Models | scikit-learn gradient boosting, isotonic calibration | Tabular data, small and fast; calibrated probabilities you can trust; no GPU needed |
| Geo | shapely, pyproj, SGP4 (satellites), GeoNames | Exact geometry for the front line, reachability and satellite passes |
| Map | MapLibre (3D globe, terrain) + deck.gl | Open-source, no tokens; draws thousands of moving objects at 30 fps |
| App | React + TypeScript + Vite | Fast to build, typed end to end |
| AI: Jev (TypeSafe) | Fast "decision" model answering typed questions | Turns a news headline into a structured event; labels missions. Code does the maths; Jev only judges |
| AI: Featherless | Open-weight LLM | Writes the situation summary, which must cite real facts or is rejected |
| Graph | TuringDB | Versioned network of dark vessels, with one commit per day (time travel) |
| Hosting | Vercel (static) | The hosted demo replays a real recording of the live system in the browser |
| Models out | Hugging Face | Published with their scorecards |

### How we use Jev (say it like this)

Jev answers **typed questions** in under half a second with a probability attached:

- **News → events:** for each headline: what kind of event (missile, drone, airstrike…), what was hit
  (energy, port, airbase…), which region, was it a Russian strike (yes/no with probability), and
  how severe (score).
- **Mission labels:** for each group of aircraft flying together, which mission
  (refuelling, surveillance orbit, airlift…).

Rule: **code computes every number, Jev only judges**, and every answer is stored with its
probability and model version. (Needs `TYPESAFE_API_KEY` in `.env`; without it, the panel says so.)

---

## Q&A: likely questions and answers

**"How often does it actually see military jets? Can it see Russian aircraft?"**
Honestly: Russian military aircraft essentially never broadcast, so no public tracker sees
them. Over 5 days we saw 40–60 military aircraft a day near Ukraine, mostly NATO cargo,
tankers and surveillance planes, and almost no fighters (fighters switch transponders off). That
is why the core of the product is the **Air Force's threat reports and alerts**, not the
flight tracker. The aircraft layer is context; the threat layer is the point.

**"Isn't this just Flightradar24 / ADS-B Exchange?"**
They show civil traffic and whatever broadcasts. We drop civil traffic, then add what they do not
have: the reported drones and missiles, the alert map, the front line, satellites, and forecasts
with scorecards. We use the same open feeds (adsb.fi, adsb.lol) as the community trackers.

**"Couldn't Russia use this?"**
Everything is already public and delayed: Air Force posts, alert maps, DeepState. We add no
new collection. We show reported headings, never predicted impact points for strikes, and we
never plot submarines or anything that is not already published.

**"How do you know the predictions are any good?"**
Trained on 2022–2025, tested on 2026 only (never random splits). The danger model beats
"same as last time" by 19% (Brier score) with AUC 0.86. The landing model gets 69% first guess,
against 53% for the best simple rule. Each scorecard is in the app.

**"What about AI hallucination?"**
The AI never produces numbers. The summary must cite numbered facts that the code computed;
if it cites one that does not exist, we throw it away. Jev answers multiple-choice questions only.

**"Who is the customer?"**
First: Ukrainian air-watch volunteers, civil defence and OSINT analysts (free, open source).
Then: NATO/EU analysts and journalists who need a traceable common picture.

**"Why has nobody done this?"**
The pieces exist in separate apps; fusing them needs geocoding of Ukrainian place names (we
handle inflections like "Сумщині" → Sumy region), parsing free-text posts, and honest
uncertainty, which is unglamorous work.

**"What if a big player copies you tomorrow?"**
It is open source: we want them to. Our edge is speed and trust: traceability and published
scorecards.

**"What is missing?"**
Phone push alerts, a Ukrainian interface, and a pilot user. Some streams (AI summary, Jev,
ships, fires) are waiting only for API keys.

**"Is the hosted demo live?"**
The website replays a real recording of the live system (it says "RECORDED" and the capture
time). The laptop version is fully live.

## Before going on stage

- Run `make live` and `make web` an hour before; check the Data streams panel is green.
- Fallback: the Vercel link (recorded) works on any laptop or phone with wifi.
- Point at: an air-threat arc, the danger map, Battlefield view on a reported point, an aircraft's
  predicted landing, the scorecard.
