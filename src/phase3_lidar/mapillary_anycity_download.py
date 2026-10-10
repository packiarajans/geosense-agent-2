# mapillary_download.py
# Purpose: Download street-level (optionally 360-degree) images for any city using Mapillary API v4.
#
# Usage examples:
#   python mapillary_download.py --city "Chennai" --country "India" --limit 200
#   python mapillary_download.py --city "New York" --country "USA" --limit 50 --all-images
#   python mapillary_download.py --city "Shillong" --country "India" --dry-run
#
# How it works:
#   1. Geocodes "city, country" with OpenStreetMap Nominatim -> bounding box (+ boundary polygon).
#   2. Splits the bbox into small tiles (Mapillary returns HTTP 500 on large bboxes).
#   3. Queries tiles in random order (spatial spread), keeps images inside the city boundary.
#   4. Downloads images + writes metadata.geojson to data/streetview/<city>_<country>/
import argparse
import json
import math
import os
import random
import re
import time
import unicodedata

import requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('MAPILLARY_TOKEN')
MLY_URL = 'https://graph.mapillary.com/images'
NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
USER_AGENT = 'GeoSense-Agent/1.0 (research use)'  # Nominatim requires a descriptive UA

try:  # optional: precise filtering to the city boundary
    from shapely.geometry import Point, shape
    from shapely.prepared import prep
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


# ---------------------------------------------------------------- geocoding
def geocode_city(city, country):
    """Return (min_lon, min_lat, max_lon, max_lat), boundary_geojson or None, display_name."""
    params = {'city': city, 'country': country, 'format': 'json',
              'limit': 1, 'polygon_geojson': 1}
    r = requests.get(NOMINATIM_URL, params=params,
                     headers={'User-Agent': USER_AGENT}, timeout=30)
    r.raise_for_status()
    results = r.json()
    if not results:  # fall back to free-text search
        r = requests.get(NOMINATIM_URL,
                         params={'q': f'{city}, {country}', 'format': 'json',
                                 'limit': 1, 'polygon_geojson': 1},
                         headers={'User-Agent': USER_AGENT}, timeout=30)
        r.raise_for_status()
        results = r.json()
    if not results:
        raise SystemExit(f'Could not find "{city}, {country}" on OpenStreetMap.')
    hit = results[0]
    south, north, west, east = map(float, hit['boundingbox'])
    geom = hit.get('geojson')
    if geom and geom.get('type') not in ('Polygon', 'MultiPolygon'):
        geom = None  # a point result has no usable boundary
    return (west, south, east, north), geom, hit.get('display_name', f'{city}, {country}')


# ---------------------------------------------------------------- mapillary
def make_tiles(min_lon, min_lat, max_lon, max_lat, step):
    nx = max(1, math.ceil(round((max_lon - min_lon) / step, 6)))
    ny = max(1, math.ceil(round((max_lat - min_lat) / step, 6)))
    tiles = []
    for i in range(nx):
        for j in range(ny):
            x0, y0 = min_lon + i * step, min_lat + j * step
            tiles.append((round(x0, 6), round(y0, 6),
                          round(min(x0 + step, max_lon), 6),
                          round(min(y0 + step, max_lat), 6)))
    return tiles


def query_tile(bbox, pano_only, per_tile, retries=3):
    params = {
        'fields': 'id,thumb_2048_url,geometry,compass_angle,captured_at,is_pano',
        'bbox': ','.join(map(str, bbox)),
        'limit': per_tile,
    }
    if pano_only:
        params['is_pano'] = 'true'
    headers = {'Authorization': f'OAuth {TOKEN}'}
    for attempt in range(retries):
        try:
            r = requests.get(MLY_URL, params=params, headers=headers, timeout=60)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.json().get('data', [])
        if r.status_code in (401, 403):
            raise SystemExit(f'Mapillary auth failed ({r.status_code}). Check MAPILLARY_TOKEN.')
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
    return []


