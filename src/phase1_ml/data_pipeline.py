# data_pipeline.py
# Purpose: One command to run the Phase-1 data pipeline end to end, and to
#          refresh flood_risk in labelled_sites.csv after the DEM is loaded.
#
# Typical order the first time:
#   1. python -m src.phase1_ml.load_osm_data          (OSM -> PostGIS)
#   2. python -m src.phase1_ml.create_indexes         (GIST indexes -> fast distances)
#   3. python -m src.phase1_ml.dem_pipeline --dem <path>   (DEM + flood zones)
#   4. python -m src.phase1_ml.create_candidates      (grid -> shapefile)
#   5. python -m src.phase1_ml.feature_engineering    (features.csv, real flood_risk)
#   6. python -m src.phase1_ml.create_labels          (labelled_sites.csv per manual rule)
#   7. python -m src.phase1_ml.train_model            (train on 3 features)
#
# Or run 1,3,4,5 in one go with:  python -m src.phase1_ml.data_pipeline --all
# (DEM step is separate because it needs the --dem path.)
#
# --labels : (re)generate labelled_sites.csv from features.csv using the
#            manual's rule (create_labels). Use this after re-running the DEM +
#            feature step so flood_risk feeds into the labels.
# --sync   : older path -- only refresh feature columns in an EXISTING
#            labelled_sites.csv without changing labels. Prefer --labels.

import argparse
import pandas as pd

BASE         = 'F:/GeoSense_Agent'
FEATURES_CSV = f'{BASE}/data/processed/features.csv'
LABELS_CSV   = f'{BASE}/data/processed/labelled_sites.csv'

FEATURE_COLS = ['dist_road_m', 'dist_hospital_m', 'flood_risk']


def _id_col(df):
    return 'location_id' if 'location_id' in df.columns else 'location_i'


def sync_features_into_labels(features_csv=FEATURES_CSV, labels_csv=LABELS_CSV):
    """Refresh the feature columns (incl. flood_risk) in labelled_sites.csv
    from features.csv, matched by location id, WITHOUT touching the label."""
    feats = pd.read_csv(features_csv)
    labels = pd.read_csv(labels_csv)

    fid, lid = _id_col(feats), _id_col(labels)
    feat_slim = feats[[fid] + FEATURE_COLS].rename(columns={fid: '_join_id'})
    labels = labels.rename(columns={lid: '_join_id'})

    # drop stale feature columns from labels, then merge fresh ones in
    labels = labels.drop(columns=[c for c in FEATURE_COLS if c in labels.columns])
    merged = labels.merge(feat_slim, on='_join_id', how='left')
    merged = merged.rename(columns={'_join_id': lid})

    n_missing = merged['flood_risk'].isna().sum()
    if n_missing:
        print(f'WARNING: {n_missing} labelled rows had no matching feature row.')
    merged.to_csv(labels_csv, index=False)
    print(f'Synced {FEATURE_COLS} into {labels_csv}')
    print('flood_risk value counts now:')
    print(merged['flood_risk'].value_counts(dropna=False))


def run_all():
    """Run OSM load -> candidates -> features. (DEM is run separately with its
    --dem argument.) Imports are local so a failure in one step reports clearly."""
    from src.phase1_ml import (load_osm_data, create_indexes, create_candidates,
                               feature_engineering, create_labels)
    from src.phase1_ml.db_connection import get_engine
    import geopandas as gpd

    print('=== Loading OSM data ===');       load_osm_data.main()
    print('=== Creating spatial indexes ==='); create_indexes.create_indexes()
    print('=== Creating candidates ===')
    grid = create_candidates.create_grid()
    grid.to_file(f'{BASE}/data/processed/candidate_locations.shp')
    print('=== Building features ===')
    engine = get_engine()
    cands = gpd.read_file(f'{BASE}/data/processed/candidate_locations.shp')
    feats = feature_engineering.build_feature_table(cands, engine)
    feats.to_csv(FEATURES_CSV, index=False)
    print(f'Features saved to {FEATURES_CSV}')
    print('=== Creating labels (manual rule) ===')
    create_labels.make_labels(FEATURES_CSV, LABELS_CSV)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='GeoSense Phase-1 data pipeline.')
    ap.add_argument('--labels', action='store_true',
                    help='(Re)generate labelled_sites.csv from features.csv (manual rule).')
    ap.add_argument('--sync', action='store_true',
                    help='Older path: refresh feature columns in existing labels only.')
    ap.add_argument('--all', action='store_true',
                    help='Run OSM load -> candidates -> features -> labels.')
    args = ap.parse_args()

    if args.labels:
        from src.phase1_ml.create_labels import make_labels
        make_labels()
    elif args.sync:
        sync_features_into_labels()
    elif args.all:
        run_all()
    else:
        print('Nothing to do. Use --all (full build), --labels (regenerate '
              'labels), or --sync (refresh features only). See header for order.')
