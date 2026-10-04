import { Orbit, Sun } from 'lucide-react'
import { useEffect } from 'react'
import { loadPasses, passes } from '../lib/feeds'
import { live, useLive } from '../lib/live'
import { ui, useStore } from '../lib/store'
import { hhmm, PanelHead } from './ui'

const PLACES = [
  { label: 'Sevastopol', lon: 33.52, lat: 44.61 },
  { label: 'Novorossiysk', lon: 37.79, lat: 44.72 },
  { label: 'Kerch bridge', lon: 36.51, lat: 45.3 },
  { label: 'Engels airbase', lon: 46.21, lat: 51.48 },
  { label: 'Kyiv', lon: 30.52, lat: 50.45 },
  { label: 'Kharkiv', lon: 36.25, lat: 49.99 },
]

function inMin(ts: number) {
  const m = Math.round((ts - Date.now()) / 60000)
  if (m <= 0) return 'now'
  return m < 60 ? `in ${m} min` : `in ${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')}`
}

/** When can a radar or optical satellite next image a place? (opportunities, not acquisitions) */
export function SpacePanel() {
  useLive(2000)
  const selected = useStore(ui, (s) => s.selected)
  const p = useStore(passes, (s) => s)
  const sel = selected ? live.entities.get(selected) : undefined
  const sats = [...live.entities.values()].filter((e) => e.kind === 'satellite')
  const sar = sats.filter((s) => s.props.sensor === 'SAR').length

  useEffect(() => {
    if (passes.get().key == null) void loadPasses(PLACES[0].lon, PLACES[0].lat, PLACES[0].label)
  }, [])

  return (
    <>
      <PanelHead icon={Orbit} title="Space" sub={`${sats.length} imaging satellites tracked · ${sar} radar (see through cloud and night)`} />
      <div className="panel-body">
        <div className="section">
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            Next looks over
          </div>
          <div className="chips">
            {sel && (
              <button className={`chip ${p.label === sel.label ? 'on' : ''}`} onClick={() => void loadPasses(sel.lon, sel.lat, sel.label)}>
                ◎ {sel.label.length > 24 ? `${sel.label.slice(0, 24)}…` : sel.label}
              </button>
            )}
            {PLACES.map((pl) => (
              <button key={pl.label} className={`chip ${p.label === pl.label ? 'on' : ''}`} onClick={() => void loadPasses(pl.lon, pl.lat, pl.label)}>
                {pl.label}
              </button>
            ))}
          </div>
        </div>
        {p.loading && <div className="empty"><p>Propagating orbits…</p></div>}
        {p.error && <div className="section"><div className="callout warn">{p.error}</div></div>}
        {!p.loading && !p.error && p.rows.length === 0 && p.key && <div className="empty"><p>No passes in the next 24 hours.</p></div>}
        {!p.loading && p.rows.slice(0, 40).map((x) => (
          <div className="pass" key={`${x.norad}-${x.start}`}>
            <time>{hhmm(x.start)}</time>
            <div className="ellipsis">
              <div style={{ fontWeight: 560 }}>{x.satellite}</div>
              <div className="xs faint">
                {x.operator} · {inMin(x.start)} · closest {x.closest_km} km
              </div>
            </div>
            <div className="row" style={{ gap: 4 }}>
              <span className={`tag ${x.sensor === 'SAR' ? 'obs' : 'gold'}`}>{x.sensor}</span>
              {x.tasked && <span className="tag est" title="Commercial: images only where a customer tasks it">tasked</span>}
              {x.sensor === 'optical' && <span className={`tag ${x.daylight ? 'green' : ''}`} title={x.daylight ? 'In daylight' : 'Night: optical imaging unlikely'}>
                <Sun size={10} /> {x.daylight ? 'day' : 'night'}
              </span>}
            </div>
          </div>
        ))}
      </div>
      <div className="panel-foot">
        A pass is an opportunity, not an acquisition. Sentinel-1/2 follow public plans; ICEYE, Capella and Umbra image only where tasked. Orbits: CelesTrak, SGP4.
      </div>
    </>
  )
}
