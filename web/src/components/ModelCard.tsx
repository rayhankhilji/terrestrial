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

/** The flight-destination model's card, verbatim from /live/models/flight. */
export function FlightModelCard() {
  const [c, setCard] = useState<Card | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    fetch('/live/models/flight')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setCard)
      .catch((err) => setError(String(err)))
  }, [])
  const close = () => ui.set({ modelCardOpen: false })
  return (
    <div className="modal" onClick={close} onKeyDown={(ev) => ev.key === 'Escape' && close()}>
      <div className="card-shell" onClick={(ev) => ev.stopPropagation()}>
        <button className="close" onClick={close} aria-label="Close">
          ×
        </button>
        <h2>Flight destination model</h2>
        {error && <div className="callout danger">{error}</div>}
        {c && (
          <section className="card">
            <h3>
              {c.name} <span className="muted small">v{c.version}</span>
            </h3>
            <p>{c.target}</p>
            <p className={c.beats_baselines ? 'ok' : 'warn'}>
              {c.beats_baselines
                ? 'Beats every baseline on top-1 and top-3 accuracy for held-out flights.'
                : 'Does NOT beat every baseline on held-out flights: treat with caution.'}
            </p>
            <table className="facts scores">
              <thead>
                <tr>
                  <th>Held-out flights ({c.split.test})</th>
                  <th>Top-1 ↑</th>
                  <th>Top-3 ↑</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(c.test_scores as Record<string, { top1: number; top3: number }>).map(([name, s]) => (
                  <tr key={name}>
                    <th>{name}</th>
                    <td className="mono">{(s.top1 * 100).toFixed(1)}%</td>
                    <td className="mono">{(s.top3 * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h4>By time to landing (model)</h4>
            <table className="facts scores">
              <tbody>
                {Object.entries(c.test_scores.model.by_time_to_landing as Record<string, { n: number; top1: number; top3: number }>).map(([k, s]) => (
                  <tr key={k}>
                    <th>{k}</th>
                    <td className="mono">n={s.n}</td>
                    <td className="mono">top-1 {(s.top1 * 100).toFixed(0)}%</td>
                    <td className="mono">top-3 {(s.top3 * 100).toFixed(0)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted small">
              Snapshots every 5 minutes of each test flight. The true airfield was among the scored candidates in {(c.candidate_coverage_test * 100).toFixed(0)}% of
              snapshots (the ceiling for any ranker). Flights: train {c.rows.train_flights}, test {c.rows.test_flights}.
            </p>
            <h4>What it relies on (permutation importance)</h4>
            <ul className="small">
              {(c.permutation_importance_test as { feature: string; ap_drop: number }[]).slice(0, 8).map((f) => (
                <li key={f.feature}>
                  <span className="mono">{f.feature}</span> — average precision drops {f.ap_drop.toFixed(3)} when shuffled
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
                <strong>algorithm</strong>: {c.algorithm}
              </li>
            </ul>
            <h4>Limitations</h4>
            <ul className="small">
              {(c.limitations as string[]).map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  )
}
