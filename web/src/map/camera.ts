import { LngLat, type Map as MLMap } from 'maplibre-gl'
import { live, projected } from '../lib/live'
import { ui, type UIState } from '../lib/store'

/**
 * Camera presets.
 *  - globe:       the whole theatre on the globe, straight down.
 *  - theatre:     Ukraine and the Black Sea, tilted.
 *  - battlefield: low and nearly horizontal over 3D terrain and buildings, at the selection (or
 *                 the map centre). Switches to satellite imagery and terrain, where buildings exist.
 *  - chase:       rides 2 km behind the selected aircraft at its altitude, looking along its
 *                 heading; any drag or zoom hands the camera back to the user.
 */

const CHASE_BACK_M = 2200
const CHASE_ABOVE_M = 380
const EARTH_M = 6371000
let pending: { center: [number, number]; zoom: number; pitch: number; bearing: number } | null = null
let chaseBearing: number | null = null

/** A view to start the next map instance with (set when a preset swaps the basemap). */
export function takePendingView() {
  const v = pending
  pending = null
  return v
}

function target(s: UIState, map: MLMap): { lon: number; lat: number; hdg: number | null } {
  const e = s.selected ? live.entities.get(s.selected) : undefined
  if (e) {
    const [lon, lat] = projected(e, Date.now())
    return { lon, lat, hdg: e.hdg ?? null }
  }
  const c = map.getCenter()
  return { lon: c.lng, lat: c.lat, hdg: null }
}

export function applyCamera(map: MLMap, s: UIState) {
  chaseBearing = null
  if (s.camera === 'globe') {
    if (!s.globe) ui.set({ globe: true })
    map.flyTo({ center: [31, 47.5], zoom: 3.3, pitch: 0, bearing: 0, duration: 2600, essential: true })
  } else if (s.camera === 'theatre') {
    map.flyTo({ center: [33.8, 48.0], zoom: 5.35, pitch: 42, bearing: -8, duration: 2400, essential: true })
  } else if (s.camera === 'battlefield') {
    const t = target(s, map)
    const view = { center: [t.lon, t.lat] as [number, number], zoom: 13.2, pitch: 78, bearing: t.hdg ?? map.getBearing() }
    const patch: Partial<UIState> = { panelOpen: false } // immersive: the inspector stays, the side panel tucks away
    if (!s.terrain) patch.terrain = true
    if (!s.layers.buildings) patch.layers = { ...s.layers, buildings: true }
    if (s.basemap !== 'satellite') {
      // The basemap swap recreates the map; it starts from this view.
      pending = view
      patch.basemap = 'satellite'
    }
    if (Object.keys(patch).length) ui.set(patch)
    if (!pending) map.flyTo({ ...view, duration: 3200, essential: true })
  } else if (s.camera === 'chase') {
    ui.set({ panelOpen: false })
    chaseFrame(map, s, true)
  }
}

function offset(lon: number, lat: number, bearingDeg: number, metres: number): [number, number] {
  const d = metres / EARTH_M
  const b = (bearingDeg * Math.PI) / 180
  const la = (lat * Math.PI) / 180
  const lo = (lon * Math.PI) / 180
  const la2 = Math.asin(Math.sin(la) * Math.cos(d) + Math.cos(la) * Math.sin(d) * Math.cos(b))
  const lo2 = lo + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la), Math.cos(d) - Math.sin(la) * Math.sin(la2))
  return [(lo2 * 180) / Math.PI, (la2 * 180) / Math.PI]
}

/** Called every frame while the chase camera is active. */
export function chaseFrame(map: MLMap, s: UIState, first = false) {
  const e = s.selected ? live.entities.get(s.selected) : undefined
  if (!e || e.kind !== 'aircraft') return
  const [lon, lat] = projected(e, Date.now())
  const hdg = e.hdg ?? map.getBearing()
  // Ease the bearing so turns look like a camera on a boom, not a cut.
  chaseBearing = chaseBearing == null ? hdg : chaseBearing + ((((hdg - chaseBearing + 540) % 360) - 180) * 0.08)
  const alt = Math.max(e.alt ?? 0, 150)
  const [clon, clat] = offset(lon, lat, (chaseBearing + 180) % 360, CHASE_BACK_M)
  const opts = map.calculateCameraOptionsFromTo(new LngLat(clon, clat), alt + CHASE_ABOVE_M, new LngLat(lon, lat), alt)
  if (first) map.flyTo({ ...opts, duration: 1800, essential: true })
  else if (!map.isMoving()) map.jumpTo(opts)
}

/** Hand the camera back when the user drags or zooms during a chase. */
export function releaseOnInteraction(map: MLMap) {
  const release = (ev: { originalEvent?: unknown }) => {
    if (ev.originalEvent && ui.get().camera === 'chase') ui.set({ camera: 'theatre' })
  }
  map.on('dragstart', release)
  map.on('wheel', release)
}
