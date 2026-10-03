export interface HoverInfo {
  x: number
  y: number
  kind: string
  title: string
  sub: string
}

export function Tooltip({ x, y, kind, title, sub }: HoverInfo) {
  return (
    <div className="tooltip" style={{ left: x + 14, top: y + 14 }}>
      <div className="tooltip-kind">{kind}</div>
      <div className="tooltip-title">{title}</div>
      <div className="tooltip-sub">{sub}</div>
    </div>
  )
}
