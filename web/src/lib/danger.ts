import { live } from './live'
import { weapon } from './weapons'

/** A place an open source reports as threatened or struck, with how we know. */
export interface DangerPoint {
  id: string
  entity: string
  lon: number
  lat: number
  title: string
  sub: string
  ts: number
  kind: 'heading' | 'reported' | 'news'
  weapon: string | null
}

const THREAT_MS = 45 * 60_000
const NEWS_MS = 6 * 3600_000

/**
 * Every recent danger point: the town an air threat is reported heading for, the place it is
 * reported at or passing, and towns named in strike-related headlines. Each keeps its source so
 * the map never shows a danger it cannot attribute.
 */
export function dangerPoints(now = Date.now()): DangerPoint[] {
  const out: DangerPoint[] = []
  for (const e of live.entities.values()) {
    const p = e.props
    if (e.kind === 'airthreat' && !p.tally && now - e.ts < THREAT_MS) {
      const w = weapon(p.weapon)
      if (p.to_place) {
        out.push({
          id: `${e.id}:to`,
          entity: e.id,
          lon: p.to_place.lon,
          lat: p.to_place.lat,
          title: `${w.short} heading for ${p.to_place.name}`,
          sub: `${p.channel_name} · reported course`,
          ts: e.ts,
          kind: 'heading',
          weapon: p.weapon,
        })
      }
      if (p.at_place) {
        out.push({
          id: `${e.id}:at`,
          entity: e.id,
          lon: p.at_place.lon,
          lat: p.at_place.lat,
          title: `${w.short} reported near ${p.at_place.name}`,
          sub: `${p.channel_name} · reported position`,
          ts: e.ts,
          kind: 'reported',
          weapon: p.weapon,
        })
      }
    } else if (e.kind === 'news' && p.strike_related && now - e.ts < NEWS_MS) {
      out.push({ id: `${e.id}:news`, entity: e.id, lon: e.lon, lat: e.lat, title: e.label, sub: `${p.outlet ?? 'News'} · ${p.place}`, ts: e.ts, kind: 'news', weapon: null })
    }
  }
  return out
}
