# make_floodzones_absolute.py
# Purpose: Build flood zones from a city DEM using ABSOLUTE elevation bands (metres above the DEM datum, NAVD88 for NYC)
# instead of the pipeline's relative terciles. Creates <city>.<city>_floodzones_abs (same columns as _floodzones).
# This is an elevation heuristic, NOT a hydraulic/surge model. Defaults: high < 3 m, medium 3-6 m, low >= 6 m.
# Run: python src/phase4_agent/make_floodzones_absolute.py --city newyork --high 3 --medium 6
import sys, argparse
from pathlib import Path
from datetime import datetime
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'phase1_ml'))
from sqlalchemy import text
from db_connection import get_engine

def main(city, hi, med):
    s = city.lower(); dem = f'{s}.{s}_dem'; out = f'{s}.{s}_floodzones_abs'
    reclass = f'[-10000-{hi}):1, [{hi}-{med}):2, [{med}-100000]:3'
    print('bands:', reclass)
    with get_engine().begin() as c:
        c.execute(text(f"""
            DROP TABLE IF EXISTS {out};
            CREATE TABLE {out} AS
            SELECT (dp).geom AS geometry, (dp).val AS risk_code
            FROM (SELECT ST_DumpAsPolygons(rast) AS dp
                  FROM (SELECT ST_Reclass(rast, 1, :r, '8BUI', 0) AS rast FROM {dem}) x) y;
        """), {'r': reclass})
        c.execute(text(f"UPDATE {out} SET geometry = ST_SetSRID(geometry, 4326) WHERE ST_SRID(geometry) <> 4326"))
        c.execute(text(f"ALTER TABLE {out} ADD COLUMN risk_level VARCHAR"))
        c.execute(text(f"UPDATE {out} SET risk_level = CASE risk_code::int WHEN 1 THEN 'high' WHEN 2 THEN 'medium' WHEN 3 THEN 'low' ELSE 'unknown' END"))
        c.execute(text(f"CREATE INDEX IF NOT EXISTS {s}_floodzones_abs_geom_idx ON {out} USING GIST (geometry)"))
        c.execute(text(f"ALTER TABLE {out} ADD COLUMN created_at TIMESTAMPTZ"))
        c.execute(text(f"UPDATE {out} SET created_at = :t"), {'t': datetime.now().astimezone().isoformat(timespec='seconds')})
    with get_engine().connect() as c:
        for r in c.execute(text(f"SELECT risk_level, count(*), round((sum(ST_Area(geometry::geography))/1e6)::numeric,1) FROM {out} GROUP BY 1 ORDER BY 1")):
            print(r[0], r[1], 'polygons', r[2], 'km2')
    print('created', out)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--city', default='newyork'); ap.add_argument('--high', type=float, default=3.0); ap.add_argument('--medium', type=float, default=6.0)
    a = ap.parse_args(); main(a.city, a.high, a.medium)
