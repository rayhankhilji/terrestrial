import { Bot, Crosshair, FileText, Gauge, Newspaper, Radar, ScrollText, ShieldAlert, Siren, Waypoints, Wifi } from 'lucide-react'
import { useState } from 'react'
import { live, useLive } from '../lib/live'
import { flyTo, select } from '../lib/store'
import { Ago, Cited, hhmm, PanelHead } from './ui'

interface Fact {
  id: string
  text: string
  refs: string[]
  kind: string
}

const KIND_ICON: Record<string, typeof Siren> = {
  threat: Siren,
  alert: ShieldAlert,
  forecast: Gauge,
  front: Waypoints,
  craft: Radar,
  gnss: Wifi,
  news: Newspaper,
  info: FileText,
}
const KIND_TONE: Record<string, string> = { threat: 'red', alert: 'red', forecast: 'violet', front: 'gold', craft: 'cyan', gnss: 'amber', news: '', info: '' }

function goTo(refs: string[]) {
  const e = refs.map((id) => live.entities.get(id)).find(Boolean)
  if (e) select(e.id, { lon: e.lon, lat: e.lat, zoom: e.kind === 'region' ? 6 : 7.5 })
}

/** The picture in one screen: headline numbers, the AI SITREP (when keyed) and its facts. */
export function BriefPanel() {
  useLive(1000)
  const [open, setOpen] = useState<string | null>(null)
  const rep = live.entities.get('sitrep:current')
  const facts: Fact[] = rep?.props.facts ?? []
  const prose = rep?.props.prose as { text: string; model: string; at: number; cited: string[] } | null | undefined
  const all = [...live.entities.values()]
  const alertNow = all.filter((e) => e.kind === 'region' && e.props.alert_active).length
  const threats = all.filter((e) => e.kind === 'airthreat' && !e.props.tally && Date.now() - e.ts < 3600_000).length
  const priority = all.filter((e) => (e.props.threat?.priority ?? 0) >= 25).length
  const jam = all.filter((e) => e.kind === 'gnss' && e.props.level === 'high').length
  const byId = Object.fromEntries(facts.map((f) => [f.id, f]))

  return (
    <>
      <PanelHead icon={ScrollText} title="Situation brief" sub={rep ? <>Built from {facts.length} facts · <Ago ts={rep.ts} /></> : 'Assembling the picture…'} />
      <div className="kpis">
        <div className={`kpi ${alertNow ? 'red' : ''}`}>
          <div className="kpi-value">{alertNow}</div>
          <div className="kpi-label">regions on alert</div>
        </div>
        <div className={`kpi ${threats ? 'red' : ''}`}>
          <div className="kpi-value">{threats}</div>
          <div className="kpi-label">air threats · 1 h</div>
        </div>
        <div className={`kpi ${priority ? 'amber' : ''}`}>
          <div className="kpi-value">{priority}</div>
          <div className="kpi-label">priority craft</div>
        </div>
        <div className={`kpi ${jam ? 'amber' : ''}`}>
          <div className="kpi-value">{jam}</div>
          <div className="kpi-label">GPS-jam cells</div>
        </div>
      </div>
      <div className="panel-body">
        <div className="section">
          <div className="section-title">
            <Bot size={15} className="faint" />
            <span className="eyebrow">Analyst summary</span>
            {prose && <span className="tag est">AI · {prose.model.split('/').pop()}</span>}
          </div>
          {prose ? (
            <>
              <p className="prose">
                <Cited text={prose.text} onCite={(id) => (byId[id] ? (setOpen(id), goTo(byId[id].refs)) : undefined)} />
              </p>
              <p className="xs faint" style={{ marginTop: 8 }}>
                Written {hhmm(prose.at)} UTC by an open-weight model from the facts below; every claim cites them, uncited text is rejected. A model estimate, not a
                finding.
              </p>
            </>
          ) : (
            <div className="callout est small">
              The AI summary is off until a Featherless key is set (<span className="mono">FEATHERLESS_API_KEY</span>). The facts below are computed directly from
              the live data and are always current.
            </div>
          )}
        </div>
        <div className="section" style={{ paddingBottom: 4 }}>
          <div className="section-title">
            <Crosshair size={15} className="faint" />
            <span className="eyebrow">Key facts</span>
          </div>
        </div>
        {facts.length === 0 && <div className="empty"><p>Waiting for the first facts.</p><p>They appear as reports, alerts and tracks arrive.</p></div>}
        <ul className="list">
          {facts.map((f) => {
            const Icon = KIND_ICON[f.kind] ?? FileText
            return (
              <li key={f.id} className={`item ${open === f.id ? 'selected' : ''}`} onClick={() => (setOpen(f.id), goTo(f.refs))}>
                <span className={`glyph ${KIND_TONE[f.kind] ?? ''}`}>
                  <Icon size={15} strokeWidth={1.8} />
                </span>
                <div className="item-main">
                  <div className="item-sub" style={{ color: 'var(--text)', marginTop: 0 }}>
                    {f.text}
                  </div>
                  <div className="item-meta">
                    <span className="cite">{f.id}</span>
                    {f.refs.length > 0 && <span>{f.refs.length} linked item{f.refs.length === 1 ? '' : 's'}</span>}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      </div>
      <div className="panel-foot">
        Open sources only. Russian military aviation does not broadcast; its activity appears through Air Force reports, alerts and news.{' '}
        <button className="link" onClick={() => flyTo({ lon: 33.5, lat: 48.6, zoom: 5.2, pitch: 35 })}>
          Frame Ukraine
        </button>
      </div>
    </>
  )
}
