# footprint_building_labels.py
# Purpose: Use official building footprints (NYC Open Data "BUILDING" dataset by default) to fix
#          the building class in a LiDAR cloud - e.g. Manhattan glass towers that the roughness
#          rules in lidar_features.py label as high vegetation - and check LiDAR roof heights
#          against the footprint heights.
#
# Run after export_classified_laz.py:
#   python src/phase3_lidar/footprint_building_labels.py --laz data/lidar/processed/nyc_fidi_classified_full.laz
#   python src/phase3_lidar/footprint_building_labels.py --laz ... --footprints my_buildings.gpkg --height-field height_m
#   python src/phase3_lidar/footprint_building_labels.py --laz ... --strict
#
# Footprints:
#   default  -> downloaded from NYC Open Data (dataset 5zhs-2jue) for the cloud's extent (needs internet)
#               and cached in data/vector/footprints/<name>_nyc_footprints.geojson
#   --footprints <file> -> any polygon file GeoPandas can read (OSM, Microsoft, city data ...)
#
# Rules:
#   point inside a footprint (shrunk by --inset m) and HAG >= 2.5 m  -> class 6 building
#   --strict: class-6 points more than --outside m away from every footprint -> vegetation 3/4/5 by HAG
#             (off by default: footprint data can miss new buildings)
#
# Outputs (next to the input LAZ):
#   <name>_classified_bldg.laz        updated classification (input is never modified)
#   <name>_footprint_check.gpkg/.csv  per building: LiDAR roof height vs footprint height
import argparse
import json
from pathlib import Path

import geopandas as gpd
import laspy
import numpy as np
import pandas as pd
from pyproj import CRS
from rasterio import features
from rasterio.transform import from_origin

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAZ = PROJECT_ROOT / 'data' / 'lidar' / 'processed' / 'nyc_fidi_classified_full.laz'
CACHE_DIR = PROJECT_ROOT / 'data' / 'vector' / 'footprints'

NYC_API = 'https://data.cityofnewyork.us/resource/5zhs-2jue.geojson'
FT_TO_M = 0.3048
TALL_M = 2.5          # same threshold as lidar_features.py
GRID_M = 0.5          # rasterisation cell for point-in-footprint lookup
ROOF_PCTL = 95        # LiDAR roof height = this percentile of building-point HAG
MISMATCH_M = 5.0      # |LiDAR - footprint| above this is flagged
MEDIUM_VEG_M, HIGH_VEG_M = 2.0, 5.0


# ---------- footprints ----------
def cloud_extent_wgs84(las, crs):
    b = gpd.GeoSeries.from_xy([las.header.mins[0], las.header.maxs[0]],
                              [las.header.mins[1], las.header.maxs[1]], crs=crs).to_crs(4326)
    return b.x.min(), b.y.min(), b.x.max(), b.y.max()


def download_nyc_footprints(bbox_wgs84, out_path):
    import requests
    lon0, lat0, lon1, lat1 = bbox_wgs84
    print(f'Downloading NYC building footprints for {lat0:.4f},{lon0:.4f} .. {lat1:.4f},{lon1:.4f} ...')
    r = None
    for geom_col in ('the_geom', 'geom', 'geometry'):   # column name has varied between dataset versions
        params = {'$where': f'within_box({geom_col}, {lat1}, {lon0}, {lat0}, {lon1})', '$limit': 50000}
        r = requests.get(NYC_API, params=params, timeout=120)
        if r.status_code != 400:
            break
    r.raise_for_status()
    data = r.json()
    if not data.get('features'):
        raise SystemExit('NYC Open Data returned no footprints for this extent.')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data))
    print(f'  {len(data["features"]):,} footprints cached to {out_path}')
    return out_path


def load_footprints(path, cloud_crs, height_field=None, height_units='auto'):
    gdf = gpd.read_file(path)
    gdf = gdf[gdf.geometry.notna() & gdf.geom_type.isin(['Polygon', 'MultiPolygon'])].copy()
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    gdf = gdf.to_crs(cloud_crs).reset_index(drop=True)
    cols = {c.lower(): c for c in gdf.columns}
    # footprint height (NYC: height_roof / heightroof in feet)
    hf = height_field or next((cols[c] for c in ('height_roof', 'heightroof') if c in cols), None)
    if hf and hf in gdf.columns:
        h = pd.to_numeric(gdf[hf], errors='coerce')
        feet = height_units == 'ft' or (height_units == 'auto' and hf.lower() in ('height_roof', 'heightroof'))
        gdf['fp_height_m'] = h * FT_TO_M if feet else h
    else:
        gdf['fp_height_m'] = np.nan
    idf = next((cols[c] for c in ('bin', 'doitt_id', 'osm_id', 'id') if c in cols), None)
    gdf['fp_id'] = gdf[idf].astype(str) if idf else gdf.index.astype(str)
    return gdf[['fp_id', 'fp_height_m', 'geometry']]


# ---------- point lookup ----------
def footprint_index(x, y, gdf, inset_m):
    """For each point: index of the footprint it falls in (after inset), or -1."""
    x0, y0 = x.min() - GRID_M, y.max() + GRID_M
    W = int(np.ceil((x.max() - x0) / GRID_M)) + 2
    H = int(np.ceil((y0 - y.min()) / GRID_M)) + 2
    geoms = gdf.geometry.buffer(-inset_m) if inset_m else gdf.geometry
    shapes = [(g, i + 1) for i, g in enumerate(geoms) if g is not None and not g.is_empty]
    grid = features.rasterize(shapes, out_shape=(H, W), transform=from_origin(x0, y0, GRID_M, GRID_M),
                              fill=0, dtype='int32')
    col = ((x - x0) / GRID_M).astype(np.int64)
    row = ((y0 - y) / GRID_M).astype(np.int64)
    return grid[row, col] - 1


