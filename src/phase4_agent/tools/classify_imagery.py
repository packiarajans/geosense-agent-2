# classify_imagery.py -- Tool 2: satellite land-cover classification (Phase 2 U-Net)
# Uses the trained U-Net (velachery_unet.pth) on the exported Sentinel-2 6-band tile of Velachery, Chennai.
# Files stay where Phase 2 produced them; point GEOVISION_DATA_DIR at another folder to move them.
import os
from pathlib import Path

from ._common import safe

CLASSES = ['tree', 'shrub', 'grass', 'crop', 'built', 'bare', 'water', 'wetland']
DATA_DIR = Path(os.getenv('GEOVISION_DATA_DIR', r'F:\geosense-agent-2\data'))
MODEL_FILE = 'velachery_unet.pth'
TILE_FILE = 'velachery_s2_6band.tif'     # bands B2,B3,B4,B8,B11,B12 as 0-1 reflectance, EPSG:4326, 10 m
_model = None


def _load_model(n_bands):
    global _model
    if _model is None:
        import torch
        import segmentation_models_pytorch as smp
        m = smp.Unet(encoder_name='resnet34', encoder_weights=None, in_channels=n_bands, classes=len(CLASSES))
        m.load_state_dict(torch.load(DATA_DIR / MODEL_FILE, map_location='cpu'))
        m.eval()
        _model = m
    return _model


@safe
def classify_imagery(latitude: float, longitude: float, radius_m: int = 500) -> dict:
    import numpy as np
    import rasterio
    import torch
    from rasterio.windows import from_bounds

    tile_path, model_path = DATA_DIR / TILE_FILE, DATA_DIR / MODEL_FILE
    if not tile_path.exists() or not model_path.exists():
        return {'latitude': latitude, 'longitude': longitude, 'status': 'unavailable',
                'error': f'Phase 2 model or imagery tile not found in {DATA_DIR}. Set GEOVISION_DATA_DIR.'}

    radius_m = max(100, min(int(radius_m), 1500))
    with rasterio.open(tile_path) as src:
        b = src.bounds
        if not (b.left <= longitude <= b.right and b.bottom <= latitude <= b.top):
            return {'latitude': latitude, 'longitude': longitude, 'status': 'out_of_coverage',
                    'error': 'classify_imagery: no imagery for this location',
                    'coverage': {'area': 'Velachery, Chennai',
                                 'bbox': [round(b.left, 4), round(b.bottom, 4), round(b.right, 4), round(b.top, 4)],
                                 'bbox_order': 'min_lon,min_lat,max_lon,max_lat'}}
        deg = radius_m / 111_000
        win = from_bounds(longitude - deg, latitude - deg, longitude + deg, latitude + deg, src.transform)
        tile = src.read(window=win, boundless=True, fill_value=0).astype('float32')
    tile = np.nan_to_num(tile)
    n, h, w = tile.shape

    # U-Net needs height/width divisible by 32: pad, predict, then crop back
    ph, pw = (-h) % 32, (-w) % 32
    padded = np.pad(tile, ((0, 0), (0, ph), (0, pw)), mode='reflect')
    model = _load_model(n)
    with torch.no_grad():
        probs = torch.softmax(model(torch.from_numpy(padded[None])), dim=1)[0].numpy()[:, :h, :w]
    pred = probs.argmax(0)

    counts = np.bincount(pred.ravel(), minlength=len(CLASSES))
    share = {CLASSES[i]: round(float(counts[i]) / pred.size * 100, 1) for i in range(len(CLASSES)) if counts[i]}
    top = int(counts.argmax())
    conf = float(probs[top][pred == top].mean())
    red, nir = tile[2], tile[3]
    ndvi = float(np.mean((nir - red) / (nir + red + 1e-6)))
    return {'latitude': latitude, 'longitude': longitude, 'radius_m': radius_m,
            'dominant_class': CLASSES[top], 'confidence': round(conf, 3),
            'class_share_percent': dict(sorted(share.items(), key=lambda kv: -kv[1])),
            'mean_ndvi': round(ndvi, 3), 'pixels': int(pred.size), 'resolution_m': 10,
            'source': 'Phase 2 U-Net on Sentinel-2 (Velachery tile)',
            'caveat': 'Model trained on one area with weak labels; treat as indicative, not survey-grade.'}
