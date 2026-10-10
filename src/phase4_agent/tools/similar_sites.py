# similar_sites.py -- Tool 7: nearest sites in feature space among the labelled Chennai candidate grid.
import pandas as pd
from ._common import ROOT, city_for, out_of_coverage, safe

_df = None

@safe
def find_similar_sites(latitude: float, longitude: float, top_k: int = 5) -> dict:
    global _df
    if city_for(latitude, longitude) != 'chennai':
        r = out_of_coverage(latitude, longitude, 'find_similar_sites')
        r['detail'] = 'Candidate sites exist for Chennai only.'
        return r
    from site_scorer import _get_features_for_point
    from db_connection import get_engine
    import os; os.environ['GEOSENSE_CITY'] = 'chennai'
    if _df is None:
        _df = pd.read_csv(ROOT / 'data' / 'processed' / 'labelled_sites.csv')
    cols = ['dist_road_m', 'dist_hospital_m', 'flood_risk']
    ref = _get_features_for_point(latitude, longitude, get_engine())
    sd = _df[cols].std().replace(0, 1)
    d = (((_df[cols] - pd.Series(ref)[cols]) / sd) ** 2).sum(axis=1) ** 0.5
    top = _df.assign(feature_distance=d.round(3)).sort_values('feature_distance').head(top_k)
    sites = [{'latitude': float(r.latitude), 'longitude': float(r.longitude), 'label': int(r.label),
              'feature_distance': float(r.feature_distance),
              'features': {c: round(float(r[c]), 1) for c in cols}} for _, r in top.iterrows()]
    return {'reference': {'latitude': latitude, 'longitude': longitude, 'features': ref},
            'similar_sites': sites, 'note': 'label 1 = good site, 0 = poor site (Phase 1 labels)'}
