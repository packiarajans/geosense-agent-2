import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import { Viewer as MlyViewer } from 'mapillary-js'
import 'mapillary-js/dist/mapillary.css'

// Task E.4.1 - base globe (modern terrain API replaces deprecated createWorldTerrain)
Cesium.Ion.defaultAccessToken = import.meta.env.VITE_CESIUM_ION_TOKEN

const viewer = new Cesium.Viewer('cesiumContainer', {
  terrain: Cesium.Terrain.fromWorldTerrain(),
  baseLayerPicker: true,
  timeline: false,
  animation: false,
  geocoder: false,
})
viewer.scene.globe.depthTestAgainstTerrain = true

const $ = (id) => document.getElementById(id)
const status = (t) => ($('status').textContent = t)

const SITES = {
  chennai:   { lon: 80.2707,  lat: 13.0827, height: 25000, flood: '/flood_risk_chennai.geojson', buildings: false },
  manhattan: { lon: -73.9565, lat: 40.7780, height: 1800,  flood: null, buildings: true },
  fidi:      { lon: -74.0100, lat: 40.7070, height: 4000,  flood: null, buildings: false },
}
const NYC_FLOOD = { abs: '/flood_risk_newyork_abs.geojson', rel: '/flood_risk_newyork.geojson' }

const RISK_COLOR = {
  high: Cesium.Color.fromCssColorString('#d62728'),
  medium: Cesium.Color.fromCssColorString('#ff9f1c'),
  low: Cesium.Color.fromCssColorString('#2ca02c'),
}

let floodDS = null
let bldDS = null
let floodUrlLoaded = null

async function loadFlood(url) {
  if (floodUrlLoaded === url && floodDS) { floodDS.show = $('chkFlood').checked; return }
  if (floodDS) viewer.dataSources.remove(floodDS, true)
  floodUrlLoaded = url
  if (!url) { floodDS = null; return }
  status('Loading flood layer…')
  floodDS = await Cesium.GeoJsonDataSource.load(url, { clampToGround: true })
  for (const e of floodDS.entities.values) {
    const lvl = e.properties.risk_level?.getValue()
    const c = (RISK_COLOR[lvl] || Cesium.Color.GRAY).withAlpha(0.5)
    e.polygon.material = c
    e.polygon.outline = false
    e.name = `Flood risk: ${lvl}`
  }
  floodDS.show = $('chkFlood').checked
  viewer.dataSources.add(floodDS)
  status(`Flood polygons: ${floodDS.entities.values.length}`)
}

async function loadBuildings() {
  if (bldDS) return
  status('Loading LiDAR buildings…')
  bldDS = await Cesium.GeoJsonDataSource.load('/lidar_buildings.geojson')
  for (const e of bldDS.entities.values) {
    const h = e.properties.height_m?.getValue() ?? 5
    e.polygon.height = 0
    e.polygon.extrudedHeight = h
    e.polygon.material = Cesium.Color.fromCssColorString('#8fb4d9').withAlpha(0.9)
    e.polygon.outline = true
    e.polygon.outlineColor = Cesium.Color.fromCssColorString('#334')
    e.name = `Building ${e.properties.building_id?.getValue()}`
    e.description = `Height (LiDAR): ${h.toFixed(1)} m<br>Footprint: ${e.properties.footprint_m2?.getValue()} m²`
  }
  viewer.dataSources.add(bldDS)
}

async function goToSite(key) {
  const s = SITES[key]
  const nyc = key !== 'chennai'
  await loadFlood(nyc ? NYC_FLOOD[$('floodMode').value] : s.flood)
  if (s.buildings) await loadBuildings()
  if (bldDS) bldDS.show = s.buildings && $('chkBuildings').checked
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(s.lon, s.lat - (nyc ? 0.01 : 0.08), s.height),
    orientation: { heading: 0, pitch: Cesium.Math.toRadians(-40), roll: 0 },
    duration: 3,
  })
  status('Ready')
}

// Public hook so Week 4 (agent results) can fly the camera.
window.flyToLonLat = (lon, lat, height = 1500) =>
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(lon, lat - 0.01, height),
    orientation: { heading: 0, pitch: Cesium.Math.toRadians(-40), roll: 0 },
  })

$('site').addEventListener('change', () => goToSite($('site').value))
$('floodMode').addEventListener('change', () => { if ($('site').value !== 'chennai') goToSite($('site').value) })
$('chkFlood').addEventListener('change', () => { if (floodDS) floodDS.show = $('chkFlood').checked })
$('chkBuildings').addEventListener('change', () => { if (bldDS) bldDS.show = $('chkBuildings').checked && $('site').value === 'manhattan' })

const qs = new URLSearchParams(location.search)   // optional: ?site=manhattan&year=1  (used for README screenshots)
if (SITES[qs.get('site')]) site.value = qs.get('site')
if (qs.get('year') != null) { yearSlider.value = qs.get('year'); setYear(Number(qs.get('year'))) }
goToSite(site.value).catch((e) => { console.error(e); status('Error: ' + e.message) })

