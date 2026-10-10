# export_flood_geojson.py
# Purpose: Export a city's flood-risk polygons from PostGIS to GeoJSON for the Digital Twin.
# (Python version of the manual's D.2 SQL, adapted to this project's per-city schemas:
#  table = <city>.<city>_floodzones, geometry already EPSG:4326.)
# Run:  python src/phase4_agent/export_flood_geojson.py --city chennai
import sys, json, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'phase1_ml'))
from sqlalchemy import text
from db_connection import get_engine

def export_flood(city, out_path, suffix='floodzones'):
    schema = table = None
    schema, table = city.lower(), f'{city.lower()}_{suffix}'
    sql = text(f"""
        SELECT jsonb_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(jsonb_agg(jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(ST_Transform(ST_SetSRID(geometry, 4326), 4326), 6)::jsonb,
                'properties', jsonb_build_object('risk_level', risk_level, 'risk_code', risk_code)
            )), '[]'::jsonb)
        ) FROM {schema}.{table}
    """)
    with get_engine().connect() as conn:
        gj = conn.execute(sql).scalar()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(gj, f)
    counts = {}
    for ft in gj['features']:
        k = ft['properties']['risk_level']; counts[k] = counts.get(k, 0) + 1
    print(f"Exported {len(gj['features'])} flood polygons {counts} to {out_path}")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--city', default='chennai')
    ap.add_argument('--suffix', default='floodzones', help='floodzones | floodzones_abs')
    ap.add_argument('--out', default=str(ROOT / 'frontend' / 'digital_twin' / 'public' / 'flood_risk.geojson'))
    a = ap.parse_args()
    export_flood(a.city, Path(a.out), a.suffix)

