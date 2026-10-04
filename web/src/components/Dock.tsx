import { Crosshair, Globe2, Map as MapIcon, ShieldAlert, View } from 'lucide-react'
import type { ReactNode } from 'react'
import { live, useLive } from '../lib/live'
import { type CameraView, select, ui, useStore } from '../lib/store'
import { css, weapon } from '../lib/weapons'
import { hhmm } from './ui'

const CAMERAS: { key: CameraView; label: string; hint: string; icon: typeof Globe2 }[] = [
  { key: 'globe', label: 'Globe', hint: 'The whole theatre on the globe', icon: Globe2 },
  { key: 'theatre', label: 'Theatre', hint: 'Ukraine and the Black Sea, tilted', icon: MapIcon },
  { key: 'battlefield', label: 'Battlefield', hint: 'Low, horizontal view over 3D terrain and buildings at the selection (or the map centre)', icon: View },
  { key: 'chase', label: 'Chase', hint: 'Follow the selected aircraft at its altitude and heading', icon: Crosshair },
]

/** Bottom dock: a ticker of the latest threats and alerts, and the camera presets. */
export function Dock() {
  useLive(2000)
  const camera = useStore(ui, (s) => s.camera)
  const selected = useStore(ui, (s) => s.selected)
  const threats = [...live.entities.values()].filter((e) => e.kind === 'airthreat' && !e.props.tally).sort((a, b) => b.ts - a.ts).slice(0, 12)
  const alerts = live.alerts.filter((a) => a.severity !== 'info').slice(0, 6)
  const items: ReactNode[] = [
    ...threats.map((e) => {
      const w = weapon(e.props.weapon)
      const Icon = w.icon
      return (
        <button key={e.id} className="tick" onClick={() => select(e.id, { lon: e.lon, lat: e.lat, zoom: 7.5 })}>
          <span className="w" style={{ background: css(w.color, 0.15), color: css(w.color) }}>
            <Icon size={12} />
          </span>
          <time>{hhmm(e.ts)}</time>
          {e.label}
        </button>
      )
    }),
    ...alerts.map((a) => (
      <button key={a.id} className="tick" onClick={() => a.lon != null && a.lat != null && select(a.entities[0] ?? null, { lon: a.lon, lat: a.lat })}>
        <span className="w" style={{ background: 'var(--red-wash)', color: 'var(--red)' }}>
          <ShieldAlert size={12} />
        </span>
        <time>{hhmm(a.ts)}</time>
        {a.title}
      </button>
    )),
  ]
  const chaseOk = !!selected && selected.startsWith('aircraft:')
  return (
    <div className="dock glass">
      <div className="ticker" aria-label="Latest threats and alerts">
        {items.length === 0 ? (
          <div className="row faint small" style={{ height: '100%', paddingLeft: 12 }}>
            Quiet: no air-threat reports in the last 45 minutes.
          </div>
        ) : (
          <div className="ticker-track" style={{ animationDuration: `${Math.max(40, items.length * 9)}s` }}>
            {items}
            {items.map((it, i) => (
              <span key={`dup-${i}`} aria-hidden style={{ display: 'contents' }}>
                {it}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="divider-v" style={{ margin: '10px 0' }} />
      <div className="seg cam" role="group" aria-label="Camera">
        {CAMERAS.map((c) => {
          const Icon = c.icon
          const disabled = c.key === 'chase' && !chaseOk
          return (
            <button
              key={c.key}
              className={camera === c.key ? 'on' : ''}
              title={disabled ? 'Select an aircraft to chase it' : c.hint}
              disabled={disabled}
              style={disabled ? { opacity: 0.4, cursor: 'not-allowed' } : undefined}
              onClick={() => ui.set({ camera: c.key, cameraNonce: Date.now() })}
            >
              <span className="row" style={{ gap: 6 }}>
                <Icon size={14} /> {c.label}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
