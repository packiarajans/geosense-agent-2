# mapillary_download.py
# Purpose: Small Mapillary API v4 helper used by streetview_semantic.py
#          (find images in a bbox + download one image). For whole-city downloads use
#          mapillary_anycity_download.py instead.
#
#   python src/phase3_lidar/mapillary_download.py --bbox 80.230 13.037 80.238 13.045 --limit 10
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
TOKEN = os.getenv('MAPILLARY_TOKEN')
BASE_URL = 'https://graph.mapillary.com/images'
TILE_DEG = 0.01          # Mapillary returns HTTP 500 on large bboxes -> query in small tiles
DEFAULT_SAVE_DIR = PROJECT_ROOT / 'data' / 'streetview' / 'images'
FIELDS = 'id,thumb_2048_url,geometry,compass_angle,captured_at,is_pano,width,height'


def _headers():
    if not TOKEN:
        raise SystemExit('MAPILLARY_TOKEN not set in F:\\GeoSense_Agent\\.env')
    return {'Authorization': f'OAuth {TOKEN}'}   # keeps the token out of URLs and error logs


def _tiles(min_lon, min_lat, max_lon, max_lat, step=TILE_DEG):
    lon = min_lon
    while lon < max_lon - 1e-9:
        lat = min_lat
        while lat < max_lat - 1e-9:
            yield (round(lon, 6), round(lat, 6),
                   round(min(lon + step, max_lon), 6), round(min(lat + step, max_lat), 6))
            lat += step
        lon += step


def _get(params, retries=3):
    for attempt in range(retries):
        try:
            resp = requests.get(BASE_URL, params=params, headers=_headers(), timeout=60)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 200:
            return resp.json().get('data', [])
        if resp.status_code in (401, 403):
            raise SystemExit(f'Mapillary auth failed ({resp.status_code}) - check MAPILLARY_TOKEN')
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
    print(f'  skipped tile after {retries} failures: {params["bbox"]}')
    return []


def find_images_in_bbox(min_lon, min_lat, max_lon, max_lat, limit=50, pano_only=False):
    """Image metadata inside a bbox (queried tile by tile). pano_only=True -> 360 images only."""
    found = {}
    for bbox in _tiles(min_lon, min_lat, max_lon, max_lat):
        params = {'fields': FIELDS, 'bbox': ','.join(map(str, bbox)), 'limit': min(limit, 2000)}
        if pano_only:
            params['is_pano'] = 'true'
        for img in _get(params):
            found[img['id']] = img
            if len(found) >= limit:
                return list(found.values())
    return list(found.values())


def download_image(image_meta, save_dir=DEFAULT_SAVE_DIR):
    """Download one image (skips if already on disk). Returns the path, or None."""
    url = image_meta.get('thumb_2048_url')
    if not url:
        return None
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    path = save_dir / f"{image_meta['id']}.jpg"
    if path.exists():
        return str(path)
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
            return str(path)
        except requests.RequestException:
            time.sleep(2 ** attempt)
    return None


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Download Mapillary images for a small bbox.')
    ap.add_argument('--bbox', nargs=4, type=float, default=[80.230, 13.037, 80.238, 13.045],
                    metavar=('MIN_LON', 'MIN_LAT', 'MAX_LON', 'MAX_LAT'),
                    help='default: T. Nagar, Chennai')
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--pano-only', action='store_true')
    args = ap.parse_args()
    images = find_images_in_bbox(*args.bbox, limit=args.limit, pano_only=args.pano_only)
    print(f'Found {len(images)} images')
    for img in images:
        print('Saved:', download_image(img))
