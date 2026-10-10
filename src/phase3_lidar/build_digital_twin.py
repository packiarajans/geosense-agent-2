# build_digital_twin.py
# Purpose: GeoSense Digital Twin - layers 1 & 2.
#   Layer 1 (3D city model): stepped (LOD1.5) building blocks at their LiDAR heights, so towers keep
#            their setbacks; trees as 3D columns at canopy height.
#   Layer 2 (analysis): 50 m grid metrics (building cover, canopy cover, heights) and the saved
#            site profiles from unified_site_profile.py, draped on the model.
#   If footprint_building_labels.py has been run, building parts also carry the official
#   footprint ID (BIN), footprint height and the LiDAR-vs-footprint height check status.
#   'Vegetation' taller than TREE_MAX_M (35 m) is drawn as a structure - no real tree is that tall;
#   these are glass towers the roughness rules mislabel (footprints fix the lower ones too).
#
# Usage (after export_classified_laz.py, ideally after footprint_building_labels.py):
#   python src/phase3_lidar/build_digital_twin.py --laz data/lidar/processed/nyc_fidi_classified_bldg.laz
#   python src/phase3_lidar/build_digital_twin.py --laz data/lidar/processed/manhattan_ues_classified_full.laz
#
# Output: outputs/digital_twin/<name>/
#   <name>_twin.html          interactive 3D viewer (open in Chrome/Edge; basemap needs internet)
#   <name>_buildings.gpkg     building parts with height_m  (QGIS 3D view / Cesium ion / ArcGIS Pro)
#   <name>_trees.gpkg         tree columns with height_m
#   <name>_grid.gpkg          50 m analysis grid
import argparse
import json
from pathlib import Path

import geopandas as gpd
import laspy
import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_origin
from scipy import ndimage

from lidar_features import TREE_MAX_M, compute_grid_metrics

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED = PROJECT_ROOT / 'data' / 'lidar' / 'processed'
REPORTS = PROJECT_ROOT / 'outputs' / 'reports'
OUT_ROOT = PROJECT_ROOT / 'outputs' / 'digital_twin'
DEFAULT_LAZ = PROCESSED / 'nyc_fidi_classified_full.laz'

BAND_M = 4.0           # roof heights are stepped in bands of this size (~1 floor)
MIN_BUILDING_M2 = 30   # smaller building blobs are dropped (chimneys, noise)
MIN_PART_CELLS = 12    # sieve: roof patches smaller than this merge into neighbours
MIN_TREE_M = 3.0       # canopy lower than this is not drawn
MAX_TREES = 60000      # tree columns are thinned (larger cells) above this count
GRID_M = 50


def _crs_name(name):
    for s in ('_classified_bldg', '_classified_full', '_classified'):
        name = name.replace(s, '')
    return name


# ---------- layer 1: buildings ----------
def structure_mask(hag, cls):
    """Building points, plus 'vegetation' taller than any realistic tree (glass towers)."""
    return (cls == 6) | (np.isin(cls, (3, 4, 5)) & (hag > TREE_MAX_M))


