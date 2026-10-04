import { useEffect } from 'react'
import { CommandBar } from './components/CommandBar'
import { Dock } from './components/Dock'
import { Inspector } from './components/Inspector'
import { LayerControl } from './components/LayerControl'
import { FlightModelCard, ModelCard } from './components/ModelCard'
import { Palette } from './components/Palette'
import { Rail, SidePanel } from './components/Rail'
import { SentinelEditor } from './components/SentinelEditor'
import { Toasts } from './components/Toasts'
import { startFeeds } from './lib/feeds'
import { ui, useStore } from './lib/store'
import MapView from './map/MapView'

export default function App() {
  const sentinelsOpen = useStore(ui, (s) => s.sentinelsOpen)
  const modelCardOpen = useStore(ui, (s) => s.modelCardOpen)
  useEffect(startFeeds, [])
  return (
    <div className="app">
      <MapView />
      <div className="map-vignette" />
      <CommandBar />
      <Rail />
      <SidePanel />
      <Inspector />
      <LayerControl />
      <Toasts />
      <Dock />
      <div className="attribution">
        ADS-B: adsb.fi, adsb.lol · Air Force of Ukraine · DeepStateMap · official air-raid alert map · VIINA · GeoNames · CelesTrak · Open-Meteo · GDELT · Kyiv Independent ·
        Ukrainska Pravda · Ukrinform · OurAirports · Wikidata · Natural Earth · Esri imagery · © OpenStreetMap. Model estimates are labelled; nothing here is a
        confirmed finding.
      </div>
      <Palette />
      {sentinelsOpen && <SentinelEditor />}
      {modelCardOpen === 'strike' && <ModelCard />}
      {modelCardOpen === 'flight' && <FlightModelCard />}
    </div>
  )
}
