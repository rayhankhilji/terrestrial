import { useEffect, useState } from 'react'
import { live, p50, useLive } from '../lib/live'
import { ms, utc } from '../lib/format'
import { ui, useStore } from '../lib/store'

function Clock() {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(t)
  }, [])
  return <span className="clock mono">{utc(now)}</span>
}

export function TopBar() {
  useLive(500)
  const basemap = useStore(ui, (s) => s.basemap)
  const globe = useStore(ui, (s) => s.globe)
  const terrain = useStore(ui, (s) => s.terrain)
  const tracks = [...live.entities.values()].filter((e) => e.kind === 'aircraft' || e.kind === 'vessel').length
  const mode = live.mode

  return (
    <header className="topbar">
      <div className="brand">
        <img src="/favicon.svg" alt="" width={30} height={30} />
        <div>
          <div className="brand-name">TERRESTRIAL</div>
          <div className="brand-sub">Black Sea maritime &amp; air picture</div>
        </div>
      </div>

      <div className="status-strip">
        <span className={`mode mode-${mode}`} title={mode === 'replay' ? 'Replaying a recorded live session' : undefined}>
          <span className="dot" />
          {mode.toUpperCase()}
        </span>
        <Clock />
        <Metric label="hub→screen" value={ms(p50(live.hubLatency))} hint="median time from the hub receiving an update to this browser drawing it" />
        <Metric label="sensor→screen" value={ms(p50(live.e2eLatency))} hint="median age of track positions when they reach this browser" />
        <Metric label="msg/s" value={String(live.rate())} />
        <Metric label="tracks" value={String(tracks)} />
      </div>

      <div className="sources">
        {live.sources.map((s) => (
          <span key={s.name} className={`pill pill-${s.state}`} title={`${s.name}: ${s.detail}`}>
            {s.name}
          </span>
        ))}
      </div>

      <div className="view-toggles">
        <button className={basemap === 'satellite' ? 'on' : ''} onClick={() => ui.set({ basemap: basemap === 'satellite' ? 'dark' : 'satellite' })}>
          {basemap === 'satellite' ? 'Satellite' : 'Dark'}
        </button>
        <button className={globe ? 'on' : ''} onClick={() => ui.set({ globe: !globe })}>
          Globe
        </button>
        <button className={terrain ? 'on' : ''} onClick={() => ui.set({ terrain: !terrain })}>
          3D terrain
        </button>
        <button className="accent" onClick={() => ui.set({ sentinelsOpen: true })}>
          Sentinels
        </button>
      </div>
    </header>
  )
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <span className="metric" title={hint}>
      <span className="metric-value mono">{value}</span>
      <span className="metric-label">{label}</span>
    </span>
  )
}
