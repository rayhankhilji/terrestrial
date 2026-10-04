import { createStore } from './store'
import { liveFetch } from './recording'

/** REST feeds polled on demand (the WebSocket carries entities; these are tables). */

export interface StreamRow {
  id: string
  name: string
  group: string
  provider: string
  what: string
  cadence: string
  licence: string
  key: string | null
  state: string
  detail: string
  last_ok?: number | null
}

export interface Pass {
  satellite: string
  norad: number
  sensor: 'SAR' | 'optical'
  operator: string
  tasked: boolean
  start: number
  end: number
  closest_km: number
  daylight: boolean | null
}

export interface Headline {
  source: string
  title: string
  url: string
  summary: string
  at: number
}

export const streams = createStore<{ rows: StreamRow[]; at: number; error: string | null }>({ rows: [], at: 0, error: null })
export const headlines = createStore<{ rows: Headline[]; at: number }>({ rows: [], at: 0 })
export const passes = createStore<{ key: string | null; label: string | null; rows: Pass[]; loading: boolean; error: string | null }>({
  key: null,
  label: null,
  rows: [],
  loading: false,
  error: null,
})

async function json<T>(url: string): Promise<T> {
  const r = await liveFetch(url)
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail ?? `HTTP ${r.status}`)
  }
  return r.json() as Promise<T>
}

export async function loadStreams() {
  try {
    streams.set({ rows: await json<StreamRow[]>('/live/streams'), at: Date.now(), error: null })
  } catch (err) {
    streams.set({ error: String(err) })
  }
}

export async function loadHeadlines() {
  try {
    headlines.set({ rows: await json<Headline[]>('/live/headlines?limit=80'), at: Date.now() })
  } catch {
    // the Feed panel shows the wires' status pill instead
  }
}

export async function loadPasses(lon: number, lat: number, label: string, hours = 24) {
  const key = `${lon.toFixed(3)},${lat.toFixed(3)}`
  passes.set({ key, label, loading: true, error: null })
  try {
    const r = await json<{ passes: Pass[] }>(`/live/passes?lon=${lon}&lat=${lat}&hours=${hours}`)
    if (passes.get().key === key) passes.set({ rows: r.passes, loading: false })
  } catch (err) {
    if (passes.get().key === key) passes.set({ rows: [], loading: false, error: String(err) })
  }
}

let started = false
/** Poll the slow tables in the background (streams every 15 s, headlines every 2 min). */
export function startFeeds() {
  if (started) return
  started = true
  void loadStreams()
  void loadHeadlines()
  setInterval(() => void loadStreams(), 15_000)
  setInterval(() => void loadHeadlines(), 120_000)
}
