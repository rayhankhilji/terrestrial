import { Database, KeyRound } from 'lucide-react'
import { useEffect } from 'react'
import { loadStreams, startFeeds, streams } from '../lib/feeds'
import { useStore } from '../lib/store'
import { PanelHead } from './ui'

const GROUPS: [string, string][] = [
  ['conflict', 'Conflict & threat'],
  ['air', 'Air'],
  ['space', 'Space'],
  ['sea', 'Sea'],
  ['news', 'News'],
  ['weather', 'Weather'],
  ['model', 'Models & AI'],
  ['history', 'History (training data)'],
  ['reference', 'Reference'],
]
const STATE_LABEL: Record<string, string> = {
  ok: 'working',
  error: 'error',
  'needs-key': 'needs key',
  missing: 'missing',
  stale: 'stale',
  starting: 'starting',
  unknown: 'idle',
  disabled: 'off',
}

/** Every stream, honestly: only the ones that work right now count as working. */
export function SourcesPanel() {
  useEffect(startFeeds, [])
  useEffect(() => void loadStreams(), [])
  const { rows, error } = useStore(streams, (s) => s)
  const ok = rows.filter((r) => r.state === 'ok').length
  const keys = [...new Set(rows.filter((r) => r.state === 'needs-key' && r.key).map((r) => r.key as string))]
  return (
    <>
      <PanelHead icon={Database} title="Data streams" sub={`${ok} of ${rows.length} working now`} />
      <div className="panel-body">
        {error && <div className="section"><div className="callout warn">{error}</div></div>}
        {keys.length > 0 && (
          <div className="section">
            <div className="callout small">
              <div className="row" style={{ marginBottom: 4 }}>
                <KeyRound size={14} />
                <strong>Waiting for keys</strong>
              </div>
              Add to <span className="mono">.env</span> in the project folder, then restart the live server:{' '}
              {keys.map((k) => (
                <span key={k} className="mono" style={{ marginRight: 8 }}>
                  {k}
                </span>
              ))}
            </div>
          </div>
        )}
        {GROUPS.map(([g, label]) => {
          const group = rows.filter((r) => r.group === g)
          if (!group.length) return null
          return (
            <div key={g}>
              <div className="section" style={{ paddingBottom: 2 }}>
                <span className="eyebrow">{label}</span>
              </div>
              {group.map((r) => (
                <div key={r.id} className="stream-row" title={r.what}>
                  <span className={`led-dot ${r.state}`} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 560 }}>{r.name}</div>
                    <div className="xs faint ellipsis">
                      {r.provider} · {r.cadence} · {r.licence}
                    </div>
                    {r.detail && <div className="xs muted" style={{ marginTop: 2 }}>{r.detail}</div>}
                  </div>
                  <span className={`tag ${r.state === 'ok' ? 'green' : r.state === 'needs-key' ? '' : r.state === 'error' || r.state === 'missing' ? 'red' : 'amber'}`}>
                    {STATE_LABEL[r.state] ?? r.state}
                  </span>
                </div>
              ))}
            </div>
          )
        })}
      </div>
    </>
  )
}
