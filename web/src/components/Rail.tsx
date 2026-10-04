import { Database, Gauge, type LucideIcon, Network, Newspaper, Orbit, Radar, ScrollText, Ship, Siren } from 'lucide-react'
import { live, useLive } from '../lib/live'
import { streams } from '../lib/feeds'
import { type LeftTab, ui, useStore } from '../lib/store'
import { AirThreatPanel } from './AirThreatPanel'
import { BriefPanel } from './BriefPanel'
import { DangerPanel } from './DangerPanel'
import { FeedPanel } from './FeedPanel'
import { IntelList } from './IntelList'
import { NetsPanel } from './NetsPanel'
import { SourcesPanel } from './SourcesPanel'
import { SpacePanel } from './SpacePanel'
import { ThreatBoard } from './ThreatBoard'

interface Tab {
  key: LeftTab
  label: string
  icon: LucideIcon
  modes: ('military' | 'maritime')[]
  badge?: () => { n: number; hot?: boolean } | null
}

const count = (kind: string, pred: (p: Record<string, unknown>) => boolean = () => true) =>
  [...live.entities.values()].filter((e) => e.kind === kind && pred(e.props)).length

const TABS: Tab[] = [
  { key: 'brief', label: 'Situation brief', icon: ScrollText, modes: ['military', 'maritime'] },
  {
    key: 'airthreats',
    label: 'Air threats',
    icon: Siren,
    modes: ['military'],
    badge: () => {
      const n = count('airthreat', (p) => !p.tally)
      return n ? { n, hot: true } : null
    },
  },
  {
    key: 'priority',
    label: 'Priority craft',
    icon: Radar,
    modes: ['military', 'maritime'],
    badge: () => {
      const n = [...live.entities.values()].filter((e) => (e.props.threat?.priority ?? 0) >= 25).length
      return n ? { n } : null
    },
  },
  { key: 'nets', label: 'Mission nets', icon: Network, modes: ['military'], badge: () => ({ n: count('net') }) },
  {
    key: 'danger',
    label: 'Danger forecast',
    icon: Gauge,
    modes: ['military'],
    badge: () => {
      const n = count('region', (p) => !!p.alert_active)
      return n ? { n, hot: true } : null
    },
  },
  { key: 'vessels', label: 'Dark vessels', icon: Ship, modes: ['maritime'] },
  { key: 'space', label: 'Space · imaging passes', icon: Orbit, modes: ['military', 'maritime'] },
  { key: 'feed', label: 'Feed · alerts & news', icon: Newspaper, modes: ['military', 'maritime'], badge: () => (live.alerts.length ? { n: live.alerts.length } : null) },
  { key: 'sources', label: 'Data streams', icon: Database, modes: ['military', 'maritime'] },
]

export function Rail() {
  useLive(1000)
  const tab = useStore(ui, (s) => s.leftTab)
  const open = useStore(ui, (s) => s.panelOpen)
  const mode = useStore(ui, (s) => s.mode)
  const rows = useStore(streams, (s) => s.rows)
  const failing = rows.filter((r) => ['error', 'missing'].includes(r.state)).length
  const tabs = TABS.filter((t) => t.modes.includes(mode))
  return (
    <nav className="rail glass" aria-label="Panels">
      {tabs.map((t, i) => {
        const b = t.key === 'sources' ? (failing ? { n: failing, hot: true } : null) : t.badge?.()
        const on = open && tab === t.key
        const Icon = t.icon
        return (
          <span key={t.key} style={{ display: 'contents' }}>
            {(t.key === 'space' || (i > 0 && t.key === 'brief')) && <span className="rail-sep" />}
            <button className={`rail-btn ${on ? 'on' : ''}`} aria-label={t.label} aria-pressed={on} onClick={() => ui.set({ leftTab: t.key, panelOpen: !on })}>
              <Icon size={19} strokeWidth={1.8} />
              {b && b.n > 0 && <span className={`badge ${b.hot ? 'hot' : ''}`}>{b.n > 99 ? '99+' : b.n}</span>}
              <span className="rail-tip">{t.label}</span>
            </button>
          </span>
        )
      })}
    </nav>
  )
}

export function SidePanel() {
  const tab = useStore(ui, (s) => s.leftTab)
  const open = useStore(ui, (s) => s.panelOpen)
  const mode = useStore(ui, (s) => s.mode)
  if (!open) return null
  const valid = TABS.find((t) => t.key === tab && t.modes.includes(mode))
  const key = valid ? tab : 'brief'
  return (
    <aside className="panel glass" key={key}>
      {key === 'brief' && <BriefPanel />}
      {key === 'airthreats' && <AirThreatPanel />}
      {key === 'priority' && <ThreatBoard />}
      {key === 'nets' && <NetsPanel />}
      {key === 'danger' && <DangerPanel />}
      {key === 'vessels' && <IntelList />}
      {key === 'space' && <SpacePanel />}
      {key === 'feed' && <FeedPanel />}
      {key === 'sources' && <SourcesPanel />}
    </aside>
  )
}
