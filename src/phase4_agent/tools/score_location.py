# score_location.py -- Tool 1: ML site suitability (Phase 1 RandomForest + SHAP)
import os
from ._common import city_for, out_of_coverage, safe

@safe
def score_location(latitude: float, longitude: float) -> dict:
    city = city_for(latitude, longitude)
    if city != 'chennai':
        r = out_of_coverage(latitude, longitude, 'score_location')
        r['detail'] = 'The saved Phase 1 model was trained on Chennai only.'
        return r
    os.environ['GEOSENSE_CITY'] = 'chennai'      # site_scorer reads the flood table for this city
    from site_scorer import score_location as _score
    out = _score(latitude, longitude)
    out['city'] = city
    return out
