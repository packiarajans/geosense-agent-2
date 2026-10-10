# load_osm_data.py
# Purpose: Load OpenStreetMap shapefiles into PostGIS database

import geopandas as gpd
try:
    from src.phase1_ml.db_connection import get_engine
except ModuleNotFoundError:
    from db_connection import get_engine
import os

DATA_DIR = 'F:/SouthernZone_OSM'

# Each tuple is: (shapefile name, table name in database)
LAYERS_TO_LOAD = [
    ('gis_osm_roads_free_1.shp',     'osm_roads'),
    ('gis_osm_buildings_a_free_1.shp',  'osm_buildings'),
    ('gis_osm_landuse_a_free_1.shp',  'osm_landuse'),
    ('gis_osm_pois_free_1.shp',       'osm_poi'),
]

def load_layer(filename, table_name, engine):
    filepath = os.path.join(DATA_DIR, filename)
    print(f'Loading {filename} ...')
    # Read the shapefile
    gdf = gpd.read_file(filepath)
    # Make sure it uses WGS84 coordinate system (standard lat/lon)
    gdf = gdf.to_crs(epsg=4326)
    # Write to PostGIS database
    gdf.to_postgis(table_name, engine, if_exists='replace', index=False)
    print(f'  Loaded {len(gdf)} features into table: {table_name}')

def main():
    engine = get_engine()
    for filename, table_name in LAYERS_TO_LOAD:
        load_layer(filename, table_name, engine)
    print('All layers loaded successfully!')

if __name__ == '__main__':
    main()
