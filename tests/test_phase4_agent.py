# test_phase4_agent.py
# Purpose: automated tests for the Phase 4 MCP server, tools and GeoAI agent.
# Run from the project root:   python -m pytest tests/test_phase4_agent.py -v
# Requires the MCP server running on port 8000 (start_all.bat). Tests marked "agent" call the Anthropic API
# (a few cents); skip them with:  python -m pytest tests/test_phase4_agent.py -v -m "not agent"
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'phase4_agent'))

MCP_URL = 'http://localhost:8000'
CHENNAI = {'latitude': 13.0827, 'longitude': 80.2707}
MANHATTAN = {'latitude': 40.7033, 'longitude': -74.017}
LONDON = {'latitude': 51.5074, 'longitude': -0.1278}
TOOL_NAMES = ['score_location', 'classify_imagery', 'lidar_profile', 'streetview_scene',
              'get_climate_risk', 'get_demographics', 'find_similar_sites']


def post(path, body, timeout=60):
    return httpx.post(f'{MCP_URL}{path}', json=body, timeout=timeout)


@pytest.fixture(scope='session', autouse=True)
def server_is_up():
    try:
        httpx.get(f'{MCP_URL}/health', timeout=5)
    except Exception:
        pytest.exit('MCP server is not running on port 8000 - start start_all.bat first', returncode=2)


# ---------- G.1 tests from the manual (adapted to this project's response fields) ----------

def test_mcp_server_docs_reachable():
    resp = httpx.get(f'{MCP_URL}/docs')
    assert resp.status_code == 200, 'MCP server docs page should be reachable'


def test_score_location_tool():
    resp = post('/tools/score_location', CHENNAI)
    assert resp.status_code == 200
    assert 'opportunity_score' in resp.json()


@pytest.mark.agent
def test_agent_returns_nonempty_answer():
    from geoai_agent import ask_agent
    answer = ask_agent('Is the area at 13.0827, 80.2707 in Chennai suitable for a new clinic?', thread_id='test-answer')
    assert isinstance(answer, str) and len(answer) > 0, 'Agent should return a non-empty answer'


@pytest.mark.agent
def test_agent_selects_at_least_one_tool():
    from geoai_agent import ask_agent
    rec = ask_agent('Score the flood risk at 13.0827, 80.2707 in Chennai', thread_id='test-tools', return_details=True)
    assert len(rec['tool_results']) > 0, 'Agent should select at least one tool'


# ---------- server tests ----------

def test_health_lists_all_seven_tools():
    body = httpx.get(f'{MCP_URL}/health').json()
    assert body['status'] == 'ok'
    assert sorted(body['tools']) == sorted(TOOL_NAMES)


def test_invalid_latitude_is_rejected():
    assert post('/tools/score_location', {'latitude': 123, 'longitude': 80}).status_code == 422


def test_cors_allows_twin_and_streamlit_origins():
    for origin in ('http://localhost:5173', 'http://localhost:8501'):
        r = httpx.get(f'{MCP_URL}/agent/latest_result', headers={'Origin': origin})
        assert r.headers.get('access-control-allow-origin') == origin


def test_latest_result_round_trip_for_twin_camera():
    r = post('/agent/latest_result', MANHATTAN)
    assert r.status_code == 200
    got = httpx.get(f'{MCP_URL}/agent/latest_result').json()
    assert got['latitude'] == MANHATTAN['latitude'] and got['longitude'] == MANHATTAN['longitude']
    assert got['stamp'] <= time.time()


# ---------- tool tests ----------

def test_climate_risk_chennai_has_flood_class():
    j = post('/tools/get_climate_risk', CHENNAI).json()
    assert not j.get('error')
    assert 'low' in str(j).lower() or 'medium' in str(j).lower() or 'high' in str(j).lower()


def test_climate_risk_manhattan_has_flood_class():
    j = post('/tools/get_climate_risk', MANHATTAN).json()
    assert not j.get('error')


def test_climate_risk_outside_coverage_is_honest():
    j = post('/tools/get_climate_risk', LONDON).json()
    assert j.get('error'), 'a location outside Chennai/Manhattan must return an explicit error, not invented data'


def test_demographics_returns_nearest_hospital():
    j = post('/tools/get_demographics', CHENNAI).json()
    assert not j.get('error')
    assert 'hospital' in str(j).lower()


def test_find_similar_sites_returns_requested_count():
    r = post('/tools/find_similar_sites', {**CHENNAI, 'top_k': 3})
    assert r.status_code == 200
    assert 'error' not in r.json() or not r.json()['error']


def test_lidar_profile_manhattan_box():
    box = {'min_lat': 40.775, 'min_lon': -73.960, 'max_lat': 40.780, 'max_lon': -73.953}
    r = post('/tools/lidar_profile', box)
    assert r.status_code == 200
    assert not r.json().get('error')


def test_classify_imagery_velachery_returns_land_cover():
    j = post('/tools/classify_imagery', {'latitude': 12.98, 'longitude': 80.22}, timeout=120).json()
    assert not j.get('error'), j
    assert j['dominant_class'] in ('tree', 'shrub', 'grass', 'crop', 'built', 'bare', 'water', 'wetland')
    assert 0 <= j['confidence'] <= 1 and abs(sum(j['class_share_percent'].values()) - 100) < 1


def test_classify_imagery_outside_velachery_is_honest():
    j = post('/tools/classify_imagery', MANHATTAN).json()
    assert j.get('error'), 'imagery exists only for the Velachery tile; elsewhere it must say so'

def test_streetview_outside_mapillary_coverage_does_not_crash():
    # mid-ocean point: fast, and must return a structured error rather than an HTTP 500
    r = post('/tools/streetview_scene', {'latitude': 0.0, 'longitude': -140.0}, timeout=120)
    assert r.status_code == 200
    assert r.json().get('error')


# ---------- agent behaviour ----------

@pytest.mark.agent
def test_agent_remembers_context_in_same_thread():
    from geoai_agent import ask_agent
    ask_agent('Is the area at 13.0827, 80.2707 in Chennai prone to flooding?', thread_id='test-memory')
    rec = ask_agent('And how far is the nearest hospital from there?', thread_id='test-memory', return_details=True)
    used = [t for t in rec['tool_results'] if t['tool'] == 'get_demographics']
    assert used, 'follow-up should call the hospital tool'
    assert abs(used[0]['args']['latitude'] - 13.0827) < 1e-3 and abs(used[0]['args']['longitude'] - 80.2707) < 1e-3


@pytest.mark.agent
def test_agent_does_not_invent_data_outside_coverage():
    from geoai_agent import ask_agent
    rec = ask_agent('What is the flood risk at 51.5074, -0.1278?', thread_id='test-london', return_details=True)
    assert 'london' in rec['answer'].lower() or 'cover' in rec['answer'].lower() or "can't" in rec['answer'].lower()
