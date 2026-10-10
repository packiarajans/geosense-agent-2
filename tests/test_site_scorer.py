# tests/test_site_scorer.py
# Unit tests for site_scorer.py
#
# Run from the project root:
#   pytest -v tests/test_site_scorer.py
#
# These are integration-style tests: they need the trained model on disk and a
# reachable PostGIS database with the osm_* tables loaded. If either is missing
# the tests SKIP (rather than fail) with a clear message, so the suite is safe
# to run at any stage of the project.

import os
import pytest

from src.phase1_ml.site_scorer import (
    score_location, _flood_risk_for_point, FEATURE_NAMES, MODEL_PATH,
)
from src.phase1_ml.db_connection import get_engine

CHENNAI_CENTRE = (13.0827, 80.2707)


def _db_ready():
    try:
        eng = get_engine()
        with eng.connect() as c:
            from sqlalchemy import text
            c.execute(text('SELECT 1 FROM osm_roads LIMIT 1'))
        return True
    except Exception:
        return False


requires_stack = pytest.mark.skipif(
    not (os.path.exists(MODEL_PATH) and _db_ready()),
    reason='Needs the trained model on disk and a PostGIS DB with osm_* tables.',
)


def test_feature_names_are_three():
    # The scorer and the trained model must agree on exactly these features.
    assert FEATURE_NAMES == ['dist_road_m', 'dist_hospital_m', 'flood_risk']


@requires_stack
def test_score_location_structure():
    result = score_location(*CHENNAI_CENTRE)
    for key in ('latitude', 'longitude', 'opportunity_score', 'risk_score',
                'features', 'shap_explanation', 'verdict'):
        assert key in result


@requires_stack
def test_scores_in_range_and_complementary():
    r = score_location(*CHENNAI_CENTRE)
    assert 0.0 <= r['opportunity_score'] <= 10.0
    assert 0.0 <= r['risk_score'] <= 10.0
    # opportunity and risk come from complementary class probabilities
    assert abs(r['opportunity_score'] + r['risk_score'] - 10.0) < 0.05


@requires_stack
def test_verdict_matches_opportunity():
    r = score_location(*CHENNAI_CENTRE)
    expected = 'GOOD SITE' if r['opportunity_score'] > 5 else 'POOR SITE'
    assert r['verdict'] == expected


@requires_stack
def test_all_features_present_and_numeric():
    r = score_location(*CHENNAI_CENTRE)
    for f in FEATURE_NAMES:
        assert f in r['features']
        assert isinstance(r['features'][f], (int, float))


@requires_stack
def test_flood_risk_in_expected_set():
    # feature_engineering maps zones to {0,2,5,8}; a point outside all zones -> 0
    val = _flood_risk_for_point(*CHENNAI_CENTRE, get_engine())
    assert val in (0.0, 2.0, 5.0, 8.0)
