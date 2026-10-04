import type { Layer, PickingInfo } from '@deck.gl/core'
import { PathStyleExtension, type PathStyleExtensionProps } from '@deck.gl/extensions'
import { ColumnLayer, LineLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import { SimpleMeshLayer } from '@deck.gl/mesh-layers'
import { ahead, type Entity, live, projected } from '../lib/live'
import { type Airfield, airfields, frontShapes, loadFront, visible } from '../lib/picture'
import type { UIState } from '../lib/store'
import type { DangerPoint } from '../lib/danger'
import { weapon } from '../lib/weapons'
import type { GlowBuilding } from './strikeGlow'
import { DEST_COLORS, type Destination, type Flight, predState, type Terminal, trackState } from '../lib/track'
import { aircraftMesh, helicopterMesh, shipMesh, uavMesh } from './meshes'

export type RGBA = [number, number, number, number]

export const COLORS = {
  civil: [147, 197, 253, 255] as RGBA,
  military: [251, 191, 36, 255] as RGBA,
  uav: [244, 114, 182, 255] as RGBA,
  isr: [251, 191, 36, 255] as RGBA,
  tanker: [45, 212, 191, 255] as RGBA,
  airlift: [147, 197, 253, 255] as RGBA,
  combat: [248, 113, 113, 255] as RGBA,
  rotary: [163, 230, 53, 255] as RGBA,
  naval_ship: [56, 189, 248, 255] as RGBA,
  airfield: [203, 213, 225, 255] as RGBA,
  vessel: [94, 234, 212, 255] as RGBA,
  listed: [244, 63, 94, 255] as RGBA,
  fire: [255, 122, 48, 255] as RGBA,
  news: [167, 139, 250, 255] as RGBA,
  station: [226, 232, 240, 255] as RGBA,
  port: [148, 163, 184, 255] as RGBA,
  refinery: [250, 204, 21, 255] as RGBA,
  airbase: [96, 165, 250, 255] as RGBA,
  naval: [56, 189, 248, 255] as RGBA,
  selected: [255, 255, 255, 255] as RGBA,
}

const AIRCRAFT_MESH = aircraftMesh()
const HELICOPTER_MESH = helicopterMesh()
const UAV_MESH = uavMesh()
const SHIP_MESH = shipMesh()
const PING_MS = 1600
const MESH_MIN_ZOOM = 5.5
const ALERT_RIPPLE_MS = 12000
const DASH = new PathStyleExtension({ dash: true })
const ON_TOP = { depthCompare: 'always' as const, depthWriteEnabled: false }

/** Aircraft colour by role group: what it is doing matters more than who flies it. */
export function aircraftColor(e: Entity): RGBA {
  const p = e.props
  if (p.uav) return COLORS.uav
  if (!p.military) return COLORS.civil
  if (p.airframe === 'helicopter' || p.airframe === 'tiltrotor') return COLORS.rotary
  switch (p.role) {
    case 'isr':
    case 'electronic':
    case 'maritime_patrol':
      return COLORS.isr
    case 'tanker':
      return COLORS.tanker
    case 'fighter':
    case 'strike':
    case 'bomber':
      return COLORS.combat
    case 'airlift':
    case 'vip':
      return COLORS.airlift
    default:
      return COLORS.military
  }
}

export function vesselColor(e: Entity): RGBA {
  if (e.props.sanctions?.sanctioned) return COLORS.listed
  return e.props.military ? COLORS.naval_ship : COLORS.vessel
}

export function facilityColor(e: Entity): RGBA {
  const t = e.props.type
  if (t === 'refinery') return COLORS.refinery
  if (t === 'airbase') return COLORS.airbase
  if (t === 'naval base') return COLORS.naval
  return COLORS.port
}

export function entityColor(e: Entity): RGBA {
  switch (e.kind) {
    case 'aircraft':
      return aircraftColor(e)
    case 'vessel':
      return vesselColor(e)
    case 'fire':
      return COLORS.fire
    case 'news':
      return COLORS.news
    case 'facility':
      return facilityColor(e)
    default:
      return COLORS.station
  }
}

export const MISSION_COLORS: Record<string, RGBA> = {
  air_refuelling: [45, 212, 191, 255],
  isr_orbit: [251, 191, 36, 255],
  maritime_patrol: [56, 189, 248, 255],
  airlift: [147, 197, 253, 255],
  fighter_cap: [248, 113, 113, 255],
  rotary_ops: [163, 230, 53, 255],
  training: [203, 213, 225, 255],
  vip_transport: [196, 181, 253, 255],
  unknown: [226, 232, 240, 255],
}

export function netColor(e: Entity): RGBA {
  return MISSION_COLORS[e.props.mission] ?? MISSION_COLORS.unknown
}

const RELATION_COLORS: Record<string, RGBA> = {
  THERMAL_ANOMALY_AT: [255, 122, 48, 220],
  REPORTED_AT: [167, 139, 250, 200],
  PRESENT_IN_AOI: [244, 63, 94, 220],
}

/** Altitude ramp for flight history: low = green, mid = amber, high = violet. */
export function altitudeColor(m: number): RGBA {
  const stops: [number, RGBA][] = [
    [0, [74, 222, 128, 255]],
    [3000, [250, 204, 21, 255]],
    [8000, [251, 146, 60, 255]],
    [12000, [192, 132, 252, 255]],
  ]
  if (m <= 0) return stops[0][1]
  for (let i = 1; i < stops.length; i++) {
    const [h1, c1] = stops[i]
    const [h0, c0] = stops[i - 1]
    if (m <= h1) {
      const k = (m - h0) / (h1 - h0)
      return c0.map((v, j) => Math.round(v + k * (c1[j] - v))) as RGBA
    }
  }
  return stops[stops.length - 1][1]
}

/** Exaggerate aircraft altitude when zoomed out so the 3D picture stays legible. */
export function altitudeScale(zoom: number) {
  return Math.min(8, Math.max(1, 2 ** (8 - zoom)))
}

interface Context {
  zoom: number
  now: number
  ui: UIState
  onClick: (info: PickingInfo) => void
  onHover: (info: PickingInfo) => void
  danger?: DangerPoint[]
  glow?: GlowBuilding[]
}

/** A great-circle arc from a to b, lifted into a parabola `peakM` high at its middle. */
function arc(a: [number, number], b: [number, number], peakM: number, n = 32): [number, number, number][] {
  const out: [number, number, number][] = []
  for (let i = 0; i <= n; i++) {
    const t = i / n
    out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, Math.sin(Math.PI * t) * peakM])
  }
  return out
}

