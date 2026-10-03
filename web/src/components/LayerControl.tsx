import { live, useLive } from '../lib/live'
import { flagEmoji, stateCounts } from '../lib/picture'
import { type LayerKey, type Mode, toggleLayer, toggleState, ui, useStore } from '../lib/store'
import { COLORS, type RGBA } from '../map/liveLayers'

const ITEMS: { key: LayerKey; label: string; color: RGBA; note?: string; modes: Mode[] }[] = [
  { key: 'aircraft', label: 'Military aircraft (ADS-B)', color: COLORS.military, modes: ['military', 'maritime'] },
  { key: 'vessels', label: 'Naval vessels (AIS)', color: COLORS.naval_ship, modes: ['military'] },
  { key: 'vessels', label: 'Vessels (AIS)', color: COLORS.vessel, modes: ['maritime'] },
  { key: 'danger', label: 'Danger zones (alert forecast)', color: [249, 115, 22, 255], note: 'model estimate', modes: ['military'] },
  { key: 'nets', label: 'Nets (inferred shared missions)', color: [45, 212, 191, 255], modes: ['military'] },
  { key: 'airfields', label: 'Military airfields', color: COLORS.airfield, modes: ['military', 'maritime'] },
  { key: 'smallFields', label: 'Private & small airfields', color: [100, 116, 139, 255], modes: ['military'] },
  { key: 'facilities', label: 'Air & naval bases (Wikidata)', color: COLORS.naval, modes: ['military'] },
  { key: 'facilities', label: 'Facilities (Wikidata)', color: COLORS.port, modes: ['maritime'] },
  { key: 'fires', label: 'Thermal anomalies', color: COLORS.fire, modes: ['military', 'maritime'] },
  { key: 'news', label: 'News events', color: COLORS.news, modes: ['military', 'maritime'] },
  { key: 'stations', label: 'Port conditions', color: COLORS.station, modes: ['maritime'] },
  { key: 'relations', label: 'Inferred links', color: [255, 255, 255, 255], modes: ['military', 'maritime'] },
  { key: 'trails', label: 'Track history', color: [148, 163, 184, 255], modes: ['military', 'maritime'] },
  { key: 'front', label: 'Front line & occupied territory', color: [255, 214, 10, 255], note: 'DeepStateMap', modes: ['military', 'maritime'] },
  { key: 'units', label: 'Russian units & airfields', color: [251, 113, 133, 255], note: 'DeepState estimate', modes: ['military'] },
  { key: 'gnss', label: 'GNSS interference', color: [244, 63, 94, 255], note: 'from ADS-B accuracy', modes: ['military', 'maritime'] },
  { key: 'predictions', label: 'Predicted landing', color: [56, 189, 248, 255], note: 'model estimate', modes: ['military'] },
  { key: 'forecast', label: 'Projected course', color: [148, 163, 184, 255], note: 'model estimate', modes: ['military', 'maritime'] },
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

export function LayerControl() {
  useLive(1000)
  const layers = useStore(ui, (s) => s.layers)
  const mode = useStore(ui, (s) => s.mode)
  const states = useStore(ui, (s) => s.states)
  const open = useStore(ui, (s) => s.layersOpen)
  const counts = stateCounts(live.entities.values(), mode)
  return (
    <details className="layer-control" open={open} onToggle={(ev) => ui.set({ layersOpen: (ev.target as HTMLDetailsElement).open })}>
      <summary className="lc-title">Layers &amp; filters</summary>
      {ITEMS.filter((i) => i.modes.includes(mode)).map((i) => (
        <label key={`${i.key}-${i.label}`} className={layers[i.key] ? '' : 'off'}>
          <input type="checkbox" checked={layers[i.key]} onChange={() => toggleLayer(i.key)} />
          <span className="swatch" style={swatch(i.color)} />
          {i.label}
          {i.note && <span className="muted small"> · {i.note}</span>}
        </label>
      ))}
      <div className="legend">
        {LEGEND.map((l) => (
          <span key={l.label}>
            <span className="swatch" style={swatch(l.color)} /> {l.label}
          </span>
        ))}
      </div>

      <div className="lc-title">
        State / organisation
        {states.length > 0 && (
          <button className="link" onClick={() => ui.set({ states: [] })}>
            show all
          </button>
        )}
      </div>
      <div className="chips">
        {counts.length === 0 && <span className="muted small">No attributed tracks yet.</span>}
        {counts.map((c) => (
          <button key={c.code} className={`chip ${states.includes(c.code) ? 'on' : ''}`} onClick={() => toggleState(c.code)} title={c.name}>
            <span aria-hidden>{flagEmoji(c.code)}</span> {c.name} <span className="mono">{c.n}</span>
          </button>
        ))}
      </div>
      <div className="muted small">
        Only aircraft that broadcast ADS-B are visible: mostly NATO and partner ISR, tankers and airlift. Russian military aircraft rarely transmit; submarines are never shown as positions.
      </div>
    </details>
  )
}
