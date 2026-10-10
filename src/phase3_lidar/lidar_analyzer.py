# lidar_analyzer.py
# PURPOSE: The main LiDAR analysis module for GeoSense Agent
# INPUT: bounding box (min_lat, min_lon, max_lat, max_lon) in WGS84
# OUTPUT: dict with avg building height, canopy %, building cover %, ground slope and slope risk
#
# Usage:
#   python src/phase3_lidar/lidar_analyzer.py                       # whole study area
#   python src/phase3_lidar/lidar_analyzer.py --bbox 40.703 -74.015 40.712 -74.005 --laz data/lidar/processed/nyc_fidi_classified.laz
#
# v2 changes vs original:
#   - the bbox is actually applied (lat/lon reprojected into the point cloud CRS);
#     a bbox outside the LiDAR coverage returns an error instead of silently using everything
#   - canopy / building cover are AREA shares (top surface per 1 m cell), not point shares
#   - slope comes from a 5 m ground DEM (ground points only, spikes removed),
#     not from building/tree heights
#   - 'impervious_surface_pct' (manual A.2) = roof cover, a lower-bound proxy: roads/parking are
#     ground points and cannot be separated from bare soil with LiDAR geometry alone.
#     The same value is also returned as 'building_cover_pct'.
import argparse
import warnings
from pathlib import Path

import numpy as np

import lidar_features as lf
from lidar_features import load_classified_cloud, classify_points

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_CLOUD_PATH = PROJECT_ROOT / 'data' / 'lidar' / 'processed' / 'study_area_classified.laz'

DEM_CELL_M = 5.0   # 1 m cells turn a few cm of noise into several degrees of fake slope
STEEP_DEG = 15.0   # slopes above this count as "steep" (landslide / runoff concern)
SPIKE_M = 3.0      # ground cell > this above/below its neighbourhood median = misclassified
                   # roof or noise pit -> ignored (stops roof edges becoming 100 m "cliffs")
SPIKE_WINDOW_CELLS = 13   # 13 x 5 m = 65 m neighbourhood for the spike test

_cache = {}        # avoid re-reading a 12M-point file for every bbox query


def _load(laz_path):
    laz_path = str(laz_path)
    if laz_path not in _cache:
        x, y, z, hag, nr, rn = load_classified_cloud(laz_path)
        labels = classify_points(hag, nr, rn)
        _cache[laz_path] = (x, y, z, hag, labels, lf._XY.get('crs'))
    return _cache[laz_path]


def _to_cloud_crs(crs, lons, lats):
    if crs is None:
        raise SystemExit('Point cloud has no CRS, cannot apply a lat/lon bbox.')
    from pyproj import Transformer
    t = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    return t.transform(lons, lats)


def data_extent_latlon(laz_path=PROCESSED_CLOUD_PATH):
    """(min_lat, min_lon, max_lat, max_lon) covered by the point cloud."""
    x, y, _, _, _, crs = _load(laz_path)
    from pyproj import Transformer
    t = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
    xs = [x.min(), x.max(), x.min(), x.max()]
    ys = [y.min(), y.min(), y.max(), y.max()]
    lons, lats = t.transform(xs, ys)
    return [round(min(lats), 5), round(min(lons), 5), round(max(lats), 5), round(max(lons), 5)]


