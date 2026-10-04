import { ExternalLink, Orbit, Sun } from 'lucide-react'
import { useEffect } from 'react'
import { loadPasses, passes } from '../lib/feeds'
import { type Entity } from '../lib/live'
import { select, useStore } from '../lib/store'
import { css, weapon } from '../lib/weapons'
import { Ago, hhmm } from './ui'

/** An Air Force / monitor report: our structured reading, then the original post. */
export function AirThreatDetail({ e }: { e: Entity }) {
  const p = e.props
  const w = weapon(p.weapon)
  const Icon = w.icon
  if (p.tally) {
    const t = p.tally
    return (
      <section>
        <h3>Overnight tally · Air Force of Ukraine</h3>
        <table className="facts">
          <tbody>
            <tr><th>Launched</th><td>{t.attacked ?? 'not stated'}</td></tr>
            <tr><th>Downed / suppressed</th><td>{t.downed}</td></tr>
            <tr><th>Impact sites</th><td>{t.hit_locations ?? 'not stated'}</td></tr>
            <tr><th>Debris sites</th><td>{t.debris_locations ?? 'not stated'}</td></tr>
            <tr><th>Launch directions</th><td>{t.launch_areas.map((a: { name: string }) => a.name).join(', ') || 'not stated'}</td></tr>
          </tbody>
        </table>
        <h4>Original post</h4>
        <div className="quote">{p.text}</div>
        <a className="xs" href={p.url} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', gap: 4, marginTop: 8 }}>
          {p.url} <ExternalLink size={11} />
        </a>
      </section>
    )
  }
  return (
    <section>
      <h3>
        <span className="glyph" style={{ width: 22, height: 22, borderRadius: 6, background: css(w.color, 0.15), color: css(w.color) }}>
          <Icon size={13} />
        </span>
        Air-threat report
        <span className="tag obs" style={{ marginLeft: 'auto' }}>observed · <Ago ts={e.ts} /></span>
      </h3>
      <table className="facts">
        <tbody>
          <tr><th>Weapon</th><td>{w.label}{p.count ? ` × ${p.count}` : ''}</td></tr>
          <tr><th>Region</th><td>{p.region_name ?? '—'}</td></tr>
          <tr><th>Reported at / passing</th><td>{p.at_place?.name ?? '—'}</td></tr>
          <tr>
            <th>Heading for</th>
            <td>
              {p.to_place ? (
                <button className="link" onClick={() => select(e.id, { lon: p.to_place.lon, lat: p.to_place.lat, zoom: 10 })}>
                  {p.to_place.name}
                </button>
              ) : (
                '—'
              )}
            </td>
          </tr>
          <tr><th>Placed on the map by</th><td>{p.placed_by}</td></tr>
          <tr><th>Source</th><td>{p.channel_name}</td></tr>
        </tbody>
      </table>
      <h4>Original post</h4>
      <div className="quote">{p.text}</div>
      <a className="xs" href={p.url} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', gap: 4, marginTop: 8 }}>
        Open on Telegram <ExternalLink size={11} />
      </a>
      {p.signal && <SignalDetail s={p.signal} />}
      <p className="xs faint" style={{ marginTop: 10 }}>
        The structured fields are Terrestrial's rule-based reading of the post; the post is the source. A heading is where the threat was reported going, not a
        prediction of impact.
      </p>
    </section>
  )
}

