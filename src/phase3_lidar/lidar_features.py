# lidar_features.py
# Purpose: Building height, canopy cover and building cover per grid cell from a classified
#          point cloud (output of pdal_pipeline.py). Saves a map-ready GeoPackage + CSV.
#
# Usage:
#   python src/phase3_lidar/lidar_features.py
#   python src/phase3_lidar/lidar_features.py --laz data/lidar/processed/manhattan_ues_classified.laz --cell 50
#
# Tree vs building (v2):
#   Points with HeightAboveGround >= 2.5 m are grouped into 1 m "surface cells".
#   - Roofs are smooth and mostly single-return  -> building
#   - Canopy is rough and/or has many multiple returns (laser passes through leaves) -> tree
#   - Roof EDGES look rough too (roof + wall points), so a "tree" cell mostly surrounded by
#     building cells is switched to building (removes the green outline around every roof).
#   - Cells taller than 35 m are buildings unless extremely rough (glass towers are
#     multi-return and look like canopy otherwise).
# Output labels (kept compatible with visualize_cloud.py): 0 = ground/low, 1 = vegetation, 2 = building
import argparse
from pathlib import Path

import laspy
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAZ = PROJECT_ROOT / 'data' / 'lidar' / 'processed' / 'study_area_classified.laz'
OUT_DIR = PROJECT_ROOT / 'data' / 'lidar' / 'processed'

LOW_VEG_M = 0.5          # HAG below this = ground
TALL_M = 2.5             # HAG above this = tree or building
SURFACE_CELL_M = 1.0     # cell size used to judge roughness
ROUGH_STD_M = 0.6        # height std inside a 1 m cell above this = rough (tree-like)
MULTI_RETURN_FRAC = 0.3  # share of multi-return points above this = porous (tree-like)
EDGE_NEIGHBOURS = 5      # tree cell with >= this many building neighbours (of 8) -> building
TREE_MAX_M = 35.0        # almost no urban/temperate trees are taller: above this -> building
TALL_ROUGH_STD_M = 3.0   # ...unless the surface is extremely rough (e.g. a very tall tree/mast)

# Module-level store so classify_points() keeps its old 3-argument signature
_XY = {}


def load_classified_cloud(filepath):
    las = laspy.read(str(filepath))
    names = set(las.point_format.dimension_names)
    if 'HeightAboveGround' not in names:
        raise SystemExit('No HeightAboveGround dimension - run pdal_pipeline.py first.')
    x, y, z = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
    hag = np.asarray(las.HeightAboveGround, dtype='float32')
    # laspy versions differ in naming: number_of_returns / num_returns, return_number / return_num
    def _dim(*cands):
        for c in cands:
            try:
                return np.asarray(getattr(las, c))
            except AttributeError:
                continue
        return np.ones(len(x), dtype='uint8')
    num_returns = _dim('number_of_returns', 'num_returns')
    return_num = _dim('return_number', 'return_num')
    _XY['x'], _XY['y'] = x, y
    try:
        _XY['crs'] = las.header.parse_crs()
    except Exception:
        _XY['crs'] = None
    return x, y, z, hag, num_returns, return_num


def _neighbour_count(mask):
    """Number of True cells among the 8 neighbours of every cell."""
    p = np.pad(mask.astype('uint8'), 1)
    h, w = mask.shape
    return sum(p[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
               for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0))


