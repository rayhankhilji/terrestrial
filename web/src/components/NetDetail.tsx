import { ago } from '../lib/format'
import { type Entity, live } from '../lib/live'
import { flagEmoji, MISSION_LABELS, ROLE_LABELS } from '../lib/picture'
import { select } from '../lib/store'

interface Member {
  id: string
  label: string
  role: string
  airframe: string
  state_code: string | null
  orbit: boolean
}

/** Inspector body for a net: why it exists, who is in it, and how its mission was labelled. */
export function NetDetail({ e }: { e: Entity }) {
  const p = e.props
  const probs: Record<string, number> | undefined = p.mission_probabilities
  return (
    <>
      <section className="callout">
        <strong>{MISSION_LABELS[p.mission] ?? p.mission}</strong>
        <div className="small">
          {p.mission_src === 'rule'
            ? 'Labelled by a deterministic rule from member roles and link evidence (set TYPESAFE_API_KEY to have Jev judge it).'
            : `Labelled by ${p.mission_src}${p.mission_p != null ? ` with probability ${p.mission_p.toFixed(2)}` : ''}.`}
        </div>
        {probs && (
          <div className="small muted">
            {Object.entries(probs)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 4)
              .map(([k, v]) => `${MISSION_LABELS[k] ?? k} ${v.toFixed(2)}`)
              .join(' · ')}
          </div>
        )}
        <div className="small muted">
          Inferred from the last {p.window_min} min of tracks · formed {ago(p.since)} · {p.org ?? 'unattributed'}
        </div>
      </section>

      <section>
        <h3>Members ({p.members.length})</h3>
        <ul className="links">
          {(p.members as Member[]).map((m) => {
            const live_e = live.entities.get(m.id)
            return (
              <li key={m.id} onClick={() => live_e && select(m.id, { lon: live_e.lon, lat: live_e.lat, zoom: 8 })}>
                {flagEmoji(m.state_code)} <strong>{m.label}</strong> <span className="muted small">{ROLE_LABELS[m.role] ?? m.role}</span>
                {m.orbit && <span className="tag">orbit</span>}
              </li>
            )
          })}
        </ul>
      </section>

      {p.links.length > 0 && (
        <section>
          <h3>Evidence links</h3>
          <ul className="links">
            {(p.links as { a: string; b: string; w: number; why: string[] }[]).map((l) => {
              const name = (id: string) => (p.members as Member[]).find((m) => m.id === id)?.label ?? id
              return (
                <li key={`${l.a}-${l.b}`}>
                  {name(l.a)} ↔ {name(l.b)} <span className="mono small">w={l.w.toFixed(2)}</span>
                  <span className="prov prov-inferred">inferred</span>
                  <div className="why small">{l.why.join(' · ')}</div>
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </>
  )
}
