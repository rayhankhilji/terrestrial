import { Crosshair, ExternalLink, LocateFixed, Navigation, View } from 'lucide-react'
import { type Entity, live, useLive } from '../lib/live'
import { ago, coord, describe, kindLabel, num, utc } from '../lib/format'
import { AIRFRAME_LABELS, flagEmoji, ROLE_LABELS } from '../lib/picture'
import { select, ui, useStore } from '../lib/store'
import { entityColor } from '../map/liveLayers'
import { AirThreatDetail, FrontDetail, GnssDetail, NewsDetail, PassesHere, SatelliteDetail } from './Details'
import { ForecastChart } from './ForecastChart'
import { Destinations } from './Destinations'
import { ThreatDetail } from './ThreatBoard'
import { FlightHistory } from './FlightHistory'
import { NetDetail } from './NetDetail'
import { RegionDetail } from './RegionDetail'

function pivots(e: Entity): { label: string; href: string }[] {
  const p = e.props
  const links: { label: string; href: string }[] = []
  if (e.kind === 'aircraft') {
    links.push({
      label: 'ADS-B Exchange',
      href: `https://globe.adsbexchange.com/?icao=${p.icao24}`,
    })
    links.push({
      label: 'adsb.lol',
      href: `https://adsb.lol/?icao=${p.icao24}`,
    })
  }
  if (e.kind === 'vessel') {
    links.push({
      label: 'MarineTraffic',
      href: `https://www.marinetraffic.com/en/ais/details/ships/mmsi:${p.mmsi}`,
    })
    if (p.sanctions?.url) links.push({ label: 'OpenSanctions', href: p.sanctions.url })
  }
  if (e.kind === 'facility') links.push({ label: 'Wikidata', href: p.url })
  if (e.kind === 'news') links.push({ label: 'Source article', href: p.url })
  if (e.kind === 'airthreat') links.push({ label: 'Telegram post', href: p.url })
  if (e.kind === 'satellite') links.push({ label: 'CelesTrak', href: `https://celestrak.org/NORAD/elements/gp.php?CATNR=${p.norad}&FORMAT=tle` })
  if (e.kind === 'fire')
    links.push({
      label: 'NASA FIRMS map',
      href: `https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@${e.lon},${e.lat},12z`,
    })
  return links
}

function facts(e: Entity): [string, string][] {
  const p = e.props
  const rows: [string, string][] = [
    ['Position', coord(e.lon, e.lat)],
    ['Observed', `${utc(e.orig_ts ?? e.ts, true)} (${ago(e.orig_ts ?? e.ts)})`],
    ['Source', e.src],
  ]
  if (e.kind === 'aircraft') {
    rows.push(
      ['State', p.state ? `${flagEmoji(p.state_code)} ${p.state}` : '—'],
      ['Organisation', p.org ?? '—'],
      ['Role', `${ROLE_LABELS[p.role] ?? p.role ?? '—'}${p.designation ? ` (${p.designation})` : ''}`],
      ['Airframe', AIRFRAME_LABELS[p.airframe] ?? p.airframe ?? '—'],
      ['Callsign', p.callsign ? `${p.callsign}${p.callsign_family ? ` · family ${p.callsign_family}` : ''}` : '—'],
      ['Registration', p.registration ?? '—'],
      ['ICAO type', p.type ?? '—'],
      ['ICAO 24-bit', String(p.icao24).toUpperCase()],
      ['Altitude', e.alt != null ? `${num(e.alt / 0.3048)} ft` : '—'],
      ['Ground speed', e.spd != null ? `${num(e.spd)} kn` : '—'],
      ['Track', e.hdg != null ? `${num(e.hdg)}°` : '—'],
      ['Squawk', p.squawk ?? '—'],
    )
    if (p.emergency) rows.push(['Emergency', p.emergency])
  }
  if (e.kind === 'vessel') {
    rows.push(
      ['Flag (MMSI)', p.state ? `${flagEmoji(p.state_code)} ${p.state}` : '—'],
      ...(p.military ? ([['Naval role', p.naval_role === 'law_enforcement' ? 'law enforcement' : 'warship']] as [string, string][]) : []),
      ['MMSI', p.mmsi],
      ['IMO', p.imo ?? '—'],
      ['Type', p.ship_type ?? '—'],
      ['Speed', e.spd != null ? `${num(e.spd, 1)} kn` : '—'],
      ['Course', e.hdg != null ? `${num(e.hdg)}°` : '—'],
      ['Destination (self-reported)', p.destination ?? '—'],
      ['Length', p.length_m ? `${p.length_m} m` : '—'],
    )
  }
  if (e.kind === 'fire') {
    rows.push(
      ['Radiative power', `${num(p.frp_mw, 1)} MW`],
      ['Satellite', `${p.satellite} (${p.product})`],
      ['Confidence', p.confidence],
      ['Day/night', p.daynight === 'D' ? 'day' : 'night'],
    )
  }
  if (e.kind === 'news' && !p.outlet) {
    rows.push(
      ['Place', p.place],
      ['Actors', (p.actors ?? []).join(', ') || '—'],
      ['CAMEO codes', (p.codes ?? []).join(', ')],
      ['Goldstein (min)', num(p.goldstein, 1)],
      ['Mentions', num(p.mentions)],
      ['Tone', num(p.tone, 1)],
    )
  }
  if (e.kind === 'facility') {
    rows.push(['Type', p.type], ['Country (Wikidata)', p.country ?? '—'], ['Wikidata', p.qid])
  }
  if (e.kind === 'station') {
    rows.push(
      ['Significant wave height', `${num(p.wave_m, 2)} m`],
      ['Cloud cover', `${num(p.cloud_pct)}%`],
      ['Wind', `${num(p.wind_kmh)} km/h from ${num(p.wind_dir)}°`],
      ['Visibility', p.visibility_m != null ? `${num(p.visibility_m / 1000, 1)} km` : '—'],
    )
  }
  return rows
}