def veg_class(hag):
    c = np.full(len(hag), 3, dtype='uint8')
    c[hag >= MEDIUM_VEG_M] = 4
    c[hag >= HIGH_VEG_M] = 5
    return c


# ---------- main ----------
def run(laz, footprints, out_laz, out_check, inset_m=0.5, strict=False, outside_m=2.0,
        height_field=None, height_units='auto'):
    las = laspy.read(str(laz))
    if 'HeightAboveGround' not in set(las.point_format.dimension_names):
        raise SystemExit('No HeightAboveGround dimension - run pdal_pipeline.py first.')
    crs = las.header.parse_crs()
    if crs is None:
        raise SystemExit('LAZ has no CRS - cannot match it to footprints.')
    x, y = np.asarray(las.x), np.asarray(las.y)
    hag = np.asarray(las.HeightAboveGround, dtype='float32')
    cls = np.asarray(las.classification, dtype='uint8').copy()
    before = np.bincount(cls, minlength=7)

    if footprints is None:
        name = laz.stem.replace('_classified_full', '').replace('_classified', '')
        footprints = CACHE_DIR / f'{name}_nyc_footprints.geojson'
        if not footprints.exists():
            download_nyc_footprints(cloud_extent_wgs84(las, crs), footprints)
        else:
            print(f'Using cached footprints {footprints}')
    gdf = load_footprints(footprints, crs, height_field, height_units)
    print(f'{len(gdf):,} footprints loaded')

    fp = footprint_index(x, y, gdf, inset_m)
    promote = (fp >= 0) & (hag >= TALL_M) & (cls != 6)
    cls[promote] = 6
    demoted = 0
    if strict:
        near = footprint_index(x, y, gdf.assign(geometry=gdf.geometry.buffer(outside_m)), 0)
        demote = (cls == 6) & (near < 0)
        cls[demote] = veg_class(hag[demote])
        demoted = int(demote.sum())
    las.classification = cls
    las.write(str(out_laz))

    after = np.bincount(cls, minlength=7)
    print(f'Relabelled {int(promote.sum()):,} points to building (6)' +
          (f', {demoted:,} building points outside footprints -> vegetation' if strict else ''))
    for c, n in [(2, 'ground'), (3, 'low veg'), (4, 'medium veg'), (5, 'high veg'), (6, 'building')]:
        print(f'  class {c} {n:<11} {before[c]:>11,} -> {after[c]:>11,}')
    if not strict:
        outside = int(((cls == 6) & (fp < 0)).sum())
        print(f'  ({outside:,} building points lie outside footprints - use --strict to reclassify them)')
    print(f'Saved {out_laz}')

    # per-building height check
    sel = (fp >= 0) & (cls == 6)
    df = pd.DataFrame({'i': fp[sel], 'hag': hag[sel]})
    stats = df.groupby('i')['hag'].agg(n_points='size', lidar_height_m=lambda s: np.percentile(s, ROOF_PCTL))
    chk = gdf.join(stats)
    chk['n_points'] = chk['n_points'].fillna(0).astype(int)
    chk['diff_m'] = chk['lidar_height_m'] - chk['fp_height_m']
    chk['status'] = np.select(
        [chk['n_points'] == 0, chk['fp_height_m'].isna(), chk['diff_m'].abs() > MISMATCH_M],
        ['no_lidar_points', 'no_footprint_height', 'height_mismatch'], 'ok')
    chk = chk.round({'fp_height_m': 1, 'lidar_height_m': 1, 'diff_m': 1})
    chk.to_file(out_check.with_suffix('.gpkg'), driver='GPKG')
    chk.drop(columns='geometry').to_csv(out_check.with_suffix('.csv'), index=False)
    print(f'Height check: {chk["status"].value_counts().to_dict()}')
    ok = chk[chk['status'].isin(['ok', 'height_mismatch'])]
    if len(ok):
        print(f'  LiDAR vs footprint height: median |diff| {ok["diff_m"].abs().median():.1f} m '
              f'over {len(ok):,} buildings')
    print(f'Saved {out_check.with_suffix(".gpkg")} (+ .csv)')


def main():
    ap = argparse.ArgumentParser(description='Footprint-guided building classification + height check.')
    ap.add_argument('--laz', default=str(DEFAULT_LAZ), help='output of export_classified_laz.py')
    ap.add_argument('--footprints', default=None,
                    help='local footprint file (default: download NYC footprints for the cloud extent)')
    ap.add_argument('--height-field', default=None, help='footprint height column (NYC: height_roof, auto)')
    ap.add_argument('--height-units', choices=['auto', 'm', 'ft'], default='auto')
    ap.add_argument('--inset', type=float, default=0.5,
                    help='shrink footprints by this many metres so overhanging trees are not relabelled')
    ap.add_argument('--strict', action='store_true',
                    help='also turn building points outside all footprints back into vegetation')
    ap.add_argument('--outside', type=float, default=2.0, help='--strict tolerance around footprints (m)')
    args = ap.parse_args()

    laz = Path(args.laz)
    laz = laz if laz.is_absolute() else PROJECT_ROOT / laz
    if not laz.exists():
        raise SystemExit(f'LAZ not found: {laz}  (run export_classified_laz.py first)')
    fps = None
    if args.footprints:
        fps = Path(args.footprints)
        fps = fps if fps.is_absolute() else PROJECT_ROOT / fps
    name = laz.stem.replace('_classified_full', '').replace('_classified', '')
    run(laz, fps, laz.parent / f'{name}_classified_bldg.laz', laz.parent / f'{name}_footprint_check',
        args.inset, args.strict, args.outside, args.height_field, args.height_units)


if __name__ == '__main__':
    main()
