import { Gauge } from 'lucide-react'
import { useMemo } from 'react'
import { type Entity, live, useLive } from '../lib/live'
import { dangerColor } from '../lib/picture'
import { select, ui, useStore } from '../lib/store'
import { hhmm, PanelHead, pct } from './ui'

/** Regions ranked by the model's probability of a new air-raid alert in the current 6-hour block. */
export function DangerPanel() {
  useLive(2000)
  const selected = useStore(ui, (s) => s.selected)
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
  const window = any ? `${hhmm(Date.parse(any.props.valid_from))}–${hhmm(Date.parse(any.props.valid_to))} UTC` : ''
  return (
    <>
      <PanelHead
        icon={Gauge}
        title="Danger forecast"
        sub={any ? `Chance of a new air-raid alert, ${window} · ${active} region${active === 1 ? '' : 's'} on alert now` : 'Waiting for the first forecast'}
      >
        <span className="tag est">model estimate</span>
      </PanelHead>
      <div className="panel-body">
        {(any?.props.degraded?.length ?? 0) > 0 && any && (
          <div className="section">
            <div className="callout warn small">Degraded inputs: {any.props.degraded.join(' · ')}</div>
          </div>
        )}
        {!any && (
          <div className="empty">
            <p>No forecast yet.</p>
            <p>Needs a trained model: uv run python -m predict.strike.train</p>
          </div>
        )}
        <ul className="list">
          {regions.map((r) => {
            const c = dangerColor(r.props.p_new, 255)
            return (
              <li key={r.id} className={`item ${selected === r.id ? 'selected' : ''}`} onClick={() => select(r.id, { lon: r.lon, lat: r.lat, zoom: 6.5 })}>
                <div className="item-main">
                  <div className="row">
                    <span className="item-title grow">{r.label}</span>
                    {r.props.alert_active && <span className="sev sev-high">alert now</span>}
                    <span className="num" style={{ minWidth: 42, textAlign: 'right', fontSize: 'var(--fs-md)' }}>
                      {r.props.p_new == null ? 'n/a' : pct(r.props.p_new)}
                    </span>
                  </div>
                  <div className="bar" style={{ marginTop: 7 }}>
                    <span style={{ width: `${(r.props.p_new ?? 0) * 100}%`, background: `rgb(${c[0]},${c[1]},${c[2]})` }} />
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      </div>
      <div className="panel-foot">
        Gradient-boosted model on every alert since 2022, news-reported attacks, weather and moon; beats climatology and persistence on 2026.{' '}
        <button className="link" onClick={() => ui.set({ modelCardOpen: 'strike' })}>
          Model card
        </button>
      </div>
    </>
  )
}
