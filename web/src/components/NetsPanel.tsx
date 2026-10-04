import { Network } from 'lucide-react'
import { useMemo } from 'react'
import { type Entity, live, useLive } from '../lib/live'
import { flagEmoji, MISSION_LABELS, netOwner, visible } from '../lib/picture'
import { select, ui, useStore } from '../lib/store'
import { netColor } from '../map/liveLayers'
import { Ago, PanelHead } from './ui'

/** Current nets grouped by the state or organisation that flies them. */
export function NetsPanel() {
  useLive(2000)
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
  const total = groups.reduce((n, [, g]) => n + g.length, 0)

  return (
    <>
      <PanelHead icon={Network} title="Mission nets" sub={`${total} net${total === 1 ? '' : 's'} of craft sharing a mission, by operator`}>
        <span className="tag est">inferred</span>
      </PanelHead>
      <div className="panel-body">
        {!groups.length && (
          <div className="empty">
            <p>No nets right now.</p>
            <p>
              A net forms when military craft share a mission: flying together, meeting a tanker, one callsign series, leaving one airfield together, or holding
              neighbouring orbits. A lone ISR, AEW, patrol or tanker aircraft in an orbit is a net of one.
            </p>
          </div>
        )}
        {groups.map(([owner, nets]) => (
          <div key={owner} className="net-group">
            <div className="net-owner row">
              {nets[0].props.states.length === 1 && <span aria-hidden>{flagEmoji(nets[0].props.states[0])}</span>}
              <span className="eyebrow" style={{ color: 'var(--text-2)' }}>
                {owner}
              </span>
              <span className="faint xs">· {nets.length}</span>
            </div>
            <ul className="list">
              {nets.map((n) => {
                const c = netColor(n)
                return (
                  <li key={n.id} className={`item ${selected === n.id ? 'selected' : ''}`} onClick={() => select(n.id, { lon: n.lon, lat: n.lat, zoom: 7 })}>
                    <span className="glyph" style={{ background: `rgba(${c[0]},${c[1]},${c[2]},0.14)`, color: `rgb(${c[0]},${c[1]},${c[2]})` }}>
                      <Network size={15} strokeWidth={1.8} />
                    </span>
                    <div className="item-main">
                      <div className="item-title">
                        {MISSION_LABELS[n.props.mission] ?? n.props.mission} <span className="faint">· {n.props.members.length} craft</span>
                      </div>
                      <div className="item-sub ellipsis">{n.props.members.map((m: { label: string }) => m.label).join(', ')}</div>
                      <div className="item-meta">
                        <span>
                          since <Ago ts={n.props.since} suffix="" />
                        </span>
                        <span className="tag est">{n.props.mission_src === 'rule' ? 'rule label' : `Jev ${n.props.mission_p != null ? `${Math.round(n.props.mission_p * 100)}%` : ''}`}</span>
                      </div>
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </div>
    </>
  )
}
