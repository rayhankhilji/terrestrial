import { duration, num, utc } from '../lib/format'
import { flyTo, ui, useStore } from '../lib/store'
import { DEST_COLORS, type Destination, predState } from '../lib/track'

function pct(p: number) {
  return p < 0.01 ? '<1%' : `${Math.round(p * 100)}%`
}

function Dest({ d, rank }: { d: Destination; rank: number }) {
  const c = DEST_COLORS[rank] ?? DEST_COLORS[2]
  return (
    <li>
      <button className="link" onClick={() => flyTo({ lon: d.lon, lat: d.lat, zoom: 9, pitch: 45 })}>
        <span className="swatch" style={{ background: `rgb(${c[0]},${c[1]},${c[2]})` }} /> <strong>{d.icao ?? d.ident}</strong> {d.name}
      </button>
      {d.military && <span className="tag">military</span>}
      <div className="pbar" title={`model probability ${pct(d.p)}`}>
        <span style={{ width: `${Math.max(2, d.p * 100)}%`, background: `rgb(${c[0]},${c[1]},${c[2]})` }} />
        <em>{pct(d.p)}</em>
      </div>
      <div className="muted small">
        {num(d.dist_km)} km on {String(d.course).padStart(3, '0')}° · ETA ~{num(d.eta_min)} min at {num(d.gs_route_kn)} kn ground speed ({d.country})
      </div>
    </li>
  )
}

/** Predicted landing for the selected aircraft: destinations, ETA, endurance, re-routes. */
export function Destinations() {
  const { data, error } = useStore(predState, (s) => s)
  return (
    <section>
      <h3>
        Predicted landing <span className="tag">model estimate</span>
      </h3>
      {error && <p className="muted small">{error}</p>}
      {!data && !error && <p className="muted small">Scoring candidate airfields…</p>}
      {data && (
        <>
          {data.on_station && (
            <p className="callout small">
              Orbiting: turned through more than 540° in 15 minutes with little net progress. Consistent with an ISR or refuelling station; it will
              leave for one of these fields when its time on station ends.
            </p>
          )}
          <ol className="dests">
            {data.destinations.map((d, i) => (
              <Dest key={d.ident} d={d} rank={i} />
            ))}
          </ol>
          <div className="endurance" title="Time airborne in this flight vs the 95th percentile flight duration of this type in the archive">
            <div className="muted small">
              Airborne {duration(data.elapsed_min * 60_000)}
              {data.origin ? ` from ${data.origin}` : ' (first seen airborne: a lower bound)'} · type endurance estimate ~{duration(data.endurance_min * 60_000)}
            </div>
            <div className="pbar">
              <span
                style={{
                  width: `${Math.min(100, (data.elapsed_min / Math.max(data.endurance_min, 1)) * 100)}%`,
                  background: data.elapsed_min > data.endurance_min * 0.85 ? '#f97316' : '#64748b',
                }}
              />
              <em>{Math.round((data.elapsed_min / Math.max(data.endurance_min, 1)) * 100)}%</em>
            </div>
          </div>
          {data.changes.length > 0 && (
            <>
              <h4>Re-routes</h4>
              <ul className="small">
                {data.changes.slice(0, 5).map((c) => (
                  <li key={c.at}>
                    {utc(c.at)} most likely field {c.from} ({pct(c.p_from)}) → {c.to} ({pct(c.p_to)})
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="muted small">
            {data.candidates} candidate airfields scored at {utc(data.at)} UTC.{' '}
            {data.winds
              ? `ETA uses forecast winds at ${data.winds.level_hpa} hPa (${num(data.winds.speed_kn)} kn from ${num(data.winds.from_deg)}°, Open-Meteo).`
              : 'ETA from current ground speed (winds aloft unavailable).'}{' '}
            Fuel is never observed; endurance is a type statistic. Carriers are candidates only if observed live.{' '}
            <button className="link" onClick={() => ui.set({ modelCardOpen: 'flight' })}>
              Model card
            </button>
            {data.model.beats_baselines === false && <strong className="warn"> This model does not beat the simple baselines.</strong>}
          </p>
        </>
      )}
    </section>
  )
}
