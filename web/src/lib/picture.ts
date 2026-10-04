import type { Entity } from './live'
import { liveFetch } from './recording'
import type { UIState } from './store'

/**
 * What the picture shows (CLAUDE.md §16.1): one military picture, air and sea together. The live
 * server already drops civil aircraft; merchant vessels appear only with the Merchant shipping
 * layer (dark-vessel work), civil facilities never. The state filter applies to anything
 * attributed to a state.
 */
export function visible(e: Entity, s: Pick<UIState, 'states' | 'layers'>): boolean {
  if (e.kind === 'vessel' && !e.props.military && !s.layers.merchant) return false
  if (e.kind === 'facility' && e.props.type !== 'airbase' && e.props.type !== 'naval base' && e.props.type !== 'port') return false
  if (s.states.length && (e.kind === 'aircraft' || e.kind === 'vessel')) {
    return s.states.includes(e.props.state_code)
  }
  if (s.states.length && e.kind === 'net') {
    return (e.props.states as string[]).some((c) => s.states.includes(c))
  }
  return true
}

export const MISSION_LABELS: Record<string, string> = {
  air_refuelling: 'Air-to-air refuelling',
  isr_orbit: 'ISR / AEW orbit',
  maritime_patrol: 'Maritime patrol',
  airlift: 'Airlift',
  fighter_cap: 'Fighter CAP / escort',
  rotary_ops: 'Rotary-wing operations',
  training: 'Training',
  vip_transport: 'VIP / staff transport',
  unknown: 'Mission unclear',
}

/** Who a net belongs to, for grouping: a single state, or the coalition of states. */
export function netOwner(e: Entity): string {
  const states: string[] = e.props.states ?? []
  if (states.length === 1) return e.props.org ?? states[0].toUpperCase()
  if (states.length > 1) return `Coalition: ${states.map((c) => c.toUpperCase()).join(' + ')}`
  return 'Unattributed'
}

export const ROLE_LABELS: Record<string, string> = {
  isr: 'ISR / reconnaissance',
  electronic: 'AEW / electronic',
  tanker: 'Tanker',
  airlift: 'Airlift',
  fighter: 'Fighter',
  strike: 'Strike / attack',
  bomber: 'Bomber',
  maritime_patrol: 'Maritime patrol',
  sar: 'Search & rescue',
  utility: 'Utility',
  vip: 'VIP / staff',
  trainer: 'Trainer',
  multi_mission: 'Multi-mission',
  weather: 'Weather',
  unknown: 'Role unknown',
}

export const AIRFRAME_LABELS: Record<string, string> = {
  fixed_wing: 'fixed-wing',
  helicopter: 'helicopter',
  tiltrotor: 'tilt-rotor',
  uav: 'unmanned',
  gyrocopter: 'gyrocopter',
}

/** Military states present in the current picture, most entities first. */
export function stateCounts(entities: Iterable<Entity>) {
  const counts = new Map<string, { code: string; name: string; n: number }>()
  for (const e of entities) {
    if (e.kind !== 'aircraft' && e.kind !== 'vessel') continue
    if (!e.props.military) continue
    const code = e.props.state_code
    if (!code) continue
    const row = counts.get(code) ?? { code, name: e.props.state ?? code.toUpperCase(), n: 0 }
    row.n++
    counts.set(code, row)
  }
  return [...counts.values()].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name))
}

export function flagEmoji(code: string | null | undefined): string {
  if (!code || code.length !== 2) return ''
  return String.fromCodePoint(...[...code.toUpperCase()].map((c) => 0x1f1a5 + c.charCodeAt(0)))
}

export interface Airfield {
  ident: string
  name: string
  kind: string
  lon: number
  lat: number
  elevation_m: number | null
  country: string
  icao: string | null
  military: boolean
  military_rule: string | null
}

/** Static airfield reference (OurAirports), fetched once from the live server. */
export const airfields: { all: Airfield[]; loaded: boolean; error: string | null } = { all: [], loaded: false, error: null }

export async function loadAirfields() {
  if (airfields.loaded) return
  try {
    const res = await liveFetch('/live/airfields')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    airfields.all = await res.json()
    airfields.loaded = true
  } catch (err) {
    airfields.error = `airfields unavailable: ${err}`
  }
}

/** Region boundaries for the danger-zone choropleth (static, from the live server). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const regionShapes: { features: any[]; loaded: boolean } = { features: [], loaded: false }

export async function loadRegions() {
  if (regionShapes.loaded) return
  try {
    const res = await liveFetch('/live/regions')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const fc = await res.json()
    // Feature ids match the live region entities so clicks select them.
    regionShapes.features = fc.features.map((f: { id: string }) => ({ ...f, id: `region:${f.id}` }))
    regionShapes.loaded = true
  } catch {
    // retried on the next map (re)load; the layer is simply absent meanwhile
  }
}

/** Probability → colour: clear at 0, amber in the middle, red near 1. */
export function dangerColor(p: number | null | undefined, alpha = 150): [number, number, number, number] {
  if (p == null) return [114, 116, 105, 40]
  const stops: [number, [number, number, number]][] = [
    [0, [60, 95, 102]],
    [0.35, [207, 169, 94]],
    [0.65, [221, 100, 64]],
    [1, [240, 70, 45]],
  ]
  for (let i = 1; i < stops.length; i++) {
    if (p <= stops[i][0]) {
      const [p0, c0] = stops[i - 1]
      const [p1, c1] = stops[i]
      const k = (p - p0) / (p1 - p0)
      return [0, 1, 2].map((j) => Math.round(c0[j] + k * (c1[j] - c0[j]))).concat(Math.round(alpha * (0.35 + 0.65 * p))) as [number, number, number, number]
    }
  }
  return [240, 70, 45, alpha]
}

/** DeepState front line, occupied territory and markers (from /live/front), refetched when the
 * live `front` entity reports a new snapshot. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const frontShapes: { areas: any[]; lines: any[]; points: any[]; snapshot: number | null } = { areas: [], lines: [], points: [], snapshot: null }
let frontLoading = false

export async function loadFront(snapshot: number | null) {
  if (frontLoading || (snapshot != null && snapshot === frontShapes.snapshot)) return
  frontLoading = true
  try {
    const res = await liveFetch('/live/front')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const fc = await res.json()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const by = (layer: string) => fc.features.filter((f: any) => f.properties.layer === layer)
    frontShapes.areas = [...by('unknown'), ...by('occupied')]
    frontShapes.lines = by('front')
    frontShapes.points = [...by('attack_direction'), ...by('unit'), ...by('airfield')]
    frontShapes.snapshot = fc.snapshot
  } catch {
    // the layer stays as it was; the deepstate status pill shows why
  } finally {
    frontLoading = false
  }
}
