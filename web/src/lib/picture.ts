import type { Entity } from './live'
import type { UIState } from './store'

/**
 * What the current mode shows (CLAUDE.md §16.1). The live server already drops civil aircraft;
 * here the Military picture also hides merchant vessels and civil facilities, and the state
 * filter applies to anything attributed to a state.
 */
export function visible(e: Entity, s: Pick<UIState, 'mode' | 'states'>): boolean {
  if (s.mode === 'military') {
    if (e.kind === 'vessel' && !e.props.military) return false
    if (e.kind === 'facility' && e.props.type !== 'airbase' && e.props.type !== 'naval base') return false
    if (e.kind === 'station') return false
  }
  if (s.states.length && (e.kind === 'aircraft' || e.kind === 'vessel')) {
    return s.states.includes(e.props.state_code)
  }
  return true
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
export function stateCounts(entities: Iterable<Entity>, mode: UIState['mode']) {
  const counts = new Map<string, { code: string; name: string; n: number }>()
  for (const e of entities) {
    if (e.kind !== 'aircraft' && e.kind !== 'vessel') continue
    if (mode === 'military' && !e.props.military) continue
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
    const res = await fetch('/live/airfields')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    airfields.all = await res.json()
    airfields.loaded = true
  } catch (err) {
    airfields.error = `airfields unavailable: ${err}`
  }
}
