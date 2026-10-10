# climate_risk.py -- Tool 5: flood/elevation risk for a point from the DEM-derived flood zones.
# Temperature anomaly and drought index have no data source in GeoSense, so they are returned as null
# (the manual's sample returns hard-coded 1.4 / 0.3 -- that would be invented data).
from sqlalchemy import text
from ._common import city_for, out_of_coverage, safe

@safe
def get_climate_risk(latitude: float, longitude: float) -> dict:
    city = city_for(latitude, longitude)
    if city is None:
        return out_of_coverage(latitude, longitude, 'get_climate_risk')
    from db_connection import get_engine
    pt = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)"
    p = {'lon': longitude, 'lat': latitude}
    out = {'latitude': latitude, 'longitude': longitude, 'city': city,
           'temperature_anomaly_c': None, 'drought_index': None}
    with get_engine().connect() as c:
        elev = c.execute(text(f"SELECT ST_Value(rast, {pt}) FROM {city}.{city}_dem WHERE ST_Intersects(rast, {pt}) LIMIT 1"), p).scalar()
        out['elevation_m'] = round(float(elev), 2) if elev is not None else None
        def zone(tbl):
            return c.execute(text(f"SELECT risk_level FROM {city}.{tbl} WHERE ST_Contains(geometry, {pt}) LIMIT 1"), p).scalar()
        out['flood_zone_relative'] = zone(f'{city}_floodzones')
        if city == 'newyork':
            out['flood_zone_absolute'] = zone('newyork_floodzones_abs')
            out['absolute_bands'] = 'high < 3 m, medium 3-6 m, low >= 6 m (NAVD88, elevation heuristic, not a surge model)'
    out['note'] = ('flood_zone_relative = terciles of the city DEM (always ~1/3 each). '
                   'temperature_anomaly_c / drought_index: no climate data source integrated.')
    return out
