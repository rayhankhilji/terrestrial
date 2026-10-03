import type { Layer, PickingInfo } from '@deck.gl/core'
import { MapLibreOverlay } from '@deck.gl/maplibre'
import * as maplibregl from 'maplibre-gl'
import type { Map as MLMap } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { useEffect, useRef, useState } from 'react'
import { describe, kindLabel } from '../lib/format'
import { type Entity, live } from '../lib/live'
import { type Airfield, loadAirfields } from '../lib/picture'
import { select, ui, useStore } from '../lib/store'
import { CARTO_DARK, satelliteStyle, TERRAIN_SOURCE } from './basemaps'
import { liveLayers } from './liveLayers'
import { type HoverInfo, Tooltip } from './Tooltip'

maplibregl.setWorkerUrl(maplibreWorkerUrl)

/** Extra layers contributed by other features (intel pipeline overlays), composed each frame. */
export type LayerProvider = (ctx: { zoom: number; now: number }) => Layer[]
const providers = new Set<LayerProvider>()
export function registerLayers(provider: LayerProvider) {
  providers.add(provider)
  return () => {
    providers.delete(provider)
  }
}

const FRAME_MS = 1000 / 30
const START = { center: [34.2, 44.4] as [number, number], zoom: 4.6, pitch: 35, bearing: -8 }

export default function MapView() {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MLMap | null>(null)
  const overlayRef = useRef<MapLibreOverlay | null>(null)
  const readyRef = useRef(false)
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const basemap = useStore(ui, (s) => s.basemap)
  const globe = useStore(ui, (s) => s.globe)
  const terrain = useStore(ui, (s) => s.terrain)
  const flyTarget = useStore(ui, (s) => s.flyTo)

  // Map lifecycle (re-created when the basemap changes so styles never mix).
  useEffect(() => {
    if (!container.current) return
    const previous = mapRef.current
    const view = previous
      ? { center: previous.getCenter().toArray() as [number, number], zoom: previous.getZoom(), pitch: previous.getPitch(), bearing: previous.getBearing() }
      : START
    previous?.remove()

    const map = new maplibregl.Map({
      container: container.current,
      style: basemap === 'satellite' ? satelliteStyle(ui.get().globe) : CARTO_DARK,
      ...view,
      maxPitch: 85,
      attributionControl: { compact: true },
      canvasContextAttributes: { antialias: true },
    })
    mapRef.current = map
    if (import.meta.env.DEV) (window as unknown as { __map: MLMap }).__map = map
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right')
    map.addControl(new maplibregl.ScaleControl({ unit: 'nautical' }), 'bottom-right')

    const onClick = (info: PickingInfo) => {
      const e = info.object as Entity | undefined
      if (e?.id) select(e.id)
    }
    const onHover = (info: PickingInfo) => {
      const o = info.object as (Entity & Partial<Airfield>) | undefined
      if (o?.id) setHover({ x: info.x, y: info.y, kind: kindLabel(o), title: o.label, sub: describe(o) })
      else if (o?.ident) {
        const a = o as unknown as Airfield
        const sub = [a.icao ?? a.ident, a.country, a.kind.replace('_', ' '), a.military_rule && `matched “${a.military_rule}”`]
        setHover({ x: info.x, y: info.y, kind: a.military ? 'Military airfield' : 'Airfield', title: a.name, sub: sub.filter(Boolean).join(' · ') })
      } else setHover(null)
    }

    // Projection and terrain must be in place before deck attaches, so its first view matches.
    map.on('style.load', () => {
      if (!map.getSource('terrain')) map.addSource('terrain', TERRAIN_SOURCE)
      applyProjection(map, ui.get().globe, ui.get().terrain)
    })

    let raf = 0
    const build = () => {
      const now = Date.now()
      const zoom = map.getZoom()
      const s = ui.get()
      return [...[...providers].flatMap((p) => p({ zoom, now })), ...liveLayers({ zoom, now, ui: s, onClick, onHover })]
    }
    map.on('load', () => {
      readyRef.current = true
      const overlay = new MapLibreOverlay({ interleaved: true, layers: [] })
      overlayRef.current = overlay
      map.addControl(overlay)
      // deck only inserts its MapLibre layer group while map.isStyleLoaded() is true, and that
      // requires every tile to be loaded — practically never while globe + terrain tiles stream
      // and we repaint each frame. MapLibre's addLayer itself only needs the style document,
      // which is loaded inside 'load'. All our layers share one group (no beforeId), so create
      // it once here; later setProps calls reuse it.
      const isStyleLoaded = map.isStyleLoaded.bind(map)
      map.isStyleLoaded = () => true
      try {
        overlay.setProps({ layers: build() })
      } finally {
        map.isStyleLoaded = isStyleLoaded
      }
      // Redraw at most ~30 fps (dead reckoning and pings stay smooth; laptops stay cool) and not
      // at all while the tab is hidden.
      let last = 0
      const frame = (t: number) => {
        raf = requestAnimationFrame(frame)
        if (document.hidden || t - last < FRAME_MS) return
        last = t
        overlay.setProps({ layers: build() })
        const s = ui.get()
        if (s.follow && s.selected) {
          const e = live.entities.get(s.selected)
          if (e && !map.isMoving()) map.easeTo({ center: [e.lon, e.lat], duration: 400 })
        }
      }
      raf = requestAnimationFrame(frame)
    })
    live.connect()
    void loadAirfields()
    return () => {
      cancelAnimationFrame(raf)
      overlayRef.current = null
      readyRef.current = false
    }
  }, [basemap])

  useEffect(() => {
    const map = mapRef.current
    if (map && readyRef.current) applyProjection(map, globe, terrain)
  }, [globe, terrain])

  useEffect(() => {
    if (!flyTarget || !mapRef.current) return
    mapRef.current.flyTo({
      center: [flyTarget.lon, flyTarget.lat],
      zoom: flyTarget.zoom ?? 8,
      pitch: flyTarget.pitch ?? 55,
      bearing: flyTarget.bearing ?? mapRef.current.getBearing(),
      duration: 2200,
      essential: true,
    })
  }, [flyTarget])

  return (
    <div className="map-wrap">
      <div ref={container} className="map" />
      {hover && <Tooltip {...hover} />}
    </div>
  )
}

function applyProjection(map: MLMap, globe: boolean, terrain: boolean) {
  map.setProjection({ type: globe ? 'globe' : 'mercator' })
  map.setTerrain(terrain ? { source: 'terrain', exaggeration: 1.6 } : null)
}
