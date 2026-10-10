# _common.py -- shared helpers for the Phase 4 MCP tools
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # F:/GeoSense_Agent
for p in (ROOT, ROOT / 'src' / 'phase1_ml', ROOT / 'src' / 'phase3_lidar'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Where each analysis module has real data. Tools outside these boxes return a clear
# "out of coverage" error instead of a made-up answer.   bbox = (min_lon, min_lat, max_lon, max_lat)
CITIES = {
    'chennai': {'bbox': (80.134, 12.965, 80.334, 13.236), 'model_trained': True},
    'newyork': {'bbox': (-74.048, 40.683, -73.907, 40.882), 'model_trained': False},   # Manhattan
}

def city_for(lat, lon):
    for name, c in CITIES.items():
        x0, y0, x1, y1 = c['bbox']
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            return name
    return None

def out_of_coverage(lat, lon, what):
    return {'latitude': lat, 'longitude': lon, 'error': f'{what}: no data for this location',
            'coverage': {k: v['bbox'] for k, v in CITIES.items()}, 'bbox_order': 'min_lon,min_lat,max_lon,max_lat'}

def safe(fn):
    """Tools must never crash the server: turn any exception into an error dict."""
    import functools
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except SystemExit as e:
            return {'error': str(e)}
        except Exception as e:
            return {'error': f'{type(e).__name__}: {e}'}
    return wrapper
