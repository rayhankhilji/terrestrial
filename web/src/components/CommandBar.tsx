import { Globe2, Layers, Mountain, Satellite, Search, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'
import { streams } from '../lib/feeds'
import { live, p50, useLive } from '../lib/live'
import { ms } from '../lib/format'
import { visible } from '../lib/picture'
import { ui, useStore } from '../lib/store'
import { Logo } from './Logo'

function Clock() {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const d = new Date(now)
  const hh = String(d.getUTCHours()).padStart(2, '0')
  const mm = String(d.getUTCMinutes()).padStart(2, '0')
  const ss = String(d.getUTCSeconds()).padStart(2, '0')
  return (
    <span className="clock" title={d.toUTCString()}>
      {hh}:{mm}
      <span className="faint">:{ss}</span>
      <small>UTC</small>
    </span>
  )
}

function Health() {
  const rows = useStore(streams, (s) => s.rows)
  const ok = rows.filter((r) => r.state === 'ok').length
  const keyed = rows.filter((r) => r.state === 'needs-key').length
  const bad = rows.filter((r) => ['error', 'missing', 'stale'].includes(r.state)).length
  return (
    <button
      className="health"
      onClick={() => ui.set({ leftTab: 'sources', panelOpen: true })}
      title={`${ok} streams working, ${bad} with problems, ${keyed} waiting for a key`}
    >
      <span className="bars" aria-hidden="true">
        {rows.slice(0, 26).map((r) => (
          <i
            key={r.id}
            className={r.state === 'ok' ? '' : r.state === 'needs-key' ? 'key' : 'err'}
            style={{ height: r.state === 'ok' ? 12 : 6 }}
          />
        ))}
      </span>
      <span className="num">
        {ok}/{rows.length || '–'}
      </span>
      <span className="faint">streams</span>
    </button>
  )
}

export function CommandBar() {
  useLive(1000)
  const mode = useStore(ui, (s) => s.mode)
  const basemap = useStore(ui, (s) => s.basemap)
  const globe = useStore(ui, (s) => s.globe)
  const terrain = useStore(ui, (s) => s.terrain)
  const layersOpen = useStore(ui, (s) => s.layersOpen)
  const tracks = [...live.entities.values()].filter((e) => (e.kind === 'aircraft' || e.kind === 'vessel') && visible(e, ui.get())).length
  const threats = [...live.entities.values()].filter((e) => e.kind === 'airthreat' && !e.props.tally).length
  const connection = live.mode === 'replay' ? 'replay' : live.connected ? 'live' : 'offline'

  return (
    <header className="cmdbar glass">
      <div className="wordmark">
        <Logo />
        <div>
          <div className="wordmark-name">TERRESTRIAL</div>
          <div className="wordmark-sub">{mode === 'military' ? 'Air & threat picture · Ukraine' : 'Maritime · dark vessels'}</div>
        </div>
      </div>
      <div className="seg" role="tablist" aria-label="Picture">
        {(['military', 'maritime'] as const).map((m) => (
          <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? 'on' : ''} onClick={() => ui.set({ mode: m, leftTab: m === 'maritime' ? 'vessels' : 'brief' })}>
            {m === 'military' ? 'Military' : 'Maritime'}
          </button>
        ))}
      </div>
      <button className="searchbox" onClick={() => ui.set({ paletteOpen: true })} aria-label="Search">
        <Search size={15} />
        <span className="label">Search craft, places, layers…</span>
        <span className="kbd">⌘K</span>
      </button>
      <div className="spacer" />
      <div className="metrics">
        <span className="metric" title="Military craft broadcasting now">
          <span className="metric-value">{tracks}</span>
          <span className="metric-label">craft</span>
        </span>
        <span className="metric" title="Air-threat reports in the last 45 minutes (Air Force of Ukraine, monitors)">
          <span className="metric-value" style={{ color: threats ? 'var(--red)' : undefined }}>
            {threats}
          </span>
          <span className="metric-label">threats</span>
        </span>
        <span className="metric" title="Median age of positions when drawn">
          <span className="metric-value">{ms(p50(live.e2eLatency))}</span>
          <span className="metric-label">latency</span>
        </span>
      </div>
      <span className={`live-chip ${connection}`} title={connection === 'replay' ? 'Replaying a recorded session' : connection === 'live' ? 'Connected to the live hub' : 'Disconnected: reconnecting'}>
        <span className="dot" />
        {connection.toUpperCase()}
      </span>
      <Clock />
      <Health />
      <div className="divider-v" />
      <button className={`iconbtn ${layersOpen ? 'on' : ''}`} title="Layers" onClick={() => ui.set({ layersOpen: !layersOpen })}>
        <Layers size={17} />
      </button>
      <button className={`iconbtn ${basemap === 'satellite' ? 'on' : ''}`} title={basemap === 'satellite' ? 'Satellite imagery (click for dark map)' : 'Dark map (click for satellite imagery)'} onClick={() => ui.set({ basemap: basemap === 'satellite' ? 'dark' : 'satellite' })}>
        <Satellite size={17} />
      </button>
      <button className={`iconbtn ${globe ? 'on' : ''}`} title="Globe projection" onClick={() => ui.set({ globe: !globe })}>
        <Globe2 size={17} />
      </button>
      <button className={`iconbtn ${terrain ? 'on' : ''}`} title="3D terrain" onClick={() => ui.set({ terrain: !terrain })}>
        <Mountain size={17} />
      </button>
      <button className="iconbtn" title="Sentinels: alert rules" onClick={() => ui.set({ sentinelsOpen: true })}>
        <Workflow size={17} />
      </button>
    </header>
  )
}
