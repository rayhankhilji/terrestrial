import { useSyncExternalStore } from 'react'

/**
 * The one state approach in the app: a tiny external store read with
 * useSyncExternalStore. Selectors must return primitives or stable references.
 */
export interface Store<T> {
  get: () => T
  set: (patch: Partial<T> | ((s: T) => Partial<T>)) => void
  subscribe: (listener: () => void) => () => void
}

export function createStore<T extends object>(initial: T): Store<T> {
  let state = initial
  const listeners = new Set<() => void>()
  return {
    get: () => state,
    set(patch) {
      const next = typeof patch === 'function' ? patch(state) : patch
      state = { ...state, ...next }
      listeners.forEach((l) => l())
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}

export function useStore<T extends object, S>(store: Store<T>, selector: (s: T) => S): S {
  return useSyncExternalStore(store.subscribe, () => selector(store.get()))
}

export type Basemap = 'dark' | 'satellite'
/** Military: the default defence picture (§16). Maritime: the dark-vessel sub-sector. */
export type Mode = 'military' | 'maritime'
export type LayerKey =
  | 'aircraft'
  | 'airfields'
  | 'smallFields'
  | 'nets'
  | 'danger'
  | 'vessels'
  | 'fires'
  | 'news'
  | 'facilities'
  | 'relations'
  | 'stations'
  | 'trails'
  | 'forecast'
  | 'predictions'
  | 'gnss'
  | 'front'
  | 'units'
  | 'airthreats'
  | 'satellites'
  | 'buildings'
  | 'aois'
  | 'gaps'
  | 'sar'
  | 'encounters'
  | 'loitering'
  | 'portVisits'
export type LeftTab = 'brief' | 'airthreats' | 'priority' | 'nets' | 'danger' | 'space' | 'feed' | 'sources' | 'vessels'
/** Camera presets: whole theatre on the globe, Ukraine tilted, a low battlefield view, or chasing the selection. */
export type CameraView = 'globe' | 'theatre' | 'battlefield' | 'chase'

export interface FlyTo {
  lon: number
  lat: number
  zoom?: number
  pitch?: number
  bearing?: number
  nonce: number
}

export interface UIState {
  mode: Mode
  /** ISO alpha-2 codes of the states to show; empty = all */
  states: string[]
  basemap: Basemap
  globe: boolean
  terrain: boolean
  layers: Record<LayerKey, boolean>
  selected: string | null
  hovered: string | null
  leftTab: LeftTab
  panelOpen: boolean
  paletteOpen: boolean
  camera: CameraView
  /** bumped on every preset click, so re-clicking a preset re-frames */
  cameraNonce: number
  sentinelsOpen: boolean
  modelCardOpen: false | 'strike' | 'flight'
  layersOpen: boolean
  follow: boolean
  flyTo: FlyTo | null
  search: string
}

export const ui = createStore<UIState>({
  mode: 'military',
  states: [],
  basemap: 'satellite',
  globe: true,
  terrain: true,
  layers: {
    aircraft: true,
    airfields: true,
    smallFields: false,
    nets: true,
    danger: true,
    vessels: true,
    fires: true,
    news: true,
    facilities: true,
    relations: true,
    stations: true,
    trails: true,
    forecast: true,
    predictions: true,
    gnss: true,
    front: true,
    units: false,
    airthreats: true,
    satellites: true,
    buildings: true,
    aois: true,
    gaps: true,
    sar: true,
    encounters: true,
    loitering: true,
    portVisits: true,
  },
  selected: null,
  hovered: null,
  leftTab: 'brief',
  panelOpen: true,
  paletteOpen: false,
  camera: 'theatre',
  cameraNonce: 0,
  sentinelsOpen: false,
  modelCardOpen: false,
  layersOpen: false,
  follow: false,
  flyTo: null,
  search: '',
})

export function flyTo(target: Omit<FlyTo, 'nonce'>) {
  ui.set({ flyTo: { ...target, nonce: performance.now() } })
}

export function select(id: string | null, fly?: { lon: number; lat: number; zoom?: number }) {
  ui.set({ selected: id })
  if (fly) flyTo({ zoom: 8, pitch: 55, ...fly })
}

export function toggleLayer(key: LayerKey) {
  ui.set((s) => ({ layers: { ...s.layers, [key]: !s.layers[key] } }))
}

export function toggleState(code: string) {
  ui.set((s) => ({ states: s.states.includes(code) ? s.states.filter((c) => c !== code) : [...s.states, code] }))
}