/** Jev's typed reading of a report or headline, with probabilities. */
export function SignalDetail({ s }: { s: Record<string, { value: string | number; probabilities?: Record<string, number> | null; confidence?: number | null }> & { model?: unknown } }) {
  const rows = ['event', 'target', 'region', 'severity', 'russian_strike'].filter((k) => s[k])
  return (
    <>
      <h4>
        Extracted signal <span className="tag est">AI · Jev</span>
      </h4>
      <table className="facts">
        <tbody>
          {rows.map((k) => (
            <tr key={k}>
              <th>{k.replace('_', ' ')}</th>
              <td>
                {k === 'russian_strike' ? `${Math.round(Number(s[k].value) * 100)}% likely` : String(s[k].value)}
                {s[k].confidence != null && <span className="faint"> · conf {Number(s[k].confidence).toFixed(2)}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

export function NewsDetail({ e }: { e: Entity }) {
  const p = e.props
  if (!p.outlet) return null
  return (
    <section>
      <h3>{p.outlet}</h3>
      {p.summary && <p className="prose" style={{ fontSize: 'var(--fs)' }}>{p.summary}</p>}
      <a className="xs" href={p.url} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', gap: 4, marginTop: 10 }}>
        Read the article <ExternalLink size={11} />
      </a>
      <p className="xs faint" style={{ marginTop: 8 }}>Placed because the {p.placed_by}. Headlines name places loosely: treat the position as approximate.</p>
      {p.signal && <SignalDetail s={p.signal} />}
    </section>
  )
}

export function SatelliteDetail({ e }: { e: Entity }) {
  const p = e.props
  return (
    <section>
      <h3>
        <Orbit size={14} /> Imaging satellite
      </h3>
      <table className="facts">
        <tbody>
          <tr><th>Sensor</th><td>{p.sensor === 'SAR' ? 'Radar (SAR): sees through cloud and at night' : 'Optical: needs daylight and clear sky'}</td></tr>
          <tr><th>Operator</th><td>{p.operator}</td></tr>
          <tr><th>Altitude</th><td>{p.alt_km} km</td></tr>
          <tr><th>Imaging reach</th><td>±{p.access_km} km from its ground track</td></tr>
          <tr><th>Tasking</th><td>{p.tasked ? 'commercial: images only where a customer tasks it' : 'fixed public acquisition plan'}</td></tr>
          <tr><th>NORAD id</th><td>{p.norad}</td></tr>
          <tr><th>Elements epoch</th><td>{String(p.elements_epoch).replace('T', ' ').slice(0, 16)} UTC</td></tr>
        </tbody>
      </table>
      <p className="xs faint" style={{ marginTop: 8 }}>The dashed line ahead of it is its ground track for the next 45 minutes (SGP4 from CelesTrak elements).</p>
    </section>
  )
}

/** Next imaging passes over the selected thing (opportunities, not acquisitions). */
export function PassesHere({ e }: { e: Entity }) {
  const p = useStore(passes, (s) => s)
  useEffect(() => {
    void loadPasses(e.lon, e.lat, e.label)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [e.id])
  const sar = p.rows.filter((x) => x.sensor === 'SAR')
  const next = p.rows.slice(0, 5)
  return (
    <section>
      <h3>Next satellite looks here</h3>
      {p.loading && <p className="small faint">Propagating orbits…</p>}
      {!p.loading && next.length === 0 && <p className="small faint">No passes in the next 24 hours.</p>}
      {!p.loading && next.length > 0 && (
        <>
          <p className="small muted" style={{ marginBottom: 8 }}>
            {sar.length} radar and {p.rows.length - sar.length} optical opportunities in 24 h. Next radar: {sar[0] ? `${sar[0].satellite} at ${hhmm(sar[0].start)} UTC` : 'none'}.
          </p>
          {next.map((x) => (
            <div key={`${x.norad}-${x.start}`} className="row small" style={{ padding: '4px 0' }}>
              <span className="num" style={{ width: 44 }}>{hhmm(x.start)}</span>
              <span className="grow ellipsis">{x.satellite}</span>
              <span className={`tag ${x.sensor === 'SAR' ? 'obs' : 'gold'}`}>{x.sensor}</span>
              {x.sensor === 'optical' && <span className="tag" title={x.daylight ? 'daylight' : 'night'}><Sun size={10} />{x.daylight ? 'day' : 'night'}</span>}
            </div>
          ))}
        </>
      )}
    </section>
  )
}

export function GnssDetail({ e }: { e: Entity }) {
  const p = e.props
  return (
    <section>
      <h3>GPS interference cell</h3>
      <div className="pbar">
        <span style={{ width: `${Math.max(3, p.frac * 100)}%`, background: p.level === 'high' ? 'var(--red)' : p.level === 'medium' ? 'var(--amber)' : 'var(--green)' }} />
        <em>{Math.round(p.frac * 100)}%</em>
      </div>
      <p className="small muted">
        {p.degraded} of {p.aircraft} aircraft seen here in the last {p.window_min} minutes reported degraded navigation accuracy ({p.rule}). Levels follow gpsjam.org:
        low under 2%, medium 2–10%, high over 10%. Consistent with jamming or spoofing; one faulty receiver can colour a quiet cell.
      </p>
    </section>
  )
}

export function FrontDetail({ e }: { e: Entity }) {
  const p = e.props
  return (
    <section>
      <h3>Front line · DeepStateMap</h3>
      <table className="facts">
        <tbody>
          <tr><th>Occupied area</th><td>{Math.round(p.occupied_km2).toLocaleString()} km²</td></tr>
          <tr><th>Front length</th><td>~{p.front_km} km</td></tr>
          <tr><th>Attack directions</th><td>{p.attack_directions}</td></tr>
          <tr><th>Unit positions</th><td>{p.units} (estimates)</td></tr>
          <tr><th>Russian airfields</th><td>{p.airfields}</td></tr>
          <tr><th>Snapshot</th><td>{p.datetime}</td></tr>
        </tbody>
      </table>
      {p.update && (
        <>
          <h4>Latest update</h4>
          <div className="quote">{p.update}</div>
        </>
      )}
    </section>
  )
}
