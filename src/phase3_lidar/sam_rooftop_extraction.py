# sam_rooftop_extraction.py
# Purpose: Outline rooftops with Meta's Segment Anything Model (SAM), measure each roof's
#          real LiDAR height, and estimate building volume. Exports a GeoPackage for QGIS.
#
# Manual step E.3.3 (default, no arguments): uses data/lidar/processed/study_area_classified.laz
#        python src/phase3_lidar/sam_rooftop_extraction.py
# Input options:
#   A) From your classified LAZ (output of pdal_pipeline.py) — no aerial photo needed.
#      The script rasterises HeightAboveGround into an nDSM (height map) and runs SAM on it:
#        python src/phase3_lidar/sam_rooftop_extraction.py --laz data/lidar/processed/manhattan_ues_classified.laz
#   B) From an existing nDSM GeoTIFF (heights in metres above ground):
#        python src/phase3_lidar/sam_rooftop_extraction.py --ndsm data/lidar/processed/manhattan_ues_ndsm.tif
#   Optional: --ortho some_ortho.tif  -> run SAM on a georeferenced aerial photo instead of the
#             height map (must be in the same CRS and cover the same area as the LiDAR).
#
# Output: <project>/data/lidar/processed/<name>_rooftops.gpkg  (+ .csv and _summary.json)
#
# v3: forest canopy rejected using lidar_features' tree/building labels (--laz input),
#     merged segments split where heights jump >= 3 m (attached towers), capture check
#     (% of building area inside accepted roofs), masks stored cropped (much less RAM).
import argparse
import json
import os
from pathlib import Path

import cv2
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.warp import reproject

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / 'data' / 'lidar' / 'processed'
CHECKPOINT = Path(os.getenv('SAM_CHECKPOINT_PATH',
                            PROJECT_ROOT / 'models' / 'saved' / 'sam' / 'sam_vit_b_01ec64.pth'))
SAM_URL = 'https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth'

# Filters that decide "is this segment a rooftop?"
MIN_HEIGHT_M = 2.5        # below this = ground, cars, low walls
MIN_AREA_M2 = 20          # smaller = sheds, AC units, noise
MAX_AREA_M2 = 50000       # larger = merged blocks / whole tile (big podiums in city centres)
MIN_RECTANGULARITY = 0.55 # mask area / min-area-rectangle area (trees are blobby, roofs boxy)
MAX_HEIGHT_CV = 0.35      # std/mean of height inside the mask (trees are rough, roofs flat-ish)
MIN_BUILDING_FRAC = 0.6   # share of tall LiDAR points inside the mask labelled "building"
                          # by lidar_features (only with --laz) - rejects forest canopy
MAX_TEXTURE_M = 0.6       # fallback without --laz: median 3x3 height std inside the mask
SPLIT_GAP_M = 3.0         # a height gap this big inside one mask = two buildings -> split
TILE = 1024               # SAM works best on ~1024 px tiles


# ---------------------------------------------------------------- inputs
def resolve(p):
    p = Path(p)
    return p if p.is_absolute() else PROJECT_ROOT / p


def laz_to_ndsm(laz_path, ndsm_path, resolution=0.5):
    """Rasterise max HeightAboveGround per cell -> nDSM GeoTIFF (needs pdal)."""
    import pdal
    stages = [
        str(laz_path),
        {'type': 'writers.gdal', 'filename': str(ndsm_path),
         'dimension': 'HeightAboveGround', 'output_type': 'max',
         'resolution': resolution, 'nodata': -9999, 'data_type': 'float32',
         'window_size': 3},  # fills small gaps between points
    ]
    pdal.Pipeline(json.dumps(stages)).execute()
    print(f'nDSM written: {ndsm_path}')


