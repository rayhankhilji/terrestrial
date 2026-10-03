import type { Entity } from '../lib/live'
import { describe, kindLabel } from '../lib/format'

export function Tooltip({ x, y, entity }: { x: number; y: number; entity: Entity }) {
  return (
    <div className="tooltip" style={{ left: x + 14, top: y + 14 }}>
      <div className="tooltip-kind">{kindLabel(entity)}</div>
      <div className="tooltip-title">{entity.label}</div>
      <div className="tooltip-sub">{describe(entity)}</div>
    </div>
  )
}