def building_parts(x, y, hag, cls, crs):
    bld = structure_mask(hag, cls)
    ext = (x.max() - x.min()) * (y.max() - y.min())
    cell = 1.0 if ext < 25e6 else 2.0
    x0, y0 = x.min(), y.max()
    W = int((x.max() - x0) / cell) + 1
    H = int((y0 - y.min()) / cell) + 1
    top = np.zeros(H * W, dtype='float32')
    idx = ((y0 - y[bld]) / cell).astype(np.int64) * W + ((x[bld] - x0) / cell).astype(np.int64)
    np.maximum.at(top, idx, hag[bld])
    top = top.reshape(H, W)

    mask = ndimage.binary_closing(top > 0, iterations=1)
    mask = ndimage.binary_fill_holes(mask) & ndimage.binary_opening(mask, iterations=1)
    # fill empty cells inside buildings with the neighbourhood roof height, then smooth
    filled = np.where(top > 0, top, ndimage.grey_dilation(top, size=5))
    roof = ndimage.median_filter(filled, size=3)
    roof[~mask] = 0

    comp, n = ndimage.label(mask)
    area = ndimage.sum(np.ones_like(comp), comp, index=np.arange(1, n + 1)) * cell * cell
    keep = np.zeros(n + 1, bool)
    keep[1:] = area >= MIN_BUILDING_M2
    comp[~keep[comp]] = 0
    mask = comp > 0

    band = np.where(mask, np.maximum(np.round(roof / BAND_M), 1), 0).astype('int32')
    part_key = np.where(mask, comp * 1000 + band, 0).astype('int32')
    part_key = features.sieve(part_key, MIN_PART_CELLS, mask=mask).astype('int32')
    part_key[~mask] = 0

    # per-part height = median roof height of its cells; per-building stats
    df = pd.DataFrame({'k': part_key[mask], 'h': roof[mask], 'b': comp[mask]})
    part_h = df.groupby('k')['h'].median()
    bstats = df.groupby('b')['h'].agg(bldg_height_m=lambda s: np.percentile(s, 95),
                                     bldg_max_m='max', cells='size')

    tr = from_origin(x0, y0, cell, cell)
    rows = []
    for geom, k in features.shapes(part_key, mask=mask, transform=tr):
        k = int(k)
        rows.append({'part_key': k, 'building_id': k // 1000, 'geometry': shapely.geometry.shape(geom)})
    gdf = gpd.GeoDataFrame(rows, crs=crs)
    gdf['geometry'] = gdf.geometry.simplify(cell * 0.75, preserve_topology=True)
    gdf = gdf[gdf.area >= cell * cell * 4]
    gdf['height_m'] = gdf['part_key'].map(part_h).round(1)
    gdf = gdf.join(bstats, on='building_id')
    gdf['footprint_m2'] = (gdf.pop('cells') * cell * cell).round(0)
    gdf['bldg_height_m'] = gdf['bldg_height_m'].round(1)
    gdf['bldg_max_m'] = gdf['bldg_max_m'].round(1)
    return gdf.drop(columns='part_key').reset_index(drop=True)


def attach_footprint_check(parts, name):
    chk = PROCESSED / f'{name}_footprint_check.gpkg'
    if not chk.exists():
        print('  (no footprint check found - run footprint_building_labels.py to add BIN / height check)')
        return parts, False
    fp = gpd.read_file(chk).to_crs(parts.crs)[['fp_id', 'fp_height_m', 'lidar_height_m', 'status', 'geometry']]
    pts = parts.assign(geometry=parts.geometry.representative_point())
    j = gpd.sjoin(pts, fp, how='left', predicate='within').drop(columns=['geometry', 'index_right'])
    j = j[~j.index.duplicated()]
    for c in ('fp_id', 'fp_height_m', 'status'):
        parts[c] = j[c]
    parts['status'] = parts['status'].fillna('outside_footprints')
    print(f'  footprint check attached from {chk.name}')
    return parts, True


# ---------- layer 1: trees ----------
def tree_columns(x, y, hag, cls):
    veg = np.isin(cls, (3, 4, 5)) & (hag <= TREE_MAX_M)
    cell = 3.0
    while True:
        gx = ((x - x.min()) // cell).astype(np.int64)
        gy = ((y - y.min()) // cell).astype(np.int64)
        key = gy * (int(gx.max()) + 1) + gx
        order = np.lexsort((-hag, key))
        first = order[np.r_[True, key[order][1:] != key[order][:-1]]]   # highest point per cell
        sel = first[veg[first] & (hag[first] >= MIN_TREE_M)]
        if len(sel) <= MAX_TREES:
            break
        cell *= 1.5
    return x[sel], y[sel], hag[sel], cell


# ---------- layer 2 ----------
def grid_layer(x, y, hag, cls, crs):
    labels = np.zeros(len(cls), 'uint8')
    labels[np.isin(cls, (3, 4, 5))] = 1
    labels[structure_mask(hag, cls)] = 2
    m = compute_grid_metrics(x, y, hag, labels, GRID_M)
    g = gpd.GeoDataFrame(m, geometry=[shapely.box(r['min_x'], r['min_y'], r['min_x'] + GRID_M,
                                                  r['min_y'] + GRID_M) for r in m], crs=crs)
    return g.drop(columns=['grid_x', 'grid_y', 'min_x', 'min_y'])


def site_profiles(bbox):
    lon0, lat0, lon1, lat1 = bbox
    out = []
    for f in sorted(REPORTS.glob('site_profile_*.json')):
        try:
            p = json.loads(f.read_text())
        except Exception:
            continue
        lat, lon = p.get('latitude'), p.get('longitude')
        if lat is None or not (lat0 <= lat <= lat1 and lon0 <= lon <= lon1):
            continue
        p['file'] = f.name
        out.append(p)
    return out


# ---------- viewer ----------
def to_geojson(gdf, cols, prec=6):
    g = gdf.to_crs(4326)
    g['geometry'] = shapely.set_precision(g.geometry, 10 ** -prec)
    return json.loads(g[cols + ['geometry']].to_json(na='null', drop_id=True))


def write_viewer(path, name, data):
    html = VIEWER_HTML.replace('__TITLE__', f'GeoSense Twin – {name}') \
                      .replace('__DATA__', json.dumps(data, separators=(',', ':')))
    path.write_text(html, encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description='GeoSense digital twin (3D city model + analysis layers).')
    ap.add_argument('--laz', default=str(DEFAULT_LAZ),
                    help='<name>_classified_bldg.laz (best) or <name>_classified_full.laz')
    ap.add_argument('--out-dir', default=None, help='output folder (default: outputs/digital_twin/<name>)')
    args = ap.parse_args()
    laz = Path(args.laz)
    laz = laz if laz.is_absolute() else PROJECT_ROOT / laz
    if not laz.exists():
        raise SystemExit(f'LAZ not found: {laz}  (run export_classified_laz.py first)')
    name = _crs_name(laz.stem)
    out_dir = Path(args.out_dir) if args.out_dir else OUT_ROOT / name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f'Reading {laz.name} ...')
    las = laspy.read(str(laz))
    crs = las.header.parse_crs()
    if crs is None:
        raise SystemExit('LAZ has no CRS.')
    x, y = np.asarray(las.x), np.asarray(las.y)
    hag = np.asarray(las.HeightAboveGround, dtype='float32')
    cls = np.asarray(las.classification, dtype='uint8')
    if not np.isin(cls, (3, 4, 5, 6)).any():
        raise SystemExit('Only ground/unclassified in this LAZ - run export_classified_laz.py first.')
    fixed = int((np.isin(cls, (3, 4, 5)) & (hag > TREE_MAX_M)).sum())
    if fixed:
        print(f'  {fixed:,} "vegetation" points above {TREE_MAX_M:.0f} m treated as structures '
              f'(footprint_building_labels.py fixes these properly)')

    print('Layer 1: building blocks ...')
    parts = building_parts(x, y, hag, cls, crs)
    parts, has_fp = attach_footprint_check(parts, name)
    print(f'  {parts["building_id"].nunique():,} buildings, {len(parts):,} roof parts, '
          f'tallest {parts["bldg_max_m"].max():.0f} m')
    tx, ty, th, tcell = tree_columns(x, y, hag, cls)
    print(f'  {len(tx):,} tree columns ({tcell:.1f} m cells)')

    print('Layer 2: analysis grid + site profiles ...')
    grid = grid_layer(x, y, hag, cls, crs)
    to_ll = Transformer.from_crs(crs, 4326, always_xy=True)
    lon, lat = to_ll.transform(tx, ty)
    blon, blat = to_ll.transform([x.min(), x.max()], [y.min(), y.max()])
    bbox = [min(blon), min(blat), max(blon), max(blat)]
    sites = site_profiles(bbox)
    print(f'  {len(grid):,} grid cells, {len(sites)} site profile(s) inside the model')

    # GIS exports
    parts.to_file(out_dir / f'{name}_buildings.gpkg', driver='GPKG')
    gpd.GeoDataFrame({'height_m': np.round(th, 1)}, geometry=gpd.points_from_xy(tx, ty), crs=crs) \
        .to_file(out_dir / f'{name}_trees.gpkg', driver='GPKG')
    grid.to_file(out_dir / f'{name}_grid.gpkg', driver='GPKG')

    bcols = ['building_id', 'height_m', 'bldg_height_m', 'bldg_max_m', 'footprint_m2'] + \
            (['fp_id', 'fp_height_m', 'status'] if has_fp else [])
    data = {
        'name': name, 'bbox': bbox, 'has_footprints': has_fp,
        'buildings': to_geojson(parts, bcols),
        'trees': {'lon': np.round(lon, 6).tolist(), 'lat': np.round(lat, 6).tolist(),
                  'h': np.round(th, 1).tolist(), 'r': tcell / 2},
        'grid': to_geojson(grid, [c for c in grid.columns if c != 'geometry']),
        'sites': sites,
        'stats': {'buildings': int(parts['building_id'].nunique()), 'parts': len(parts),
                  'tallest_m': float(parts['bldg_max_m'].max()), 'trees': len(tx),
                  'canopy_pct': float(np.round(grid['canopy_cover_pct'].mean(), 1)),
                  'building_cover_pct': float(np.round(grid['building_cover_pct'].mean(), 1))},
    }
    html = out_dir / f'{name}_twin.html'
    write_viewer(html, name, data)
    print(f'Saved {html}  ({html.stat().st_size / 1e6:.1f} MB)')
    print(f'GIS layers in {out_dir}')


VIEWER_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<script src="https://unpkg.com/deck.gl@9.1.14/dist.min.js"></script>
<style>
:root{--bg:#0f1720;--panel:rgba(18,26,36,.92);--fg:#e6edf3;--muted:#9fb0c0;--line:#2a3746;--accent:#5fb3f9}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font:13px/1.45 system-ui,Segoe UI,Roboto,sans-serif}
#map{position:absolute;inset:0}
.panel{position:absolute;top:12px;left:12px;width:290px;max-height:calc(100% - 24px);overflow:auto;background:var(--panel);
border:1px solid var(--line);border-radius:10px;padding:14px 16px;backdrop-filter:blur(6px)}
h1{font-size:15px;margin:0 0 2px}.sub{color:var(--muted);font-size:12px;margin-bottom:12px}
h2{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:14px 0 6px}
label{display:flex;align-items:center;gap:8px;margin:4px 0;cursor:pointer}
select{width:100%;background:#1b2633;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:6px}.stat{background:#17212c;border-radius:6px;padding:6px 8px}
.stat b{display:block;font-size:15px}.stat span{color:var(--muted);font-size:11px}
.legend{display:flex;height:8px;border-radius:4px;overflow:hidden;margin-top:6px}.legend div{flex:1}
.lgl{display:flex;justify-content:space-between;color:var(--muted);font-size:11px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.chip{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--muted)}
.chip i{width:10px;height:10px;border-radius:2px;display:inline-block}
.hint{color:var(--muted);font-size:11px;margin-top:10px}
.attr{position:absolute;right:6px;bottom:4px;font-size:10px;color:var(--muted);background:rgba(15,23,32,.7);padding:1px 6px;border-radius:4px}
#tip{position:absolute;pointer-events:none;background:#0b1118;border:1px solid var(--line);border-radius:6px;padding:8px 10px;font-size:12px;display:none;max-width:280px;z-index:5}
@media (max-width:600px){.panel{width:calc(100% - 24px);max-height:45%}}
</style></head><body>
<div id="map"></div><div id="tip"></div><div class="attr">Basemap © Esri, Maxar, Earthstar Geographics, HERE, OpenStreetMap contributors · LiDAR: USGS 3DEP · Footprints: NYC Open Data</div>
<div class="panel">
 <h1 id="ttl"></h1><div class="sub">GeoSense digital twin · LiDAR 3D city model + site analysis</div>
 <div class="stats" id="stats"></div>
 <h2>Layer 1 · 3D city</h2>
 <label><input type="checkbox" id="lb" checked> Buildings</label>
 <label><input type="checkbox" id="lt" checked> Trees</label>
 <select id="colorBy"><option value="height">Colour buildings by height</option></select>
 <div id="legend"></div>
 <h2>Layer 2 · Analysis</h2>
 <label><input type="checkbox" id="lg"> 50 m grid</label>
 <select id="gridMetric">
  <option value="building_cover_pct">Building cover %</option>
  <option value="canopy_cover_pct">Canopy cover %</option>
  <option value="avg_building_height_m">Avg building height (m)</option>
  <option value="max_building_height_m">Max building height (m)</option></select>
 <div id="glegend"></div>
 <label><input type="checkbox" id="ls" checked> Site profiles</label>
 <h2>Basemap</h2>
 <select id="basemap"><option value="dark">Dark grey (Esri)</option><option value="satellite">Satellite (Esri)</option>
  <option value="light">Light grey (Esri)</option><option value="none">None</option></select>
 <div class="hint">Drag to pan · right-drag / Ctrl-drag to tilt & rotate · click an object for details</div>
</div>
<script>
const D = __DATA__;
// Esri public basemaps - no API key needed, work from a local file
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/';
const BASEMAPS = {dark: ESRI + 'Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
  satellite: ESRI + 'World_Imagery/MapServer/tile/{z}/{y}/{x}',
  light: ESRI + 'Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}', none: null};
const $ = id => document.getElementById(id);
$('ttl').textContent = 'GeoSense Twin – ' + D.name;
const S = D.stats;
$('stats').innerHTML = [[S.buildings.toLocaleString(),'buildings'],[Math.round(S.tallest_m)+' m','tallest'],
 [S.trees.toLocaleString(),'tree columns'],[S.canopy_pct+'%','avg canopy']].map(([b,s])=>`<div class="stat"><b>${b}</b><span>${s}</span></div>`).join('');
if (D.has_footprints) $('colorBy').insertAdjacentHTML('beforeend','<option value="status">Colour by footprint height check</option>');

// sequential ramp (light -> deep blue) and status colours
const RAMP = [[222,235,247],[179,207,232],[125,171,214],[74,134,194],[37,96,166],[16,60,125]];
const ramp = (v, lo, hi) => { const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo || 1))) * (RAMP.length - 1);
  const i = Math.floor(t), f = t - i, a = RAMP[i], b = RAMP[Math.min(i + 1, RAMP.length - 1)];
  return a.map((c, k) => Math.round(c + (b[k] - c) * f)); };
const STATUS = {ok:[120,190,140], height_mismatch:[240,150,60], no_lidar_points:[150,150,150],
  no_footprint_height:[180,180,200], outside_footprints:[210,90,90]};
const hs = D.buildings.features.map(f => f.properties.height_m).sort((a,b)=>a-b);
const HMAX = hs[Math.floor(hs.length * .98)] || 50;
const legendBar = (lo, hi, unit) => `<div class="legend">${RAMP.map(c=>`<div style="background:rgb(${c})"></div>`).join('')}</div><div class="lgl"><span>${lo}</span><span>${hi}${unit}</span></div>`;
function drawLegend(){
  $('legend').innerHTML = $('colorBy').value === 'height' ? legendBar(0, Math.round(HMAX), ' m')
   : `<div class="chips">${Object.entries(STATUS).map(([k,c])=>`<span class="chip"><i style="background:rgb(${c})"></i>${k.replace(/_/g,' ')}</span>`).join('')}</div>`;
  const m = $('gridMetric').value, vals = D.grid.features.map(f => f.properties[m]);
  $('glegend').innerHTML = $('lg').checked ? legendBar(0, Math.round(Math.max(...vals)), m.endsWith('pct') ? '%' : ' m') : '';
}

const [lon0, lat0, lon1, lat1] = D.bbox;
const INITIAL = {longitude:(lon0+lon1)/2, latitude:(lat0+lat1)/2, zoom:15.6, pitch:55, bearing:-25};
const trees = D.trees.h.map((h, i) => ({p:[D.trees.lon[i], D.trees.lat[i]], h}));

function layers(){
  const colorBy = $('colorBy').value, gm = $('gridMetric').value;
  const gmax = Math.max(...D.grid.features.map(f => f.properties[gm])) || 1;
  return [
    BASEMAPS[$('basemap').value] && new deck.TileLayer({id:'basemap-' + $('basemap').value, data:BASEMAPS[$('basemap').value],
      minZoom:0, maxZoom:19, tileSize:256,
      renderSubLayers: p => { const {west,south,east,north} = p.tile.bbox;
        return new deck.BitmapLayer(p, {data:null, image:p.data, bounds:[west,south,east,north]}); }}),
    $('lg').checked && new deck.GeoJsonLayer({id:'grid', data:D.grid, pickable:true, stroked:true, filled:true,
      getFillColor: f => [...ramp(f.properties[gm], 0, gmax), 150], getLineColor:[255,255,255,40], lineWidthMinPixels:1,
      updateTriggers:{getFillColor:gm}}),
    $('lb').checked && new deck.GeoJsonLayer({id:'buildings', data:D.buildings, pickable:true, extruded:true, wireframe:false,
      getElevation: f => f.properties.height_m,
      getFillColor: f => colorBy === 'status' ? STATUS[f.properties.status] || [200,200,200] : ramp(f.properties.height_m, 0, HMAX),
      material:{ambient:.35, diffuse:.6, shininess:24}, updateTriggers:{getFillColor:colorBy}}),
    $('lt').checked && new deck.ColumnLayer({id:'trees', data:trees, diskResolution:8, radius:D.trees.r, extruded:true,
      pickable:true, getPosition:d=>d.p, getElevation:d=>d.h, getFillColor:d=>[50, 140 + Math.min(80, d.h * 3), 70]}),
    $('ls').checked && new deck.ScatterplotLayer({id:'sites', data:D.sites, pickable:true, radiusMinPixels:8,
      getPosition:d=>[d.longitude, d.latitude, 5], getFillColor:[255,90,120], stroked:true, getLineColor:[255,255,255], lineWidthMinPixels:2}),
  ].filter(Boolean);
}

const fmt = v => v == null ? '–' : (typeof v === 'number' ? (Math.round(v * 10) / 10).toLocaleString() : v);
function describe(info){
  const o = info.object; if (!o) return null;
  const id = info.layer.id;
  if (id === 'buildings') { const p = o.properties; return `<b>Building ${p.building_id}</b><br>This part: ${fmt(p.height_m)} m<br>Building p95 / max: ${fmt(p.bldg_height_m)} / ${fmt(p.bldg_max_m)} m<br>Footprint: ${fmt(p.footprint_m2)} m²` +
     (D.has_footprints ? `<br>BIN: ${fmt(p.fp_id)}<br>Official height: ${fmt(p.fp_height_m)} m<br>Check: ${fmt(p.status)}` : ''); }
  if (id === 'trees') return `<b>Tree canopy</b><br>${fmt(o.h)} m`;
  if (id === 'grid') { const p = o.properties; return `<b>50 m cell</b><br>Building cover ${fmt(p.building_cover_pct)}%<br>Canopy cover ${fmt(p.canopy_cover_pct)}%<br>Avg / max height ${fmt(p.avg_building_height_m)} / ${fmt(p.max_building_height_m)} m`; }
  if (id === 'sites') { const keys = ['avg_building_height_m','canopy_cover_pct','building_cover_pct','mean_ground_slope_deg','slope_risk_score','walkability_score'];
    return `<b>Site profile</b><br>${o.latitude.toFixed(4)}, ${o.longitude.toFixed(4)}<br>` + keys.filter(k => o[k] != null).map(k => `${k.replace(/_/g,' ')}: ${fmt(o[k])}`).join('<br>'); }
}
const tip = $('tip');
const dg = new deck.DeckGL({container:'map', initialViewState:INITIAL, controller:true, layers:layers(),
  onHover: info => { const h = describe(info); tip.style.display = h ? 'block' : 'none';
    if (h) { tip.innerHTML = h; tip.style.left = (info.x + 14) + 'px'; tip.style.top = (info.y + 14) + 'px'; } },
  getCursor: ({isHovering}) => isHovering ? 'pointer' : 'grab'});
['lb','lt','lg','ls','colorBy','gridMetric','basemap'].forEach(id => $(id).addEventListener('change', () => { dg.setProps({layers:layers()}); drawLegend(); }));
drawLegend();
</script></body></html>'''


if __name__ == '__main__':
    main()
