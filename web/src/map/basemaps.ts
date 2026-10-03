import type { StyleSpecification } from 'maplibre-gl'

/**
 * Basemaps. Both are token-free:
 *  - dark: CARTO Dark Matter (vector) — the analyst default from the spec
 *  - satellite: Esri World Imagery + AWS/Mapzen terrain + OpenFreeMap 3D buildings and labels,
 *    a Google-Earth-like view with real elevation and extruded buildings when zoomed in
 * Terrain (raster-dem) is added to either style so the camera can tilt over real relief.
 */

export const CARTO_DARK = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'

export const TERRAIN_SOURCE = {
  type: 'raster-dem' as const,
  tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
  encoding: 'terrarium' as const,
  tileSize: 256,
  maxzoom: 14,
  attribution: 'Terrain: Mapzen / AWS Terrain Tiles',
}

const OPENMAPTILES = { type: 'vector' as const, url: 'https://tiles.openfreemap.org/planet' }

const label = (minzoom: number) => ({
  'text-field': ['coalesce', ['get', 'name:en'], ['get', 'name:latin'], ['get', 'name']],
  'text-font': ['Noto Sans Regular'],
  'text-size': ['interpolate', ['linear'], ['zoom'], minzoom, 11, 12, 15],
})

export function satelliteStyle(globe: boolean): StyleSpecification {
  return {
    version: 8,
    name: 'terrestrial-satellite',
    projection: { type: globe ? 'globe' : 'mercator' },
    glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
    sources: {
      imagery: {
        type: 'raster',
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        tileSize: 256,
        maxzoom: 19,
        attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
      },
      terrain: TERRAIN_SOURCE,
      // a separate DEM source for hillshading renders better than sharing the 3D terrain one
      'terrain-shade': TERRAIN_SOURCE,
      openmaptiles: { ...OPENMAPTILES, attribution: '© OpenStreetMap contributors, OpenFreeMap' },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': '#02040a' } },
      { id: 'imagery', type: 'raster', source: 'imagery', paint: { 'raster-saturation': -0.15, 'raster-contrast': 0.08 } },
      {
        id: 'hillshade',
        type: 'hillshade',
        source: 'terrain-shade',
        paint: { 'hillshade-exaggeration': 0.25, 'hillshade-shadow-color': '#000000', 'hillshade-highlight-color': '#ffffff' },
      },
      {
        id: 'boundaries',
        type: 'line',
        source: 'openmaptiles',
        'source-layer': 'boundary',
        filter: ['all', ['<=', ['get', 'admin_level'], 2], ['!=', ['get', 'maritime'], 1]],
        paint: { 'line-color': 'rgba(255,255,255,0.55)', 'line-width': 1.2, 'line-dasharray': [3, 2] },
      },
      {
        id: 'roads',
        type: 'line',
        source: 'openmaptiles',
        'source-layer': 'transportation',
        minzoom: 11,
        filter: ['in', ['get', 'class'], ['literal', ['motorway', 'trunk', 'primary', 'secondary']]],
        paint: { 'line-color': 'rgba(255,214,140,0.45)', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 0.5, 16, 3] },
      },
      {
        id: 'buildings-3d',
        type: 'fill-extrusion',
        source: 'openmaptiles',
        'source-layer': 'building',
        minzoom: 13,
        paint: {
          'fill-extrusion-color': ['interpolate', ['linear'], ['coalesce', ['get', 'render_height'], 8], 0, '#9fb3c8', 60, '#e2e8f0'],
          'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 8],
          'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
          'fill-extrusion-opacity': 0.88,
        },
      },
      {
        id: 'places',
        type: 'symbol',
        source: 'openmaptiles',
        'source-layer': 'place',
        filter: ['in', ['get', 'class'], ['literal', ['city', 'town', 'country']]],
        layout: label(4) as never,
        paint: { 'text-color': '#f8fafc', 'text-halo-color': 'rgba(0,0,0,0.85)', 'text-halo-width': 1.4 },
      },
    ],
    sky: {
      'sky-color': '#0b1a33',
      'horizon-color': '#33507a',
      'fog-color': '#0b1220',
      'sky-horizon-blend': 0.6,
      'horizon-fog-blend': 0.5,
      'fog-ground-blend': 0.6,
      'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 6, 0.6, 9, 0],
    },
  } as StyleSpecification
}
