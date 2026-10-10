# streetview_semantic.py
# PURPOSE: Classify street-level imagery into scene categories and compute a walkability score
# INPUT: latitude, longitude
# OUTPUT: dict with scene composition percentages, walkability score and its components
#
# Usage:
#   python src/phase3_lidar/streetview_semantic.py --lat 13.0418 --lon 80.2341
#   python src/phase3_lidar/streetview_semantic.py --lat 40.7075 --lon -74.010 --model vistas --max-images 20
#
# Models (--model):
#   cityscapes (default) nvidia/segformer-b0-finetuned-cityscapes-1024-1024   ~15 MB, fast on CPU
#                        trained on German dash-cam images; no crosswalk/curb classes
#   vistas               facebook/mask2former-swin-large-mapillary-vistas-semantic  ~800 MB, slow on CPU
#                        trained on Mapillary images worldwide (incl. India); has crosswalk, curb,
#                        bike lane, pedestrian area -> better sidewalk detection
#
# 360 panoramas (2:1 equirectangular) are split into 4 perspective views (front/right/back/left,
# 90 deg field of view) before segmentation - segmenting the stretched panorama directly
# under-detects sidewalks badly.
from pathlib import Path

import numpy as np
from PIL import Image

from mapillary_download import download_image, find_images_in_bbox

MODELS = {
    'cityscapes': ('segformer', 'nvidia/segformer-b0-finetuned-cityscapes-1024-1024'),
    'vistas': ('mask2former', 'facebook/mask2former-swin-large-mapillary-vistas-semantic'),
}
GROUPS = ['road', 'sidewalk', 'crosswalk', 'bike_lane', 'building', 'vegetation',
          'sky', 'person', 'vehicle']
SEARCH_RADII_DEG = [0.0015, 0.003, 0.005, 0.01]   # ~165 m -> ~1.1 km (Mapillary errors on larger)
PANO_ASPECT = 1.8      # width/height above this = equirectangular panorama
VIEW_SIZE = 640        # perspective view size in pixels
VIEW_FOV_DEG = 90

_state = {}


# ---------------------------------------------------------------- label grouping
def label_group(name):
    """Map a model's class name (Cityscapes or Mapillary Vistas) to a walkability group."""
    n = name.lower().strip()
    if 'crosswalk' in n:
        return 'crosswalk'
    if 'sidewalk' in n or 'pedestrian area' in n or n == 'curb' or n == 'curb cut':
        return 'sidewalk'
    if 'bike lane' in n:
        return 'bike_lane'
    if n in ('road', 'service lane', 'parking'):
        return 'road'
    if n in ('person', 'rider', 'bicyclist', 'motorcyclist', 'other rider'):
        return 'person'
    if n in ('vegetation', 'terrain'):
        return 'vegetation'
    if n in ('car', 'truck', 'bus', 'caravan', 'trailer', 'other vehicle', 'motorcycle', 'on rails'):
        return 'vehicle'
    if n == 'building':
        return 'building'
    if n == 'sky':
        return 'sky'
    return None


def _load_model(model_key):
    if _state.get('key') == model_key:
        return _state
    import torch
    from transformers import AutoImageProcessor
    kind, name = MODELS[model_key]
    print(f'Loading {model_key} model ({name}) ...')
    processor = AutoImageProcessor.from_pretrained(name)
    if kind == 'segformer':
        from transformers import SegformerForSemanticSegmentation as M
    else:
        from transformers import Mask2FormerForUniversalSegmentation as M
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model = M.from_pretrained(name).to(device).eval()
    id2group = {int(i): label_group(lbl) for i, lbl in model.config.id2label.items()}
    _state.update(key=model_key, kind=kind, processor=processor, model=model,
                  device=device, id2group=id2group, torch=torch)
    return _state


def segment(image, model_key='cityscapes'):
    """Return an HxW array of class ids for a PIL image."""
    st = _load_model(model_key)
    torch = st['torch']
    inputs = st['processor'](images=image, return_tensors='pt').to(st['device'])
    with torch.no_grad():
        out = st['model'](**inputs)
    if st['kind'] == 'segformer':
        return out.logits.argmax(dim=1)[0].cpu().numpy()
    return st['processor'].post_process_semantic_segmentation(
        out, target_sizes=[image.size[::-1]])[0].cpu().numpy()


def composition_from_seg(seg, id2group):
    total = seg.size
    ids, counts = np.unique(seg, return_counts=True)
    comp = {g: 0.0 for g in GROUPS}
    for i, c in zip(ids, counts):
        g = id2group.get(int(i))
        if g:
            comp[g] += 100.0 * c / total
    return comp


# ---------------------------------------------------------------- panoramas
def is_panorama(image, meta=None):
    if meta and meta.get('is_pano') is not None:
        return bool(meta['is_pano'])
    w, h = image.size
    return w / h > PANO_ASPECT


