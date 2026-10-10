# export_lidar_geojson.py
# Purpose: Convert Phase 3 LiDAR building footprints (with heights) into a GeoJSON the twin can extrude in 3D.
# The manual's D.3 sketch exports one Point per 50 m grid cell and says to "replace with a real polygon in
# production". Phase 3 already produced real building polygons (outputs/digital_twin/<site>/<site>_buildings.gpkg),
# so we use those: reprojected EPSG:32618 -> EPSG:4326, property height_m for extrusion.
# Run:  python src/phase4_agent/export_lidar_geojson.py --site manhattan_ues
import json, argparse
from pathlib import Path
import geopandas as gpd

ROOT = Path(__file__).resolve().parents[2]

def export_geojson(site, output_path):
    src = ROOT / 'outputs' / 'digital_twin' / site / f'{site}_buildings.gpkg'
    if not src.exists():
        raise SystemExit(f'Not found: {src}')
    g = gpd.read_file(src)
    n0 = len(g)
    g = g[g['height_m'].notna() & (g['height_m'] > 0)].copy()
    g = g.to_crs(4326)
    g['height_m'] = g['height_m'].round(1)
    g['footprint_m2'] = g['footprint_m2'].round(0)
    keep = g[['building_id', 'height_m', 'footprint_m2', 'geometry']]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gj = json.loads(keep.to_json(drop_id=True))
    for ft in gj['features']:  # trim coordinate precision (~0.1 m) to keep the file small
        def r(c): return [round(v, 6) for v in c] if isinstance(c[0], (int, float)) else [r(x) for x in c]
        ft['geometry']['coordinates'] = r(ft['geometry']['coordinates'])
    with open(output_path, 'w') as f:
        json.dump(gj, f)
    b = keep.total_bounds
    print(f'Exported {len(keep)} building features (of {n0}) to {output_path}')
    print(f'Height m: min {keep.height_m.min()}  mean {keep.height_m.mean():.1f}  max {keep.height_m.max()}')
    print(f'Bounds lon/lat: {b[0]:.4f},{b[1]:.4f} -> {b[2]:.4f},{b[3]:.4f}')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--site', default='manhattan_ues', help='manhattan_ues | nyc_fidi')
    ap.add_argument('--out', default=str(ROOT / 'frontend' / 'digital_twin' / 'public' / 'lidar_buildings.geojson'))
    a = ap.parse_args()
    export_geojson(a.site, Path(a.out))
