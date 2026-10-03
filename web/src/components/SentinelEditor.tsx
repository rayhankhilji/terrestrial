import { useEffect, useMemo, useRef, useState } from 'react'
import { live, type Sentinel, type SentinelNode, useLive } from '../lib/live'
import { ui } from '../lib/store'

const NODE_W = 230
const AOIS = ['Sevastopol', 'Feodosia', 'Kerch', 'Berdyansk', 'Mariupol', 'Port Kavkaz', 'Novorossiysk']
const FIELDS = ['props.military', 'props.uav', 'spd', 'alt', 'hdg', 'label', 'props.type', 'props.ship_type', 'props.callsign', 'props.destination', 'props.squawk', 'props.frp_mw', 'props.goldstein']

const PALETTE: { type: SentinelNode['type']; label: string; params: SentinelNode['params'] }[] = [
  { type: 'area', label: 'In area', params: { area: 'occupied' } },
  { type: 'near', label: 'Near', params: { target: 'facility', facility_type: 'refinery', km: 10 } },
  { type: 'compare', label: 'Compare', params: { field: 'spd', op: '<', value: 2 } },
  { type: 'listed', label: 'Sanctions-listed', params: {} },
  { type: 'and', label: 'AND', params: {} },
  { type: 'or', label: 'OR', params: {} },
  { type: 'alert', label: 'Alert', params: { severity: 'warn', title: '{label} matched', cooldown_min: 30 } },
]

const TITLES: Record<SentinelNode['type'], string> = {
  source: 'Source',
  area: 'In area',
  near: 'Near',
  compare: 'Compare',
  listed: 'Sanctions-listed',
  and: 'AND',
  or: 'OR',
  alert: 'Alert',
}

function parseValue(text: string): unknown {
  if (text === 'true') return true
  if (text === 'false') return false
  if (text.trim() !== '' && !Number.isNaN(Number(text))) return Number(text)
  return text
}

function blank(): Sentinel {
  const id = `sentinel-${Date.now().toString(36)}`
  return {
    id,
    name: 'New sentinel',
    enabled: true,
    nodes: [
      { id: 's', type: 'source', x: 40, y: 120, params: { kind: 'vessel' } },
      { id: 'a', type: 'alert', x: 620, y: 120, params: { severity: 'warn', title: '{label} matched', cooldown_min: 30 } },
    ],
    edges: [['s', 'a']],
  }
}

