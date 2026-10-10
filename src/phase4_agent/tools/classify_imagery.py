# classify_imagery.py -- Tool 2: satellite land-cover classification
from ._common import safe

@safe
def classify_imagery(latitude: float, longitude: float, radius_m: int = 500) -> dict:
    # The manual expects src/phase2_dl/image_classifier.py. That folder is empty in this project
    # (the land-use U-Net lives in the separate GeoVision_Agent project), so report honestly.
    return {'latitude': latitude, 'longitude': longitude, 'status': 'unavailable',
            'error': 'No Phase 2 image classifier is wired into GeoSense yet (src/phase2_dl is empty).',
            'next_step': 'Point this tool at the GeoVision_Agent land-use model once its inference function is exposed.'}
