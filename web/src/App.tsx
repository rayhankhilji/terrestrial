import { Inspector } from './components/Inspector'
import { FlightModelCard, ModelCard } from './components/ModelCard'
import { LayerControl } from './components/LayerControl'
import { LeftPanel } from './components/LeftPanel'
import { SentinelEditor } from './components/SentinelEditor'
import { TopBar } from './components/TopBar'
import { ui, useStore } from './lib/store'
import MapView from './map/MapView'

export default function App() {
  const sentinelsOpen = useStore(ui, (s) => s.sentinelsOpen)
  const modelCardOpen = useStore(ui, (s) => s.modelCardOpen)
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
        Data: ADS-B (adsb.fi, adsb.lol), ADS-B Exchange DB, OurAirports, air-raid alerts (Vadimkin dataset, ubilling.net.ua), VIINA 2.0, geoBoundaries,
        Open-Meteo, GDELT, NASA FIRMS, Wikidata, OpenSanctions, Global Fishing Watch, OpenStreetMap. Model estimates and inferred groupings —
        candidate findings, not conclusions.
      </footer>
      {sentinelsOpen && <SentinelEditor />}
      {modelCardOpen === 'strike' && <ModelCard />}
      {modelCardOpen === 'flight' && <FlightModelCard />}
    </div>
  )
}
