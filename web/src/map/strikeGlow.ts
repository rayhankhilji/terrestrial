import type { Map as MLMap } from 'maplibre-gl'
import type { DangerPoint } from '../lib/danger'

/**
 * Buildings near reported danger points (battlefield view). The footprints and heights are the
 * real OpenStreetMap buildings already loaded for the 3D basemap (OpenFreeMap tiles); we only
 * pick those within RADIUS_M of a reported point so they can be lit. Nothing is invented: if no
 * building tiles are loaded (zoomed out, dark basemap), nothing glows.
 */

export interface GlowBuilding {
  polygon: [number, number][][]
  height: number
  base: number
  point: string
  dist: number
}

const RADIUS_M = 400
const MAX_BUILDINGS = 900
const REFRESH_MS = 1500
let cache: GlowBuilding[] = []
let at = 0

function metres(lon1: number, lat1: number, lon2: number, lat2: number) {
  const k = Math.cos(((lat1 + lat2) / 2) * (Math.PI / 180))
  return Math.hypot((lon2 - lon1) * 111320 * k, (lat2 - lat1) * 110574)
}

export function glowBuildings(map: MLMap, points: DangerPoint[], now: number): GlowBuilding[] {
  if (now - at < REFRESH_MS) return cache
  at = now
  if (map.getZoom() < 13 || !points.length || !map.getSource('openmaptiles') || !map.getLayer('buildings-3d')) {
    cache = []
    return cache
  }
  const bounds = map.getBounds()
  const near = points.filter((p) => bounds.contains([p.lon, p.lat]))
  if (!near.length) {
    cache = []
    return cache
  }
  const out: GlowBuilding[] = []
  const seen = new Set<string>()
  for (const f of map.querySourceFeatures('openmaptiles', { sourceLayer: 'building' })) {
    const g = f.geometry
    if (g.type !== 'Polygon') continue
    const ring = g.coordinates[0] as [number, number][]
    const [lon, lat] = ring[0]
    for (const p of near) {
      const d = metres(lon, lat, p.lon, p.lat)
      if (d > RADIUS_M) continue
      const key = `${lon.toFixed(6)},${lat.toFixed(6)}`
      if (seen.has(key)) break
      seen.add(key)
      out.push({
        polygon: g.coordinates as [number, number][][],
        height: Number(f.properties?.render_height ?? 8),
        base: Number(f.properties?.render_min_height ?? 0),
        point: p.id,
        dist: d,
      })
      break
    }
    if (out.length >= MAX_BUILDINGS) break
  }
  cache = out
  return cache
}
