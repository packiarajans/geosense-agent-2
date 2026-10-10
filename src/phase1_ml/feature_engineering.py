# feature_engineering.py
# Purpose: Calculate geographic features for every candidate location.
#
# FIXES:
#   * flood_risk reads the REAL flood-zone table from dem_pipeline.py.
#   * Distances are now computed INSIDE PostGIS using the GIST index and the
#     <-> KNN operator (fast), instead of pulling whole layers into Python.
#     Run create_indexes.py first so the indexes exist.
#   * Dual-mode import (module or script).

import geopandas as gpd
import pandas as pd
import numpy as np
from sqlalchemy import text

try:
    from src.phase1_ml.db_connection import get_engine
    from src.phase1_ml import config
except ModuleNotFoundError:
    from db_connection import get_engine
    import config

RISK_MAP  = {'high': 8, 'medium': 5, 'low': 2}   # 0-10 scale, higher = riskier
TMP_TABLE = 'tmp_candidates'


def calc_distances_knn(candidates_gdf, engine, id_col):
    """Index-accelerated nearest-distance (metres) to osm_roads and osm_poi
    for every candidate, computed in PostGIS via the <-> KNN operator.

    Returns a DataFrame indexed by id_col with dist_road_m, dist_hospital_m.
    Falls back to the in-Python method if the DB path fails.
    """
    try:
        pts = candidates_gdf[[id_col, 'geometry']].copy().to_crs(epsg=4326)
        pts = pts.rename(columns={id_col: 'loc_id'})
        # push candidate points to a temp table (replaced each run)
        pts.to_postgis(TMP_TABLE, engine, if_exists='replace', index=False)

        with engine.begin() as conn:
            conn.execute(text(
                f'CREATE INDEX IF NOT EXISTS {TMP_TABLE}_geom_idx '
                f'ON {TMP_TABLE} USING GIST (geometry);'))

        def knn(layer):
            sql = text(f"""
                SELECT c.loc_id,
                       nn.dist AS d
                FROM {TMP_TABLE} c
                CROSS JOIN LATERAL (
                    SELECT ST_Distance(l.geometry::geography,
                                       c.geometry::geography) AS dist
                    FROM {layer} l
                    ORDER BY l.geometry <-> c.geometry
                    LIMIT 1
                ) nn;
            """)
            return pd.read_sql(sql, engine).set_index('loc_id')['d']

        road = knn('osm_roads')
        hosp = knn('osm_poi')
        out = pd.DataFrame({'dist_road_m': road, 'dist_hospital_m': hosp})
        return out
    except Exception as e:
        print(f'  KNN path failed ({e}); falling back to in-Python distances.')
        return None


def _calc_distance_python(candidates_gdf, layer_table, engine):
    """Fallback: nearest distance computed in geopandas (slower)."""
    cands_proj = candidates_gdf.to_crs(epsg=32644)
    layer = gpd.read_postgis(f'SELECT geometry FROM {layer_table}',
                             engine, geom_col='geometry').to_crs(epsg=32644)
    target = layer.geometry.union_all() if hasattr(layer.geometry, 'union_all') \
        else layer.geometry.unary_union
    return cands_proj.geometry.distance(target)


def assign_flood_risk(candidates_gdf, engine, floodzone_table=None):
    """Flood-risk score (0-10) from overlap with the DEM-derived flood zones."""
    floodzone_table = floodzone_table or config.floodzone_table()
    try:
        flood_gdf = gpd.read_postgis(
            f'SELECT geometry, risk_level FROM {floodzone_table}',
            engine, geom_col='geometry')
    except Exception as e:
        print(f'  WARNING: flood-zone table {floodzone_table} not found ({e}).')
        print('  Run dem_pipeline.py first. flood_risk set to 0 for now.')
        return pd.Series(np.zeros(len(candidates_gdf)), index=candidates_gdf.index)

    if flood_gdf.crs is None:
        flood_gdf.set_crs(epsg=4326, inplace=True)
    cands = candidates_gdf.to_crs(flood_gdf.crs)
    joined = gpd.sjoin(cands, flood_gdf, how='left', predicate='within')
    joined = joined[~joined.index.duplicated(keep='first')]
    risk = joined['risk_level'].map(RISK_MAP)
    return risk.reindex(candidates_gdf.index).fillna(0)


def build_feature_table(candidates_gdf, engine):
    id_col = 'location_id' if 'location_id' in candidates_gdf.columns else 'location_i'
    df = candidates_gdf[[id_col, 'latitude', 'longitude']].copy()

    print('Calculating: distances to nearest road & hospital (PostGIS KNN) ...')
    knn = calc_distances_knn(candidates_gdf, engine, id_col)
    if knn is not None:
        df = df.merge(knn, left_on=id_col, right_index=True, how='left')
    else:
        df['dist_road_m']     = _calc_distance_python(candidates_gdf, 'osm_roads', engine)
        df['dist_hospital_m'] = _calc_distance_python(candidates_gdf, 'osm_poi', engine)

    print('Calculating: flood risk score ...')
    df['flood_risk'] = assign_flood_risk(candidates_gdf, engine)

    print(f'Feature table built: {len(df)} locations, {len(df.columns)} columns')
    print('flood_risk value counts:')
    print(df['flood_risk'].value_counts(dropna=False))
    return df


if __name__ == '__main__':
    engine = get_engine()
    cands = gpd.read_file('data/processed/candidate_locations.shp')
    features = build_feature_table(cands, engine)
    features.to_csv('data/processed/features.csv', index=False)
    print('Features saved to data/processed/features.csv')
