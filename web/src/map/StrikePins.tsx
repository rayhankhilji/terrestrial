import type { Map as MLMap } from 'maplibre-gl'
import { useEffect, useState } from 'react'
import { type DangerPoint, dangerPoints } from '../lib/danger'
import { select, ui } from '../lib/store'
import { css, weapon } from '../lib/weapons'

interface Pin {
  d: DangerPoint
  x: number
  y: number
}

const MIN_ZOOM = 10
const MAX_PINS = 5

/** Cards pinned over reported danger points once zoomed in (battlefield view): what was reported,
 * by whom, how long ago. Clicking opens the report. */
export function StrikePins({ map }: { map: MLMap | null }) {
  const [pins, setPins] = useState<Pin[]>([])
  useEffect(() => {
    if (!map) return
    let raf = 0
    let last = 0
    const tick = (t: number) => {
      raf = requestAnimationFrame(tick)
      if (t - last < 120) return
      last = t
      if (map.getZoom() < MIN_ZOOM || !ui.get().layers.buildings) {
        setPins((p) => (p.length ? [] : p))
        return
      }
      const c = map.getCenter()
      const canvas = map.getCanvas()
      const w = canvas.clientWidth
      const h = canvas.clientHeight
      const next = dangerPoints()
        .map((d) => {
          const pt = map.project([d.lon, d.lat])
          return { d, x: pt.x, y: pt.y, dist: Math.hypot(d.lon - c.lng, d.lat - c.lat) }
        })
        .filter((p) => p.x > 40 && p.x < w - 40 && p.y > 90 && p.y < h - 60)
        .sort((a, b) => a.dist - b.dist)
        .slice(0, MAX_PINS)
      setPins(next)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [map])

  return (
    <>
      {pins.map(({ d, x, y }) => {
        const w = d.weapon ? weapon(d.weapon) : null
        const mins = Math.max(0, Math.round((Date.now() - d.ts) / 60000))
        return (
          <div key={d.id} className="pin" style={{ left: x, top: y - 8 }} onClick={() => select(d.entity)}>
            <div className="pin-card" style={{ borderColor: w ? css(w.color, 0.6) : 'rgba(255,178,36,0.55)' }}>
              <div className="t">{d.title}</div>
              <div className="s">
                {d.sub} · {mins} min ago
              </div>
            </div>
            <div className="pin-stem" />
          </div>
        )
      })}
    </>
  )
}
