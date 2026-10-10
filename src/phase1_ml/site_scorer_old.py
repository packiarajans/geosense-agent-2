# site_scorer.py
# PURPOSE: The main site scoring module for GeoSense Agent
# INPUT:  latitude (float), longitude (float)
# OUTPUT: dictionary with risk_score, opportunity_score, explanation

import joblib
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from sqlalchemy import text
from db_connection import get_engine
import shap

# Load model once when the module is first imported (not every call)
_model = None
_explainer = None

def _load_model():
    global _model, _explainer
    if _model is None:
        _model = joblib.load('F:/GeoSense_Agent/models/saved/site_scorer_model.pkl')
        _explainer = shap.TreeExplainer(_model)
    return _model, _explainer

def _get_features_for_point(lat, lon, engine):
    """Fetch all geographic features for a single lat/lon point."""
    pt = Point(lon, lat)
    pt_gdf = gpd.GeoDataFrame([{'geometry': pt}], crs='EPSG:4326')
    pt_proj = pt_gdf.to_crs(epsg=32644)

    def dist_to_layer(table):
        layer = gpd.read_postgis(f'SELECT geometry FROM {table} LIMIT 5000',
                                 engine, geom_col='geometry').to_crs(epsg=32644)
        return float(layer.geometry.distance(pt_proj.geometry.iloc[0]).min())

    features = {
        'dist_road_m':     dist_to_layer('osm_roads'),
        'dist_hospital_m': dist_to_layer('osm_poi'),
        'flood_risk':      0.0,   # default: expand with real flood data
    }
    return features

def score_location(lat: float, lon: float) -> dict:
    """
    Main function: score any location on Earth.
    Returns: dict with risk_score, opportunity_score, explanation
    """
    engine = get_engine()
    model, explainer = _load_model()

    # Get features
    features = _get_features_for_point(lat, lon, engine)
    feature_names = ['dist_road_m', 'dist_hospital_m', 'flood_risk']
    X = pd.DataFrame([[features[f] for f in feature_names]], columns=feature_names)

    # Predict probability of being a 'good site'
    proba = model.predict_proba(X)[0]
    opportunity_score = round(float(proba[1]) * 10, 2)  # 0-10 scale
    risk_score        = round(float(proba[0]) * 10, 2)  # 0-10 scale

    # SHAP explanation
    shap_vals = explainer.shap_values(X)
    if hasattr(shap_vals, 'values'):
        shap_vals = shap_vals.values

    if isinstance(shap_vals, list):
        shap_for_positive = shap_vals[1][0] if len(shap_vals) > 1 else shap_vals[0][0]
    elif isinstance(shap_vals, np.ndarray):
        if shap_vals.ndim == 3:
            shap_for_positive = shap_vals[0, :, 1] if shap_vals.shape[2] > 1 else shap_vals[0, :, 0]
        elif shap_vals.ndim == 2:
            shap_for_positive = shap_vals[0]
        else:
            shap_for_positive = np.ravel(shap_vals)
    else:
        shap_for_positive = np.array(shap_vals).flatten()

    explanation = {
        name: round(float(np.ravel(val)[0]), 4)
        for name, val in zip(feature_names, shap_for_positive)
    }

    return {
        'latitude':          lat,
        'longitude':         lon,
        'opportunity_score': opportunity_score,
        'risk_score':        risk_score,
        'features':          features,
        'shap_explanation':  explanation,
        'verdict':           'GOOD SITE' if opportunity_score > 5 else 'POOR SITE'
    }

# Test the module when run directly
if __name__ == '__main__':
    result = score_location(13.0827, 80.2707)  # Chennai city centre
    for key, value in result.items():
        print(f'{key:20s}: {value}') 