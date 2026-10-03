import { COLORS, type RGBA } from '../map/liveLayers'
import { type LayerKey, toggleLayer, ui, useStore } from '../lib/store'

const ITEMS: { key: LayerKey; label: string; color: RGBA; note?: string }[] = [
  { key: 'aircraft', label: 'Aircraft (ADS-B)', color: COLORS.civil },
  { key: 'vessels', label: 'Vessels (AIS)', color: COLORS.vessel },
  { key: 'fires', label: 'Thermal anomalies', color: COLORS.fire },
  { key: 'news', label: 'News events', color: COLORS.news },
  { key: 'facilities', label: 'Facilities (Wikidata)', color: COLORS.port },
  { key: 'stations', label: 'Port conditions', color: COLORS.station },
  { key: 'relations', label: 'Inferred links', color: [255, 255, 255, 255] },
  { key: 'trails', label: 'Track history', color: [148, 163, 184, 255] },
  { key: 'forecast', label: 'Projected course', color: [148, 163, 184, 255], note: 'model estimate' },
]

const swatch = (c: RGBA) => ({ background: `rgb(${c[0]},${c[1]},${c[2]})` })

export function LayerControl() {
  const layers = useStore(ui, (s) => s.layers)
  return (
    <div className="layer-control">
      <div className="lc-title">Layers</div>
      {ITEMS.map((i) => (
        <label key={i.key} className={layers[i.key] ? '' : 'off'}>
          <input type="checkbox" checked={layers[i.key]} onChange={() => toggleLayer(i.key)} />
          <span className="swatch" style={swatch(i.color)} />
          {i.label}
          {i.note && <span className="muted small"> · {i.note}</span>}
        </label>
      ))}
      <div className="legend">
        <span>
          <span className="swatch" style={swatch(COLORS.military)} /> military
        </span>
        <span>
          <span className="swatch" style={swatch(COLORS.uav)} /> unmanned
        </span>
        <span>
          <span className="swatch" style={swatch(COLORS.listed)} /> listed
        </span>
      </div>
      <div className="muted small">Aircraft altitude exaggerated when zoomed out.</div>
    </div>
  )
}