def pano_to_views(image, yaws=(0, 90, 180, 270), fov_deg=VIEW_FOV_DEG, size=VIEW_SIZE, pitch_deg=0):
    """Equirectangular panorama -> list of perspective PIL images (pinhole camera)."""
    pano = np.asarray(image)
    H, W = pano.shape[:2]
    f = 0.5 * size / np.tan(np.radians(fov_deg) / 2)
    u, v = np.meshgrid(np.arange(size) - size / 2 + 0.5, np.arange(size) - size / 2 + 0.5)
    # camera rays (x right, y down, z forward)
    rays = np.stack([u, v, np.full_like(u, f)], axis=-1)
    rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
    p = np.radians(pitch_deg)
    rot_x = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    views = []
    for yaw in yaws:
        y = np.radians(yaw)
        rot_y = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
        d = rays @ (rot_y @ rot_x).T
        lon = np.arctan2(d[..., 0], d[..., 2])                 # -pi..pi
        lat = np.arcsin(np.clip(d[..., 1], -1, 1))              # -pi/2..pi/2 (down positive)
        px = ((lon / (2 * np.pi) + 0.5) * W).astype(np.int64) % W
        py = np.clip(((lat / np.pi + 0.5) * H).astype(np.int64), 0, H - 1)
        views.append(Image.fromarray(pano[py, px]))
    return views


def classify_image(image_path, model_key='cityscapes', meta=None):
    """Percentage of each group in one image (panoramas: averaged over 4 perspective views)."""
    st = _load_model(model_key)
    image = Image.open(image_path).convert('RGB')
    views = pano_to_views(image) if is_panorama(image, meta) else [image]
    comps = [composition_from_seg(segment(v, model_key), st['id2group']) for v in views]
    return {g: float(np.mean([c[g] for c in comps])) for g in GROUPS}, len(views) > 1


# ---------------------------------------------------------------- score
def compute_walkability_score(c):
    """
    0-10 heuristic, each component scaled to the range seen in real street imagery:
      walk surface 40%  (sidewalk + 2x crosswalk + bike lane, saturates at 15% of the image)
      greenery     20%  (vegetation, saturates at 25%)
      pedestrians  15%  (people, saturates at 2%)
      low traffic  15%  (1 - vehicles/25%)
      road share   10%  (penalty only above 30% road)
    Weights are a starting point - calibrate against known walkable / unwalkable places.
    """
    parts = {
        'walk_surface': min((c.get('sidewalk', 0) + 2 * c.get('crosswalk', 0) + c.get('bike_lane', 0)) / 15, 1),
        'greenery': min(c.get('vegetation', 0) / 25, 1),
        'pedestrians': min(c.get('person', 0) / 2, 1),
        'low_traffic': 1 - min(c.get('vehicle', 0) / 25, 1),
        'road_share_ok': 1 - min(max(c.get('road', 0) - 30, 0) / 30, 1),
    }
    w = {'walk_surface': .40, 'greenery': .20, 'pedestrians': .15, 'low_traffic': .15, 'road_share_ok': .10}
    score = 10 * sum(w[k] * parts[k] for k in w)
    return round(score, 2), {k: round(v, 2) for k, v in parts.items()}


# ---------------------------------------------------------------- main API
def analyze_location(lat, lon, radius_deg=None, max_images=10, model='cityscapes'):
    """
    Street-level scene composition near a location. Widens the search (up to ~1.1 km) if no
    imagery is found. Returns composition %, walkability_score and its components.
    """
    radii = [radius_deg] if radius_deg else SEARCH_RADII_DEG
    images, used_radius = [], None
    for r in radii:
        images = find_images_in_bbox(lon - r, lat - r, lon + r, lat + r, limit=max_images)
        print(f'Search radius {r:.4f} deg (~{r * 111:.2f} km): {len(images)} images')
        if images:
            used_radius = r
            break
    if not images:
        return {'latitude': lat, 'longitude': lon,
                'error': f'No street imagery within ~{radii[-1] * 111:.1f} km. '
                         'Check coverage at https://www.mapillary.com/app and try other coordinates.'}
    comps, n_pano = [], 0
    for meta in images:
        path = download_image(meta)
        if not path:
            continue
        comp, was_pano = classify_image(path, model, meta)
        comps.append(comp)
        n_pano += was_pano
    if not comps:
        return {'latitude': lat, 'longitude': lon, 'error': 'Images found but none could be downloaded'}
    avg = {g: round(float(np.mean([c[g] for c in comps])), 1) for g in GROUPS}
    score, parts = compute_walkability_score(avg)
    return {
        'latitude': lat,
        'longitude': lon,
        'model': model,
        'search_radius_km': round(used_radius * 111, 2),
        'images_analyzed': len(comps),
        'panoramas_split': n_pano,
        'scene_composition': avg,
        'walkability_score': score,
        'walkability_components': parts,
    }


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Street-scene composition + walkability near a point.')
    ap.add_argument('--lat', type=float, default=13.0418, help='latitude (default: T. Nagar, Chennai)')
    ap.add_argument('--lon', type=float, default=80.2341, help='longitude')
    ap.add_argument('--radius', type=float, default=None, help='fixed search radius in degrees')
    ap.add_argument('--max-images', type=int, default=10)
    ap.add_argument('--model', choices=list(MODELS), default='cityscapes')
    args = ap.parse_args()
    result = analyze_location(args.lat, args.lon, args.radius, args.max_images, args.model)
    for key, value in result.items():
        print(f'{key:22s}: {value}')
