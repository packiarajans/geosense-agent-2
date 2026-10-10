# pdal_pipeline.py
# Purpose: Clean a LiDAR point cloud — remove noise, classify ground vs non-ground,
#          compute HeightAboveGround.
#
# Manual step E.2 (default, no arguments): processes data/lidar/raw/study_area.laz
#        python src/phase3_lidar/pdal_pipeline.py
# Other inputs:
#   A) A local LAS/LAZ file:
#        python src/phase3_lidar/pdal_pipeline.py --input data/lidar/raw/study_area.laz
#   B) Stream free USGS 3DEP LiDAR (USA only) for a lon/lat bbox — no manual download:
#        python src/phase3_lidar/pdal_pipeline.py --usgs --bbox -73.968 40.775 -73.962 40.780 --name central_park
#      (the script finds which USGS dataset covers the bbox automatically)
#   Add --urban for city centres / skyscrapers (wider ground-filter window so big roofs
#   are not classed as ground):
#        python src/phase3_lidar/pdal_pipeline.py --usgs --bbox -74.015 40.703 -74.005 40.712 --name nyc_fidi --urban
#
# Output: <project>/data/lidar/processed/<name>_classified.laz
import argparse
import json
import math
import os
from pathlib import Path

import pdal
import requests

# Anchor paths to the project root (F:\GeoSense_Agent), not the folder you run from.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / 'data' / 'lidar' / 'raw'
OUT_DIR = PROJECT_ROOT / 'data' / 'lidar' / 'processed'
DEFAULT_INPUT = RAW_DIR / 'study_area.laz'   # manual E.2: python src/phase3_lidar/pdal_pipeline.py

USGS_EPT_ROOT = 'https://s3-us-west-2.amazonaws.com/usgs-lidar-public'
USGS_INDEX = ('https://raw.githubusercontent.com/hobuinc/usgs-lidar/'
              'master/boundaries/resources.geojson')


# ---------------------------------------------------------------- helpers
def utm_epsg(lon, lat):
    """WGS84 UTM zone EPSG for a point (e.g. NYC -> 32618, Chennai -> 32644)."""
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def lonlat_to_3857(lon, lat):
    x = lon * 20037508.34 / 180
    y = math.log(math.tan((90 + lat) * math.pi / 360)) * 6378137
    return x, y


def ept_reader(resource, bbox, resolution=None):
    x0, y0 = lonlat_to_3857(bbox[0], bbox[1])
    x1, y1 = lonlat_to_3857(bbox[2], bbox[3])
    r = {'type': 'readers.ept', 'filename': f'{USGS_EPT_ROOT}/{resource}/ept.json',
         'bounds': f'([{x0}, {x1}], [{y0}, {y1}])'}
    if resolution:
        r['resolution'] = resolution  # coarse sample - fast point-count probe
    return r


def probe_points(resource, bbox):
    """Quick coarse read (~5 m spacing) to see whether a dataset really has points here."""
    try:
        return pdal.Pipeline(json.dumps([ept_reader(resource, bbox, resolution=5)])).execute()
    except RuntimeError as e:
        print(f'  {resource}: probe failed ({str(e)[:80]})')
        return 0


def choose_usgs_resource(bbox):
    """Among datasets whose footprint covers the bbox, pick the one with the most points there.
    (Footprints are coarse outlines: a dataset can 'cover' an area yet have no points in it.)"""
    candidates = find_usgs_resource(bbox)
    best, best_n = None, 0
    for name in candidates:
        n = probe_points(name, bbox)
        print(f'  {name}: ~{n:,} points in a coarse sample')
        if n > best_n:
            best, best_n = name, n
    if not best:
        raise SystemExit('None of the covering USGS datasets has points in this bbox.')
    return best


def find_usgs_resource(bbox):
    """Return all USGS EPT dataset names whose footprint covers the bbox centre."""
    from shapely.geometry import Point, shape
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    idx = requests.get(USGS_INDEX, timeout=120).json()
    hits = [(f['properties']['name'], f['properties'].get('count', 0))
            for f in idx['features'] if shape(f['geometry']).contains(Point(cx, cy))]
    if not hits:
        raise SystemExit('No USGS 3DEP dataset covers this bbox (USGS covers the USA only).')
    print('USGS datasets covering this area:', ', '.join(h[0] for h in hits))
    return [h[0] for h in hits]


# ---------------------------------------------------------------- pipeline
# SMRF settings. The default window (18 m) is smaller than big building footprints, so large
# flat roofs get labelled "ground". The urban profile uses a wider window for city centres.
SMRF_PROFILES = {
    'default': {},                                                     # PDAL defaults
    'urban':   {'window': 60.0, 'slope': 0.2, 'threshold': 0.45, 'scalar': 1.2},
}
MIN_HAG_M = -2.0     # points further below ground than this are noise
MAX_HAG_M = 600.0    # taller than any real structure -> noise (birds, multipath)


