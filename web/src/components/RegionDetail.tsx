import { ago, num, utc } from '../lib/format'
import type { Entity } from '../lib/live'
import { ui } from '../lib/store'

const pct = (p: number | null | undefined) => (p == null ? '—' : p < 0.01 ? '<1%' : p > 0.99 ? '>99%' : `${Math.round(p * 100)}%`)

/** Inspector body for a region: live alert state, the forecast and why it differs from usual. */
export function RegionDetail({ e }: { e: Entity }) {
  const p = e.props
  return (
    <>
      <section className={`callout ${p.alert_active ? 'danger' : ''}`}>
        <strong>{p.alert_active ? 'Air-raid alert in effect' : p.alert_active === false ? 'No alert right now' : 'Alert state unknown'}</strong>
        {p.alert_active && p.alert_since && <div className="small">observed since {utc(p.alert_since, true)} ({ago(p.alert_since)})</div>}
        <div className="muted small">Live state from the official alert channels (via ubilling.net.ua), polled every 30 s.</div>
      </section>

      <section>
        <h3>Forecast · {p.valid_from ? `${utc(Date.parse(p.valid_from))}–${utc(Date.parse(p.valid_to))} UTC` : 'pending'}</h3>
        {p.p_new == null ? (
          <p className="muted small">{p.issued ? 'Not modelled: there is no air-raid alert data for this region (occupied).' : 'Waiting for the first forecast.'}</p>
        ) : (
          <table className="facts">
            <tbody>
              <tr>
                <th>New alert starts</th>
                <td>
                  <strong>{pct(p.p_new)}</strong> <span className="muted small">model estimate</span>
                </td>
              </tr>
              <tr>
                <th>Any alert active</th>
                <td>{pct(p.p_active)}</td>
              </tr>
            </tbody>
          </table>
        )}
        {p.degraded?.length > 0 && <div className="callout warn small">Degraded: {p.degraded.join(' · ')}</div>}
      </section>

      {p.drivers?.length > 0 && (
        <section>
          <h3>Why, compared with this region's last 30 days</h3>
          <ul className="links">
            {p.drivers.map((d: { feature: string; label: string; value: number; typical: number; delta_p: number }) => (
              <li key={d.feature}>
                <span className={d.delta_p > 0 ? 'up' : 'down'}>{d.delta_p > 0 ? '▲' : '▼'} {num(Math.abs(d.delta_p) * 100, 0)} pts</span> {d.label}
                <div className="muted small">
                  now {num(d.value, 1)} vs typical {num(d.typical, 1)}
                </div>
              </li>
            ))}
          </ul>
          <p className="muted small">Each row: how much the probability would change if that input were at its 30-day median for this region.</p>
        </section>
      )}

      <section>
        <button className="link" onClick={() => ui.set({ modelCardOpen: true })}>
          Model card: data, time-split test, scores against baselines ↗
        </button>
      </section>
    </>
  )
}