function km(a: [number, number], b: [number, number]) {
  const k = Math.cos(((a[1] + b[1]) / 2) * (Math.PI / 180))
  return Math.hypot((b[0] - a[0]) * 111.32 * k, (b[1] - a[1]) * 110.574)
}

const THREAT_TTL_MS = 45 * 60_000
const LOFT: Record<string, number> = { ballistic: 0.35, cruise_missile: 0.06, missile: 0.12, glide_bomb: 0.08, jet_uav: 0.04, uav: 0.03, unknown: 0.03 }

export function liveLayers({ zoom, now, ui, onClick, onHover, danger = [], glow = [] }: Context): Layer[] {
  const L = ui.layers
  const all = [...live.entities.values()].filter((e) => visible(e, ui))
  const by = (kind: Entity['kind']) => all.filter((e) => e.kind === kind)
  const aircraft = L.aircraft ? by('aircraft') : []
  const vessels = L.vessels ? by('vessel') : []
  const facilities = L.facilities ? by('facility') : []
  const fires = L.fires ? by('fire') : []
  const news = L.news ? by('news') : []
  const stations = L.stations ? by('station') : []
  const nets = L.nets ? by('net') : []
  const fields = airfields.all.filter((a) => (a.military ? L.airfields : L.smallFields && (a.kind === 'small_airport' || a.kind === 'heliport')))
  const altK = altitudeScale(zoom)
  // Meshes are in metres. Scale them to a legible on-screen size (~24 px aircraft, ~18 px
  // hulls at ~44°N) but never below true size; below MESH_MIN_ZOOM only dots are drawn.
  const aircraftScale = Math.max(1, 67600 / 2 ** zoom)
  const shipScale = Math.max(1, 11270 / 2 ** zoom)
  const meshes = zoom >= MESH_MIN_ZOOM
  const pos = (e: Entity): [number, number, number] => {
    const [lon, lat] = projected(e, now)
    return [lon, lat, (e.alt ?? 0) * altK]
  }
  const common = { pickable: true, onClick, onHover }
  const layers: Layer[] = []

  // DeepStateMap markers on demand: attack directions, estimated Russian unit positions and the
  // airfields Russia operates from. (Occupied territory, grey zone and the front line itself are
  // native MapLibre layers draped on the terrain: map/nativeLayers.ts.)
  const frontEntity = live.entities.get('front:deepstate')
  if (frontEntity && frontEntity.props.snapshot !== frontShapes.snapshot) void loadFront(frontEntity.props.snapshot)
  if (L.units && frontShapes.snapshot != null) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    type P = any
    layers.push(
      new ScatterplotLayer<P>({
        id: 'front-points',
        data: frontShapes.points,
        getPosition: (f: P) => f.geometry.coordinates,
        getRadius: (f: P) => (f.properties.layer === 'airfield' ? 6 : f.properties.layer === 'unit' ? 3.5 : 5),
        radiusUnits: 'pixels',
        stroked: true,
        lineWidthMinPixels: 1.5,
        getFillColor: (f: P) => (f.properties.layer === 'airfield' ? [2, 6, 12, 230] : f.properties.layer === 'unit' ? [251, 113, 133, 200] : [255, 69, 58, 230]),
        getLineColor: (f: P) => (f.properties.layer === 'airfield' ? [251, 113, 133, 255] : [2, 6, 12, 230]),
        pickable: true,
        onHover: (info) => {
          const f = info.object as P
          onHover({ ...info, object: f ? { id: `deepstate:${f.properties.key ?? 'attack'}`, kind: 'deepstate', label: f.properties.name ?? 'Direction of attack', props: f.properties } : undefined })
        },
        parameters: ON_TOP,
      }),
    )
  }

  // Air threats in flight (Air Force of Ukraine / monitors): a marker where the report places the
  // threat, a lofted arc to the town it is reported heading for with a comet travelling along it,
  // and a pulsing ring on that town. Everything fades out over the report's 45-minute life.
  if (L.airthreats && ui.mode === 'military') {
    const reports = by('airthreat').filter((e) => !e.props.tally && now - e.ts < THREAT_TTL_MS)
    const life = (e: Entity) => Math.max(0.15, 1 - (now - e.ts) / THREAT_TTL_MS)
    const arcs = reports
      .filter((e) => e.props.to_place)
      .map((e) => {
        const a: [number, number] = [e.lon, e.lat]
        const b: [number, number] = [e.props.to_place.lon, e.props.to_place.lat]
        const peak = Math.min(120_000, km(a, b) * 1000 * (LOFT[e.props.weapon ?? 'unknown'] ?? 0.04))
        return { e, path: arc(a, b, peak * altK) }
      })
    layers.push(
      new PathLayer<{ e: Entity; path: [number, number, number][] }, PathStyleExtensionProps<{ e: Entity; path: [number, number, number][] }>>({
        id: 'threat-arcs',
        data: arcs,
        getPath: (d) => d.path,
        getColor: (d) => {
          const c = weapon(d.e.props.weapon).color
          return [c[0], c[1], c[2], Math.round(200 * life(d.e))]
        },
        getWidth: (d) => (ui.selected === d.e.id ? 3.5 : 2),
        widthUnits: 'pixels',
        getDashArray: [6, 4],
        extensions: [DASH],
        pickable: true,
        onClick: (info) => info.object && onClick({ ...info, object: info.object.e }),
        onHover: (info) => onHover({ ...info, object: info.object?.e }),
        updateTriggers: { getColor: now, getWidth: ui.selected, getPath: altK },
        parameters: ON_TOP,
      }),
      new ScatterplotLayer<{ e: Entity; path: [number, number, number][] }>({
        id: 'threat-comets',
        data: arcs,
        getPosition: (d) => {
          const t = ((now - d.e.ts) % 4000) / 4000
          const i = Math.min(d.path.length - 1, Math.floor(t * (d.path.length - 1)))
          return d.path[i]
        },
        getRadius: 4,
        radiusUnits: 'pixels',
        getFillColor: (d) => {
          const c = weapon(d.e.props.weapon).color
          return [c[0], c[1], c[2], 255]
        },
        updateTriggers: { getPosition: now },
        parameters: ON_TOP,
      }),
      new ScatterplotLayer<{ e: Entity; path: [number, number, number][] }>({
        id: 'threat-targets',
        data: arcs,
        getPosition: (d) => d.path[d.path.length - 1],
        getRadius: () => 7 + 6 * (0.5 + 0.5 * Math.sin(now / 260)),
        radiusUnits: 'pixels',
        stroked: true,
        filled: false,
        lineWidthMinPixels: 1.5,
        getLineColor: (d) => {
          const c = weapon(d.e.props.weapon).color
          return [c[0], c[1], c[2], Math.round(230 * life(d.e))]
        },
        updateTriggers: { getRadius: now, getLineColor: now },
        parameters: ON_TOP,
      }),
      new ScatterplotLayer<Entity>({
        id: 'threat-markers',
        data: reports,
        getPosition: (e) => [e.lon, e.lat],
        getRadius: (e) => (ui.selected === e.id ? 9 : 6.5),
        radiusUnits: 'pixels',
        stroked: true,
        lineWidthMinPixels: 2,
        getFillColor: (e) => {
          const c = weapon(e.props.weapon).color
          return [c[0], c[1], c[2], Math.round(240 * life(e))]
        },
        getLineColor: [5, 7, 10, 230],
        ...common,
        updateTriggers: { getFillColor: now, getRadius: ui.selected },
        parameters: ON_TOP,
      }),
    )
    if (zoom >= 5.8) {
      layers.push(
        new TextLayer<Entity>({
          id: 'threat-labels',
          data: reports,
          getPosition: (e) => [e.lon, e.lat],
          getText: (e) => `${e.props.count ? `${e.props.count}× ` : ''}${weapon(e.props.weapon).short}${e.props.to_place ? ` → ${e.props.to_place.name}` : ''}`,
          getSize: 11.5,
          sizeUnits: 'pixels',
          getColor: (e) => {
            const c = weapon(e.props.weapon).color
            return [c[0], c[1], c[2], Math.round(255 * life(e))]
          },
          getPixelOffset: [0, -16],
          fontFamily: 'Geist Mono, ui-monospace, monospace',
          fontWeight: 600,
          outlineWidth: 3,
          outlineColor: [5, 7, 10, 230],
          fontSettings: { sdf: true },
          updateTriggers: { getColor: now },
          parameters: { ...ON_TOP, cullMode: 'none' as const },
        }),
      )
    }
    // The latest overnight tally: dashed arcs from each stated launch direction into Ukraine.
    const tally = by('airthreat')
      .filter((e) => e.props.tally && now - e.ts < 24 * 3600_000)
      .sort((a, b) => b.ts - a.ts)[0]
    if (tally && zoom < 7.5) {
      const into: [number, number] = [tally.lon, tally.lat]
      const launches = (tally.props.tally.launch_areas as { name: string; lon: number; lat: number }[]).map((a) => ({
        a,
        path: arc([a.lon, a.lat], into, Math.min(150_000, km([a.lon, a.lat], into) * 120) * altK),
      }))
      layers.push(
        new PathLayer<{ a: { name: string }; path: [number, number, number][] }, PathStyleExtensionProps<{ a: { name: string }; path: [number, number, number][] }>>({
          id: 'launch-arcs',
          data: launches,
          getPath: (d) => d.path,
          getColor: [255, 77, 94, 120],
          getWidth: 1.5,
          widthUnits: 'pixels',
          getDashArray: [2, 5],
          extensions: [DASH],
          updateTriggers: { getPath: altK },
        }),
        new TextLayer<{ a: { name: string; lon: number; lat: number } }>({
          id: 'launch-labels',
          data: launches,
          getPosition: (d) => [d.a.lon, d.a.lat],
          getText: (d) => `launch area · ${d.a.name}`,
          getSize: 11,
          sizeUnits: 'pixels',
          getColor: [255, 122, 138, 230],
          getPixelOffset: [0, 14],
          fontFamily: 'Geist Mono, ui-monospace, monospace',
          outlineWidth: 3,
          outlineColor: [5, 7, 10, 230],
          fontSettings: { sdf: true },
          parameters: { ...ON_TOP, cullMode: 'none' as const },
        }),
      )
    }
  }

  // Reported danger points in 3D: a light beam over each, and the real buildings around it lit
  // red when zoomed in (battlefield view). Sources are air-threat headings/positions and
  // strike-related headlines; nothing glows without a report behind it.
  if (L.buildings && danger.length) {
    const beams = danger.filter((d) => d.kind !== 'reported' || zoom >= 9)
    layers.push(
      new ColumnLayer<DangerPoint>({
        id: 'danger-beams',
        data: beams,
        getPosition: (d) => [d.lon, d.lat],
        diskResolution: 24,
        radius: Math.max(40, 9000 / 2 ** (zoom - 7)),
        extruded: true,
        getElevation: (d) => (d.kind === 'news' ? 1800 : 3200) * Math.max(1, altK),
        getFillColor: (d) => (d.kind === 'news' ? [255, 178, 36, 70] : [255, 59, 79, 85]),
        material: false,
        pickable: true,
        onClick: (info) => {
          const d = info.object as DangerPoint | undefined
          const e = d ? live.entities.get(d.entity) : undefined
          if (e) onClick({ ...info, object: e })
        },
        onHover: (info) => {
          const d = info.object as DangerPoint | undefined
          onHover({ ...info, object: d ? live.entities.get(d.entity) : undefined })
        },
        updateTriggers: { getElevation: altK },
      }),
    )
    if (glow.length) {
      const pulse = 0.55 + 0.45 * Math.sin(now / 420)
      layers.push(
        new PolygonLayer<GlowBuilding>({
          id: 'danger-buildings',
          data: glow,
          getPolygon: (b) => b.polygon,
          extruded: true,
          getElevation: (b) => b.height + 0.5,
          getFillColor: (b) => [255, 70 + Math.round(60 * (b.dist / 400)), 60, Math.round(210 * pulse * (1 - b.dist / 520))],
          getLineColor: [255, 120, 100, 200],
          material: { ambient: 0.6, diffuse: 0.5, shininess: 8, specularColor: [255, 120, 100] },
          updateTriggers: { getFillColor: now },
        }),
      )
    }
  }

  // Imaging satellites: position at true altitude (on the globe they float in space), a dashed
  // ground track for the next 45 minutes, and the imaging reach of the selected one.
  if (L.satellites) {
    const sats = by('satellite')
    const satColor = (e: Entity): RGBA => (e.props.sensor === 'SAR' ? [63, 213, 242, 255] : [245, 197, 24, 255])
    const sel = sats.find((e) => e.id === ui.selected)
    layers.push(
      new PathLayer<Entity, PathStyleExtensionProps<Entity>>({
        id: 'sat-tracks',
        data: zoom < 7 ? sats : sel ? [sel] : [],
        getPath: (e) => e.props.track,
        getColor: (e) => {
          const c = satColor(e)
          return [c[0], c[1], c[2], e.id === ui.selected ? 200 : 45]
        },
        getWidth: (e) => (e.id === ui.selected ? 2 : 1),
        widthUnits: 'pixels',
        getDashArray: [3, 5],
        extensions: [DASH],
        updateTriggers: { getColor: ui.selected, getWidth: ui.selected },
      }),
      new ScatterplotLayer<Entity>({
        id: 'sat-footprint',
        data: sel ? [sel] : [],
        getPosition: (e) => [e.lon, e.lat],
        getRadius: (e) => e.props.access_km * 1000,
        stroked: true,
        filled: true,
        getFillColor: (e) => {
          const c = satColor(e)
          return [c[0], c[1], c[2], 18]
        },
        getLineColor: (e) => satColor(e),
        lineWidthMinPixels: 1,
      }),
      new ScatterplotLayer<Entity>({
        id: 'satellites',
        data: sats,
        getPosition: (e) => [e.lon, e.lat, zoom < 6 ? (e.alt ?? 0) * 0.35 : 0],
        getRadius: (e) => (e.id === ui.selected ? 6 : 3.5),
        radiusUnits: 'pixels',
        stroked: true,
        lineWidthMinPixels: 1,
        getFillColor: (e) => satColor(e),
        getLineColor: [5, 7, 10, 255],
        ...common,
        updateTriggers: { getRadius: ui.selected, getPosition: zoom < 6 },
      }),
    )
    if (zoom < 6.5 && zoom > 2.5) {
      layers.push(
        new TextLayer<Entity>({
          id: 'sat-labels',
          data: sats.filter((e) => e.props.sensor === 'SAR' && !e.props.tasked || e.id === ui.selected),
          getPosition: (e) => [e.lon, e.lat, zoom < 6 ? (e.alt ?? 0) * 0.35 : 0],
          getText: (e) => e.label,
          getSize: 10.5,
          sizeUnits: 'pixels',
          getColor: (e) => satColor(e),
          getPixelOffset: [0, -12],
          fontFamily: 'Geist Mono, ui-monospace, monospace',
          outlineWidth: 3,
          outlineColor: [5, 7, 10, 230],
          fontSettings: { sdf: true },
          parameters: { cullMode: 'none' as const },
        }),
      )
    }
  }

  // GNSS interference: share of aircraft per cell reporting degraded GPS accuracy (gpsjam-style).
  if (L.gnss) {
    const cells = by('gnss')
    const fill = (lv: string): RGBA => (lv === 'high' ? [244, 63, 94, 115] : lv === 'medium' ? [250, 204, 21, 85] : [34, 197, 94, 28])
    layers.push(
      new PolygonLayer<Entity>({
        id: 'gnss',
        data: cells,
        getPolygon: (e) => e.props.polygon,
        getFillColor: (e) => fill(e.props.level),
        getLineColor: (e) => (e.props.level === 'low' ? [34, 197, 94, 50] : [244, 63, 94, 140]),
        getLineWidth: 1,
        lineWidthUnits: 'pixels',
        stroked: true,
        ...common,
        updateTriggers: { getFillColor: live.version },
      }),
    )
  }

  if (fields.length) {
    layers.push(
      new ScatterplotLayer<Airfield>({
        id: 'airfields',
        data: fields,
        getPosition: (a) => [a.lon, a.lat],
        getRadius: (a) => (a.military ? 4.5 : 2.5),
        radiusUnits: 'pixels',
        stroked: true,
        lineWidthMinPixels: 1.5,
        getFillColor: (a) => (a.military ? [30, 41, 59, 230] : [15, 23, 42, 160]),
        getLineColor: (a) => (a.military ? COLORS.airfield : [100, 116, 139, 200]),
        parameters: ON_TOP,
        pickable: true,
        onHover,
      }),
    )
    if (zoom >= 7) {
      layers.push(
        new TextLayer<Airfield>({
          id: 'airfield-labels',
          data: fields.filter((a) => a.military || zoom >= 9),
          getPosition: (a) => [a.lon, a.lat],
          getText: (a) => a.name,
          getSize: 11,
          sizeUnits: 'pixels',
          sizeMaxPixels: 12,
          getColor: [203, 213, 225, 220],
          getPixelOffset: [0, 14],
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          outlineWidth: 3,
          outlineColor: [2, 6, 12, 230],
          fontSettings: { sdf: true },
          parameters: { ...ON_TOP, cullMode: 'none' as const },
        }),
      )
    }
  }

  if (L.facilities) {
    layers.push(
      new ScatterplotLayer<Entity>({
        id: 'facilities',
        data: facilities,
        getPosition: (e) => [e.lon, e.lat],
        getRadius: (e) => (e.props.type === 'port' ? 3.5 : 5),
        radiusUnits: 'pixels',
        stroked: true,
        lineWidthMinPixels: 1.5,
        getFillColor: [8, 12, 20, 200],
        getLineColor: (e) => facilityColor(e),
        parameters: ON_TOP,
        ...common,
      }),
    )
  }

  if (L.stations) {
    layers.push(
      new ScatterplotLayer<Entity>({
        id: 'stations',
        data: stations,
        getPosition: (e) => [e.lon, e.lat],
        getRadius: 9,
        radiusUnits: 'pixels',
        stroked: true,
        filled: false,
        lineWidthMinPixels: 1,
        getLineColor: (e) => (e.props.occupied_ua ? [244, 63, 94, 160] : [226, 232, 240, 110]),
        parameters: ON_TOP,
        ...common,
      }),
    )
  }

  // Nets: a hull over each group's recent tracks and a line for every evidence link.
  if (nets.length) {
    const memberLinks = nets.flatMap((n) =>
      (n.props.links as { a: string; b: string; w: number }[])
        .map((l) => ({ net: n, a: live.entities.get(l.a), b: live.entities.get(l.b), w: l.w }))
        .filter((l): l is { net: Entity; a: Entity; b: Entity; w: number } => !!l.a && !!l.b),
    )
    layers.push(
      new PolygonLayer<Entity>({
        id: 'net-hulls',
        data: nets.filter((n) => n.props.hull?.length > 3),
        getPolygon: (n) => n.props.hull,
        getFillColor: (n) => {
          const c = netColor(n)
          return [c[0], c[1], c[2], ui.selected === n.id ? 55 : 28]
        },
        getLineColor: (n) => {
          const c = netColor(n)
          return [c[0], c[1], c[2], 200]
        },
        getLineWidth: (n) => (ui.selected === n.id ? 2.5 : 1.5),
        lineWidthUnits: 'pixels',
        stroked: true,
        filled: true,
        parameters: ON_TOP,
        updateTriggers: { getFillColor: ui.selected, getLineWidth: ui.selected },
        ...common,
      }),
      new LineLayer<{ net: Entity; a: Entity; b: Entity; w: number }>({
        id: 'net-links',
        data: memberLinks,
        getSourcePosition: (l) => pos(l.a),
        getTargetPosition: (l) => pos(l.b),
        getColor: (l) => {
          const c = netColor(l.net)
          return [c[0], c[1], c[2], Math.round(120 + 135 * l.w)]
        },
        getWidth: (l) => 1 + 2 * l.w,
        widthUnits: 'pixels',
        parameters: ON_TOP,
        updateTriggers: { getSourcePosition: now, getTargetPosition: now },
      }),
      new TextLayer<Entity>({
        id: 'net-labels',
        data: nets,
        getPosition: (n) => {
          const ring: [number, number][] = n.props.hull ?? []
          const top = ring.reduce((m, p) => (p[1] > m[1] ? p : m), [n.lon, n.lat] as [number, number])
          return [n.lon, top[1]]
        },
        getText: (n) => `${n.label.toUpperCase()} · ${(n.props.states as string[]).map((c) => c.toUpperCase()).join('+')}`,
        getSize: 12,
        sizeUnits: 'pixels',
        getColor: netColor,
        getPixelOffset: [0, -10],
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontWeight: 700,
        outlineWidth: 3,
        outlineColor: [2, 6, 12, 230],
        fontSettings: { sdf: true },
        parameters: { ...ON_TOP, cullMode: 'none' as const },
      }),
    )
  }

  if (L.relations) {
    const rels = [...live.relations.values()].filter((r) => live.entities.has(r.a) && live.entities.has(r.b))
    layers.push(
      new LineLayer({
        id: 'relations',
        data: rels,
        getSourcePosition: (r) => {
          const e = live.entities.get(r.a)!
          return [e.lon, e.lat]
        },
        getTargetPosition: (r) => {
          const e = live.entities.get(r.b)!
          return [e.lon, e.lat]
        },
        getColor: (r) => RELATION_COLORS[r.rel] ?? [255, 255, 255, 160],
        getWidth: 2,
        widthMinPixels: 1.5,
        parameters: ON_TOP,
      }),
    )
  }

  if (fires.length) {
    layers.push(
      new ScatterplotLayer<Entity>({
        id: 'fires-glow',
        data: fires,
        getPosition: (e) => [e.lon, e.lat],
        getRadius: (e) => 6 + 2 * Math.sqrt(e.props.frp_mw ?? 1),
        radiusUnits: 'pixels',
        getFillColor: [255, 122, 48, 60],
        parameters: ON_TOP,
      }),
      new ScatterplotLayer<Entity>({
        id: 'fires',
        data: fires,
        getPosition: (e) => [e.lon, e.lat],
        getRadius: 3,
        radiusUnits: 'pixels',
        getFillColor: COLORS.fire,
        parameters: ON_TOP,
        ...common,
      }),
    )
  }

  if (news.length) {
    layers.push(
      new ScatterplotLayer<Entity>({
        id: 'news',
        data: news,
        getPosition: (e) => [e.lon, e.lat],
        getRadius: 6,
        radiusUnits: 'pixels',
        stroked: true,
        lineWidthMinPixels: 1.5,
        getFillColor: [167, 139, 250, 90],
        getLineColor: COLORS.news,
        parameters: ON_TOP,
        ...common,
      }),
    )
  }

  if (L.trails) {
    const trails = [...aircraft, ...vessels]
      .map((e) => ({ e, path: live.trails.get(e.id) }))
      .filter((t): t is { e: Entity; path: [number, number, number][] } => !!t.path && t.path.length > 1)
    layers.push(
      new PathLayer<{ e: Entity; path: [number, number, number][] }>({
        id: 'trails',
        data: trails,
        getPath: (t) => t.path.map(([lon, lat, alt]) => [lon, lat, alt * altK] as [number, number, number]),
        getColor: (t) => {
          const c = entityColor(t.e)
          return [c[0], c[1], c[2], 110]
        },
        getWidth: 2,
        widthMinPixels: 1.2,
        updateTriggers: { getPath: altK },
      }),
    )
  }

  if (aircraft.length) {
    const airborne = aircraft.filter((e) => (e.alt ?? 0) > 50)
    layers.push(
      new LineLayer<Entity>({
        id: 'aircraft-stalks',
        data: airborne,
        getSourcePosition: (e) => {
          const p = pos(e)
          return [p[0], p[1], 0]
        },
        getTargetPosition: pos,
        getColor: (e) => {
          const c = aircraftColor(e)
          return [c[0], c[1], c[2], 70]
        },
        getWidth: 1,
        updateTriggers: { getSourcePosition: now, getTargetPosition: now },
      }),
      ...(
        [
          ['aircraft-mesh', AIRCRAFT_MESH, (e: Entity) => e.props.airframe !== 'helicopter' && !e.props.uav, 1],
          ['helicopter-mesh', HELICOPTER_MESH, (e: Entity) => e.props.airframe === 'helicopter', 1.8],
          ['uav-mesh', UAV_MESH, (e: Entity) => !!e.props.uav, 1.6],
        ] as const
      ).map(
        ([id, mesh, keep, k]) =>
          new SimpleMeshLayer<Entity>({
            id,
            data: meshes ? aircraft.filter(keep) : [],
            mesh,
            getPosition: pos,
            getOrientation: (e) => [0, -(e.hdg ?? 0), 0],
            getColor: aircraftColor,
            sizeScale: aircraftScale * k,
            updateTriggers: { getPosition: now },
            ...common,
          }),
      ),
      new ScatterplotLayer<Entity>({
        id: 'aircraft-dots',
        data: aircraft,
        getPosition: pos,
        getRadius: meshes ? 1.5 : 3,
        radiusUnits: 'pixels',
        getFillColor: aircraftColor,
        updateTriggers: { getPosition: now },
        ...common,
      }),
    )
  }

  if (vessels.length) {
    layers.push(
      new SimpleMeshLayer<Entity>({
        id: 'vessel-mesh',
        data: meshes ? vessels : [],
        mesh: SHIP_MESH,
        getPosition: (e) => [e.lon, e.lat, 0],
        getOrientation: (e) => [0, -(e.hdg ?? 0), 0],
        getColor: vesselColor,
        sizeScale: shipScale,
        ...common,
      }),
      new ScatterplotLayer<Entity>({
        id: 'vessel-dots',
        data: vessels,
        getPosition: (e) => [e.lon, e.lat],
        getRadius: 3,
        radiusUnits: 'pixels',
        getFillColor: vesselColor,
        parameters: ON_TOP,
        ...common,
      }),
    )
  }

  // Short-horizon forecast: where each moving track will be in 10 minutes at current course/speed.
  if (L.forecast) {
    const moving = [...aircraft, ...vessels].filter((e) => (e.spd ?? 0) > 1 && e.hdg != null)
    layers.push(
      new PathLayer<Entity, PathStyleExtensionProps<Entity>>({
        id: 'forecast',
        data: moving,
        getPath: (e) => {
          const [lon, lat, z] = pos(e)
          const [lon2, lat2] = ahead({ ...e, lon, lat }, e.kind === 'aircraft' ? 10 : 60)
          return [
            [lon, lat, z],
            [lon2, lat2, z],
          ] as [number, number, number][]
        },
        getColor: (e) => {
          const c = entityColor(e)
          return [c[0], c[1], c[2], ui.selected === e.id ? 230 : 90]
        },
        getWidth: (e) => (ui.selected === e.id ? 3 : 1.5),
        widthUnits: 'pixels',
        getDashArray: [4, 4],
        extensions: [DASH],
        updateTriggers: { getPath: now, getColor: ui.selected, getWidth: ui.selected },
      }),
    )
  }

  // Pings: every entity that just updated emits an expanding, fading ring.
  const pings: { e: Entity; age: number }[] = []
  for (const [id, at] of live.touched) {
    const age = now - at
    if (age > PING_MS) continue
    const e = live.entities.get(id)
    if (e && L[e.kind === 'aircraft' ? 'aircraft' : e.kind === 'vessel' ? 'vessels' : e.kind === 'fire' ? 'fires' : 'news']) {
      pings.push({ e, age })
    }
  }
  layers.push(
    new ScatterplotLayer<{ e: Entity; age: number }>({
      id: 'pings',
      data: pings,
      getPosition: ({ e }) => pos(e),
      getRadius: ({ age }) => 4 + 22 * (age / PING_MS),
      radiusUnits: 'pixels',
      stroked: true,
      filled: false,
      getLineWidth: 1.5,
      lineWidthUnits: 'pixels',
      getLineColor: ({ e, age }) => {
        const c = entityColor(e)
        return [c[0], c[1], c[2], Math.round(220 * (1 - age / PING_MS))]
      },
      updateTriggers: { getRadius: now, getLineColor: now, getPosition: now },
      parameters: ON_TOP,
    }),
  )

  // Alert ripples
  const ripples = live.freshAlerts.filter((a) => now - a.at < ALERT_RIPPLE_MS && a.alert.lon != null)
  live.freshAlerts = ripples
  layers.push(
    new ScatterplotLayer<{ alert: { lon?: number; lat?: number; severity: string }; at: number }>({
      id: 'alert-ripples',
      data: ripples.flatMap((r) => [0, 1, 2].map((k) => ({ ...r, at: r.at + k * 500 }))),
      getPosition: (r) => [r.alert.lon!, r.alert.lat!],
      getRadius: (r) => 8 + 70 * (((now - r.at) % 3000) / 3000),
      radiusUnits: 'pixels',
      stroked: true,
      filled: false,
      getLineWidth: 2,
      lineWidthUnits: 'pixels',
      getLineColor: (r) => {
        const base: RGBA = r.alert.severity === 'high' ? [244, 63, 94, 255] : [251, 191, 36, 255]
        const fade = 1 - ((now - r.at) % 3000) / 3000
        return [base[0], base[1], base[2], now < r.at ? 0 : Math.round(230 * fade)]
      },
      updateTriggers: { getRadius: now, getLineColor: now },
      parameters: ON_TOP,
    }),
  )

  // Full observed history of the selected track, coloured by altitude, with its airfields.
  const history = trackState.get()
  if (L.trails && history.data && history.id === ui.selected) {
    const flights = history.data.flights.filter((f) => f.points.length > 1)
    const terminals: { t: Terminal; what: string }[] = flights.flatMap((f) => [
      ...(f.origin ? [{ t: f.origin, what: 'from' }] : []),
      ...(f.landing ? [{ t: f.landing, what: 'landed' }] : []),
    ])
    layers.push(
      new PathLayer<Flight>({
        id: 'history',
        data: flights,
        getPath: (f) => f.points.map((p) => [p[1], p[2], p[3] * altK] as [number, number, number]),
        getColor: (f) => f.points.map((p) => altitudeColor(p[3])),
        getWidth: 3,
        widthUnits: 'pixels',
        updateTriggers: { getPath: altK },
      }),
      new ScatterplotLayer<{ t: Terminal; what: string }>({
        id: 'history-terminals',
        data: terminals,
        getPosition: ({ t }) => [t.lon, t.lat],
        getRadius: 7,
        radiusUnits: 'pixels',
        stroked: true,
        lineWidthMinPixels: 2,
        getFillColor: [2, 6, 12, 220],
        getLineColor: ({ what }) => (what === 'landed' ? [74, 222, 128, 255] : [226, 232, 240, 255]),
        parameters: ON_TOP,
      }),
      new TextLayer<{ t: Terminal; what: string }>({
        id: 'history-terminal-labels',
        data: terminals,
        getPosition: ({ t }) => [t.lon, t.lat],
        getText: ({ t, what }) => `${what} ${t.icao ?? t.ident} · ${t.name}`,
        getSize: 12,
        sizeUnits: 'pixels',
        getColor: [226, 232, 240, 255],
        getPixelOffset: [0, 18],
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontWeight: 600,
        outlineWidth: 3,
        outlineColor: [2, 6, 12, 230],
        fontSettings: { sdf: true },
        parameters: { ...ON_TOP, cullMode: 'none' as const },
      }),
    )
  }

  // Predicted landing: every predicted aircraft gets a faint great-circle hint to its most likely
  // field; the selected one gets its top destinations with full paths (opacity = probability).
  if (L.predictions && L.aircraft) {
    const pred = predState.get()
    const selectedDests = pred.data && pred.id === ui.selected ? pred.data.destinations.filter((d) => d.path) : []
    const hints = aircraft.filter((e) => e.props.pred && e.id !== ui.selected)
    layers.push(
      new LineLayer<Entity>({
        id: 'pred-hints',
        data: hints,
        getSourcePosition: (e) => pos(e),
        getTargetPosition: (e) => [e.props.pred.dest_lon, e.props.pred.dest_lat, 0],
        getColor: (e) => [56, 189, 248, Math.round(25 + 90 * e.props.pred.p)],
        getWidth: 1,
        widthUnits: 'pixels',
        updateTriggers: { getSourcePosition: now },
      }),
      new PathLayer<Destination, PathStyleExtensionProps<Destination>>({
        id: 'pred-paths',
        data: selectedDests,
        getPath: (d) => d.path!.map((p) => [p[0], p[1], p[2] * altK] as [number, number, number]),
        getColor: (d) => {
          const c = DEST_COLORS[selectedDests.indexOf(d)] ?? DEST_COLORS[2]
          return [c[0], c[1], c[2], Math.round(70 + 185 * Math.min(1, d.p * 1.5))]
        },
        getWidth: (d) => 2 + 3 * d.p,
        widthUnits: 'pixels',
        getDashArray: [6, 4],
        extensions: [DASH],
        updateTriggers: { getPath: altK },
      }),
      new ScatterplotLayer<Destination>({
        id: 'pred-fields',
        data: selectedDests,
        getPosition: (d) => [d.lon, d.lat],
        getRadius: 9,
        radiusUnits: 'pixels',
        stroked: true,
        filled: false,
        lineWidthMinPixels: 2,
        getLineColor: (d) => {
          const c = DEST_COLORS[selectedDests.indexOf(d)] ?? DEST_COLORS[2]
          return [c[0], c[1], c[2], 255]
        },
        parameters: ON_TOP,
      }),
      new TextLayer<Destination>({
        id: 'pred-labels',
        data: selectedDests,
        getPosition: (d) => [d.lon, d.lat],
        getText: (d) => `${d.icao ?? d.ident} ${Math.round(d.p * 100)}% · ETA ${Math.round(d.eta_min)} min`,
        getSize: 12,
        sizeUnits: 'pixels',
        getColor: (d) => {
          const c = DEST_COLORS[selectedDests.indexOf(d)] ?? DEST_COLORS[2]
          return [c[0], c[1], c[2], 255]
        },
        getPixelOffset: [0, -18],
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontWeight: 600,
        outlineWidth: 3,
        outlineColor: [2, 6, 12, 230],
        fontSettings: { sdf: true },
        parameters: { ...ON_TOP, cullMode: 'none' as const },
      }),
    )
  }

  // Selection halo
  const sel = ui.selected ? live.entities.get(ui.selected) : undefined
  if (sel) {
    layers.push(
      new ScatterplotLayer<Entity>({
        id: 'selection',
        data: [sel],
        getPosition: pos,
        getRadius: 16 + 3 * Math.sin(now / 200),
        radiusUnits: 'pixels',
        stroked: true,
        filled: false,
        getLineWidth: 2.5,
        lineWidthUnits: 'pixels',
        getLineColor: COLORS.selected,
        updateTriggers: { getRadius: now, getPosition: now },
        parameters: ON_TOP,
      }),
    )
  }

  if (zoom >= 6.5) {
    const strategic = facilities.filter((e) => e.props.type !== 'port' || zoom >= 10)
    const labelled = [...aircraft.filter((e) => e.props.military || e.props.uav || zoom >= 8), ...vessels, ...(zoom >= 9 ? strategic : [])]
    layers.push(
      new TextLayer<Entity>({
        id: 'labels',
        data: labelled,
        getPosition: pos,
        getText: (e) => e.label,
        getSize: 12,
        sizeUnits: 'pixels',
        sizeMaxPixels: 14,
        getColor: (e) => entityColor(e),
        getPixelOffset: [0, -16],
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontWeight: 600,
        outlineWidth: 3,
        outlineColor: [2, 6, 12, 230],
        fontSettings: { sdf: true },
        updateTriggers: { getPosition: now },
        parameters: { ...ON_TOP, cullMode: 'none' as const },
      }),
    )
  }

  return layers
}
