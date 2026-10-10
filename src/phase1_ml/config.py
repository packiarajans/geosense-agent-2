# config.py
# Central, city-aware configuration for the GeoSense Phase-1 pipeline.
#
# The active city comes from the GEOSENSE_CITY environment variable (set by
# run_pipeline.py) and defaults to 'chennai' so the individual scripts still
# work on their own. Per-city settings (bounding box, DEM path, OSM folder)
# live in pipeline/cities.json so you can add a city without touching code.

import os
import json

BASE = os.environ.get('GEOSENSE_BASE', 'F:/GeoSense_Agent')
CITIES_JSON = os.path.join(BASE, 'pipeline', 'cities.json')

DEFAULT_CITY = 'chennai'

# --- shared file paths (one city processed at a time) ----------------------
DATA_PROCESSED = f'{BASE}/data/processed'
FEATURES_CSV   = f'{DATA_PROCESSED}/features.csv'
LABELS_CSV     = f'{DATA_PROCESSED}/labelled_sites.csv'
CANDIDATES_SHP = f'{DATA_PROCESSED}/candidate_locations.shp'
MODEL_PATH     = f'{BASE}/models/saved/site_scorer_model.pkl'
REPORT_DIR     = f'{BASE}/outputs/reports'


def _registry():
    try:
        with open(CITIES_JSON) as f:
            return json.load(f)
    except Exception:
        return {}


def current_city():
    """Active city name (lowercase). Set via GEOSENSE_CITY env var."""
    return os.environ.get('GEOSENSE_CITY', DEFAULT_CITY).strip().lower()


def city_config(city=None):
    return _registry().get((city or current_city()).lower(), {})


def schema(city=None):
    """PostGIS schema name for a city (lowercase)."""
    return (city or current_city()).lower()


def bbox(city=None):
    """(min_lon, min_lat, max_lon, max_lat) for the city, or None."""
    b = city_config(city).get('bbox')
    return tuple(b) if b else None


def dem_table(city=None):
    s = schema(city)
    return f'{s}.{s}_dem'


def floodzone_table(city=None):
    s = schema(city)
    return f'{s}.{s}_floodzones'


def dem_path(city=None):
    return city_config(city).get('dem_path')


def osm_dir(city=None):
    return city_config(city).get('osm_dir')
