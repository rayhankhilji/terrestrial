import { X } from 'lucide-react'
import { live, useLive } from '../lib/live'
import { flagEmoji, stateCounts } from '../lib/picture'
import { type LayerKey, type Mode, toggleLayer, toggleState, ui, useStore } from '../lib/store'
import { COLORS, type RGBA } from '../map/liveLayers'

interface Item {
  key: LayerKey
  label: string
  color: RGBA
  note?: string
  modes: Mode[]
  est?: boolean
}

const GROUPS: { title: string; items: Item[] }[] = [
  {
    title: 'Threat',
    items: [
      { key: 'airthreats', label: 'Air threats in flight', color: [255, 178, 36, 255], note: 'Air Force reports', modes: ['military'] },
      { key: 'danger', label: 'Danger forecast', color: [249, 115, 22, 255], note: 'next 6 h', modes: ['military'], est: true },
      { key: 'front', label: 'Front line & occupied', color: [245, 197, 24, 255], note: 'DeepState', modes: ['military', 'maritime'] },
      { key: 'units', label: 'Russian units & airfields', color: [251, 113, 133, 255], note: 'DeepState estimate', modes: ['military'], est: true },
      { key: 'gnss', label: 'GPS jamming', color: [255, 77, 94, 255], note: 'ADS-B accuracy', modes: ['military', 'maritime'], est: true },
    ],
  },
  {
    title: 'Craft',
    items: [
      { key: 'aircraft', label: 'Military aircraft', color: COLORS.military, note: 'ADS-B', modes: ['military', 'maritime'] },
      { key: 'vessels', label: 'Naval vessels', color: COLORS.naval_ship, note: 'AIS', modes: ['military'] },
      { key: 'vessels', label: 'Vessels', color: COLORS.vessel, note: 'AIS', modes: ['maritime'] },
      { key: 'nets', label: 'Mission nets', color: [45, 212, 191, 255], modes: ['military'], est: true },
      { key: 'trails', label: 'Track history', color: [148, 163, 184, 255], modes: ['military', 'maritime'] },
      { key: 'predictions', label: 'Predicted landing', color: [166, 139, 250, 255], modes: ['military'], est: true },
      { key: 'forecast', label: 'Projected course', color: [148, 163, 184, 255], note: '10 min', modes: ['military', 'maritime'], est: true },
    ],
  },
  {
    title: 'Ground & space',
    items: [
      { key: 'airfields', label: 'Military airfields', color: COLORS.airfield, modes: ['military', 'maritime'] },
      { key: 'smallFields', label: 'All airfields', color: [100, 116, 139, 255], modes: ['military'] },
      { key: 'facilities', label: 'Bases & facilities', color: COLORS.naval, note: 'Wikidata', modes: ['military', 'maritime'] },
      { key: 'satellites', label: 'Imaging satellites', color: [63, 213, 242, 255], note: 'CelesTrak', modes: ['military', 'maritime'] },
      { key: 'buildings', label: '3D buildings & strike glow', color: [226, 232, 240, 255], note: 'zoom in', modes: ['military', 'maritime'] },
      { key: 'fires', label: 'Thermal anomalies', color: COLORS.fire, note: 'NASA FIRMS', modes: ['military', 'maritime'] },
      { key: 'news', label: 'News events', color: COLORS.news, modes: ['military', 'maritime'] },
      { key: 'stations', label: 'Port conditions', color: COLORS.station, modes: ['maritime'] },
      { key: 'relations', label: 'Inferred links', color: [255, 255, 255, 255], modes: ['military', 'maritime'], est: true },
    ],
  },
]

const LEGEND: { label: string; color: RGBA }[] = [
  { label: 'ISR / AEW / patrol', color: COLORS.isr },
  { label: 'tanker', color: COLORS.tanker },
  { label: 'airlift / VIP', color: COLORS.airlift },
  { label: 'combat', color: COLORS.combat },
  { label: 'rotary', color: COLORS.rotary },
  { label: 'unmanned', color: COLORS.uav },
]

const swatch = (c: RGBA) => ({ background: `rgb(${c[0]},${c[1]},${c[2]})` })

/** Layers and state filters, as a popover opened from the command bar. */
export function LayerControl() {
  useLive(1000)
  const layers = useStore(ui, (s) => s.layers)
  const mode = useStore(ui, (s) => s.mode)
  const states = useStore(ui, (s) => s.states)
  const open = useStore(ui, (s) => s.layersOpen)
  if (!open) return null
  const counts = stateCounts(live.entities.values(), mode)
  return (
    <div className="popover glass" style={{ left: 'auto', right: 'var(--gap)', top: 'calc(var(--gap) * 2 + var(--bar-h))', bottom: 'auto' }} role="dialog" aria-label="Layers">
      <div className="panel-head" style={{ paddingBottom: 10 }}>
        <div className="grow">
          <h2>Layers</h2>
          <div className="sub">
            Dashed tags mark <span className="tag est">model estimates</span>
          </div>
        </div>
        <button className="iconbtn" onClick={() => ui.set({ layersOpen: false })} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      {GROUPS.map((g) => {
        const items = g.items.filter((i) => i.modes.includes(mode))
        if (!items.length) return null
        return (
          <div key={g.title} className="layer-group">
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              {g.title}
            </div>
            {items.map((i) => (
              <label key={`${i.key}-${i.label}`} className="layer-row">
                <input className="sr-only" type="checkbox" checked={layers[i.key]} onChange={() => toggleLayer(i.key)} />
                <span className={`toggle ${layers[i.key] ? 'on' : ''}`} aria-hidden />
                <span className="swatch" style={swatch(i.color)} />
                <span style={{ color: layers[i.key] ? 'var(--text)' : 'var(--text-3)' }}>{i.label}</span>
                {i.note && <span className="note">{i.note}</span>}
                {i.est && <span className="tag est" style={{ marginLeft: i.note ? 4 : 'auto' }}>est</span>}
              </label>
            ))}
          </div>
        )
      })}
      <div className="layer-group">
        <div className="eyebrow" style={{ marginBottom: 8 }}>
          Aircraft role
        </div>
        <div className="legend">
          {LEGEND.map((l) => (
            <span key={l.label}>
              <span className="swatch" style={swatch(l.color)} /> {l.label}
            </span>
          ))}
        </div>
      </div>
      <div className="layer-group">
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="eyebrow grow">Operator</span>
          {states.length > 0 && (
            <button className="link xs" onClick={() => ui.set({ states: [] })}>
              show all
            </button>
          )}
        </div>
        <div className="chips">
          {counts.length === 0 && <span className="faint small">No attributed craft yet.</span>}
          {counts.map((c) => (
            <button key={c.code} className={`chip ${states.includes(c.code) ? 'on' : ''}`} onClick={() => toggleState(c.code)} title={c.name}>
              <span aria-hidden>{flagEmoji(c.code)}</span> {c.name} <span className="count">{c.n}</span>
            </button>
          ))}
        </div>
        <p className="xs faint" style={{ marginTop: 10 }}>
          Only aircraft that broadcast ADS-B are visible: mostly NATO and partner ISR, tankers and airlift. Russian military aviation rarely transmits; submarines are
          never shown as positions.
        </p>
      </div>
    </div>
  )
}
