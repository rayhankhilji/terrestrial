import { ROLE_LABELS } from './picture'
import type { Entity } from './live'

export function ago(ts: number, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - ts) / 1000))
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 48) return `${h} h ago`
  return `${Math.round(h / 24)} d ago`
}

export function utc(ts: number, withDate = false): string {
  const iso = new Date(ts).toISOString()
  return withDate ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}Z` : `${iso.slice(11, 19)}Z`
}

export function ms(v: number | null | undefined): string {
  if (v == null) return '—'
  return v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`
}

export function num(v: number | null | undefined, digits = 0): string {
  return v == null ? '—' : v.toLocaleString('en-GB', { maximumFractionDigits: digits, minimumFractionDigits: digits })
}

export function coord(lon: number, lat: number): string {
  return `${Math.abs(lat).toFixed(3)}°${lat >= 0 ? 'N' : 'S'} ${Math.abs(lon).toFixed(3)}°${lon >= 0 ? 'E' : 'W'}`
}

export function kindLabel(e: Entity): string {
  switch (e.kind) {
    case 'aircraft': {
      const p = e.props
      const what = p.uav ? 'Unmanned aircraft' : p.airframe === 'helicopter' ? 'Military helicopter' : p.military ? 'Military aircraft' : 'Aircraft'
      return p.state ? `${what} · ${p.state}` : what
    }
    case 'vessel':
      if (e.props.sanctions?.sanctioned) return 'Listed vessel'
      if (e.props.military) return `${e.props.naval_role === 'law_enforcement' ? 'Law-enforcement vessel' : 'Naval vessel'}${e.props.state ? ` · ${e.props.state}` : ''}`
      return 'Vessel'
    case 'fire':
      return 'Thermal anomaly'
    case 'news':
      return 'News event'
    case 'facility':
      return `Facility · ${e.props.type}`
    case 'station':
      return 'Port conditions'
  }
}

export function describe(e: Entity): string {
  switch (e.kind) {
    case 'aircraft': {
      const ft = e.alt != null ? `${num(e.alt / 0.3048)} ft` : ''
      const role = e.props.role && e.props.role !== 'unknown' ? (ROLE_LABELS[e.props.role] ?? e.props.role) : ''
      return [role, e.props.designation ?? e.props.type, ft, e.spd != null ? `${num(e.spd)} kn` : '', ago(e.orig_ts ?? e.ts)].filter(Boolean).join(' · ')
    }
    case 'vessel':
      return [e.props.ship_type, e.spd != null ? `${num(e.spd, 1)} kn` : '', e.props.destination ? `→ ${e.props.destination}` : '', ago(e.orig_ts ?? e.ts)]
        .filter(Boolean)
        .join(' · ')
    case 'fire':
      return `FRP ${num(e.props.frp_mw, 1)} MW · ${e.props.satellite} · ${ago(e.ts)}`
    case 'news':
      return `${e.props.place} · ${ago(e.ts)}`
    case 'facility':
      return [e.props.country, e.props.qid].filter(Boolean).join(' · ')
    case 'station':
      return `waves ${num(e.props.wave_m, 1)} m · cloud ${num(e.props.cloud_pct)}% · wind ${num(e.props.wind_kmh)} km/h`
  }
}

export function duration(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60000))
  if (m < 60) return `${m} min`
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`
}