def _top_surface_cover(x, y, hag, labels):
    """Share of 1 m cells whose highest point is vegetation (>=2.5 m) / building."""
    ix = ((x - x.min()) // 1).astype(np.int64)
    iy = ((y - y.min()) // 1).astype(np.int64)
    cell = iy * (int(ix.max()) + 1) + ix
    order = np.lexsort((-hag, cell))
    first = order[np.r_[True, cell[order][1:] != cell[order][:-1]]]
    top_l, top_h = labels[first], hag[first]
    n = len(first)
    canopy = np.sum((top_l == 1) & (top_h >= lf.TALL_M)) / n * 100
    building = np.sum(top_l == 2) / n * 100
    return round(float(canopy), 1), round(float(building), 1), n


def _ground_slope(x, y, z, labels):
    """Mean slope (deg) and % steep area from a 5 m DEM of ground points."""
    g = labels == 0
    if g.sum() < 100:
        return None, None
    gx, gy, gz = x[g], y[g], z[g]
    ix = ((gx - gx.min()) // DEM_CELL_M).astype(np.int64)
    iy = ((gy - gy.min()) // DEM_CELL_M).astype(np.int64)
    W, H = int(ix.max()) + 1, int(iy.max()) + 1
    key = iy * W + ix
    n = np.bincount(key, minlength=W * H)
    s = np.bincount(key, gz, minlength=W * H)
    with np.errstate(invalid='ignore', divide='ignore'):
        dem = (s / n).reshape(H, W)
    # Remove spikes/pits: compare each cell with the median of a ~65 m neighbourhood.
    # The window must be wider than the roofs that SMRF wrongly calls ground; on a real
    # uniform slope the median equals the centre value, so genuine hills are kept.
    k = SPIKE_WINDOW_CELLS // 2
    pad = np.pad(dem, k, constant_values=np.nan)
    stack = np.stack([pad[dy:dy + H, dx:dx + W]
                      for dy in range(2 * k + 1) for dx in range(2 * k + 1)])
    with np.errstate(invalid='ignore'), warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)   # all-NaN windows
        local_med = np.nanmedian(stack, axis=0)
        dem = np.where(np.abs(dem - local_med) > SPIKE_M, np.nan, dem)
    dzdy, dzdx = np.gradient(dem, DEM_CELL_M)   # NaN where a neighbour cell has no ground
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    valid = slope[~np.isnan(slope)]
    if valid.size == 0:
        return None, None
    return round(float(np.mean(valid)), 2), round(float(np.mean(valid > STEEP_DEG) * 100), 1)


def _slope_risk(mean_slope, steep_pct):
    """0-10: mostly driven by how much ground is steeper than 15 deg."""
    if mean_slope is None:
        return None
    return round(min(10.0, steep_pct / 5 + mean_slope / 5), 2)


def analyze_bbox(min_lat, min_lon, max_lat, max_lon, laz_path=PROCESSED_CLOUD_PATH):
    """
    Main function: analyse LiDAR-derived metrics for a lat/lon bounding box.
    Returns a dict; contains 'error' if the bbox has no LiDAR coverage.
    """
    x, y, z, hag, labels, crs = _load(laz_path)
    xs, ys = _to_cloud_crs(crs, [min_lon, max_lon, min_lon, max_lon],
                           [min_lat, min_lat, max_lat, max_lat])
    inside = ((x >= min(xs)) & (x <= max(xs)) & (y >= min(ys)) & (y <= max(ys)))
    bbox = [min_lat, min_lon, max_lat, max_lon]
    if inside.sum() < 1000:
        return {'bbox': bbox, 'error': 'No LiDAR coverage for this bbox',
                'data_extent_latlon': data_extent_latlon(laz_path)}

    x, y, z, hag, labels = x[inside], y[inside], z[inside], hag[inside], labels[inside]
    bbox_area = (max(xs) - min(xs)) * (max(ys) - min(ys))
    canopy, building_cover, cells = _top_surface_cover(x, y, hag, labels)
    bh = hag[labels == 2]
    mean_slope, steep_pct = _ground_slope(x, y, z, labels)
    return {
        'bbox': bbox,
        'data_coverage_pct': round(min(100.0, 100 * cells / bbox_area), 1),
        'avg_building_height_m': round(float(np.mean(bh)), 2) if bh.size else 0.0,
        'p95_building_height_m': round(float(np.percentile(bh, 95)), 2) if bh.size else 0.0,
        'canopy_cover_pct': canopy,
        'building_cover_pct': building_cover,
        # Manual A.2 output. LiDAR geometry alone cannot separate roads from bare soil, so this
        # is a lower-bound proxy (roof area only); add imagery/intensity for roads & paving.
        'impervious_surface_pct': building_cover,
        'mean_ground_slope_deg': mean_slope,
        'steep_ground_pct': steep_pct,
        'slope_risk_score': _slope_risk(mean_slope, steep_pct),
        'total_points_analyzed': int(inside.sum()),
    }


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='LiDAR metrics for a lat/lon bounding box.')
    ap.add_argument('--bbox', nargs=4, type=float, metavar=('MIN_LAT', 'MIN_LON', 'MAX_LAT', 'MAX_LON'),
                    help='default: the full extent of the point cloud')
    ap.add_argument('--laz', default=str(PROCESSED_CLOUD_PATH))
    args = ap.parse_args()
    laz = Path(args.laz)
    if not laz.is_absolute():
        laz = PROJECT_ROOT / laz
    bbox = args.bbox or data_extent_latlon(laz)
    result = analyze_bbox(*bbox, laz_path=laz)
    for key, value in result.items():
        print(f'{key:24s}: {value}')
