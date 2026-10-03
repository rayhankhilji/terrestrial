import { useEffect, useState } from 'react'

/** Wire types from live/hub.py */
export type Kind = 'aircraft' | 'vessel' | 'fire' | 'news' | 'facility' | 'station' | 'net' | 'region' | 'gnss' | 'front'

export interface Entity {
  id: string
  kind: Kind
  label: string
  lon: number
  lat: number
  alt?: number
  hdg?: number | null
  spd?: number | null
  ts: number
  rx: number
  orig_ts?: number
  src: string
  prov: 'observed' | 'inferred'
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  props: Record<string, any>
}

export interface Relation {
  id: string
  rel: string
  a: string
  b: string
  prov: 'observed' | 'inferred'
  src: string
  why: string[]
  detail?: string
  ts: number
}

export interface Alert {
  id: string
  severity: 'info' | 'warn' | 'high'
  title: string
  body: string
  entities: string[]
  lon?: number
  lat?: number
  prov: 'observed' | 'inferred'
  why: string[]
  ts: number
  url?: string
  sentinel?: string
}

export interface SourceStatus {
  name: string
  state: 'starting' | 'ok' | 'error' | 'disabled'
  detail: string
  last_ok: number | null
  messages: number
  per_min: number
  lag_p50_ms: number | null
}

export interface SentinelNode {
  id: string
  type: 'source' | 'area' | 'near' | 'compare' | 'listed' | 'and' | 'or' | 'alert'
  x?: number
  y?: number
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  params: Record<string, any>
}

export interface Sentinel {
  id: string
  name: string
  enabled: boolean
  nodes: SentinelNode[]
  edges: [string, string][]
}

type Message =
  | { t: 'snapshot'; entities: Entity[]; relations: Relation[]; alerts: Alert[]; status: SourceStatus[]; server_time: number; mode: 'live' | 'replay'; sentinels: Sentinel[] }
  | { t: 'batch'; m: Message[]; srv: number }
  | { t: 'upsert'; e: Entity }
  | { t: 'relate'; r: Relation }
  | { t: 'alert'; a: Alert }
  | { t: 'remove'; ids: string[] }
  | { t: 'status'; sources: SourceStatus[]; srv: number; entities: number }
  | { t: 'sentinel_hits'; hits: Record<string, Record<string, number>> }
  | { t: 'sentinels'; sentinels: Sentinel[] }
  | { t: 'resync' }

const TRAIL_POINTS = 40
const RING = 300

/** Mutable live picture: written by the socket, read by the map every frame. */
class LiveState {
  entities = new Map<string, Entity>()
  relations = new Map<string, Relation>()
  alerts: Alert[] = []
  sources: SourceStatus[] = []
  sentinels: Sentinel[] = []
  sentinelHits: Record<string, Record<string, number>> = {}
  mode: 'live' | 'replay' | 'offline' = 'offline'
  connected = false
  /** client receive time per entity id: drives the "ping" pulse */
  touched = new Map<string, number>()
  trails = new Map<string, [number, number, number][]>()
  freshAlerts: { alert: Alert; at: number }[] = []
  /** hub receive → browser receive (ms) and source observation → browser (ms) */
  hubLatency: number[] = []
  e2eLatency: number[] = []
  received: number[] = []
  version = 0
  private listeners = new Set<() => void>()
  private ws: WebSocket | null = null
  private retry = 1000

  subscribe(fn: () => void) {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }

  private changed() {
    this.version++
    this.listeners.forEach((l) => l())
  }

