import { useEffect, useState } from 'react'
import { ui } from '../lib/store'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Card = Record<string, any>

/** The danger models' cards, shown verbatim from /live/models/strike. */
export function ModelCard() {
  const [cards, setCards] = useState<Record<string, Card> | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    fetch('/live/models/strike')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setCards)
      .catch((err) => setError(String(err)))
  }, [])
  const close = () => ui.set({ modelCardOpen: false })
  return (
    <div className="modal" onClick={close} onKeyDown={(ev) => ev.key === 'Escape' && close()}>
      <div className="card-shell" onClick={(ev) => ev.stopPropagation()}>
        <button className="close" onClick={close} aria-label="Close">
          ×
        </button>
        <h2>Danger models</h2>
        {error && <div className="callout danger">{error}</div>}
        {cards && Object.keys(cards).length === 0 && <p className="muted">No trained model. Run `uv run python -m predict.strike.train`.</p>}
        {cards &&
          Object.entries(cards).map(([target, c]) => (
            <section key={target} className="card">
              <h3>
                {c.name} <span className="muted small">v{c.version}</span>
              </h3>
              <p>{c.target}</p>
              <p className={c.beats_baselines ? 'ok' : 'warn'}>
                {c.beats_baselines ? 'Beats both baselines on the held-out test period.' : 'Does NOT beat the baselines on the test period: treat with caution.'}
              </p>
              <table className="facts scores">
                <thead>
                  <tr>
                    <th>Test period ({c.split.test})</th>
                    <th>Brier ↓</th>
                    <th>ROC AUC ↑</th>
                    <th>PR AUC ↑</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(c.test_scores as Record<string, Record<string, number>>).map(([name, s]) => (
                    <tr key={name}>
                      <th>{name}</th>
                      <td className="mono">{s.brier.toFixed(3)}</td>
                      <td className="mono">{s.roc_auc.toFixed(3)}</td>
                      <td className="mono">{s.pr_auc.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small">
                Brier skill vs climatology {(c.brier_skill_vs.climatology * 100).toFixed(0)}%, vs persistence {(c.brier_skill_vs.persistence * 100).toFixed(0)}%. Test base rate{' '}
                {(c.test_base_rate * 100).toFixed(0)}%. Rows: train {c.rows.train.toLocaleString()}, calibration {c.rows.calibration.toLocaleString()}, test{' '}
                {c.rows.test.toLocaleString()}.
              </p>
              <h4>Calibration on the test period</h4>
              <div className="reliability">
                {(c.reliability_test as { bin: string; n: number; predicted: number; observed: number }[]).map((r) => (
                  <div key={r.bin} title={`${r.bin}: n=${r.n}, predicted ${r.predicted}, observed ${r.observed}`}>
                    <span className="rel-obs" style={{ height: `${r.observed * 100}%` }} />
                    <span className="rel-pred" style={{ bottom: `${r.predicted * 100}%` }} />
                  </div>
                ))}
              </div>
              <p className="muted small">Bars: observed frequency per predicted-probability bin; ticks: mean predicted. Equal heights = well calibrated.</p>
              <h4>What it relies on (permutation importance, test period)</h4>
              <ul className="small">
                {(c.permutation_importance_test as { feature: string; roc_auc_drop: number }[]).slice(0, 8).map((f) => (
                  <li key={f.feature}>
                    <span className="mono">{f.feature}</span> — AUC drops {f.roc_auc_drop.toFixed(3)} when shuffled
                  </li>
                ))}
              </ul>
              <h4>Data</h4>
              <ul className="small">
                {Object.entries(c.data as Record<string, string>).map(([k, v]) => (
                  <li key={k}>
                    <strong>{k}</strong>: {v}
                  </li>
                ))}
                <li>
                  <strong>split</strong>: train {c.split.train}; calibration {c.split.calibration}; test {c.split.test}
                </li>
                <li>
                  <strong>excluded</strong>: {c.excluded_regions}
                </li>
              </ul>
              <h4>Limitations</h4>
              <ul className="small">
                {(c.limitations as string[]).map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            </section>
          ))}
      </div>
    </div>
  )
}
