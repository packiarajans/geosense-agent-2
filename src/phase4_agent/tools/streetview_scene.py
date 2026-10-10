# streetview_scene.py -- Tool 4: street-view scene composition + walkability (Phase 3, Mapillary + SegFormer)
from ._common import safe

@safe
def streetview_scene(latitude: float, longitude: float) -> dict:
    from streetview_semantic import analyze_location
    return analyze_location(latitude, longitude)
