import { useMemo } from 'react'
import { utc } from '../lib/format'
import { type Entity, live } from '../lib/live'
import { dangerColor } from '../lib/picture'
import { select, ui } from '../lib/store'

const pct = (p: number | null | undefined) => (p == null ? '—' : p < 0.01 ? '<1%' : p > 0.99 ? '>99%' : `${Math.round(p * 100)}%`)

/** Regions ranked by the model's probability of a new air-raid alert in the current 6-hour block. */
export function DangerPanel() {
  const version = live.version
  const regions = useMemo(
    () =>
      [...live.entities.values()]
        .filter((e) => e.kind === 'region')
        .sort((a, b) => (b.props.p_new ?? -1) - (a.props.p_new ?? -1) || Number(!!b.props.alert_active) - Number(!!a.props.alert_active)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version],
  )
  const any: Entity | undefined = regions.find((r) => r.props.issued)
  const active = regions.filter((r) => r.props.alert_active).length
  return (
    <div className="danger-panel">
      <div className="danger-head">
        {any ? (
          <>
            <div>
              <strong>New air-raid alert, {utc(Date.parse(any.props.valid_from))}–{utc(Date.parse(any.props.valid_to))} UTC</strong>
            </div>
            <div className="muted small">
              Model estimate per region, issued {utc(Date.parse(any.props.issued), true)}. {active} region{active === 1 ? '' : 's'} under alert now.
            </div>
            {any.props.degraded?.length > 0 && <div className="callout warn small">Degraded: {any.props.degraded.join(' · ')}</div>}
            <button className="link" onClick={() => ui.set({ modelCardOpen: true })}>
              Model card: how it was trained and tested ↗
            </button>
          </>
        ) : (
          <div className="muted small">Waiting for the first forecast… (needs a trained model: `uv run python -m predict.strike.train`)</div>
        )}
      </div>
      <ul className="list">
        {regions.map((r) => {
          const c = dangerColor(r.props.p_new, 255)
          return (
            <li key={r.id} className="entity danger-row" onClick={() => select(r.id, { lon: r.lon, lat: r.lat, zoom: 6.5 })}>
              <div className="entity-main">
                <div className="entity-title">
                  {r.label}
                  {r.props.alert_active && <span className="sev sev-high">alert now</span>}
                </div>
                <div className="bar">
                  <span style={{ width: `${(r.props.p_new ?? 0) * 100}%`, background: `rgb(${c[0]},${c[1]},${c[2]})` }} />
                </div>
              </div>
              <span className="mono danger-p">{r.props.p_new == null ? 'n/a' : pct(r.props.p_new)}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
