# demographics.py -- Tool 6: healthcare access near a point (nearest hospital from OSM).
# Population density needs a ward/population table in PostGIS, which is not loaded (only CSVs/shapefiles on disk),
# so it is returned as null instead of the manual's hard-coded 8200.
from sqlalchemy import text
from ._common import safe

@safe
def get_demographics(latitude: float, longitude: float) -> dict:
    from db_connection import get_engine
    pt = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)"
    with get_engine().connect() as c:
        row = c.execute(text(f"""
            SELECT name, ST_Distance(geometry::geography, {pt}::geography) AS d
            FROM osm_poi WHERE fclass = 'hospital'
            ORDER BY geometry <-> {pt} LIMIT 1"""), {'lon': longitude, 'lat': latitude}).fetchone()
    if row is None or row[1] is None or row[1] > 50000:
        return {'latitude': latitude, 'longitude': longitude, 'error': 'No OSM hospital data within 50 km of this location'}
    return {'latitude': latitude, 'longitude': longitude,
            'nearest_hospital_name': row[0], 'nearest_hospital_km': round(row[1] / 1000, 2),
            'population_density_per_km2': None,
            'note': 'population density not available (ward population not loaded into PostGIS)'}
