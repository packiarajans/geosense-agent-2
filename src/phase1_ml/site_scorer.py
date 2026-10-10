# site_scorer.py
# PURPOSE: Main site scoring module for GeoSense Agent
# INPUT:  latitude (float), longitude (float)
# OUTPUT: dict with risk_score, opportunity_score, explanation
#
# FIXES:
#   * flood_risk is a real feature again -- looked up live from the DEM-derived
#     flood-zone polygons (same table + 0-10 mapping as feature_engineering.py),
#     so the 3 features here match the 3 features the model trained on.
#   * DataFrame input -> no sklearn feature-name warning.
#   * Robust SHAP unpacking (fixes the "0-dimensional arrays" crash).
#   * Import works as module (python -m ...) or as a script.

import joblib
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from sqlalchemy import text
import shap

try:
    from src.phase1_ml.db_connection import get_engine
    from src.phase1_ml import config
except ModuleNotFoundError:
    from db_connection import get_engine
    import config

MODEL_PATH      = config.MODEL_PATH
FEATURE_NAMES   = ['dist_road_m', 'dist_hospital_m', 'flood_risk']
RISK_MAP        = {'high': 8, 'medium': 5, 'low': 2}

_model = None
_explainer = None


def _load_model():
    global _model, _explainer
    if _model is None:
        _model = joblib.load(MODEL_PATH)
        _explainer = shap.TreeExplainer(_model)
    return _model, _explainer


def _flood_risk_for_point(lat, lon, engine):
    """0-10 flood risk (higher=riskier) from the flood-zone polygon a point
    falls in. Returns 0 if the table is missing or the point is outside all
    zones -- consistent with feature_engineering.assign_flood_risk."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text(f"""
                SELECT risk_level FROM {config.floodzone_table()}
                WHERE ST_Contains(geometry,
                                  ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
                LIMIT 1
            """), {'lon': lon, 'lat': lat}).fetchone()
    except Exception:
        return 0.0
    if row is None or row[0] is None:
        return 0.0
    return float(RISK_MAP.get(row[0], 0))


def _get_features_for_point(lat, lon, engine):
    """Fetch all model features for a single lat/lon point.

    Distances use the PostGIS GIST index via the <-> KNN operator, so each
    lookup is a fast indexed nearest-neighbour query (run create_indexes.py
    once so the indexes exist)."""
    def dist_to_layer(conn, table):
        row = conn.execute(text(f"""
            SELECT ST_Distance(l.geometry::geography,
                               ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography)
            FROM {table} l
            ORDER BY l.geometry <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
            LIMIT 1
        """), {'lon': lon, 'lat': lat}).fetchone()
        return float(row[0]) if row and row[0] is not None else float('nan')

    with engine.connect() as conn:
        dist_road = dist_to_layer(conn, 'osm_roads')
        dist_hosp = dist_to_layer(conn, 'osm_poi')

    return {
        'dist_road_m':     dist_road,
        'dist_hospital_m': dist_hosp,
        'flood_risk':      _flood_risk_for_point(lat, lon, engine),
    }


def _positive_class_shap(explainer, X, model):
    """1-D SHAP vector for the positive ('good') class, any SHAP shape."""
    good_idx = list(model.classes_).index(1)
    shap_vals = explainer.shap_values(X)
    if isinstance(shap_vals, list):
        vec = np.asarray(shap_vals[good_idx])[0]
    else:
        arr = np.asarray(shap_vals)
        vec = arr[0, :, good_idx] if arr.ndim == 3 else arr[0]
    return np.ravel(vec)


def score_location(lat: float, lon: float) -> dict:
    """Score any location. Returns risk_score, opportunity_score, explanation."""
    engine = get_engine()
    model, explainer = _load_model()

    features = _get_features_for_point(lat, lon, engine)
    X = pd.DataFrame([[features[f] for f in FEATURE_NAMES]], columns=FEATURE_NAMES)

    proba = model.predict_proba(X)[0]
    good_idx = list(model.classes_).index(1)
    poor_idx = list(model.classes_).index(0)

    opportunity_score = round(float(proba[good_idx]) * 10, 2)   # 0-10
    risk_score        = round(float(proba[poor_idx]) * 10, 2)   # 0-10

    shap_for_positive = _positive_class_shap(explainer, X, model)
    explanation = {
        name: round(float(val), 4)
        for name, val in zip(FEATURE_NAMES, shap_for_positive)
    }

    return {
        'latitude':          lat,
        'longitude':         lon,
        'opportunity_score': opportunity_score,
        'risk_score':        risk_score,
        'features':          features,
        'shap_explanation':  explanation,
        'verdict':           'GOOD SITE' if opportunity_score > 5 else 'POOR SITE',
    }


if __name__ == '__main__':
    result = score_location(13.0827, 80.2707)   # Chennai city centre
    for key, value in result.items():
        print(f'{key:20s}: {value}')