  connect() {
    if (this.ws) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/live/ws`)
    this.ws = ws
    ws.onopen = () => {
      this.connected = true
      this.retry = 1000
      this.changed()
    }
    ws.onmessage = (ev) => this.handle(JSON.parse(ev.data) as Message, Date.now())
    ws.onclose = () => {
      this.ws = null
      this.connected = false
      this.mode = 'offline'
      this.changed()
      setTimeout(() => this.connect(), this.retry)
      this.retry = Math.min(15000, this.retry * 2)
    }
  }

  private handle(msg: Message, now: number) {
    switch (msg.t) {
      case 'snapshot':
        this.entities = new Map(msg.entities.map((e) => [e.id, e]))
        this.relations = new Map(msg.relations.map((r) => [r.id, r]))
        this.alerts = msg.alerts
        this.sources = msg.status
        this.mode = msg.mode
        this.sentinels = msg.sentinels
        this.trails.clear()
        for (const e of msg.entities) this.trail(e)
        break
      case 'batch':
        for (const m of msg.m) this.handle(m, now)
        this.hubLatency.push(now - msg.srv)
        if (this.hubLatency.length > RING) this.hubLatency.shift()
        break
      case 'upsert': {
        const e = msg.e
        this.entities.set(e.id, e)
        this.touched.set(e.id, now)
        this.trail(e)
        this.received.push(now)
        if (e.kind === 'aircraft' || e.kind === 'vessel') {
          this.e2eLatency.push(now - e.ts)
          if (this.e2eLatency.length > RING) this.e2eLatency.shift()
        }
        break
      }
      case 'relate':
        this.relations.set(msg.r.id, msg.r)
        break
      case 'alert':
        this.alerts = [msg.a, ...this.alerts].slice(0, 300)
        this.freshAlerts.push({ alert: msg.a, at: now })
        break
      case 'remove':
        for (const id of msg.ids) {
          this.entities.delete(id)
          this.trails.delete(id)
          this.touched.delete(id)
        }
        break
      case 'status':
        this.sources = msg.sources
        break
      case 'sentinel_hits':
        this.sentinelHits = msg.hits
        break
      case 'sentinels':
        this.sentinels = msg.sentinels
        break
      case 'resync':
        break
    }
    if (msg.t !== 'upsert' && msg.t !== 'relate' && msg.t !== 'alert' && msg.t !== 'remove') this.changed()
  }

  private trail(e: Entity) {
    if (e.kind !== 'aircraft' && e.kind !== 'vessel') return
    const t = this.trails.get(e.id) ?? []
    const last = t[t.length - 1]
    if (!last || last[0] !== e.lon || last[1] !== e.lat) {
      t.push([e.lon, e.lat, e.alt ?? 0])
      if (t.length > TRAIL_POINTS) t.shift()
      this.trails.set(e.id, t)
    }
  }

  /** messages received in the last second */
  rate(now = Date.now()) {
    while (this.received.length && this.received[0] < now - 1000) this.received.shift()
    return this.received.length
  }
}

export const live = new LiveState()

export function p50(values: number[]): number | null {
  if (!values.length) return null
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)]
}

/** Re-render at most every `ms` while the live picture changes. */
export function useLive(ms = 500): number {
  const [version, setVersion] = useState(live.version)
  useEffect(() => {
    let pending = false
    const unsub = live.subscribe(() => {
      if (pending) return
      pending = true
      setTimeout(() => {
        pending = false
        setVersion(live.version)
      }, ms)
    })
    const tick = setInterval(() => setVersion((v) => v + 1), ms)
    return () => {
      unsub()
      clearInterval(tick)
    }
  }, [ms])
  return version
}

/** Dead-reckon a moving entity forward to `now` (great-circle-free small-step approximation). */
export function projected(e: Entity, now: number): [number, number] {
  if (!e.spd || e.hdg == null || e.kind !== 'aircraft') return [e.lon, e.lat]
  const dtH = Math.min(30, Math.max(0, (now - e.ts) / 1000)) / 3600
  const distKm = e.spd * 1.852 * dtH
  const rad = (e.hdg * Math.PI) / 180
  const dLat = (distKm * Math.cos(rad)) / 110.574
  const dLon = (distKm * Math.sin(rad)) / (111.32 * Math.cos((e.lat * Math.PI) / 180))
  return [e.lon + dLon, e.lat + dLat]
}

/** Position `minutes` ahead along the current heading at the current speed. */
export function ahead(e: Entity, minutes: number): [number, number] {
  if (!e.spd || e.hdg == null) return [e.lon, e.lat]
  const distKm = e.spd * 1.852 * (minutes / 60)
  const rad = (e.hdg * Math.PI) / 180
  return [
    e.lon + (distKm * Math.sin(rad)) / (111.32 * Math.cos((e.lat * Math.PI) / 180)),
    e.lat + (distKm * Math.cos(rad)) / 110.574,
  ]
}

if (import.meta.env.DEV) (window as unknown as { __live: LiveState }).__live = live