// ---- Historical imagery time slider (Esri World Imagery Wayback snapshots; free, no key) ----
const WAYBACK = [
  { label: '2015 (Jan)', id: 20222 },
  { label: '2018 (Jan)', id: 13161 },
  { label: '2021 (Jan)', id: 1049 },
  { label: '2024 (Jan)', id: 41468 },
  { label: 'Current (Sep 2026)', id: null },
]
let histLayer = null
function setYear(i) {
  const w = WAYBACK[i]
  $('yearLabel').textContent = w.label
  if (histLayer) { viewer.imageryLayers.remove(histLayer, true); histLayer = null }
  if (w.id == null) return
  const provider = new Cesium.UrlTemplateImageryProvider({
    url: `https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/${w.id}/{z}/{y}/{x}`,
    maximumLevel: 19,
    credit: 'Esri World Imagery Wayback',
  })
  histLayer = viewer.imageryLayers.addImageryProvider(provider)   // drawn on top of the base imagery
}
$('yearSlider').addEventListener('input', (e) => setYear(Number(e.target.value)))
// ---- Street view (Mapillary) -------------------------------------------------
const MLY_TOKEN = import.meta.env.VITE_MAPILLARY_TOKEN
let mly = null
const svBox = $('sv'), svMsg = $('svMsg'), svTitle = $('svTitle')

async function nearestImage(lat, lon) {
  for (const d of [0.0006, 0.0015, 0.004]) {
    const url = `https://graph.mapillary.com/images?access_token=${MLY_TOKEN}&fields=id,geometry&limit=1` +
                `&bbox=${lon - d},${lat - d},${lon + d},${lat + d}`
    const r = await fetch(url)
    if (!r.ok) throw new Error('Mapillary HTTP ' + r.status)
    const j = await r.json()
    if (j.data && j.data.length) return j.data[0]
  }
  return null
}

async function showStreetView(lat, lon) {
  svBox.style.display = 'block'
  svTitle.textContent = `Street view  ${lat.toFixed(5)}, ${lon.toFixed(5)}`
  if (!MLY_TOKEN) { svMsg.style.display = 'flex'; svMsg.textContent = 'Add VITE_MAPILLARY_TOKEN to frontend/digital_twin/.env and restart npm run dev.'; return }
  svMsg.style.display = 'flex'; svMsg.textContent = 'Looking for street imagery…'
  try {
    const img = await nearestImage(lat, lon)
    if (!img) { svMsg.textContent = 'No Mapillary street imagery within ~400 m of this point.'; return }
    svMsg.style.display = 'none'
    if (!mly) mly = new MlyViewer({ accessToken: MLY_TOKEN, container: 'svView', imageId: img.id })
    else mly.moveTo(img.id)
  } catch (e) { svMsg.textContent = 'Street view error: ' + e.message }
}
window.showStreetView = showStreetView
$('svClose').addEventListener('click', () => { svBox.style.display = 'none' })

// click on the globe -> street view at that point
const clickHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
clickHandler.setInputAction((ev) => {
  const ray = viewer.camera.getPickRay(ev.position)
  const pos = ray && viewer.scene.globe.pick(ray, viewer.scene)
  if (!pos) return
  const c = Cesium.Cartographic.fromCartesian(pos)
  viewer.entities.removeById('sv-pin')
  viewer.entities.add({ id: 'sv-pin', position: Cesium.Cartesian3.fromRadians(c.longitude, c.latitude),
    point: { pixelSize: 10, color: Cesium.Color.YELLOW, heightReference: Cesium.HeightReference.CLAMP_TO_GROUND } })
  showStreetView(Cesium.Math.toDegrees(c.latitude), Cesium.Math.toDegrees(c.longitude))
}, Cesium.ScreenSpaceEventType.LEFT_CLICK)
// E.5.1: poll the MCP server for the agent's latest location and fly there when it changes.
let lastStamp = null
async function pollAgentResult() {
  try {
    const resp = await fetch('http://localhost:8000/agent/latest_result')
    const data = await resp.json()
    if (data.latitude != null && data.longitude != null && data.stamp !== lastStamp) {
      {
        lastStamp = data.stamp
        viewer.entities.removeById('agent-pin')
        viewer.entities.add({
          id: 'agent-pin',
          position: Cesium.Cartesian3.fromDegrees(data.longitude, data.latitude),
          point: { pixelSize: 14, color: Cesium.Color.CYAN, outlineColor: Cesium.Color.WHITE, outlineWidth: 2,
                   heightReference: Cesium.HeightReference.CLAMP_TO_GROUND },
          name: 'Agent result',
        })
        window.flyToLonLat(data.longitude, data.latitude, 800)
        showStreetView(data.latitude, data.longitude)
        status(`Agent location: ${data.latitude.toFixed(4)}, ${data.longitude.toFixed(4)}`)
      }
    }
  } catch (e) { /* server not running yet */ }
}
setInterval(pollAgentResult, 3000)