def building_fraction_raster(laz_path, profile):
    """Per nDSM pixel: share of tall (>=2.5 m) points that lidar_features labels 'building'.
    NaN where there are no tall points. Smoothed over 3x3 pixels (few points per 0.5 m pixel)."""
    from lidar_features import classify_points, load_classified_cloud
    x, y, _, hag, nr, rn = load_classified_cloud(laz_path)
    labels = classify_points(hag, nr, rn)
    t = profile['transform']
    H, W = profile['height'], profile['width']
    col = np.floor((x - t.c) / t.a).astype(np.int64)
    row = np.floor((y - t.f) / t.e).astype(np.int64)
    ok = (hag >= MIN_HEIGHT_M) & (col >= 0) & (col < W) & (row >= 0) & (row < H)
    key = row[ok] * W + col[ok]
    tall = np.bincount(key, minlength=H * W).reshape(H, W).astype('float32')
    bld = np.bincount(key[labels[ok] == 2], minlength=H * W).reshape(H, W).astype('float32')
    tall, bld = cv2.boxFilter(tall, -1, (3, 3), normalize=False), cv2.boxFilter(bld, -1, (3, 3), normalize=False)
    with np.errstate(invalid='ignore', divide='ignore'):
        frac = np.where(tall > 0, bld / tall, np.nan)
    valid = frac[~np.isnan(frac)]
    print(f'Building-fraction raster: {np.mean(valid > 0.5) * 100 if valid.size else 0:.1f}% of tall pixels are building')
    return frac


def texture_raster(ndsm):
    """Local 3x3 height standard deviation (m). Tree canopy is rough, roofs are smooth."""
    h = np.nan_to_num(ndsm, nan=0.0).astype('float32')
    m = cv2.blur(h, (3, 3))
    m2 = cv2.blur(h * h, (3, 3))
    return np.sqrt(np.maximum(m2 - m * m, 0))


def read_ndsm(path):
    with rasterio.open(path) as src:
        ndsm = src.read(1).astype('float32')
        nodata = src.nodata
        profile = src.profile
    if nodata is not None:
        ndsm[ndsm == nodata] = np.nan
    ndsm[ndsm < 0] = 0
    return ndsm, profile


def ndsm_to_rgb(ndsm):
    """Height map -> 8-bit 3-channel image SAM can read (tall = bright) + edge shading."""
    h = np.nan_to_num(ndsm, nan=0.0)
    top = np.nanpercentile(h[h > 0], 98) if (h > 0).any() else 1.0
    gray = np.clip(h / max(top, 1e-6) * 255, 0, 255).astype('uint8')
    # hillshade-like channel makes roof edges crisp for SAM
    gx, gy = np.gradient(h)
    shade = np.clip(128 + (gx - gy) * 20, 0, 255).astype('uint8')
    return np.dstack([gray, gray, shade])


