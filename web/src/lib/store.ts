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
export type LayerKey =
  | 'aircraft'
  | 'vessels'
  | 'fires'
  | 'news'
  | 'facilities'
  | 'relations'
  | 'stations'
  | 'trails'
  | 'forecast'
  | 'aois'
  | 'gaps'
  | 'sar'
  | 'encounters'
  | 'loitering'
  | 'portVisits'
export type LeftTab = 'alerts' | 'live' | 'vessels' | 'highlights'

export interface FlyTo {
  lon: number
  lat: number
  zoom?: number
  pitch?: number
  bearing?: number
  nonce: number
}

export interface UIState {
  basemap: Basemap
  globe: boolean
  terrain: boolean
  layers: Record<LayerKey, boolean>
  selected: string | null
  hovered: string | null
  leftTab: LeftTab
  sentinelsOpen: boolean
  follow: boolean
  flyTo: FlyTo | null
  search: string
}

export const ui = createStore<UIState>({
  basemap: 'satellite',
  globe: true,
  terrain: true,
  layers: {
    aircraft: true,
    vessels: true,
    fires: true,
    news: true,
    facilities: true,
    relations: true,
    stations: true,
    trails: true,
    forecast: true,
    aois: true,
    gaps: true,
    sar: true,
    encounters: true,
    loitering: true,
    portVisits: true,
  },
  selected: null,
  hovered: null,
  leftTab: 'alerts',
  sentinelsOpen: false,
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
