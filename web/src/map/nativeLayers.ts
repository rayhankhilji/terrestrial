import type { GeoJSONSource, Map as MLMap } from 'maplibre-gl'
import { live } from '../lib/live'
import { frontShapes, regionShapes } from '../lib/picture'
import type { UIState } from '../lib/store'

/**
 * Area layers drawn by MapLibre itself rather than deck.gl: the danger-forecast choropleth, live
 * air-raid alert outlines, occupied territory, the grey zone and the front line. Native fill/line
 * layers drape over 3D terrain and order correctly with the basemap's own layers (they sit under
 * the place labels), which deck's interleaved polygons did not.
 *
 * Region colours are feature-state (p = probability of a new alert, alert = active now), set
 * from the live region entities whenever they change, so the GeoJSON is uploaded once.
 */

const REGIONS = 'tl-regions'
const FRONT = 'tl-front'
const LABELS_BEFORE = ['places', 'place_city', 'place-city', 'place_label_city', 'watername_ocean']

/** What has been pushed to a given map instance (maps are recreated with the basemap). */
interface Synced {
  version: number
  front: number | null
  regions: number
  pulse: number
}
const synced = new WeakMap<MLMap, Synced>()

function beforeId(map: MLMap): string | undefined {
  const ids = new Set(map.getStyle().layers.map((l) => l.id))
  return LABELS_BEFORE.find((id) => ids.has(id)) ?? map.getStyle().layers.find((l) => l.type === 'symbol')?.id
}

/** Add sources and layers; call on every style load (styles are swapped with the basemap). */
export function installNativeLayers(map: MLMap) {
  if (map.getSource(REGIONS)) return
  const before = beforeId(map)
  map.addSource(REGIONS, { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, promoteId: 'iso' })
  map.addSource(FRONT, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
  const p = ['coalesce', ['feature-state', 'p'], -1]
  map.addLayer(
    {
      id: 'tl-danger',
      type: 'fill',
      source: REGIONS,
      paint: {
        'fill-color': ['interpolate', ['linear'], p, -1, 'rgba(0,0,0,0)', 0, '#3c5f66', 0.35, '#cfa95e', 0.65, '#dd6440', 1, '#f0462d'] as never,
        'fill-opacity': ['case', ['<', p, 0], 0, ['+', 0.04, ['*', 0.17, p]]] as never,
      },
    },
    before,
  )
  map.addLayer(
    {
      id: 'tl-region-line',
      type: 'line',
      source: REGIONS,
      paint: { 'line-color': 'rgba(232,227,215,0.24)', 'line-width': 0.8 },
    },
    before,
  )
  map.addLayer({ id: 'tl-grey', type: 'fill', source: FRONT, filter: ['==', ['get', 'layer'], 'unknown'], paint: { 'fill-color': '#8c8a80', 'fill-opacity': 0.26 } }, before)
  map.addLayer(
    {
      id: 'tl-occupied',
      type: 'fill',
      source: FRONT,
      filter: ['==', ['get', 'layer'], 'occupied'],
      paint: { 'fill-color': '#9a3b22', 'fill-opacity': 0.22 },
    },
    before,
  )
  map.addLayer(
    {
      id: 'tl-alert',
      type: 'line',
      source: REGIONS,
      paint: { 'line-color': '#f0582f', 'line-width': 2.6, 'line-opacity': ['case', ['boolean', ['feature-state', 'alert'], false], 0.9, 0] as never },
    },
    before,
  )
  map.addLayer(
    {
      id: 'tl-alert-glow',
      type: 'line',
      source: REGIONS,
      paint: {
        'line-color': '#f0582f',
        'line-width': 10,
        'line-blur': 8,
        'line-opacity': ['case', ['boolean', ['feature-state', 'alert'], false], 0.35, 0] as never,
      },
    },
    before,
  )
  map.addLayer(
    {
      id: 'tl-front-casing',
      type: 'line',
      source: FRONT,
      filter: ['==', ['get', 'layer'], 'front'],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': '#0d1110', 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 4, 10, 8], 'line-opacity': 0.85 },
    },
    before,
  )
  map.addLayer(
    {
      id: 'tl-front',
      type: 'line',
      source: FRONT,
      filter: ['==', ['get', 'layer'], 'front'],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': '#cfa95e', 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 1.8, 10, 3.5] },
    },
    before,
  )
  synced.set(map, { version: -1, front: null, regions: 0, pulse: 0 })
}

/** Per frame: push data and feature-state when it changed, layer visibility, the alert pulse. */
export function syncNativeLayers(map: MLMap, ui: UIState, now: number) {
  const regions = map.getSource(REGIONS) as GeoJSONSource | undefined
  const front = map.getSource(FRONT) as GeoJSONSource | undefined
  const st = synced.get(map)
  if (!regions || !front || !st) return
  if (regionShapes.loaded && regionShapes.features.length !== st.regions) {
    st.regions = regionShapes.features.length
    regions.setData({
      type: 'FeatureCollection',
      features: regionShapes.features.map((f) => ({ ...f, id: undefined, properties: { ...f.properties, iso: String(f.id).replace('region:', '') } })),
    })
    st.version = -1
  }
  if (frontShapes.snapshot != null && frontShapes.snapshot !== st.front) {
    st.front = frontShapes.snapshot
    front.setData({ type: 'FeatureCollection', features: [...frontShapes.areas, ...frontShapes.lines] })
  }
  if (live.version !== st.version && st.regions) {
    st.version = live.version
    for (const e of live.entities.values()) {
      if (e.kind !== 'region') continue
      map.setFeatureState({ source: REGIONS, id: e.props.iso }, { p: e.props.p_new ?? -1, alert: !!e.props.alert_active })
    }
  }
  const show = (id: string, on: boolean) => {
    if (map.getLayer(id) && (map.getLayoutProperty(id, 'visibility') !== 'none') !== on) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none')
  }
  show('tl-danger', ui.layers.danger)
  show('tl-region-line', ui.layers.danger)
  show('tl-alert', ui.layers.danger)
  show('tl-alert-glow', ui.layers.danger)
  for (const id of ['tl-grey', 'tl-occupied', 'tl-front-casing', 'tl-front']) show(id, ui.layers.front)
  for (const id of ['buildings-3d']) show(id, ui.layers.buildings)
  if (map.getLayer('tl-alert') && now - st.pulse > 90) {
    st.pulse = now
    const o = Math.round((0.5 + 0.45 * Math.sin(now / 380)) * 100) / 100
    map.setPaintProperty('tl-alert', 'line-opacity', ['case', ['boolean', ['feature-state', 'alert'], false], o, 0])
  }
}

/** The region under a screen point, if any (for clicks and hover on the choropleth). */
export function regionAt(map: MLMap, x: number, y: number): string | null {
  if (!map.getLayer('tl-danger')) return null
  const f = map.queryRenderedFeatures([x, y], { layers: ['tl-danger'] })[0]
  return f ? `region:${f.properties.iso}` : null
}