def read_ortho_aligned(ortho_path, profile):
    """Read an ortho GeoTIFF resampled onto the nDSM grid."""
    with rasterio.open(ortho_path) as src:
        bands = min(src.count, 3)
        out = np.zeros((bands, profile['height'], profile['width']), dtype='float32')
        for b in range(bands):
            reproject(rasterio.band(src, b + 1), out[b],
                      dst_transform=profile['transform'], dst_crs=profile['crs'],
                      resampling=Resampling.bilinear)
    if bands == 1:
        out = np.repeat(out, 3, axis=0)
    img = np.moveaxis(out, 0, -1)
    lo, hi = np.percentile(img, 2), np.percentile(img, 98)
    return np.clip((img - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype('uint8')


# ---------------------------------------------------------------- SAM
def load_sam_generator(model_type='vit_b'):
    if not CHECKPOINT.exists():
        raise SystemExit(f'SAM checkpoint not found: {CHECKPOINT}\nDownload it from:\n  {SAM_URL}\n'
                         f'or set SAM_CHECKPOINT_PATH in .env')
    import torch
    from segment_anything import SamAutomaticMaskGenerator, sam_model_registry
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f'Loading SAM ({model_type}) on {device}...')
    sam = sam_model_registry[model_type](checkpoint=str(CHECKPOINT)).to(device)
    return SamAutomaticMaskGenerator(sam, points_per_side=32,
                                     pred_iou_thresh=0.86, stability_score_thresh=0.90,
                                     min_mask_region_area=20)


def run_sam_tiled(generator, image):
    """Run SAM tile by tile. Returns masks as (row0, col0, cropped_bool_array) to save memory
    (a full-size 3000x3000 mask is 9 MB; 1000+ of them used to need >10 GB of RAM)."""
    H, W = image.shape[:2]
    masks = []
    for ty in range(0, H, TILE):
        for tx in range(0, W, TILE):
            tile = image[ty:ty + TILE, tx:tx + TILE]
            if tile.shape[0] < 32 or tile.shape[1] < 32 or tile.max() == 0:
                continue
            for m in generator.generate(np.ascontiguousarray(tile)):
                seg = m['segmentation']
                x, y, w, h = (int(round(v)) for v in m['bbox'])
                sub = seg[y:y + h + 1, x:x + w + 1]
                if sub.any():
                    masks.append((ty + y, tx + x, sub.copy()))
    print(f'SAM produced {len(masks)} raw segments')
    return masks


# ---------------------------------------------------------------- rooftops
def _win(arr, mask):
    r0, c0, sub = mask
    return arr[r0:r0 + sub.shape[0], c0:c0 + sub.shape[1]]


def rectangularity(sub):
    cnts, _ = cv2.findContours(sub.astype('uint8'), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return 0.0
    c = max(cnts, key=cv2.contourArea)
    (_, _), (w, h), _ = cv2.minAreaRect(c)
    return min(1.0, float(sub.sum() / (w * h))) if w * h > 0 else 0.0


def split_by_height(mask, ndsm, min_px):
    """Split one SAM mask into several where its heights form separate levels
    (e.g. attached towers of different height merged into one segment)."""
    r0, c0, sub = mask
    hwin = _win(ndsm, mask)
    h = hwin[sub & ~np.isnan(hwin)]
    if h.size < 2 * min_px or np.ptp(h) < SPLIT_GAP_M:
        return [mask]
    # 1 m histogram; bins with almost no pixels are gaps between height levels
    edges = np.arange(np.floor(h.min()), np.ceil(h.max()) + 2)
    counts, _ = np.histogram(h, bins=edges)
    empty = counts < max(3, 0.005 * h.size)
    cuts, run = [], 0
    for i, e in enumerate(empty):
        run = run + 1 if e else 0
        if run == int(SPLIT_GAP_M):
            cuts.append(edges[i + 1 - run + run // 2])   # middle of the gap
    if not cuts:
        return [mask]
    bands = np.digitize(np.nan_to_num(hwin, nan=-1), cuts)
    parts = []
    for b in np.unique(bands[sub]):
        n, lab = cv2.connectedComponents((sub & (bands == b)).astype('uint8'), connectivity=4)
        for k in range(1, n):
            comp = lab == k
            if comp.sum() < min_px:
                continue
            rows, cols = np.where(comp)
            parts.append((r0 + rows.min(), c0 + cols.min(),
                          comp[rows.min():rows.max() + 1, cols.min():cols.max() + 1]))
    return parts or [mask]


def measure_rooftops(masks, ndsm, pixel_area_m2, bfrac=None, texture=None):
    """Keep segments that look like roofs; attach LiDAR height + volume.
    Returns (roofs, taken) where taken is the union of accepted roof pixels."""
    min_px = int(MIN_AREA_M2 / pixel_area_m2)
    candidates = []
    for m in masks:
        candidates.extend(split_by_height(m, ndsm, min_px))
    n_split = len(candidates) - len(masks)
    taken = np.zeros(ndsm.shape, dtype=bool)
    roofs, rejected = [], {'size': 0, 'overlap': 0, 'coverage': 0, 'low': 0,
                           'shape': 0, 'vegetation': 0}
    # largest first, so a whole roof wins over its parts; skip heavy overlaps
    for mask in sorted(candidates, key=lambda m: -m[2].sum()):
        sub = mask[2]
        npx = int(sub.sum())
        area_m2 = npx * pixel_area_m2
        if not (MIN_AREA_M2 <= area_m2 <= MAX_AREA_M2):
            rejected['size'] += 1
            continue
        if (_win(taken, mask) & sub).sum() > 0.3 * npx:
            rejected['overlap'] += 1
            continue
        h = _win(ndsm, mask)[sub]
        h = h[~np.isnan(h)]
        if h.size < 0.5 * npx:
            rejected['coverage'] += 1
            continue  # mostly no LiDAR coverage
        height = float(np.median(h))
        if height < MIN_HEIGHT_M:
            rejected['low'] += 1
            continue
        cv = float(np.std(h) / height)
        rect = rectangularity(sub)
        if rect < MIN_RECTANGULARITY or cv > MAX_HEIGHT_CV:
            rejected['shape'] += 1
            continue
        # vegetation test: LiDAR point labels if available, else surface texture
        bf = None
        if bfrac is not None:
            vals = _win(bfrac, mask)[sub]
            vals = vals[~np.isnan(vals)]
            bf = float(vals.mean()) if vals.size else 0.0
            if bf < MIN_BUILDING_FRAC:
                rejected['vegetation'] += 1
                continue
        elif texture is not None:
            inner = cv2.erode(sub.astype('uint8'), np.ones((3, 3), 'uint8')).astype(bool)
            t = _win(texture, mask)[inner if inner.any() else sub]
            if float(np.median(t)) > MAX_TEXTURE_M:
                rejected['vegetation'] += 1
                continue
        _win(taken, mask)[sub] = True
        roofs.append({
            'mask': mask,
            'footprint_area_m2': round(float(area_m2), 1),
            'height_m': round(height, 2),
            'max_height_m': round(float(np.percentile(h, 95)), 2),
            # sum of per-pixel heights = volume that respects stepped roofs
            'volume_m3': round(float(h.sum() * pixel_area_m2), 1),
            'rectangularity': round(rect, 2),
            'building_fraction': None if bf is None else round(bf, 2),
        })
    print(f'Split {n_split} merged segments by height level; {len(candidates)} candidates')
    print(f'Kept {len(roofs)} rooftops | rejected: ' + ', '.join(f'{k} {v}' for k, v in rejected.items()))
    return roofs, taken


def capture_check(taken, ndsm, bfrac, texture):
    """Share of 'building' pixels in the nDSM that ended up inside an accepted rooftop."""
    tall = np.nan_to_num(ndsm, nan=0) >= MIN_HEIGHT_M
    if bfrac is not None:
        bld = tall & (np.nan_to_num(bfrac, nan=0) >= 0.5)
    else:
        bld = tall & (texture <= MAX_TEXTURE_M)
    n = int(bld.sum())
    pct = 100.0 * (taken & bld).sum() / n if n else 0.0
    false_px = int((taken & ~bld).sum())
    return round(pct, 1), n, false_px


def export(roofs, profile, out_gpkg):
    import geopandas as gpd
    from rasterio.transform import Affine
    from shapely.geometry import shape
    from shapely.ops import unary_union
    rows = []
    for i, r in enumerate(roofs, 1):
        r0, c0, sub = r['mask']
        tr = profile['transform'] * Affine.translation(c0, r0)
        polys = [shape(g) for g, v in shapes(sub.astype('uint8'), mask=sub, transform=tr) if v == 1]
        geom = unary_union(polys).simplify(profile['transform'].a * 0.5)
        rows.append({'roof_id': i, **{k: v for k, v in r.items() if k != 'mask'}, 'geometry': geom})
    gdf = gpd.GeoDataFrame(rows, geometry='geometry', crs=profile['crs'])
    gdf.to_file(out_gpkg, layer='rooftops', driver='GPKG')
    gdf.drop(columns='geometry').to_csv(out_gpkg.with_suffix('.csv'), index=False)
    print(f'Saved {len(gdf)} rooftops -> {out_gpkg}')
    return gdf


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description='SAM rooftop extraction + LiDAR building volume.')
    src = ap.add_mutually_exclusive_group()
    src.add_argument('--laz', default=None,
                     help='classified LAZ from pdal_pipeline.py '
                          '(default: data/lidar/processed/study_area_classified.laz, as in the manual)')
    src.add_argument('--ndsm', help='nDSM GeoTIFF (height above ground, metres)')
    ap.add_argument('--ortho', help='optional georeferenced aerial GeoTIFF to segment instead')
    ap.add_argument('--resolution', type=float, default=0.5, help='nDSM cell size in metres')
    ap.add_argument('--name', help='output name (default: from input file)')
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    laz = None
    if not args.laz and not args.ndsm:
        args.laz = str(OUT_DIR / 'study_area_classified.laz')   # manual E.3.3 default
    if args.laz:
        laz = resolve(args.laz)
        if not laz.exists():
            raise SystemExit(f'LAZ not found: {laz}\nRun pdal_pipeline.py first.')
        name = args.name or laz.stem.replace('_classified', '')
        ndsm_path = OUT_DIR / f'{name}_ndsm.tif'
        laz_to_ndsm(laz, ndsm_path, args.resolution)
    else:
        ndsm_path = resolve(args.ndsm)
        if not ndsm_path.exists():
            raise SystemExit(f'nDSM not found: {ndsm_path}')
        name = args.name or ndsm_path.stem.replace('_ndsm', '')

    ndsm, profile = read_ndsm(ndsm_path)
    if profile['crs'] is None or profile['crs'].is_geographic:
        raise SystemExit('nDSM must be in a projected CRS in metres (e.g. UTM).')
    pixel_area_m2 = abs(profile['transform'].a * profile['transform'].e)
    print(f'nDSM {ndsm.shape[1]}x{ndsm.shape[0]} px, {pixel_area_m2:.2f} m2/pixel')

    texture = texture_raster(ndsm)
    bfrac = building_fraction_raster(laz, profile) if laz else None
    if bfrac is None:
        print('No LAZ given: vegetation filter uses surface texture only (less reliable).')

    if args.ortho:
        ortho = resolve(args.ortho)
        if not ortho.exists():
            raise SystemExit(f'Ortho not found: {ortho}')
        image = read_ortho_aligned(ortho, profile)
    else:
        image = ndsm_to_rgb(ndsm)
    cv2.imwrite(str(OUT_DIR / f'{name}_sam_input.png'), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))

    masks = run_sam_tiled(load_sam_generator(), image)
    roofs, taken = measure_rooftops(masks, ndsm, pixel_area_m2, bfrac, texture)
    if not roofs:
        print('No rooftops passed the filters. Check the *_sam_input.png, or relax the '
              'thresholds at the top of the script.')
        return
    gdf = export(roofs, profile, OUT_DIR / f'{name}_rooftops.gpkg')
    pct, n_bld, false_px = capture_check(taken, ndsm, bfrac, texture)
    summary = {
        'rooftops': len(gdf),
        'total_volume_m3': round(float(gdf.volume_m3.sum()), 0),
        'total_footprint_m2': round(float(gdf.footprint_area_m2.sum()), 0),
        'building_area_captured_pct': pct,
        'building_area_in_ndsm_m2': round(n_bld * pixel_area_m2, 0),
        'roof_area_outside_building_pixels_m2': round(false_px * pixel_area_m2, 0),
    }
    with open(OUT_DIR / f'{name}_rooftops_summary.json', 'w') as f:
        json.dump(summary, f, indent=2)
    print(gdf.drop(columns='geometry').head(10).to_string(index=False))
    for k, v in summary.items():
        print(f'{k:40s}: {v:,}' if isinstance(v, (int, float)) else f'{k}: {v}')


if __name__ == '__main__':
    main()