export function Inspector() {
  useLive(400)
  const selected = useStore(ui, (s) => s.selected)
  const follow = useStore(ui, (s) => s.follow)
  const e = selected ? live.entities.get(selected) : undefined
  if (!selected || !e) return null
  const c = entityColor(e)
  const relations = [...live.relations.values()].filter((r) => r.a === e.id || r.b === e.id)
  const alerts = live.alerts.filter((a) => a.entities.includes(e.id)).slice(0, 8)
  const craft = e.kind === 'aircraft' || e.kind === 'vessel'
  const placeable = !['region', 'net', 'sitrep', 'front'].includes(e.kind)

  return (
    <aside className="inspector glass" key={e.id}>
      <header className="insp-head">
        <div className="insp-kind" style={{ color: `rgb(${c[0]},${c[1]},${c[2]})` }}>
          {kindLabel(e)}
        </div>
        <h2>{e.label}</h2>
        <div className="sub">{describe(e)}</div>
        <div className="insp-actions">
          <span className={`prov prov-${e.prov}`}>{e.prov}</span>
          {craft && (
            <button className={`btn ${follow ? 'on' : ''}`} onClick={() => ui.set({ follow: !follow })}>
              <LocateFixed size={14} /> {follow ? 'Following' : 'Follow'}
            </button>
          )}
          <button className="btn" onClick={() => select(e.id, { lon: e.lon, lat: e.lat, zoom: e.kind === 'facility' ? 13 : e.kind === 'region' ? 6.5 : 9 })}>
            <Navigation size={14} /> Fly to
          </button>
          {placeable && (
            <button className="btn" onClick={() => ui.set({ camera: 'battlefield', cameraNonce: Date.now() })} title="Low 3D view over terrain and buildings here">
              <View size={14} /> Battlefield
            </button>
          )}
          {e.kind === 'aircraft' && (
            <button className="btn" onClick={() => ui.set({ camera: 'chase', cameraNonce: Date.now() })} title="Follow at its altitude and heading">
              <Crosshair size={14} /> Chase
            </button>
          )}
        </div>
        <button className="close" onClick={() => select(null)} aria-label="Close">
          ×
        </button>
      </header>
      <div className="insp-scroll">

      {e.props.sanctions && (
        <section className="callout danger">
          <strong>OpenSanctions match ({String(e.props.sanctions.matched_on).toUpperCase()})</strong>
          <div>
            {e.props.sanctions.name}: {e.props.sanctions.topics.join(', ')}
          </div>
          <div className="muted small">{e.props.sanctions.datasets.join(' · ')}</div>
        </section>
      )}

      {e.kind === 'net' && <NetDetail e={e} />}
      {e.kind === 'region' && <RegionDetail e={e} />}
      {e.kind === 'airthreat' && <AirThreatDetail e={e} />}
      {e.kind === 'satellite' && <SatelliteDetail e={e} />}
      {e.kind === 'news' && <NewsDetail e={e} />}
      {e.kind === 'gnss' && <GnssDetail e={e} />}
      {e.kind === 'front' && <FrontDetail e={e} />}

      {e.props.mil_evidence?.length > 0 && (
        <section>
          <h3>Why it is classed military</h3>
          <ul className="evidence">
            {e.props.mil_evidence.map((x: string) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </section>
      )}

      {!['net', 'region', 'airthreat', 'satellite', 'gnss', 'front', 'sitrep'].includes(e.kind) && (
        <section>
          <h3>Facts</h3>
          <table className="facts">
            <tbody>
              {facts(e).map(([k, v]) => (
                <tr key={k}>
                  <th>{k}</th>
                  <td>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {(e.kind === 'aircraft' || e.kind === 'vessel') && <ThreatDetail e={e} />}
      {e.kind === 'aircraft' && !e.props.on_ground && <Destinations />}
      {(e.kind === 'aircraft' || e.kind === 'vessel') && <FlightHistory kind={e.kind} />}
      {['airthreat', 'facility', 'vessel', 'fire', 'news', 'region'].includes(e.kind) && <PassesHere e={e} />}

      {e.kind === 'station' && e.props.forecast && (
        <section>
          <h3>Forecast windows</h3>
          <p className="muted small">{e.props.forecast.kind}. Shaded hours meet the threshold.</p>
          <ForecastChart
            title={`Ship-to-ship feasible (waves < ${e.props.forecast.sts_max_wave_m} m)`}
            times={e.props.forecast.times}
            values={e.props.forecast.wave_h}
            unit="m"
            threshold={e.props.forecast.sts_max_wave_m}
            below
          />
          <ForecastChart
            title={`Optical imagery useful (cloud < ${e.props.forecast.optical_max_cloud_pct}%)`}
            times={e.props.forecast.times}
            values={e.props.forecast.cloud_h}
            unit="%"
            threshold={e.props.forecast.optical_max_cloud_pct}
            below
          />
        </section>
      )}

      {(e.kind === 'aircraft' || e.kind === 'vessel') && e.spd != null && e.hdg != null && (
        <section>
          <h3>Projection</h3>
          <p className="muted small">Model estimate: constant course and speed from the last report.</p>
          <table className="facts">
            <tbody>
              {(e.kind === 'aircraft' ? [5, 10, 20] : [30, 60, 180]).map((m) => {
                const km = (e.spd ?? 0) * 1.852 * (m / 60)
                return (
                  <tr key={m}>
                    <th>+{m} min</th>
                    <td>
                      {num(km)} km along {num(e.hdg)}°
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </section>
      )}

      {relations.length > 0 && (
        <section>
          <h3>Links</h3>
          <ul className="links">
            {relations.map((r) => {
              const other = live.entities.get(r.a === e.id ? r.b : r.a)
              return (
                <li
                  key={r.id}
                  onClick={() =>
                    other &&
                    select(other.id, {
                      lon: other.lon,
                      lat: other.lat,
                      zoom: 10,
                    })
                  }
                >
                  <span className="rel">{r.rel.replaceAll('_', ' ').toLowerCase()}</span> {other?.label ?? r.b}
                  <span className={`prov prov-${r.prov}`}>{r.prov}</span>
                  {r.detail && <div className="muted small">{r.detail}</div>}
                  <div className="why small">derived from: {r.why.join(', ')}</div>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {alerts.length > 0 && (
        <section>
          <h3>Alerts</h3>
          <ul className="links">
            {alerts.map((a) => (
              <li key={a.id}>
                <span className={`sev sev-${a.severity}`}>{a.severity}</span> {a.title}
                <div className="muted small">
                  {ago(a.ts)} · {a.body}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3>{e.kind === 'net' ? 'About nets' : 'Verify outside Terrestrial'}</h3>
        {e.kind === 'net' && (
          <p className="muted small">
            A net is Terrestrial's inference that these craft share a mission, from their observed tracks. It is a candidate grouping, not a confirmed
            tasking. Open each member to verify its track on ADS-B Exchange.
          </p>
        )}
        <div className="pivots small">
          {pivots(e).map((l) => (
            <a key={l.href} href={l.href} target="_blank" rel="noreferrer">
              {l.label} <ExternalLink size={11} />
            </a>
          ))}
        </div>
      </section>
      </div>
    </aside>
  )
}
