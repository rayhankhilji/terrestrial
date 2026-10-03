import { duration, num, utc } from '../lib/format'
import { type Flight, type Terminal, trackState } from '../lib/track'
import { useStore } from '../lib/store'
import { altitudeColor } from '../map/liveLayers'

function place(t: Terminal | null) {
  if (!t) return null
  return (
    <span title={`${t.km} km from the airfield reference point`}>
      {t.icao ?? t.ident} {t.name}
      {t.military && <span className="tag">military</span>}
    </span>
  )
}

function legend() {
  return [0, 3000, 8000, 12000].map((m) => {
    const c = altitudeColor(m)
    return (
      <span key={m}>
        <span className="swatch" style={{ background: `rgb(${c[0]},${c[1]},${c[2]})` }} /> {num(m / 0.3048 / 1000)}k ft
      </span>
    )
  })
}

export function FlightHistory({ kind }: { kind: 'aircraft' | 'vessel' }) {
  const { data, error } = useStore(trackState, (s) => s)
  return (
    <section>
      <h3>{kind === 'aircraft' ? 'Flight history' : 'Track history'}</h3>
      {error && <p className="muted small">{error}</p>}
      {!data && !error && <p className="muted small">Loading…</p>}
      {data && (
        <>
          <p className="muted small">
            Observed since {data.first_seen ? utc(data.first_seen, true) : '—'} · {data.points} positions · last {data.hours} h. Only what open
            ADS-B/AIS receivers heard; gaps are coverage, not proof of anything.
          </p>
          {kind === 'aircraft' && <div className="legend">{legend()}</div>}
          <ul className="links">
            {[...data.flights].reverse().map((f: Flight, i) => (
              <li key={f.start}>
                <div>
                  <strong>{i === 0 && !f.landing ? 'Current' : `Leg ${data.flights.length - i}`}</strong> · {utc(f.start, true)} → {i === 0 && !f.landing ? 'now' : utc(f.end, true)} ({duration(f.end - f.start)})
                </div>
                <div className="small">
                  {place(f.origin) ?? <span className="muted">first seen airborne</span>} →{' '}
                  {place(f.landing) ?? <span className="muted">{i === 0 ? 'airborne / in coverage' : 'left coverage'}</span>}
                </div>
                <div className="muted small">{f.points.length} positions</div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
