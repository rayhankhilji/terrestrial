import { ExternalLink, Siren } from 'lucide-react'
import { useMemo, useState } from 'react'
import { type Entity, live, useLive } from '../lib/live'
import { select, ui, useStore } from '../lib/store'
import { css, weapon, WEAPONS } from '../lib/weapons'
import { Ago, PanelHead } from './ui'

function Tally({ e }: { e: Entity }) {
  const t = e.props.tally
  return (
    <div className="section">
      <div className="section-title">
        <span className="eyebrow">Latest Air Force tally</span>
        <span className="tag obs">observed</span>
        <span className="faint xs" style={{ marginLeft: 'auto' }}>
          <Ago ts={e.ts} />
        </span>
      </div>
      <div className="kpis" style={{ gridTemplateColumns: 'repeat(3, 1fr)', borderRadius: 10, overflow: 'hidden', border: '1px solid var(--hairline)' }}>
        <div className="kpi red">
          <div className="kpi-value">{t.attacked ?? '–'}</div>
          <div className="kpi-label">launched</div>
        </div>
        <div className="kpi">
          <div className="kpi-value" style={{ color: 'var(--green)' }}>
            {t.downed}
          </div>
          <div className="kpi-label">downed / suppressed</div>
        </div>
        <div className="kpi amber">
          <div className="kpi-value">{t.hit_locations ?? '–'}</div>
          <div className="kpi-label">impact sites</div>
        </div>
      </div>
      {t.launch_areas?.length > 0 && (
        <>
          <div className="eyebrow" style={{ margin: '12px 0 6px' }}>
            Launch directions
          </div>
          <div className="chips">
            {t.launch_areas.map((a: { name: string; country: string; lon: number; lat: number }) => (
              <button key={a.name} className="chip" onClick={() => select(e.id, { lon: a.lon, lat: a.lat, zoom: 6 })}>
                {a.name}
                <span className="count">{a.country}</span>
              </button>
            ))}
          </div>
        </>
      )}
      <a className="xs" href={e.props.url} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', gap: 4, marginTop: 10 }}>
        Original post <ExternalLink size={11} />
      </a>
    </div>
  )
}

/** Live air-threat reports (Air Force of Ukraine + monitors), newest first. */
export function AirThreatPanel() {
  useLive(1000)
  const selected = useStore(ui, (s) => s.selected)
  const [only, setOnly] = useState<string | null>(null)
  const version = live.version
  const { reports, tally, counts } = useMemo(() => {
    const all = [...live.entities.values()].filter((e) => e.kind === 'airthreat')
    const reports = all.filter((e) => !e.props.tally).sort((a, b) => b.ts - a.ts)
    const tally = all.filter((e) => e.props.tally).sort((a, b) => b.ts - a.ts)[0]
    const counts: Record<string, number> = {}
    for (const r of reports) counts[r.props.weapon ?? 'unknown'] = (counts[r.props.weapon ?? 'unknown'] ?? 0) + 1
    return { reports, tally, counts }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version])
  const shown = only ? reports.filter((r) => (r.props.weapon ?? 'unknown') === only) : reports

  return (
    <>
      <PanelHead icon={Siren} title="Air threats" sub="Air Force of Ukraine and monitoring channels · live, last 45 min" />
      <div className="panel-body">
        {tally && <Tally e={tally} />}
        <div className="section" style={{ paddingTop: tally ? 4 : 14 }}>
          <div className="chips">
            <button className={`chip ${only == null ? 'on' : ''}`} onClick={() => setOnly(null)}>
              All <span className="count">{reports.length}</span>
            </button>
            {Object.entries(counts)
              .sort((a, b) => b[1] - a[1])
              .map(([cls, n]) => {
                const w = weapon(cls)
                return (
                  <button key={cls} className={`chip ${only === cls ? 'on' : ''}`} onClick={() => setOnly(only === cls ? null : cls)}>
                    <span className="swatch" style={{ background: css(w.color) }} />
                    {w.short}
                    <span className="count">{n}</span>
                  </button>
                )
              })}
          </div>
        </div>
        {shown.length === 0 && (
          <div className="empty">
            <p>No air threats reported in the last 45 minutes.</p>
            <p>Reports appear here within seconds of being posted by the Air Force of Ukraine.</p>
          </div>
        )}
        <ul className="list">
          {shown.map((r) => {
            const w = weapon(r.props.weapon)
            const Icon = w.icon
            const fresh = Date.now() - r.ts < 120_000
            return (
              <li key={r.id} className={`item ${selected === r.id ? 'selected' : ''} ${fresh ? 'fresh' : ''}`} onClick={() => select(r.id, { lon: r.lon, lat: r.lat, zoom: 7.5 })}>
                <span className="glyph" style={{ background: css(w.color, 0.13), color: css(w.color) }}>
                  <Icon size={16} strokeWidth={1.8} />
                </span>
                <div className="item-main">
                  <div className="item-title">{r.label}</div>
                  <div className="item-meta">
                    <span>{r.props.channel_name}</span>
                    {r.props.region_name && <span>· {r.props.region_name}</span>}
                    {r.props.placed_by === 'region centre' && <span className="tag est">region centre</span>}
                  </div>
                </div>
                <div className="item-aside">
                  <Ago ts={r.ts} suffix="" />
                </div>
              </li>
            )
          })}
        </ul>
      </div>
      <div className="panel-foot">
        Parsed by transparent rules from public posts; the original text and link are kept with every report. Positions are where the post places the threat, never
        guessed. {Object.keys(WEAPONS).length} weapon classes.
      </div>
    </>
  )
}
