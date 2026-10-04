import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'

export function PanelHead({ icon: Icon, title, sub, children }: { icon?: LucideIcon; title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="panel-head">
      {Icon && (
        <span className="glyph gold">
          <Icon size={17} strokeWidth={1.8} />
        </span>
      )}
      <div className="grow">
        <h2>{title}</h2>
        {sub && <div className="sub">{sub}</div>}
      </div>
      {children}
    </div>
  )
}

/** Relative time that keeps itself fresh ("12 s", "4 min", "2 h"). */
export function Ago({ ts, suffix = ' ago' }: { ts: number; suffix?: string }) {
  const [, tick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 15_000)
    return () => clearInterval(t)
  }, [])
  const s = Math.max(0, (Date.now() - ts) / 1000)
  const text = s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.floor(s / 3600)} h` : `${Math.floor(s / 86400)} d`
  return (
    <time dateTime={new Date(ts).toISOString()} title={new Date(ts).toUTCString()}>
      {text}
      {suffix}
    </time>
  )
}

export function pct(p: number | null | undefined): string {
  if (p == null) return '—'
  if (p < 0.01) return '<1%'
  if (p > 0.99) return '>99%'
  return `${Math.round(p * 100)}%`
}

export function hhmm(ts: number): string {
  const d = new Date(ts)
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`
}

/** Text with [F3] / [F3, F7] citations rendered as clickable chips. */
export function Cited({ text, onCite }: { text: string; onCite?: (id: string) => void }) {
  const parts = text.split(/(\[F\d+(?:\s*,\s*F\d+)*\])/g)
  return (
    <>
      {parts.map((part, i) => {
        const m = part.match(/^\[(.*)\]$/)
        if (!m) return <span key={i}>{part}</span>
        return m[1].split(',').map((id) => (
          <button key={`${i}-${id}`} className="cite" onClick={() => onCite?.(id.trim())}>
            {id.trim()}
          </button>
        ))
      })}
    </>
  )
}