export function SentinelEditor() {
  useLive(250)
  const [draft, setDraft] = useState<Sentinel | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const sentinels = live.sentinels

  useEffect(() => {
    if (!draft && sentinels.length) setDraft(structuredClone(sentinels[0]))
  }, [draft, sentinels])

  const close = () => ui.set({ sentinelsOpen: false })

  async function save(s: Sentinel) {
    setError(null)
    const res = await fetch(`/live/sentinels/${encodeURIComponent(s.id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      setError(typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`)
      return
    }
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }

  async function remove(s: Sentinel) {
    const res = await fetch(`/live/sentinels/${encodeURIComponent(s.id)}`, { method: 'DELETE' })
    if (!res.ok && res.status !== 404) setError(`HTTP ${res.status}`)
    setDraft(null)
  }

  return (
    <div className="modal" onKeyDown={(ev) => ev.key === 'Escape' && close()}>
      <div className="sentinel-shell">
        <aside className="sentinel-list">
          <div className="sl-head">
            <h2>Sentinels</h2>
            <button className="close" onClick={close} aria-label="Close">
              ×
            </button>
          </div>
          <p className="muted small">Programmable rules evaluated on every live update. Wire a source through filters into an alert.</p>
          <ul>
            {sentinels.map((s) => {
              const hits = Object.values(live.sentinelHits[s.id] ?? {})
              const alerts = live.alerts.filter((a) => a.sentinel === s.id).length
              return (
                <li key={s.id} className={draft?.id === s.id ? 'on' : ''} onClick={() => setDraft(structuredClone(s))}>
                  <span className={`led ${s.enabled ? 'led-on' : ''}`} />
                  <div>
                    <div>{s.name}</div>
                    <div className="muted small">
                      {hits.length ? Math.max(...hits) : 0} evaluations passed · {alerts} alerts
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
          <button className="accent wide" onClick={() => setDraft(blank())}>
            + New sentinel
          </button>
        </aside>
        {draft ? (
          <Canvas key={draft.id} draft={draft} setDraft={setDraft} onSave={save} onDelete={remove} error={error} saved={saved} />
        ) : (
          <div className="empty">Select or create a sentinel.</div>
        )}
      </div>
    </div>
  )
}

function Canvas({
  draft,
  setDraft,
  onSave,
  onDelete,
  error,
  saved,
}: {
  draft: Sentinel
  setDraft: (s: Sentinel) => void
  onSave: (s: Sentinel) => void
  onDelete: (s: Sentinel) => void
  error: string | null
  saved: boolean
}) {
  const area = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<{ id: string; dx: number; dy: number } | null>(null)
  const [wire, setWire] = useState<{ from: string; x: number; y: number } | null>(null)
  const [heights, setHeights] = useState<Record<string, number>>({})
  const hits = live.sentinelHits[draft.id] ?? {}
  const prevHits = useRef<Record<string, number>>({})
  const [flash, setFlash] = useState<Record<string, number>>({})

  useEffect(() => {
    const now = Date.now()
    const next: Record<string, number> = {}
    for (const [id, n] of Object.entries(hits)) if ((prevHits.current[id] ?? 0) < n) next[id] = now
    prevHits.current = { ...hits }
    if (Object.keys(next).length) setFlash((f) => ({ ...f, ...next }))
  }, [hits])

  const byId = useMemo(() => Object.fromEntries(draft.nodes.map((n) => [n.id, n])), [draft.nodes])
  const update = (patch: Partial<Sentinel>) => setDraft({ ...draft, ...patch })
  const updateNode = (id: string, patch: Partial<SentinelNode>) =>
    update({ nodes: draft.nodes.map((n) => (n.id === id ? { ...n, ...patch, params: { ...n.params, ...(patch.params ?? {}) } } : n)) })
  const port = (n: SentinelNode, side: 'in' | 'out') => ({
    x: (n.x ?? 0) + (side === 'out' ? NODE_W : 0),
    y: (n.y ?? 0) + (heights[n.id] ?? 90) / 2,
  })
  const local = (ev: React.PointerEvent) => {
    const r = area.current!.getBoundingClientRect()
    return { x: ev.clientX - r.left + area.current!.scrollLeft, y: ev.clientY - r.top + area.current!.scrollTop }
  }

  function addNode(p: (typeof PALETTE)[number]) {
    const id = `${p.type}-${Math.random().toString(36).slice(2, 6)}`
    update({ nodes: [...draft.nodes, { id, type: p.type, x: 320, y: 40 + draft.nodes.length * 30, params: { ...p.params } }] })
  }

  function connect(to: string) {
    if (!wire || wire.from === to) return setWire(null)
    if (!draft.edges.some(([a, b]) => a === wire.from && b === to)) update({ edges: [...draft.edges, [wire.from, to]] })
    setWire(null)
  }

  const now = Date.now()
  return (
    <section className="sentinel-main">
      <div className="sm-toolbar">
        <input className="name" value={draft.name} onChange={(ev) => update({ name: ev.target.value })} />
        <label className="switch">
          <input type="checkbox" checked={draft.enabled} onChange={(ev) => update({ enabled: ev.target.checked })} /> enabled
        </label>
        <div className="palette">
          {PALETTE.map((p) => (
            <button key={p.type} onClick={() => addNode(p)}>
              + {p.label}
            </button>
          ))}
        </div>
        <button className="accent" onClick={() => onSave(draft)}>
          {saved ? 'Saved ✓' : 'Save & deploy'}
        </button>
        <button className="danger" onClick={() => onDelete(draft)}>
          Delete
        </button>
      </div>
      {error && <div className="callout danger">{error}</div>}
      <div
        ref={area}
        className="graph-area"
        onPointerMove={(ev) => {
          const p = local(ev)
          if (drag) updateNode(drag.id, { x: Math.max(0, p.x - drag.dx), y: Math.max(0, p.y - drag.dy) })
          if (wire) setWire({ ...wire, x: p.x, y: p.y })
        }}
        onPointerUp={() => {
          setDrag(null)
          setWire(null)
        }}
      >
        <svg className="wires">
          {draft.edges.map(([a, b]) => {
            const na = byId[a]
            const nb = byId[b]
            if (!na || !nb) return null
            const p1 = port(na, 'out')
            const p2 = port(nb, 'in')
            const dx = Math.max(60, (p2.x - p1.x) / 2)
            const hot = now - (flash[a] ?? 0) < 700 && now - (flash[b] ?? 0) < 900
            return (
              <path
                key={`${a}-${b}`}
                d={`M${p1.x},${p1.y} C${p1.x + dx},${p1.y} ${p2.x - dx},${p2.y} ${p2.x},${p2.y}`}
                className={`wire ${hot ? 'hot' : ''}`}
                onClick={() => update({ edges: draft.edges.filter(([x, y]) => !(x === a && y === b)) })}
              >
                <title>Click to delete this wire</title>
              </path>
            )
          })}
          {wire && byId[wire.from] && (
            <path d={`M${port(byId[wire.from], 'out').x},${port(byId[wire.from], 'out').y} L${wire.x},${wire.y}`} className="wire pending" />
          )}
        </svg>
        {draft.nodes.map((n) => (
          <div
            key={n.id}
            className={`gnode gnode-${n.type} ${now - (flash[n.id] ?? 0) < 600 ? 'flash' : ''}`}
            style={{ left: n.x, top: n.y, width: NODE_W }}
            ref={(el) => {
              if (el && heights[n.id] !== el.offsetHeight) setHeights((h) => ({ ...h, [n.id]: el.offsetHeight }))
            }}
          >
            <div
              className="gnode-head"
              onPointerDown={(ev) => {
                const p = local(ev)
                setDrag({ id: n.id, dx: p.x - (n.x ?? 0), dy: p.y - (n.y ?? 0) })
              }}
            >
              <span>{TITLES[n.type]}</span>
              <span className="hits mono" title="evaluations that passed this node">
                {hits[n.id] ?? 0}
              </span>
              {n.type !== 'source' && (
                <button
                  className="x"
                  onPointerDown={(ev) => ev.stopPropagation()}
                  onClick={() => update({ nodes: draft.nodes.filter((m) => m.id !== n.id), edges: draft.edges.filter(([a, b]) => a !== n.id && b !== n.id) })}
                >
                  ×
                </button>
              )}
            </div>
            <NodeParams node={n} onChange={(params) => updateNode(n.id, { params })} />
            {n.type !== 'source' && <span className="port in" onPointerUp={() => connect(n.id)} title="input" />}
            {n.type !== 'alert' && (
              <span
                className="port out"
                title="drag to connect"
                onPointerDown={(ev) => {
                  ev.stopPropagation()
                  setWire({ from: n.id, ...local(ev) })
                }}
              />
            )}
          </div>
        ))}
      </div>
      <p className="muted small hint">
        Drag node headers to move · drag from a right-hand port to a left-hand port to wire · click a wire to remove it · counters show evaluations passing each node, live.
      </p>
    </section>
  )
}

function NodeParams({ node, onChange }: { node: SentinelNode; onChange: (p: SentinelNode['params']) => void }) {
  const p = node.params
  switch (node.type) {
    case 'source':
      return (
        <div className="gnode-body">
          <select value={p.kind} onChange={(e) => onChange({ kind: e.target.value })}>
            {['vessel', 'aircraft', 'fire', 'news', 'station'].map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </div>
      )
    case 'area':
      return (
        <div className="gnode-body">
          <select value={p.area} onChange={(e) => onChange({ area: e.target.value })}>
            <option value="occupied">any occupied-UA port</option>
            <option value="any_aoi">any monitored port</option>
            {AOIS.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </div>
      )
    case 'near':
      return (
        <div className="gnode-body">
          <select value={p.target} onChange={(e) => onChange({ target: e.target.value })}>
            <option value="facility">facility</option>
            <option value="fire">thermal anomaly (24 h)</option>
            <option value="news">news event (24 h)</option>
          </select>
          {p.target === 'facility' && (
            <select value={p.facility_type ?? ''} onChange={(e) => onChange({ facility_type: e.target.value || undefined })}>
              <option value="">any type</option>
              {['refinery', 'port', 'naval base', 'airbase'].map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          )}
          <label>
            within <input type="number" min={0.5} step={0.5} value={p.km} onChange={(e) => onChange({ km: Number(e.target.value) })} /> km
          </label>
        </div>
      )
    case 'compare':
      return (
        <div className="gnode-body">
          <input list="sentinel-fields" value={p.field} onChange={(e) => onChange({ field: e.target.value })} />
          <datalist id="sentinel-fields">
            {FIELDS.map((f) => (
              <option key={f} value={f} />
            ))}
          </datalist>
          <div className="row">
            <select value={p.op} onChange={(e) => onChange({ op: e.target.value })}>
              {['==', '!=', '>', '>=', '<', '<=', 'contains'].map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
            <input value={String(p.value)} onChange={(e) => onChange({ value: parseValue(e.target.value) })} />
          </div>
        </div>
      )
    case 'alert':
      return (
        <div className="gnode-body">
          <select value={p.severity} onChange={(e) => onChange({ severity: e.target.value })}>
            {['info', 'warn', 'high'].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          <input value={p.title} onChange={(e) => onChange({ title: e.target.value })} />
          <label>
            cooldown <input type="number" min={1} value={p.cooldown_min} onChange={(e) => onChange({ cooldown_min: Number(e.target.value) })} /> min
          </label>
        </div>
      )
    default:
      return <div className="gnode-body muted small">{node.type === 'listed' ? 'OpenSanctions sanctions / shadow-fleet match' : `passes when ${node.type === 'and' ? 'all' : 'any'} inputs pass`}</div>
  }
}
