import { Rocket, ShieldAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { live, useLive } from '../lib/live'
import { select } from '../lib/store'
import { weapon } from '../lib/weapons'

interface Toast {
  id: string
  title: string
  body: string
  tone: 'red' | 'amber' | 'violet'
  lon?: number
  lat?: number
  target?: string
  at: number
}

const TTL_MS = 9000

/** Pop the events that matter: missiles and ballistic threats, high-severity alerts. */
export function Toasts() {
  useLive(800)
  const seen = useRef<Set<string>>(new Set())
  const since = useRef(Date.now())
  const [toasts, setToasts] = useState<Toast[]>([])

  useEffect(() => {
    const fresh: Toast[] = []
    for (const e of live.entities.values()) {
      if (e.kind !== 'airthreat' || e.props.tally || seen.current.has(e.id)) continue
      seen.current.add(e.id)
      const missile = ['ballistic', 'cruise_missile', 'missile'].includes(e.props.weapon)
      if (missile && e.ts > since.current) {
        fresh.push({ id: e.id, title: weapon(e.props.weapon).label, body: e.label, tone: 'red', lon: e.lon, lat: e.lat, target: e.id, at: Date.now() })
      }
    }
    for (const a of live.alerts) {
      if (seen.current.has(a.id)) continue
      seen.current.add(a.id)
      if (a.severity === 'high' && a.ts > since.current) {
        fresh.push({ id: a.id, title: a.title, body: a.body, tone: 'amber', lon: a.lon ?? undefined, lat: a.lat ?? undefined, target: a.entities[0], at: Date.now() })
      }
    }
    // Only what arrives after the page opened pops: the backlog is in the panels.
    if (fresh.length) setToasts((t) => [...fresh, ...t].slice(0, 3))
  })

  useEffect(() => {
    if (!toasts.length) return
    const t = setTimeout(() => setToasts((ts) => ts.filter((x) => Date.now() - x.at < TTL_MS)), 1000)
    return () => clearTimeout(t)
  }, [toasts])

  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`toast glass ${t.tone}`}
          onClick={() => {
            if (t.lon != null && t.lat != null) select(t.target ?? null, { lon: t.lon, lat: t.lat, zoom: 7 })
            setToasts((ts) => ts.filter((x) => x.id !== t.id))
          }}
        >
          <span className={`glyph ${t.tone}`}>{t.tone === 'red' ? <Rocket size={16} /> : <ShieldAlert size={16} />}</span>
          <div>
            <div className="toast-title">{t.title}</div>
            <div className="toast-body">{t.body}</div>
          </div>
        </div>
      ))}
    </div>
  )
}
