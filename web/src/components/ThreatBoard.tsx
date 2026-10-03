import { useMemo } from 'react'
import { describe } from '../lib/format'
import { type Entity, live } from '../lib/live'
import { flagEmoji, visible } from '../lib/picture'
import { select, ui, useStore } from '../lib/store'

export interface ThreatReason {
  axis: 'threat' | 'intel'
  points: number
  reason: string
  evidence: string | null
}

export interface ThreatScore {
  priority: number
  threat: number
  intel: number
  dist_ua_km: number
  reasons: ThreatReason[]
}

export function threatColor(priority: number): string {
  if (priority >= 50) return '#f43f5e'
  if (priority >= 25) return '#f97316'
  if (priority >= 10) return '#facc15'
  return '#64748b'
}

/** Live craft ranked by priority (threat to Ukraine + half their intelligence significance). */
export function ThreatBoard() {
  const mode = useStore(ui, (s) => s.mode)
  const states = useStore(ui, (s) => s.states)
  const selected = useStore(ui, (s) => s.selected)
  const version = live.version
  const rows = useMemo(
    () =>
      [...live.entities.values()]
        .filter((e) => (e.kind === 'aircraft' || e.kind === 'vessel') && e.props.threat && visible(e, { mode, states }))
        .sort((a, b) => b.props.threat.priority - a.props.threat.priority || b.props.threat.intel - a.props.threat.intel),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version, mode, states],
  )
  const hostile = rows.filter((e) => e.props.threat.threat > 0).length
  return (
    <div className="danger-panel">
      <div className="danger-head">
        <strong>Craft ranked by priority</strong>
        <div className="muted small">
          Heuristic score: threat to Ukraine (hostile or unattributed operator, role, distance, heading) plus half the intelligence significance
          (missions, emergencies, GNSS interference, orbits). Not a probability, says nothing about intent. {hostile} of {rows.length} broadcasting
          craft carry threat points; Russian military aviation does not broadcast.
        </div>
      </div>
      <ul className="list">
        {rows.map((e: Entity) => {
          const t: ThreatScore = e.props.threat
          const top = t.reasons.filter((r) => r.points > 0).sort((a, b) => b.points - a.points)
          return (
            <li key={e.id} className={`entity danger-row ${selected === e.id ? 'selected' : ''}`} onClick={() => select(e.id, { lon: e.lon, lat: e.lat, zoom: 7 })}>
              <span className="prio" style={{ borderColor: threatColor(t.priority), color: threatColor(t.priority) }}>
                {t.priority}
              </span>
              <div className="entity-main">
                <div className="entity-title">
                  {flagEmoji(e.props.state_code)} {e.label} <span className="muted small">{describe(e)}</span>
                </div>
                <div className="entity-sub">
                  {top.length ? top.slice(0, 3).map((r) => r.reason).join(' · ') : 'no scored factors'}
                </div>
              </div>
              <span className="mono small muted" title="threat / intel">
                {t.threat}/{t.intel}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/** Inspector section: the full breakdown for one craft. */
export function ThreatDetail({ e }: { e: Entity }) {
  const t: ThreatScore | undefined = e.props.threat
  if (!t) return null
  return (
    <section>
      <h3>
        Priority <span className="prio" style={{ borderColor: threatColor(t.priority), color: threatColor(t.priority) }}>{t.priority}</span>{' '}
        <span className="tag">heuristic</span>
      </h3>
      <p className="muted small">
        Threat {t.threat} + ½ × intel {t.intel} (capped at 100). {t.dist_ua_km === 0 ? 'Over Ukrainian territory.' : `${t.dist_ua_km} km from Ukrainian territory.`}
      </p>
      <table className="facts">
        <tbody>
          {t.reasons.map((r) => (
            <tr key={r.reason}>
              <th className="mono">{r.points > 0 ? `+${r.points}` : '·'}</th>
              <td>
                <span className={`tag axis-${r.axis}`}>{r.axis}</span> {r.reason}
                {r.evidence && <div className="muted small">{r.evidence}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
