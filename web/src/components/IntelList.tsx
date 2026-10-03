import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'

interface VesselRow {
  vessel_id: string
  name: string | null
  flag: string | null
  imo: string | null
  risk: number
  reasons: { reason: string; points: number }[]
}

export function IntelList() {
  const [rows, setRows] = useState<VesselRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<VesselRow[]>('/vessels')
      .then(setRows)
      .catch((e: ApiError | Error) => setError(e.message))
  }, [])

  if (error) {
    return (
      <div className="empty">
        <p>Dark-vessel analysis is not loaded.</p>
        <p className="muted small mono">{error}</p>
        <p className="muted">
          Run the pipeline (<span className="mono">make pipeline</span>, needs a Global Fishing Watch token) and the API (
          <span className="mono">make api</span>).
        </p>
      </div>
    )
  }
  if (!rows) return <div className="empty muted">Loading…</div>
  return (
    <ul className="list">
      {rows.map((v) => (
        <li key={v.vessel_id} className="entity">
          <span className="risk">{v.risk}</span>
          <div className="entity-main">
            <div className="entity-title">{v.name ?? v.vessel_id}</div>
            <div className="entity-sub">{v.reasons[0]?.reason}</div>
          </div>
        </li>
      ))}
    </ul>
  )
}
