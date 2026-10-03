import { createStore, ui } from './store'

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
    const res = await fetch(`/live/track/${encodeURIComponent(id)}?hours=48`)
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
