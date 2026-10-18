# lidar_profile.py -- Tool 3: LiDAR building height / canopy / slope for a bounding box (Phase 3)
# Picks the classified point cloud whose extent contains the bbox centre (Manhattan UES, NYC FiDi, or the
# upstate-NY study area), so the agent does not need to know which file to use.
from ._common import ROOT, safe

_extents = {}
_errors = {}

def _candidates():
    return sorted((ROOT / 'data' / 'lidar' / 'processed').glob('*_classified.laz'))

def _pick(min_lat, min_lon, max_lat, max_lon):
    from lidar_analyzer import data_extent_latlon
    clat, clon = (min_lat + max_lat) / 2, (min_lon + max_lon) / 2
    for f in _candidates():
        if _extents.get(f) is None:          # retry failed reads; only successful extents are cached
            try:
                _extents[f] = data_extent_latlon(f)
            except Exception as ex:
                _extents[f] = None
                _errors[f.name] = f'{type(ex).__name__}: {ex}'
        e = _extents[f]
        if e and e[0] <= clat <= e[2] and e[1] <= clon <= e[3]:
            return f
    return None

@safe
def lidar_profile(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> dict:
    from lidar_analyzer import analyze_bbox
    f = _pick(min_lat, min_lon, max_lat, max_lon)
    if f is None:
        return {'bbox': [min_lat, min_lon, max_lat, max_lon], 'error': 'No LiDAR coverage for this bbox',
                'available_extents_latlon [min_lat,min_lon,max_lat,max_lon]':
                    {p.stem.replace('_classified', ''): e for p, e in _extents.items() if e},
                'read_errors': dict(_errors)}
    out = analyze_bbox(min_lat, min_lon, max_lat, max_lon, laz_path=f)
    out['point_cloud'] = f.stem.replace('_classified', '')
    return out
