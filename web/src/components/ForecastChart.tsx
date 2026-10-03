/** Tiny SVG bar chart of an hourly forecast with the hours meeting a threshold highlighted. */
export function ForecastChart({
  title,
  times,
  values,
  unit,
  threshold,
  below,
}: {
  title: string
  times: string[]
  values: (number | null)[]
  unit: string
  threshold: number
  below?: boolean
}) {
  const W = 320
  const H = 70
  const max = Math.max(threshold * 1.6, ...values.map((v) => v ?? 0))
  const bw = W / Math.max(1, values.length)
  const ok = (v: number | null) => v != null && (below ? v < threshold : v >= threshold)
  const firstOk = values.findIndex(ok)
  return (
    <figure className="chart">
      <figcaption>
        {title}
        <span className="muted small">
          {firstOk === -1 ? ' · no window in 48 h' : firstOk === 0 ? ' · window open now' : ` · next window ${times[firstOk].slice(11, 16)}Z ${times[firstOk].slice(5, 10)}`}
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H + 14}`} role="img" aria-label={title}>
        {values.map((v, i) => {
          const h = v == null ? 0 : (v / max) * H
          return <rect key={i} x={i * bw + 0.5} y={H - h} width={Math.max(1, bw - 1)} height={h} className={ok(v) ? 'bar ok' : 'bar'} />
        })}
        <line x1={0} x2={W} y1={H - (threshold / max) * H} y2={H - (threshold / max) * H} className="threshold" />
        <text x={W - 2} y={H - (threshold / max) * H - 3} textAnchor="end" className="axis">
          {threshold}
          {unit}
        </text>
        {times.map((t, i) =>
          t.endsWith('00:00') ? (
            <text key={t} x={i * bw} y={H + 12} className="axis">
              {t.slice(5, 10)}
            </text>
          ) : null,
        )}
      </svg>
    </figure>
  )
}
