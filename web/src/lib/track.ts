import { createStore, ui } from './store'
import { liveFetch } from './recording'

/** One row per observed position: [ts ms, lon, lat, alt m, speed kn, heading, on ground]. */
export type TrackPoint = [number, number, number, number, number | null, number | null, boolean]

export interface Terminal {
  ident: string
  name: string
  icao: string | null
  country: string
  military: boolean
  lon: number
  lat: number
  km: number
}

export interface Flight {
  start: number
  end: number
  origin: Terminal | null
  landing: Terminal | null
  points: TrackPoint[]
}

export interface TrackHistory {
  id: string
  label: string | null
  hours: number
  first_seen: number | null
  points: number
  flights: Flight[]
}

interface TrackState {
  id: string | null
  data: TrackHistory | null
  error: string | null
}

/** History of the selected track, from /live/track (server-side store, survives restarts). */
export const trackState = createStore<TrackState>({ id: null, data: null, error: null })

const REFRESH_MS = 10_000
let timer: ReturnType<typeof setInterval> | null = null

async function load(id: string) {
  try {
    const res = await liveFetch(`/live/track/${encodeURIComponent(id)}?hours=48`)
    if (trackState.get().id !== id) return
    if (res.status === 404) return trackState.set({ data: null, error: 'No history recorded for this track yet.' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    trackState.set({ data: (await res.json()) as TrackHistory, error: null })
  } catch (err) {
    if (trackState.get().id === id) trackState.set({ error: `Track history unavailable: ${err}` })
  }
}

function follow(selected: string | null) {
  if (selected === trackState.get().id) return
  if (timer) clearInterval(timer)
  timer = null
  const tracked = selected && (selected.startsWith('aircraft:') || selected.startsWith('vessel:'))
  trackState.set({ id: tracked ? selected : null, data: null, error: null })
  if (!tracked) return
  void load(selected)
  timer = setInterval(() => void load(selected), REFRESH_MS)
}

ui.subscribe(() => follow(ui.get().selected))

/** Colours of the 1st, 2nd and 3rd most likely destination (panel and globe). */
export const DEST_COLORS: [number, number, number][] = [
  [121, 169, 178],
  [208, 203, 190],
  [130, 132, 124],
]

/** One predicted destination: [lon, lat, alt m, seconds from now] along the path. */
export interface Destination {
  ident: string
  name: string
  icao: string | null
  lon: number
  lat: number
  country: string
  military: boolean
  p: number
  dist_km: number
  course: number
  gs_route_kn: number
  eta_min: number
  path?: [number, number, number, number][]
}

export interface Reroute {
  at: number
  from: string
  to: string
  p_from: number
  p_to: number
}

export interface Prediction {
  at: number
  destinations: Destination[]
  on_station: boolean
  elapsed_min: number
  endurance_min: number
  origin: string | null
  candidates: number
  winds: { level_hpa: number; speed_kn: number; from_deg: number } | null
  changes: Reroute[]
  model: { version: string; beats_baselines: boolean | null }
}

interface PredState {
  id: string | null
  data: Prediction | null
  error: string | null
}

/** Destination forecast of the selected aircraft, from /live/predict (re-routed every few seconds). */
export const predState = createStore<PredState>({ id: null, data: null, error: null })

const PREDICT_MS = 5_000
let predTimer: ReturnType<typeof setInterval> | null = null

async function loadPrediction(id: string) {
  try {
    const res = await liveFetch(`/live/predict/${encodeURIComponent(id)}`)
    if (predState.get().id !== id) return
    if (res.status === 404 || res.status === 503) {
      const body = await res.json().catch(() => ({}))
      return predState.set({ data: null, error: body.detail ?? `HTTP ${res.status}` })
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    predState.set({ data: (await res.json()) as Prediction, error: null })
  } catch (err) {
    if (predState.get().id === id) predState.set({ error: `Prediction unavailable: ${err}` })
  }
}

function followPrediction(selected: string | null) {
  if (selected === predState.get().id) return
  if (predTimer) clearInterval(predTimer)
  predTimer = null
  const aircraft = selected?.startsWith('aircraft:') ? selected : null
  predState.set({ id: aircraft, data: null, error: null })
  if (!aircraft) return
  void loadPrediction(aircraft)
  predTimer = setInterval(() => void loadPrediction(aircraft), PREDICT_MS)
}

ui.subscribe(() => followPrediction(ui.get().selected))
