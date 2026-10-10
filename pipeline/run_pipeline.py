# run_pipeline.py
# One command to run the whole GeoSense Phase-1 pipeline for a given city.
#
# USAGE (from the project root F:\GeoSense_Agent, geosense env active):
#   python -m pipeline.run_pipeline --city chennai
#
# The city's bounding box, DEM path and OSM folder come from
# pipeline/cities.json. Override any of them on the command line:
#   python -m pipeline.run_pipeline --city mumbai ^
#       --dem F:/Cities/mumbai/dem/mumbai_dem.tif ^
#       --osm-dir F:/WesternZone_OSM ^
#       --bbox 72.77 18.89 72.99 19.27
#
# Add a brand-new city permanently by adding an entry to pipeline/cities.json
# (bbox + dem_path + osm_dir) -- then just: --city <name>.
#
# Handy flags:
#   --skip-osm     don't reload OSM (use if it's already in PostGIS)
#   --skip-dem     don't reload the DEM / rebuild flood zones
#   --skip-tests   don't run pytest at the end
#   --mode         flood banding: relative (default) or absolute

import os
import sys
import time
import argparse
import subprocess

# Make sure the project root is importable whether run as a module or a script
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _run_step(title, fn):
    print('\n' + '=' * 70)
    print(f'STEP: {title}')
    print('=' * 70)
    t0 = time.time()
    fn()
    print(f'--- done in {time.time() - t0:.1f}s ---')


def main():
    ap = argparse.ArgumentParser(
        description='Run the GeoSense Phase-1 pipeline end to end for one city.')
    ap.add_argument('--city', required=True, help='City name, e.g. chennai.')
    ap.add_argument('--dem', default=None, help='DEM GeoTIFF path (else from cities.json).')
    ap.add_argument('--osm-dir', default=None, help='OSM shapefile folder (else from cities.json).')
    ap.add_argument('--bbox', nargs=4, type=float, default=None,
                    metavar=('MIN_LON', 'MIN_LAT', 'MAX_LON', 'MAX_LAT'),
                    help='Bounding box (else from cities.json).')
    ap.add_argument('--mode', choices=['relative', 'absolute'], default='relative')
    ap.add_argument('--skip-osm', action='store_true')
    ap.add_argument('--skip-dem', action='store_true')
    ap.add_argument('--skip-tests', action='store_true')
    args = ap.parse_args()

    city = args.city.strip().lower()
    # IMPORTANT: set the active city BEFORE importing the step modules, so
    # config.current_city() picks it up everywhere.
    os.environ['GEOSENSE_CITY'] = city

    from src.phase1_ml import (config, load_osm_data, create_indexes,
                               dem_pipeline, create_candidates,
                               feature_engineering, create_labels,
                               train_model, site_scorer)
    from src.phase1_ml.db_connection import get_engine
    import geopandas as gpd

    dem = args.dem or config.dem_path(city)
    osm = args.osm_dir or config.osm_dir(city)
    box = tuple(args.bbox) if args.bbox else config.bbox(city)

    # --- validate what we need up front, with clear messages ---------------
    if box is None:
        sys.exit(f'ERROR: no bounding box for "{city}". Add it to '
                 f'pipeline/cities.json or pass --bbox.')
    if not args.skip_dem and not dem:
        sys.exit(f'ERROR: no DEM path for "{city}". Add dem_path to '
                 f'pipeline/cities.json or pass --dem (or use --skip-dem).')
    if not args.skip_osm and not osm:
        sys.exit(f'ERROR: no OSM folder for "{city}". Add osm_dir to '
                 f'pipeline/cities.json or pass --osm-dir (or use --skip-osm).')

    print(f'City: {city}')
    print(f'  bbox     : {box}')
    print(f'  DEM      : {dem if not args.skip_dem else "(skipped)"}')
    print(f'  OSM dir  : {osm if not args.skip_osm else "(skipped)"}')
    print(f'  schema   : {config.schema(city)}')

    t_start = time.time()

    # 1. OSM -> PostGIS
    if not args.skip_osm:
        def _osm():
            load_osm_data.DATA_DIR = osm
            load_osm_data.main()
        _run_step('Load OSM data', _osm)
    else:
        print('\n(skipping OSM load)')

    # 2. Spatial indexes (fast distances)
    _run_step('Create spatial indexes', create_indexes.create_indexes)

    # 3. DEM + flood zones
    if not args.skip_dem:
        def _dem():
            aoi = dem_pipeline.load_dem_to_postgis(dem_path=dem, aoi_override=city)
            if not aoi:
                raise RuntimeError('DEM load did not complete.')
            dem_pipeline.create_floodzones(get_engine(), aoi, mode=args.mode)
        _run_step('Load DEM & build flood zones', _dem)
    else:
        print('\n(skipping DEM / flood zones)')

    # 4. Candidate grid
    def _cands():
        grid = create_candidates.create_grid(box)
        grid.to_file(config.CANDIDATES_SHP)
        print(f'Saved {config.CANDIDATES_SHP}')
    _run_step('Create candidate grid', _cands)

    # 5. Feature engineering
    def _feats():
        cands = gpd.read_file(config.CANDIDATES_SHP)
        feats = feature_engineering.build_feature_table(cands, get_engine())
        feats.to_csv(config.FEATURES_CSV, index=False)
        print(f'Saved {config.FEATURES_CSV}')
    _run_step('Feature engineering', _feats)

    # 6. Labels (manual rule)
    _run_step('Create labels', create_labels.make_labels)

    # 7. Train models
    _run_step('Train models', train_model.train_and_evaluate)

    # 8. Sample score at the bbox centre (sanity check)
    def _score():
        lat = (box[1] + box[3]) / 2.0
        lon = (box[0] + box[2]) / 2.0
        result = site_scorer.score_location(lat, lon)
        print(f'Sample score at city centre ({lat:.4f}, {lon:.4f}):')
        for k, v in result.items():
            print(f'  {k:20s}: {v}')
    _run_step('Sample site score', _score)

    # 9. Unit tests
    if not args.skip_tests:
        def _tests():
            subprocess.run([sys.executable, '-m', 'pytest', '-v',
                            'tests/test_site_scorer.py'], cwd=ROOT, check=False)
        _run_step('Run unit tests', _tests)

    print('\n' + '#' * 70)
    print(f'PIPELINE COMPLETE for "{city}" in {time.time() - t_start:.1f}s')
    print('#' * 70)


if __name__ == '__main__':
    main()
