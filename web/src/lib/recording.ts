/**
 * Hosted builds (VITE_RECORDED=1, e.g. Vercel) have no Python live server. They replay a real
 * capture of it (`uv run python -m live.capture`, files under /rec/): the WebSocket stream
 * exactly as the server sent it, and the REST answers the panels ask for. Timestamps are shifted
 * so the recording plays "now", the same contract as live/replay.py, and the UI says it is a
 * recording and when it was captured. Nothing here invents data.
 *
 * Builds with VITE_LIVE_URL talk to a remote live server instead (WebSocket and REST).
 */

export const RECORDED = import.meta.env.VITE_RECORDED === '1'
export const LIVE_URL: string = (import.meta.env.VITE_LIVE_URL ?? '').replace(/\/$/, '')

const BASE = `${import.meta.env.BASE_URL}rec/`

export interface RecordingMeta {
  captured_from: number
  captured_to: number
  messages: number
  craft: number
  grid_deg: number
}

let meta: RecordingMeta | null = null
/** Added to recorded epoch times to make them "now". Re-set at each loop of the recording. */
let offset = 0

export const recordingMeta = () => meta

const cache = new Map<string, Promise<unknown>>()
function get<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    cache.set(
      path,
      fetch(BASE + path).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      }),
    )
  }
  return cache.get(path) as Promise<T>
}

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/

/** Deep copy with every epoch time (ms, s or ISO string) moved by `off` ms. */
export function shift<T>(v: T, off = offset): T {
  if (typeof v === 'number') {
    if (v > 1.6e12 && v < 2.1e12) return (v + off) as T
    if (v > 1.6e9 && v < 2.1e9) return (v + off / 1000) as T
    return v
  }
  if (typeof v === 'string') {
    if (v.length >= 16 && v.length <= 32 && ISO.test(v)) {
      const t = Date.parse(v)
      if (!Number.isNaN(t)) return new Date(t + off).toISOString() as T
    }
    return v
  }
  if (Array.isArray(v)) return v.map((x) => shift(x, off)) as T
  if (v && typeof v === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, x] of Object.entries(v)) out[k] = shift(x, off)
    return out as T
  }
  return v
}

const fileOf = (id: string) => id.replace(/[:/]/g, '_')

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Msg = any

/** Play the recording into the live store's message handler, looping. */
export async function replay(handle: (msg: Msg, now: number) => void, onOpen: () => void) {
  meta = await get<RecordingMeta>('meta.json')
  const [snapshot, stream] = await Promise.all([get<Msg>('snapshot.json'), get<[number, Msg][]>('stream.json')])
  onOpen()
  const loop = () => {
    const start = Date.now()
    offset = start - meta!.captured_from
    handle({ ...shift(snapshot), mode: 'replay' }, Date.now())
    let i = 0
    const tick = () => {
      const t = Date.now() - start
      while (i < stream.length && stream[i][0] <= t) {
        handle(shift(stream[i][1]), Date.now())
        i++
      }
      if (i >= stream.length) setTimeout(loop, 1500)
      else setTimeout(tick, 200)
    }
    tick()
  }
  loop()
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const REST: Record<string, [file: string, timed: boolean]> = {
  '/live/streams': ['streams', true],
  '/live/headlines': ['headlines', true],
  '/live/airfields': ['airfields', false],
  '/live/regions': ['regions', false],
  '/live/front': ['front', true],
  '/live/models/strike': ['models_strike', false],
  '/live/models/flight': ['models_flight', false],
}

type Place = [string, number, number, string, number, string, string[]]

const norm = (s: string) =>
  s
    .replace(/[’ʼ`]/g, "'")
    .replace(/ё/g, 'е')
    .replace(/Ё/g, 'Е')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ')

async function answer(url: URL): Promise<Response> {
  const p = url.pathname
  const q = url.searchParams
  const rest = REST[p]
  if (rest) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let body: any = await get(`rest/${rest[0]}.json`)
    if (p === '/live/headlines') body = body.slice(0, Number(q.get('limit') ?? 60))
    return json(rest[1] ? shift(body) : body)
  }
  if (p.startsWith('/live/track/')) {
    const id = decodeURIComponent(p.slice('/live/track/'.length))
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const t: any = shift(await get(`track/${fileOf(id)}.json`))
      const now = Date.now()
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      t.flights = t.flights.map((f: any) => ({ ...f, points: f.points.filter((pt: number[]) => pt[0] <= now) })).filter((f: any) => f.points.length)
      return json(t)
    } catch {
      return json({ detail: 'No history recorded for this track.' }, 404)
    }
  }
  if (p.startsWith('/live/predict/')) {
    const id = decodeURIComponent(p.slice('/live/predict/'.length))
    try {
      const rows = await get<[number, unknown][]>(`predict/${fileOf(id)}.json`)
      const at = Date.now() - offset
      const row = [...rows].reverse().find((r) => r[0] <= at) ?? rows[0]
      return json(shift(row[1]))
    } catch {
      return json({ detail: 'No prediction in this recording for this craft.' }, 404)
    }
  }
  if (p === '/live/passes') {
    const lon = Math.round(Number(q.get('lon')))
    const lat = Math.round(Number(q.get('lat')))
    const grid = await get<Record<string, unknown[]>>('passes.json')
    const rows = grid[`${lon},${lat}`]
    if (!rows) return json({ detail: 'This recording has passes only for Ukraine and the Black Sea.' }, 404)
    const now = Date.now()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const passes = (shift(rows) as any[]).filter((r) => r.end >= now)
    return json({ lon, lat, from: now, hours: 24, grid_deg: 1, passes })
  }
  if (p === '/live/geocode') {
    const term = norm(q.get('q') ?? '')
    if (term.length < 2) return json([])
    const places = await get<Place[]>('places.json')
    const rank: Record<string, number> = { UA: 0, RU: 1, BY: 1 }
    const hits = places.filter((r) => r[6].some((k) => k.startsWith(term)))
    hits.sort((a, b) => (rank[a[3]] ?? 2) - Math.log10(a[4] + 1) - ((rank[b[3]] ?? 2) - Math.log10(b[4] + 1)))
    const limit = Number(q.get('limit') ?? 8)
    return json(hits.slice(0, limit).map((r) => ({ name: r[0], lon: r[1], lat: r[2], country: r[3], population: r[4], feature: r[5] })))
  }
  if (p === '/live/sentinels') return json([])
  return json({ detail: 'Not part of this recording.' }, 404)
}

/** fetch() for the live server's REST API: the server itself, a remote one, or the recording. */
export function liveFetch(path: string, init?: RequestInit): Promise<Response> {
  if (!RECORDED) return fetch(LIVE_URL + path, init)
  if (init?.method && init.method !== 'GET') {
    return Promise.resolve(json({ detail: 'This is a read-only recording; run the live server to edit sentinels.' }, 405))
  }
  return answer(new URL(path, location.origin)).catch((err) => json({ detail: String(err) }, 503))
}
