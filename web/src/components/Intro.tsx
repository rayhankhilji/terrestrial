import { useEffect, useState } from 'react'
import { ui } from '../lib/store'
import { Logo, Wordmark } from './Logo'

/** A field of topographic contour lines (deterministic, so it is the same every visit). */
const FIELD: string[] = (() => {
  const lines: string[] = []
  for (let i = 0; i < 26; i++) {
    const y0 = 40 + i * 34
    let d = ''
    for (let x = -20; x <= 1620; x += 20) {
      const y =
        y0 +
        26 * Math.sin(x / 210 + i * 0.37) +
        14 * Math.sin(x / 97 - i * 0.61) +
        40 * Math.exp(-(((x - 980) / 260) ** 2)) * Math.sin(i / 3.2)
      d += `${d ? ' L' : 'M'}${x} ${y.toFixed(1)}`
    }
    lines.push(d)
  }
  return lines
})()

const HOLD_MS = 2300

/** Brand intro: the mark draws as contours, the wordmark rises, then the globe flies to Ukraine. */
export function Intro() {
  const reduced = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
  const [out, setOut] = useState(false)
  const [gone, setGone] = useState(false)
  useEffect(() => {
    const t1 = setTimeout(() => {
      setOut(true)
      ui.set({ camera: 'theatre', cameraNonce: Date.now() })
    }, reduced ? 300 : HOLD_MS)
    const t2 = setTimeout(() => setGone(true), (reduced ? 300 : HOLD_MS) + 800)
    return () => {
      clearTimeout(t1)
      clearTimeout(t2)
    }
  }, [reduced])
  if (gone) return null
  return (
    <div className={`intro ${out ? 'out' : ''}`} onClick={() => setOut(true)} aria-hidden="true">
      <svg className="intro-contours" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice">
        {FIELD.map((d, i) => (
          <path key={i} d={d} pathLength={1} style={{ ['--i' as string]: i }} />
        ))}
      </svg>
      <div>
        <div className="intro-lockup">
          <Logo size={88} animate />
          <Wordmark size={76} />
        </div>
        <div className="intro-line">Open predictive defence picture · Ukraine</div>
      </div>
    </div>
  )
}
