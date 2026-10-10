# test_phase3_modules.py
# Purpose: Automated tests to verify Phase 3 modules work correctly
# Run with: python -m pytest tests/ -v
#
# Tests 1-4 are the manual's (G.1) tests. They use a bbox inside the processed study area,
# because lidar_analyzer now applies the bbox (Chennai has no LiDAR - see the extra tests).
# Set GEOSENSE_OFFLINE=1 to skip the one test that needs internet (Mapillary + model download).
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'src' / 'phase3_lidar'))

from lidar_analyzer import PROCESSED_CLOUD_PATH, analyze_bbox  # noqa: E402
from unified_site_profile import build_profile  # noqa: E402

COVERED_BBOX = (42.950, -75.608, 42.955, -75.600)   # inside study_area (Madison County, NY)
CHENNAI_BBOX = (13.05, 80.24, 13.06, 80.25)          # manual's example bbox - no LiDAR here

needs_lidar = pytest.mark.skipif(not Path(PROCESSED_CLOUD_PATH).exists(),
                                 reason=f'{PROCESSED_CLOUD_PATH} not found - run pdal_pipeline.py first')
needs_internet = pytest.mark.skipif(os.getenv('GEOSENSE_OFFLINE') == '1',
                                    reason='GEOSENSE_OFFLINE=1')


@pytest.fixture(scope='module')
def covered_result():
    """Run the analyzer once for all tests (loading millions of points is the slow part)."""
    if not Path(PROCESSED_CLOUD_PATH).exists():
        pytest.skip(f'{PROCESSED_CLOUD_PATH} not found - run pdal_pipeline.py first')
    return analyze_bbox(*COVERED_BBOX)


# ---------------------------------------------------------------- manual G.1 tests
def test_lidar_analyzer_returns_dict(covered_result):
    assert isinstance(covered_result, dict), 'Result should be a dictionary'


def test_lidar_analyzer_required_keys(covered_result):
    required = ['avg_building_height_m', 'canopy_cover_pct', 'impervious_surface_pct', 'slope_risk_score']
    assert 'error' not in covered_result, covered_result.get('error')
    for key in required:
        assert key in covered_result, f'Missing key: {key}'


def test_canopy_cover_in_valid_range(covered_result):
    assert 0 <= covered_result['canopy_cover_pct'] <= 100, 'Canopy cover must be 0-100'


@needs_lidar
@needs_internet
def test_unified_profile_has_all_fields():
    profile = build_profile(13.0827, 80.2707)
    required = ['avg_building_height_m', 'canopy_cover_pct', 'scene_composition', 'walkability_score']
    for key in required:
        assert key in profile, f'Missing key: {key}'


# ---------------------------------------------------------------- extra checks
def test_other_metrics_in_valid_range(covered_result):
    for key in ('building_cover_pct', 'impervious_surface_pct', 'data_coverage_pct'):
        assert 0 <= covered_result[key] <= 100, f'{key} must be 0-100'
    assert 0 <= covered_result['slope_risk_score'] <= 10
    assert 0 <= covered_result['avg_building_height_m'] <= covered_result['p95_building_height_m'] <= 600


def test_bbox_filter_is_applied(covered_result):
    """A small bbox must use fewer points than the whole file."""
    full = analyze_bbox(42.946, -75.61303, 42.9596, -75.59451)
    assert 0 < covered_result['total_points_analyzed'] < full['total_points_analyzed']


@needs_lidar
def test_no_coverage_returns_error():
    result = analyze_bbox(*CHENNAI_BBOX)
    assert 'error' in result and 'data_extent_latlon' in result


def test_walkability_score_ranks_sensibly():
    from streetview_semantic import compute_walkability_score
    ped_street, _ = compute_walkability_score(dict(road=10, sidewalk=25, crosswalk=3,
                                                   vegetation=25, person=3, vehicle=2))
    highway, _ = compute_walkability_score(dict(road=60, sidewalk=0, vegetation=5, vehicle=30))
    assert 0 <= highway < 2 < 8 < ped_street <= 10


def test_label_groups_cover_both_models():
    from streetview_semantic import label_group
    assert label_group('sidewalk') == 'sidewalk'                 # Cityscapes
    assert label_group('Crosswalk - Plain') == 'crosswalk'       # Mapillary Vistas
    assert label_group('Pedestrian Area') == 'sidewalk'
    assert label_group('Car') == 'vehicle' and label_group('Ego Vehicle') is None


def test_panorama_split_gives_four_views():
    from PIL import Image
    from streetview_semantic import is_panorama, pano_to_views
    pano = Image.new('RGB', (2048, 1024))
    assert is_panorama(pano) and not is_panorama(Image.new('RGB', (800, 600)))
    views = pano_to_views(pano)
    assert len(views) == 4 and all(v.size == (640, 640) for v in views)
