/**
 * Terrestrial mark: nested rounded triangles read as topographic contours, the brand's own
 * form. Inner contours drift toward the lower left like a slope steepening, so the mark reads
 * as terrain, not a target. Generated, so it stays crisp at 16 px and at slide size.
 */

type Pt = [number, number]

const OUTER: Pt[] = [
  [9, 15],
  [55, 13],
  [31, 55],
]
const RINGS = [
  { k: 1, dx: 0, dy: 0 },
  { k: 0.79, dx: -0.9, dy: 1.1 },
  { k: 0.59, dx: -1.7, dy: 2.1 },
  { k: 0.4, dx: -2.3, dy: 2.9 },
  { k: 0.22, dx: -2.7, dy: 3.4 },
]

function centroid(v: Pt[]): Pt {
  return [(v[0][0] + v[1][0] + v[2][0]) / 3, (v[0][1] + v[1][1] + v[2][1]) / 3]
}

/** Closed path of a triangle with corners rounded by a fraction `t` of each edge. */
function rounded(v: Pt[], t = 0.36): string {
  const lerp = (a: Pt, b: Pt, f: number): Pt => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]
  const f = (p: Pt) => `${p[0].toFixed(2)} ${p[1].toFixed(2)}`
  let d = `M${f(lerp(v[0], v[1], t))}`
  for (let i = 0; i < 3; i++) {
    const a = v[i]
    const b = v[(i + 1) % 3]
    const c = v[(i + 2) % 3]
    d += ` L${f(lerp(a, b, 1 - t))} Q${f(b)} ${f(lerp(b, c, t))}`
  }
  return `${d} Z`
}

export const CONTOURS: string[] = (() => {
  const c = centroid(OUTER)
  return RINGS.map(({ k, dx, dy }) => rounded(OUTER.map(([x, y]) => [c[0] + (x - c[0]) * k + dx, c[1] + (y - c[1]) * k + dy] as Pt)))
})()

export function Logo({ size = 28, stroke = 'currentColor', animate = false }: { size?: number; stroke?: string; animate?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" className={animate ? 'mark draw' : 'mark'}>
      {CONTOURS.map((d, i) => (
        <path
          key={i}
          d={d}
          pathLength={1}
          fill="none"
          stroke={stroke}
          strokeWidth={i === 0 ? 2.1 : 1.7}
          strokeLinejoin="round"
          style={{ ['--i' as string]: i }}
        />
      ))}
    </svg>
  )
}

/** Lower-case serif wordmark, as on the brand board. */
export function Wordmark({ size = 20 }: { size?: number }) {
  return (
    <span className="wordmark-text" style={{ fontSize: size }}>
      terrestrial
    </span>
  )
}
