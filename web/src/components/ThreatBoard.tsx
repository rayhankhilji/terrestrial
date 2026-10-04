import { Radar } from 'lucide-react'
import { useMemo } from 'react'
import { describe } from '../lib/format'
import { type Entity, live, useLive } from '../lib/live'
import { flagEmoji, visible } from '../lib/picture'
import { select, ui, useStore } from '../lib/store'
import { PanelHead } from './ui'

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
  useLive(2000)
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
    <>
      <PanelHead icon={Radar} title="Priority craft" sub={`${rows.length} broadcasting military craft · ${hostile} with threat points`}>
        <span className="tag est">heuristic</span>
      </PanelHead>
      <div className="panel-body">
        {rows.length === 0 && (
          <div className="empty">
            <p>No military craft in the picture yet.</p>
          </div>
        )}
        <ul className="list">
          {rows.map((e: Entity) => {
            const t: ThreatScore = e.props.threat
            const top = t.reasons.filter((r) => r.points > 0).sort((a, b) => b.points - a.points)
            return (
              <li key={e.id} className={`item ${selected === e.id ? 'selected' : ''}`} onClick={() => select(e.id, { lon: e.lon, lat: e.lat, zoom: 7 })}>
                <span className="prio" style={{ borderColor: threatColor(t.priority), color: threatColor(t.priority) }}>
                  {t.priority}
                </span>
                <div className="item-main">
                  <div className="item-title ellipsis">
                    {flagEmoji(e.props.state_code)} {e.label}
                  </div>
                  <div className="item-sub ellipsis">{describe(e)}</div>
                  <div className="item-meta">
                    {top.length ? top.slice(0, 3).map((r) => <span key={r.reason}>{r.reason}</span>) : <span>no scored factors</span>}
                  </div>
                </div>
                <span className="num xs faint" title="threat / intel">
                  {t.threat}/{t.intel}
                </span>
              </li>
            )
          })}
        </ul>
      </div>
      <div className="panel-foot">
        Threat to Ukraine (hostile or unattributed operator, role, distance to territory and front, heading) plus half the intelligence significance. Not a
        probability; says nothing about intent. Russian military aviation does not broadcast.
      </div>
    </>
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
