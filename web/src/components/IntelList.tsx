import { Ship } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { PanelHead } from './ui'

interface VesselRow {
  vessel_id: string
  name: string | null
  flag: string | null
  imo: string | null
  risk: number
  reasons: { reason: string; points: number }[]
}

function riskTone(r: number) {
  return r >= 60 ? 'red' : r >= 30 ? 'amber' : ''
}

/** Dark-vessel ranking from the maritime pipeline (GFW gaps, SAR, sanctions). */
export function IntelList() {
  const [rows, setRows] = useState<VesselRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<VesselRow[]>('/vessels')
      .then(setRows)
      .catch((e: ApiError | Error) => setError(e.message))
  }, [])

  return (
    <>
      <PanelHead icon={Ship} title="Dark vessels" sub="Black Sea hulls ranked by a transparent heuristic: AIS gaps, radar matches, sanctions, occupied-port calls">
        <span className="tag est">heuristic</span>
      </PanelHead>
      <div className="panel-body">
        {error && (
          <div className="empty">
            <p>Dark-vessel analysis is not loaded.</p>
            <p className="mono">{error}</p>
            <p>
              Add a Global Fishing Watch token (<span className="mono">GFW_API_TOKEN</span>), then run <span className="mono">make pipeline</span> and{' '}
              <span className="mono">make api</span>.
            </p>
          </div>
        )}
        {!rows && !error && <div className="empty"><p>Loading…</p></div>}
        <ul className="list">
          {rows?.map((v) => (
            <li key={v.vessel_id} className="item">
              <span className={`prio`} style={{ color: `var(--${riskTone(v.risk) === 'red' ? 'red' : riskTone(v.risk) === 'amber' ? 'amber' : 'text-2'})` }}>
                {v.risk}
              </span>
              <div className="item-main">
                <div className="item-title">{v.name ?? v.vessel_id}</div>
                <div className="item-sub">{v.reasons[0]?.reason}</div>
                <div className="item-meta">
                  {v.flag && <span>{v.flag}</span>}
                  {v.imo && <span>IMO {v.imo}</span>}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </>
  )
}
