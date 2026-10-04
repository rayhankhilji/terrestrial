import { ExternalLink, Newspaper, ShieldAlert } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { headlines, startFeeds } from '../lib/feeds'
import { type Alert, live, useLive } from '../lib/live'
import { select, useStore } from '../lib/store'
import { Ago, PanelHead } from './ui'

type Row = { kind: 'alert'; at: number; a: Alert } | { kind: 'news'; at: number; title: string; url: string; source: string; summary: string }

function flyToAlert(a: Alert) {
  const first = a.entities.map((id) => live.entities.get(id)).find(Boolean)
  if (first) select(first.id, { lon: first.lon, lat: first.lat, zoom: first.kind === 'aircraft' ? 7 : 9 })
  else if (a.lon != null && a.lat != null) select(null, { lon: a.lon, lat: a.lat })
}

/** One chronological feed: system alerts (correlations, sentinels, re-routes) and news wires. */
export function FeedPanel() {
  useLive(1000)
  useEffect(startFeeds, [])
  const news = useStore(headlines, (s) => s.rows)
  const [only, setOnly] = useState<'all' | 'alert' | 'news'>('all')
  const version = live.version
  const rows = useMemo(() => {
    const out: Row[] = [
      ...live.alerts.map((a) => ({ kind: 'alert' as const, at: a.ts, a })),
      ...news.map((n) => ({ kind: 'news' as const, at: n.at, title: n.title, url: n.url, source: n.source, summary: n.summary })),
    ]
    return out.filter((r) => only === 'all' || r.kind === only).sort((x, y) => y.at - x.at).slice(0, 200)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, news, only])

  return (
    <>
      <PanelHead icon={Newspaper} title="Feed" sub="System alerts and Ukrainian news wires, newest first" />
      <div className="section" style={{ paddingTop: 10, paddingBottom: 10 }}>
        <div className="seg">
          {(['all', 'alert', 'news'] as const).map((k) => (
            <button key={k} className={only === k ? 'on' : ''} onClick={() => setOnly(k)}>
              {k === 'all' ? 'Everything' : k === 'alert' ? `Alerts · ${live.alerts.length}` : `News · ${news.length}`}
            </button>
          ))}
        </div>
      </div>
      <div className="panel-body" style={{ paddingTop: 0 }}>
        {rows.length === 0 && <div className="empty"><p>Nothing yet.</p><p>Alerts and headlines stream in here.</p></div>}
        <ul className="list">
          {rows.map((r) =>
            r.kind === 'alert' ? (
              <li key={r.a.id} className="item" onClick={() => flyToAlert(r.a)}>
                <span className={`glyph ${r.a.severity === 'high' ? 'red' : r.a.severity === 'warn' ? 'amber' : 'cyan'}`}>
                  <ShieldAlert size={15} strokeWidth={1.8} />
                </span>
                <div className="item-main">
                  <div className="item-title">{r.a.title}</div>
                  <div className="item-sub">{r.a.body}</div>
                  <div className="item-meta">
                    <span className={`sev sev-${r.a.severity}`}>{r.a.severity}</span>
                    <span className={`prov prov-${r.a.prov}`}>{r.a.prov}</span>
                    {r.a.sentinel && <span className="tag">sentinel</span>}
                  </div>
                </div>
                <div className="item-aside">
                  <Ago ts={r.at} suffix="" />
                </div>
              </li>
            ) : (
              <li key={r.url} className="item" onClick={() => window.open(r.url, '_blank', 'noopener')}>
                <span className="glyph">
                  <Newspaper size={15} strokeWidth={1.8} />
                </span>
                <div className="item-main">
                  <div className="item-title">{r.title}</div>
                  <div className="item-meta">
                    <span>{r.source}</span>
                    <ExternalLink size={11} />
                  </div>
                </div>
                <div className="item-aside">
                  <Ago ts={r.at} suffix="" />
                </div>
              </li>
            ),
          )}
        </ul>
      </div>
    </>
  )
}
