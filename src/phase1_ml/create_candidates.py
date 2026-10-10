# create_candidates.py
# Purpose: Generate a grid of candidate locations across the study area.
# Think of it as placing dots every ~500 metres across the city.
#
# City-aware: the bounding box comes from pipeline/cities.json for the active
# city (GEOSENSE_CITY), and can be overridden by passing bbox to create_grid().

import geopandas as gpd
import numpy as np
from shapely.geometry import Point

try:
    from src.phase1_ml import config
except ModuleNotFoundError:
    import config

# Fallback box (Chennai) if the city has no bbox configured
DEFAULT_BBOX = (80.18, 12.87, 80.30, 13.09)
GRID_SPACING_DEGREES = 0.005  # approximately 500 metres


def create_grid(bbox=None):
    bbox = bbox or config.bbox() or DEFAULT_BBOX
    min_lon, min_lat, max_lon, max_lat = bbox
    lons = np.arange(min_lon, max_lon, GRID_SPACING_DEGREES)
    lats = np.arange(min_lat, max_lat, GRID_SPACING_DEGREES)
    points = [Point(lon, lat) for lon in lons for lat in lats]
    gdf = gpd.GeoDataFrame({'geometry': points}, crs='EPSG:4326')
    gdf['location_id'] = range(len(gdf))
    gdf['longitude']   = gdf.geometry.x
    gdf['latitude']    = gdf.geometry.y
    print(f'Created {len(gdf)} candidate locations for bbox {bbox}')
    return gdf


if __name__ == '__main__':
    grid = create_grid()
    grid.to_file(config.CANDIDATES_SHP)
    print(f'Saved candidate locations to {config.CANDIDATES_SHP}')