def build_pipeline(reader, output_laz, epsg, smrf_profile='default'):
    """PDAL pipeline as a list of stages."""
    stages = [reader]
    if epsg:
        # Metric, locally-correct coordinates (SMRF & HAG assume metres)
        stages.append({'type': 'filters.reprojection', 'out_srs': f'EPSG:{epsg}'})
    return stages + [
        # Drop points the data provider already flagged as noise
        # (7 = low noise, 18 = high noise in LAS 1.4) BEFORE we reset classes.
        # PDAL ORs ranges on the same dimension, so list the classes to KEEP.
        {'type': 'filters.range',
         'limits': 'Classification[0:6],Classification[8:17],Classification[19:255]'},
        # Start from a clean slate so the output reflects OUR classification
        {'type': 'filters.assign', 'value': 'Classification = 1'},
        # Extended Local Minimum: flags isolated LOW points (water/glass multipath) as noise
        {'type': 'filters.elm'},
        # Statistical outliers (isolated points in any direction) -> noise
        {'type': 'filters.outlier', 'method': 'statistical',
         'mean_k': 8, 'multiplier': 2.5},
        # ...and actually drop them (the filters above only label, they don't remove)
        {'type': 'filters.range', 'limits': 'Classification![7:7]'},
        # Ground (2) vs non-ground using the Simple Morphological Filter
        {'type': 'filters.smrf', **SMRF_PROFILES[smrf_profile]},
        # HeightAboveGround = height above nearest ground points
        {'type': 'filters.hag_nn'},
        # Remove anything still impossibly far below / above the ground
        {'type': 'filters.range', 'limits': f'HeightAboveGround[{MIN_HAG_M}:{MAX_HAG_M}]'},
        {'type': 'writers.las', 'filename': str(output_laz),
         'compression': 'laszip', 'extra_dims': 'all', 'minor_version': 4},
    ]


def run(reader, output_laz, epsg, smrf_profile='default'):
    output_laz.parent.mkdir(parents=True, exist_ok=True)
    print(f'Ground filter profile: {smrf_profile} {SMRF_PROFILES[smrf_profile] or "(PDAL defaults)"}')
    stages = build_pipeline(reader, output_laz, epsg, smrf_profile)
    pipeline = pdal.Pipeline(json.dumps(stages))
    count = pipeline.execute()
    if count == 0:
        raise SystemExit('0 points read - the dataset has no data in this area. '
                         'Try another --resource, or check the bbox (MIN_LON MIN_LAT MAX_LON MAX_LAT).')
    arr = pipeline.arrays[0]
    ground = int((arr['Classification'] == 2).sum())
    print(f'Processed {count} points. Output saved to {output_laz}')
    print(f'  ({ground:,} ground, {count - ground:,} non-ground)')
    print(f'HeightAboveGround range: {arr["HeightAboveGround"].min():.1f} '
          f'to {arr["HeightAboveGround"].max():.1f} m')
    return pipeline


def main():
    ap = argparse.ArgumentParser(description='Denoise + ground-classify a LiDAR point cloud.')
    src = ap.add_mutually_exclusive_group()
    src.add_argument('--input', default=None,
                     help='local LAS/LAZ file (default: data/lidar/raw/study_area.laz, as in the manual)')
    src.add_argument('--usgs', action='store_true', help='stream USGS 3DEP data for --bbox')
    ap.add_argument('--bbox', nargs=4, type=float, metavar=('MIN_LON', 'MIN_LAT', 'MAX_LON', 'MAX_LAT'))
    ap.add_argument('--name', default=None, help='output name (default: input file name)')
    ap.add_argument('--epsg', type=int, default=None, help='output CRS (default: auto UTM)')
    ap.add_argument('--resource', default=None, help='USGS dataset name (default: auto-detect)')
    ap.add_argument('--urban', action='store_true',
                    help='wide ground-filter window for city centres / skyscrapers '
                         '(stops big roofs being classed as ground)')
    args = ap.parse_args()

    if args.usgs:
        if not args.bbox:
            ap.error('--usgs needs --bbox MIN_LON MIN_LAT MAX_LON MAX_LAT')
        b = args.bbox
        area_km2 = ((b[2] - b[0]) * 111 * math.cos(math.radians(b[1]))) * ((b[3] - b[1]) * 111)
        if area_km2 > 4:
            print(f'Warning: bbox is ~{area_km2:.1f} km2 - city LiDAR can be 10-50 M points/km2.')
        resource = args.resource or choose_usgs_resource(b)
        print(f'Using USGS dataset: {resource}')
        reader = ept_reader(resource, b)
        epsg = args.epsg or utm_epsg((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        name = args.name or resource.lower()
    else:
        in_path = Path(args.input or DEFAULT_INPUT)
        if not in_path.is_absolute():
            in_path = PROJECT_ROOT / in_path
        if not in_path.exists():
            raise SystemExit(f'Input not found: {in_path}\n'
                             f'Put a LAS/LAZ file in {RAW_DIR} or use --usgs --bbox ...')
        reader = str(in_path)
        # Local files are usually already in a projected (metre) CRS; only
        # reproject if asked. Use --epsg if the file is in lat/lon degrees.
        epsg = args.epsg
        name = args.name or in_path.stem

    print(f'Output CRS: {"EPSG:%d" % epsg if epsg else "unchanged (input CRS)"}')
    run(reader, OUT_DIR / f'{name}_classified.laz', epsg, 'urban' if args.urban else 'default')


if __name__ == '__main__':
    main()
