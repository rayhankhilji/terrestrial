import { Inspector } from './components/Inspector'
import { LayerControl } from './components/LayerControl'
import { LeftPanel } from './components/LeftPanel'
import { SentinelEditor } from './components/SentinelEditor'
import { TopBar } from './components/TopBar'
import { ui, useStore } from './lib/store'
import MapView from './map/MapView'

export default function App() {
  const sentinelsOpen = useStore(ui, (s) => s.sentinelsOpen)
  return (
    <div className="app">
      <TopBar />
      <LeftPanel />
      <main className="stage">
        <MapView />
        <LayerControl />
        <Inspector />
      </main>
      <footer className="footer">
        Data: Global Fishing Watch, OpenSanctions, ADS-B (adsb.fi, adsb.lol), GDELT, NASA FIRMS, Open-Meteo, Wikidata, OpenStreetMap. Heuristic risk
        score — candidate findings, not conclusions.
      </footer>
      {sentinelsOpen && <SentinelEditor />}
    </div>
  )
}
