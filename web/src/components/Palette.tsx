import { Command, Crosshair, Layers, MapPin, Search } from 'lucide-react'
import { liveFetch } from '../lib/recording'
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'
import { describe, kindLabel } from '../lib/format'
import { live } from '../lib/live'
import { type CameraView, flyTo, type LayerKey, type LeftTab, select, toggleLayer, ui, useStore } from '../lib/store'

interface Result {
  id: string
  group: 'Craft & reports' | 'Places' | 'Commands'
  title: string
  sub: string
  run: () => void
}

interface Place {
  name: string
  lon: number
  lat: number
  country: string
  population: number
  feature: string
}

const COMMANDS: { title: string; sub: string; run: () => void }[] = [
  ...(['brief', 'airthreats', 'priority', 'nets', 'danger', 'space', 'feed', 'sources'] as LeftTab[]).map((t) => ({
    title: `Open ${t === 'airthreats' ? 'air threats' : t}`,
    sub: 'panel',
    run: () => ui.set({ leftTab: t, panelOpen: true }),
  })),
  ...(['globe', 'theatre', 'battlefield', 'chase'] as CameraView[]).map((c) => ({ title: `Camera: ${c}`, sub: 'view', run: () => ui.set({ camera: c, cameraNonce: Date.now() }) })),
  ...(['danger', 'front', 'units', 'gnss', 'airthreats', 'satellites', 'predictions', 'nets', 'buildings', 'merchant'] as LayerKey[]).map((k) => ({
    title: `Toggle layer: ${k}`,
    sub: 'layer',
    run: () => toggleLayer(k),
  })),
  { title: 'Open dark vessels', sub: 'panel', run: () => ui.set({ leftTab: 'vessels', panelOpen: true }) },
  { title: 'Satellite imagery / dark map', sub: 'view', run: () => ui.set((s) => ({ basemap: s.basemap === 'satellite' ? 'dark' : 'satellite' })) },
  { title: 'Sentinels: alert rules', sub: 'tool', run: () => ui.set({ sentinelsOpen: true }) },
]

/** ⌘K: find any craft, report, place or command. */
export function Palette() {
  const open = useStore(ui, (s) => s.paletteOpen)
  const [q, setQ] = useState('')
  const [places, setPlaces] = useState<Place[]>([])
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const onKey = (ev: globalThis.KeyboardEvent) => {
      if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'k') {
        ev.preventDefault()
        ui.set((s) => ({ paletteOpen: !s.paletteOpen }))
      } else if (ev.key === '/' && !(ev.target instanceof HTMLInputElement) && !(ev.target instanceof HTMLTextAreaElement)) {
        ev.preventDefault()
        ui.set({ paletteOpen: true })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) {
      setQ('')
      setActive(0)
      setTimeout(() => input.current?.focus(), 10)
    }
  }, [open])

  useEffect(() => {
    if (q.trim().length < 2) {
      setPlaces([])
      return
    }
    const ctl = new AbortController()
    const t = setTimeout(() => {
      liveFetch(`/live/geocode?q=${encodeURIComponent(q.trim())}&limit=6`, { signal: ctl.signal })
        .then((r) => (r.ok ? r.json() : []))
        .then(setPlaces)
        .catch(() => undefined)
    }, 140)
    return () => {
      clearTimeout(t)
      ctl.abort()
    }
  }, [q])

  const results = useMemo<Result[]>(() => {
    const term = q.trim().toLowerCase()
    const close = () => ui.set({ paletteOpen: false })
    const craft: Result[] = term
      ? [...live.entities.values()]
          .filter((e) => !['region', 'facility', 'gnss', 'front', 'sitrep'].includes(e.kind))
          .filter((e) => e.label.toLowerCase().includes(term) || [e.props.callsign, e.props.registration, e.props.type, e.props.icao24, e.props.place].some((v) => typeof v === 'string' && v.toLowerCase().includes(term)))
          .slice(0, 8)
          .map((e) => ({
            id: e.id,
            group: 'Craft & reports',
            title: e.label,
            sub: `${kindLabel(e)} · ${describe(e)}`,
            run: () => {
              select(e.id, { lon: e.lon, lat: e.lat, zoom: 8 })
              close()
            },
          }))
      : []
    const regions: Result[] = term
      ? [...live.entities.values()]
          .filter((e) => e.kind === 'region' && e.label.toLowerCase().includes(term))
          .slice(0, 3)
          .map((e) => ({ id: e.id, group: 'Places', title: `${e.label} region`, sub: describe(e), run: () => (select(e.id, { lon: e.lon, lat: e.lat, zoom: 6.5 }), close()) }))
      : []
    const towns: Result[] = places.map((p) => ({
      id: `place:${p.name}:${p.lon}`,
      group: 'Places',
      title: p.name,
      sub: `${p.country} · ${p.feature === 'AIRB' ? 'airfield' : p.population ? `${p.population.toLocaleString()} people` : 'place'}`,
      run: () => {
        flyTo({ lon: p.lon, lat: p.lat, zoom: 11, pitch: 60 })
        close()
      },
    }))
    const commands: Result[] = COMMANDS.filter((c) => !term || c.title.toLowerCase().includes(term))
      .slice(0, term ? 6 : 10)
      .map((c) => ({ id: `cmd:${c.title}`, group: 'Commands', title: c.title, sub: c.sub, run: () => (c.run(), close()) }))
    return [...craft, ...regions, ...towns, ...commands]
  }, [q, places])

  if (!open) return null
  const onKey = (ev: KeyboardEvent<HTMLInputElement>) => {
    if (ev.key === 'Escape') ui.set({ paletteOpen: false })
    else if (ev.key === 'ArrowDown') (ev.preventDefault(), setActive((a) => Math.min(a + 1, results.length - 1)))
    else if (ev.key === 'ArrowUp') (ev.preventDefault(), setActive((a) => Math.max(a - 1, 0)))
    else if (ev.key === 'Enter') results[active]?.run()
  }
  let lastGroup = ''
  return (
    <div className="palette-shell" onClick={() => ui.set({ paletteOpen: false })}>
      <div className="palette-box glass" onClick={(ev) => ev.stopPropagation()} role="dialog" aria-label="Search">
        <div className="palette-input">
          <Search size={17} className="faint" />
          <input
            ref={input}
            autoFocus
            value={q}
            placeholder="Callsign, registration, town, region, layer, command…"
            onChange={(ev) => (setQ(ev.target.value), setActive(0))}
            onKeyDown={onKey}
            aria-label="Search"
          />
          <span className="kbd">esc</span>
        </div>
        <div className="palette-results">
          {results.length === 0 && <div className="empty"><p>No matches.</p></div>}
          {results.map((r, i) => {
            const header = r.group !== lastGroup ? r.group : null
            lastGroup = r.group
            const Icon = r.group === 'Places' ? MapPin : r.group === 'Commands' ? (r.sub === 'layer' ? Layers : Command) : Crosshair
            return (
              <div key={r.id}>
                {header && <div className="palette-group eyebrow">{header}</div>}
                <button className={`palette-item ${i === active ? 'active' : ''}`} onMouseEnter={() => setActive(i)} onClick={r.run}>
                  <Icon size={15} className="faint" />
                  <span className="grow ellipsis">{r.title}</span>
                  <span className="xs faint ellipsis" style={{ maxWidth: 260 }}>
                    {r.sub}
                  </span>
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
