import { useMemo } from 'react'
import { ago } from '../lib/format'
import { type Entity, live } from '../lib/live'
import { flagEmoji, MISSION_LABELS, netOwner, visible } from '../lib/picture'
import { select, ui, useStore } from '../lib/store'
import { netColor } from '../map/liveLayers'

/** Current nets grouped by the state or organisation that flies them. */
export function NetsPanel() {
  const mode = useStore(ui, (s) => s.mode)
  const states = useStore(ui, (s) => s.states)
  const selected = useStore(ui, (s) => s.selected)
  const version = live.version
  const groups = useMemo(() => {
    const nets = [...live.entities.values()].filter((e) => e.kind === 'net' && visible(e, { mode, states }))
    const by = new Map<string, Entity[]>()
    for (const n of nets) by.set(netOwner(n), [...(by.get(netOwner(n)) ?? []), n])
    return [...by.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, mode, states])

  if (!groups.length) {
    return (
      <div className="empty">
        <p>No nets right now.</p>
        <p className="muted">
          A net forms when military craft share a mission: flying together, meeting a tanker, flying one callsign series, leaving one airfield
          together, or holding neighbouring orbits. A lone ISR, AEW, patrol or tanker aircraft in an orbit is a net of one. Evidence needs about 10
          minutes of shared track.
        </p>
      </div>
    )
  }
  return (
    <ul className="list nets">
      {groups.map(([owner, nets]) => (
        <li key={owner} className="net-group">
          <div className="net-owner">
            {nets[0].props.states.length === 1 && <span aria-hidden>{flagEmoji(nets[0].props.states[0])} </span>}
            {owner} <span className="muted small">· {nets.length} net{nets.length > 1 ? 's' : ''}</span>
          </div>
          {nets.map((n) => {
            const c = netColor(n)
            return (
              <div key={n.id} className={`entity ${selected === n.id ? 'selected' : ''}`} onClick={() => select(n.id, { lon: n.lon, lat: n.lat, zoom: 7 })}>
                <span className="swatch" style={{ background: `rgb(${c[0]},${c[1]},${c[2]})` }} />
                <div className="entity-main">
                  <div className="entity-title">
                    {MISSION_LABELS[n.props.mission] ?? n.props.mission}
                    <span className="muted small"> · {n.props.members.length} craft</span>
                  </div>
                  <div className="entity-sub">
                    {n.props.members.map((m: { label: string }) => m.label).join(', ')} · since {ago(n.props.since)} ·{' '}
                    {n.props.mission_src === 'rule' ? 'rule label' : `Jev ${n.props.mission_p != null ? `p=${n.props.mission_p.toFixed(2)}` : ''}`}
                  </div>
                </div>
              </div>
            )
          })}
        </li>
      ))}
    </ul>
  )
}