def classify_points(hag, num_returns, return_num, x=None, y=None):
    """0 = ground/low, 1 = vegetation, 2 = building (vectorised, surface-roughness based)."""
    x = _XY.get('x') if x is None else x
    y = _XY.get('y') if y is None else y
    labels = np.zeros(len(hag), dtype='uint8')
    labels[(hag >= LOW_VEG_M) & (hag < TALL_M)] = 1          # low vegetation / shrubs
    tall = hag >= TALL_M
    if not tall.any():
        return labels

    # 1 m surface cells for tall points
    tx, ty, th = x[tall], y[tall], hag[tall].astype('float64')
    multi = (num_returns[tall] > 1).astype('float64')
    ix = ((tx - x.min()) // SURFACE_CELL_M).astype(np.int64)
    iy = ((ty - y.min()) // SURFACE_CELL_M).astype(np.int64)
    W, H = int(ix.max()) + 1, int(iy.max()) + 1
    cell = iy * W + ix
    n = np.bincount(cell, minlength=W * H)
    s = np.bincount(cell, th, minlength=W * H)
    s2 = np.bincount(cell, th * th, minlength=W * H)
    m = np.bincount(cell, multi, minlength=W * H)
    with np.errstate(invalid='ignore', divide='ignore'):
        mean = s / n
        std = np.sqrt(np.maximum(s2 / n - mean ** 2, 0))
        mfrac = m / n
    occupied = n > 0
    # If the sensor never recorded multiple returns, rely on roughness only
    use_returns = (num_returns > 1).any()
    treeish = occupied & ((std > ROUGH_STD_M) | (use_returns & (mfrac > MULTI_RETURN_FRAC)))
    # Glass curtain walls, setbacks and rooftop plant look "rough"/multi-return like canopy.
    # Anything taller than a realistic tree is a structure unless it is extremely rough.
    skyscraper = occupied & (mean >= TREE_MAX_M) & (std <= TALL_ROUGH_STD_M)
    treeish = treeish & ~skyscraper
    building = occupied & ~treeish

    # roof-edge cleanup: rough cells hugging roofs are walls/edges, not trees
    b2, t2 = building.reshape(H, W), treeish.reshape(H, W)
    edge = t2 & (_neighbour_count(b2) >= EDGE_NEIGHBOURS)
    b2 = b2 | edge
    # isolated single "building" cells inside canopy are usually flat leaf patches
    lone = b2 & (_neighbour_count(b2) <= 1)
    b2 = b2 & ~lone
    building = b2.ravel()

    labels[np.flatnonzero(tall)] = np.where(building[cell], 2, 1)
    return labels


def compute_grid_metrics(x, y, hag, labels, cell_size_m=50):
    """Aggregate per cell_size_m grid cell. Returns list of dicts with real-world bounds."""
    x0, y0 = np.floor(x.min()), np.floor(y.min())
    gx = ((x - x0) // cell_size_m).astype(np.int64)
    gy = ((y - y0) // cell_size_m).astype(np.int64)
    W = int(gx.max()) + 1
    key = gy * W + gx
    size = int(key.max()) + 1
    total = np.bincount(key, minlength=size)
    bmask, tmask = labels == 2, labels == 1
    b_n = np.bincount(key[bmask], minlength=size)
    b_sum = np.bincount(key[bmask], hag[bmask].astype('float64'), minlength=size)
    b_max = np.zeros(size)
    np.maximum.at(b_max, key[bmask], hag[bmask])
    # area-based cover: share of 1 m sub-cells whose top surface is tree / building
    sub = (((y - y0) // 1).astype(np.int64) * (W * cell_size_m + 1)
           + ((x - x0) // 1).astype(np.int64))
    order = np.lexsort((-hag, sub))                  # highest point per 1 m sub-cell
    first = order[np.r_[True, sub[order][1:] != sub[order][:-1]]]
    top_lbl, top_key = labels[first], key[first]
    sub_n = np.bincount(top_key, minlength=size)
    sub_t = np.bincount(top_key[top_lbl == 1], minlength=size)
    sub_b = np.bincount(top_key[top_lbl == 2], minlength=size)
    t_tall = np.bincount(key[tmask & (hag >= TALL_M)], minlength=size)

    results = []
    for k in np.flatnonzero(total):
        cx, cy = int(k % W), int(k // W)
        with np.errstate(invalid='ignore', divide='ignore'):
            results.append({
                'grid_x': cx, 'grid_y': cy,
                'min_x': float(x0 + cx * cell_size_m), 'min_y': float(y0 + cy * cell_size_m),
                'points': int(total[k]),
                'avg_building_height_m': round(float(b_sum[k] / b_n[k]), 2) if b_n[k] else 0.0,
                'max_building_height_m': round(float(b_max[k]), 2),
                'building_cover_pct': round(100 * sub_b[k] / sub_n[k], 1) if sub_n[k] else 0.0,
                'canopy_cover_pct': round(100 * sub_t[k] / sub_n[k], 1) if sub_n[k] else 0.0,
                'tall_tree_points': int(t_tall[k]),
            })
    return results


def save_grid(metrics, cell_size_m, crs, out_base):
    import csv
    csv_path = out_base.with_suffix('.csv')
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(metrics[0].keys()))
        w.writeheader()
        w.writerows(metrics)
    print(f'CSV saved: {csv_path}')
    try:
        import geopandas as gpd
        from shapely.geometry import box
    except ImportError:
        print('geopandas not installed - skipped GeoPackage')
        return
    geoms = [box(m['min_x'], m['min_y'], m['min_x'] + cell_size_m, m['min_y'] + cell_size_m)
             for m in metrics]
    gdf = gpd.GeoDataFrame(metrics, geometry=geoms, crs=crs)
    gpkg = out_base.with_suffix('.gpkg')
    gdf.to_file(gpkg, layer='grid_metrics', driver='GPKG')
    print(f'GeoPackage saved: {gpkg}' + ('' if crs else '  (no CRS in LAZ - set it in QGIS)'))


def main():
    ap = argparse.ArgumentParser(description='Grid metrics from a classified LiDAR point cloud.')
    ap.add_argument('--laz', default=str(DEFAULT_LAZ), help='classified LAZ from pdal_pipeline.py')
    ap.add_argument('--cell', type=int, default=50, help='grid cell size in metres')
    args = ap.parse_args()

    laz = Path(args.laz)
    if not laz.is_absolute():
        laz = PROJECT_ROOT / laz
    if not laz.exists():
        raise SystemExit(f'LAZ not found: {laz}')
    print(f'Reading {laz.name} ...')
    x, y, z, hag, num_returns, return_num = load_classified_cloud(laz)
    labels = classify_points(hag, num_returns, return_num)
    counts = np.bincount(labels, minlength=3)
    print(f'{len(labels):,} points: ground {counts[0]:,} | vegetation {counts[1]:,} | building {counts[2]:,}')

    metrics = compute_grid_metrics(x, y, hag, labels, args.cell)
    print(f'Computed metrics for {len(metrics)} grid cells of {args.cell} m')
    name = laz.stem.replace('_classified', '')
    save_grid(metrics, args.cell, _XY.get('crs'), OUT_DIR / f'{name}_grid_{args.cell}m')
    b = [m['avg_building_height_m'] for m in metrics if m['avg_building_height_m'] > 0]
    c = [m['canopy_cover_pct'] for m in metrics]
    print(f'Mean building height (cells with buildings): {np.mean(b):.1f} m' if b else 'No buildings found')
    print(f'Mean canopy cover: {np.mean(c):.1f} %  | max {np.max(c):.1f} %')


if __name__ == '__main__':
    main()
