import { useMemo } from 'react'
import { type Alert, type Entity, live, useLive } from '../lib/live'
import { ago, describe, kindLabel } from '../lib/format'
import { type LeftTab, select, ui, useStore } from '../lib/store'
import { entityColor } from '../map/liveLayers'
import { IntelList } from './IntelList'

const TABS: { key: LeftTab; label: string }[] = [
  { key: 'alerts', label: 'Alerts' },
  { key: 'live', label: 'Live' },
  { key: 'vessels', label: 'Dark vessels' },
]

export function LeftPanel() {
  const tab = useStore(ui, (s) => s.leftTab)
  useLive(700)
  return (
    <aside className="left">
      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? 'on' : ''} onClick={() => ui.set({ leftTab: t.key })}>
            {t.label}
            {t.key === 'alerts' && live.alerts.length > 0 && <span className="count">{live.alerts.length}</span>}
          </button>
        ))}
      </nav>
      {tab === 'alerts' && <Alerts />}
      {tab === 'live' && <LiveList />}
      {tab === 'vessels' && <IntelList />}
    </aside>
  )
}

function flyToAlert(a: Alert) {
  const first = a.entities.map((id) => live.entities.get(id)).find(Boolean)
  if (first) select(first.id, { lon: first.lon, lat: first.lat, zoom: first.kind === 'aircraft' ? 7 : 9 })
  else if (a.lon != null && a.lat != null) select(null, { lon: a.lon, lat: a.lat })
}

function Alerts() {
  if (!live.alerts.length) {
    return (
      <div className="empty">
        <p>No alerts yet.</p>
        <p className="muted">Correlations and Sentinels raise alerts here as the live feed arrives. Every alert lists the observations behind it.</p>
      </div>
    )
  }
  return (
    <ul className="list">
      {live.alerts.map((a) => (
        <li key={a.id} className={`alert sev-${a.severity}`} onClick={() => flyToAlert(a)}>
          <div className="alert-head">
            <span className={`sev sev-${a.severity}`}>{a.severity}</span>
            <span className="muted small">{ago(a.ts)}</span>
            <span className={`prov prov-${a.prov}`}>{a.prov}</span>
            {a.sentinel && <span className="tag">sentinel</span>}
          </div>
          <div className="alert-title">{a.title}</div>
          <div className="alert-body">{a.body}</div>
        </li>
      ))}
    </ul>
  )
}

const KIND_ORDER: Entity['kind'][] = ['vessel', 'aircraft', 'fire', 'news', 'station', 'facility']

function LiveList() {
  const search = useStore(ui, (s) => s.search)
  const selected = useStore(ui, (s) => s.selected)
  const version = live.version
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return [...live.entities.values()]
      .filter((e) => e.kind !== 'facility' || q)
      .filter((e) => !q || e.label.toLowerCase().includes(q) || JSON.stringify(e.props).toLowerCase().includes(q))
      .sort((a, b) => {
        const k = KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind)
        if (k) return k
        const ma = Number(!!(a.props.military || a.props.uav || a.props.sanctions))
        const mb = Number(!!(b.props.military || b.props.uav || b.props.sanctions))
        return mb - ma || b.ts - a.ts
      })
      .slice(0, 400)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, version])

  return (
    <>
      <input
        className="search"
        placeholder="Search callsign, name, MMSI, type, place…"
        value={search}
        onChange={(ev) => ui.set({ search: ev.target.value })}
      />
      <ul className="list">
        {rows.map((e) => {
          const c = entityColor(e)
          return (
            <li key={e.id} className={`entity ${selected === e.id ? 'selected' : ''}`} onClick={() => select(e.id, { lon: e.lon, lat: e.lat, zoom: 8 })}>
              <span className="swatch" style={{ background: `rgb(${c[0]},${c[1]},${c[2]})` }} />
              <div className="entity-main">
                <div className="entity-title">{e.label}</div>
                <div className="entity-sub">
                  {kindLabel(e)} · {describe(e)}
                </div>
              </div>
            </li>
          )
        })}
      </ul>
    </>
  )
}
