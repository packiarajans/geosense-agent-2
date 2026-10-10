# create_indexes.py
# Purpose: Create GIST spatial indexes on the PostGIS layers so nearest-feature
#          queries are fast. Run once after loading OSM data (safe to re-run).
#
#   python -m src.phase1_ml.create_indexes
#
# Why this matters: a GIST index lets PostGIS answer "nearest road to this point"
# using the <-> KNN operator in milliseconds instead of scanning every geometry.

from sqlalchemy import text

try:
    from src.phase1_ml.db_connection import get_engine
except ModuleNotFoundError:
    from db_connection import get_engine

# table -> geometry column
LAYERS = {
    'osm_roads':     'geometry',
    'osm_poi':       'geometry',
    'osm_buildings': 'geometry',
    'osm_landuse':   'geometry',
}


def create_indexes():
    engine = get_engine()
    with engine.begin() as conn:
        for table, geom in LAYERS.items():
            # does the table exist?
            exists = conn.execute(text(
                "SELECT to_regclass(:t)"), {'t': f'public.{table}'}).scalar()
            if not exists:
                print(f'  skip {table}: table not found')
                continue
            idx = f'{table}_{geom}_idx'
            conn.execute(text(
                f'CREATE INDEX IF NOT EXISTS {idx} '
                f'ON {table} USING GIST ({geom});'))
            conn.execute(text(f'ANALYZE {table};'))   # refresh planner stats
            print(f'  indexed {table} ({idx})')
    print('Spatial indexes ready.')


if __name__ == '__main__':
    create_indexes()