def find_images(bbox, boundary, limit, pano_only, tile_deg, per_tile, seed=42):
    inside = None
    if boundary and HAS_SHAPELY:
        poly = prep(shape(boundary))
        inside = lambda lon, lat: poly.contains(Point(lon, lat))

    tiles = make_tiles(*bbox, step=tile_deg)
    random.Random(seed).shuffle(tiles)  # spread samples across the city
    found = {}
    for i, t in enumerate(tiles, 1):
        for img in query_tile(t, pano_only, per_tile):
            lon, lat = img['geometry']['coordinates']
            if inside and not inside(lon, lat):
                continue
            found[img['id']] = img
            if len(found) >= limit:
                print(f'  reached limit after {i}/{len(tiles)} tiles')
                return list(found.values())
        if i % 25 == 0:
            print(f'  scanned {i}/{len(tiles)} tiles, {len(found)} images so far')
    return list(found.values())


def download_image(meta, save_dir):
    url = meta.get('thumb_2048_url')
    if not url:
        return None
    path = os.path.join(save_dir, f"{meta['id']}.jpg")
    if os.path.exists(path):
        return path
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            with open(path, 'wb') as f:
                f.write(r.content)
            return path
        except requests.RequestException:
            time.sleep(2 ** attempt)
    return None


def write_geojson(images, path):
    feats = [{
        'type': 'Feature',
        'geometry': img['geometry'],
        'properties': {k: img.get(k) for k in
                       ('id', 'compass_angle', 'captured_at', 'is_pano', 'thumb_2048_url')},
    } for img in images]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'type': 'FeatureCollection', 'features': feats}, f)


def slug(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description='Download Mapillary street-level images for a city.')
    ap.add_argument('--city', required=True)
    ap.add_argument('--country', required=True)
    ap.add_argument('--limit', type=int, default=100, help='max images to download')
    ap.add_argument('--all-images', action='store_true',
                    help='include normal (non-360) images; default is 360 panoramas only')
    ap.add_argument('--tile-deg', type=float, default=0.01, help='tile size in degrees')
    ap.add_argument('--per-tile', type=int, default=20,
                    help='max images taken from one tile (lower = better spatial spread)')
    ap.add_argument('--out', default='data/streetview', help='base output folder')
    ap.add_argument('--dry-run', action='store_true', help='geocode and show tile count only')
    args = ap.parse_args()

    bbox, boundary, name = geocode_city(args.city, args.country)
    tiles = make_tiles(*bbox, step=args.tile_deg)
    print(f'Location : {name}')
    print(f'BBox     : {bbox}')
    print(f'Boundary : {"polygon" if boundary else "bbox only"}'
          f'{"" if HAS_SHAPELY or not boundary else " (install shapely to filter by it)"}')
    print(f'Tiles    : {len(tiles)} x {args.tile_deg} deg')
    if args.dry_run:
        return
    if not TOKEN:
        raise SystemExit('MAPILLARY_TOKEN not set in .env')

    out_dir = os.path.join(args.out, f'{slug(args.city)}_{slug(args.country)}')
    img_dir = os.path.join(out_dir, 'images')
    os.makedirs(img_dir, exist_ok=True)

    kind = 'all' if args.all_images else '360-degree'
    print(f'Searching for up to {args.limit} {kind} images...')
    images = find_images(bbox, boundary, args.limit, not args.all_images,
                         args.tile_deg, args.per_tile)
    print(f'Found {len(images)} images')
    if not images and not args.all_images:
        print('No 360 images found - try again with --all-images.')
        return

    write_geojson(images, os.path.join(out_dir, 'metadata.geojson'))
    ok = 0
    for i, img in enumerate(images, 1):
        if download_image(img, img_dir):
            ok += 1
        if i % 10 == 0 or i == len(images):
            print(f'  downloaded {ok}/{i}')
    print(f'Done. Images: {img_dir}\nMetadata: {os.path.join(out_dir, "metadata.geojson")}')


if __name__ == '__main__':
    main()
