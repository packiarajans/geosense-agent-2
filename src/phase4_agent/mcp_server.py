# mcp_server.py
# Purpose: Expose the 7 GeoSense analysis modules as tools, two ways from ONE process:
#   * REST   POST /tools/<name>   -> for Postman, Streamlit and the LangGraph agent (docs at /docs)
#   * MCP    streamable HTTP at /mcp  -> real Model Context Protocol, for any MCP client (Claude Desktop, MCP Inspector ...)
# Run from the project ROOT:  python src/phase4_agent/mcp_server.py
import sys, contextlib
try:   # trust the Windows certificate store (TLS-inspecting proxy) for Mapillary / model downloads
    import truststore; truststore.inject_into_ssl()
except Exception:
    pass
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from pydantic import BaseModel, Field
from mcp.server.mcpserver import MCPServer

from src.phase4_agent.tools.score_location import score_location
from src.phase4_agent.tools.classify_imagery import classify_imagery
from src.phase4_agent.tools.lidar_profile import lidar_profile
from src.phase4_agent.tools.streetview_scene import streetview_scene
from src.phase4_agent.tools.climate_risk import get_climate_risk
from src.phase4_agent.tools.demographics import get_demographics
from src.phase4_agent.tools.similar_sites import find_similar_sites

# ---------------------------------------------------------------- MCP (real protocol)
mcp = MCPServer('geosense', instructions=(
    'GeoSense geospatial tools. Coverage: Chennai (site scoring, flood, similar sites) and Manhattan NYC '
    '(LiDAR, flood). Outside coverage a tool returns an error object -- report that, never guess.'))

TOOLS = {
    'score_location':    (score_location,    'Score a location for site suitability (ML model + SHAP explanation). Chennai only.'),
    'classify_imagery':  (classify_imagery,  'Classify satellite land cover at a location. Currently unavailable (no Phase 2 model wired).'),
    'lidar_profile':     (lidar_profile,     'LiDAR building height, canopy cover and ground slope for a lat/lon bounding box. NYC study area.'),
    'streetview_scene':  (streetview_scene,  'Street-view scene composition and walkability score near a point (Mapillary + segmentation).'),
    'get_climate_risk':  (get_climate_risk,  'Elevation and flood-zone risk class at a point (Chennai, Manhattan). Temperature/drought not available.'),
    'get_demographics':  (get_demographics,  'Nearest hospital distance (OSM). Population density not available.'),
    'find_similar_sites':(find_similar_sites,'Find labelled Chennai candidate sites with a similar feature profile to a reference point.'),
}
for _name, (_fn, _desc) in TOOLS.items():
    mcp.tool(name=_name, description=_desc)(_fn)

# ---------------------------------------------------------------- REST (Postman / agent)
def _warmup():
    # Import the heavy geo libraries ONCE, in the main thread, before serving. Importing them lazily from FastAPI's
    # worker threads can race and leave pyproj half-initialised ('partially initialized module').
    for mod in ('pyproj', 'geopandas', 'site_scorer', 'lidar_analyzer'):
        try:
            __import__(mod)
        except Exception as e:
            print(f'[warmup] {mod} not preloaded: {type(e).__name__}: {e}')

@contextlib.asynccontextmanager
async def lifespan(app):
    _warmup()
    async with mcp.session_manager.run():
        yield

app = FastAPI(title='GeoSense Agent MCP Server', version='1.0', lifespan=lifespan)

class LocationInput(BaseModel):
    latitude: float = Field(..., ge=-90, le=90, description='Latitude in WGS84 decimal degrees')
    longitude: float = Field(..., ge=-180, le=180, description='Longitude in WGS84 decimal degrees')

class BboxInput(BaseModel):
    min_lat: float; min_lon: float; max_lat: float; max_lon: float

class SimilarInput(LocationInput):
    top_k: int = Field(5, ge=1, le=20)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173', 'http://localhost:8501', 'http://127.0.0.1:8501'],
    allow_methods=['*'],
    allow_headers=['*'],
)

# E.5.1: the latest location the agent worked on; the 3D twin polls this and flies the camera there.
LAST_AGENT_RESULT = {}

@app.get('/agent/latest_result', summary='Latest agent location (polled by the 3D twin)')
def get_latest_result():
    return LAST_AGENT_RESULT

@app.post('/agent/latest_result', summary='Set the latest agent location (called by the Streamlit app)')
def set_latest_result(loc: LocationInput):
    import time
    LAST_AGENT_RESULT.clear()
    LAST_AGENT_RESULT.update({'latitude': loc.latitude, 'longitude': loc.longitude, 'stamp': time.time()})
    return LAST_AGENT_RESULT
from fastapi.responses import RedirectResponse

@app.get('/', include_in_schema=False)
def root():
    return RedirectResponse('/docs')

@app.get('/health', summary='Server + tool list')
def health():
    return {'status': 'ok', 'tools': list(TOOLS), 'mcp_endpoint': '/mcp'}

@app.post('/tools/score_location', summary=TOOLS['score_location'][1])
def r_score(p: LocationInput): return score_location(p.latitude, p.longitude)

@app.post('/tools/classify_imagery', summary=TOOLS['classify_imagery'][1])
def r_classify(p: LocationInput): return classify_imagery(p.latitude, p.longitude)

@app.post('/tools/lidar_profile', summary=TOOLS['lidar_profile'][1])
def r_lidar(p: BboxInput): return lidar_profile(p.min_lat, p.min_lon, p.max_lat, p.max_lon)

@app.post('/tools/streetview_scene', summary=TOOLS['streetview_scene'][1])
def r_street(p: LocationInput): return streetview_scene(p.latitude, p.longitude)

@app.post('/tools/get_climate_risk', summary=TOOLS['get_climate_risk'][1])
def r_climate(p: LocationInput): return get_climate_risk(p.latitude, p.longitude)

@app.post('/tools/get_demographics', summary=TOOLS['get_demographics'][1])
def r_demo(p: LocationInput): return get_demographics(p.latitude, p.longitude)

@app.post('/tools/find_similar_sites', summary=TOOLS['find_similar_sites'][1])
def r_similar(p: SimilarInput): return find_similar_sites(p.latitude, p.longitude, p.top_k)

app.mount('/', mcp.streamable_http_app(streamable_http_path='/mcp'))   # MCP lives at /mcp

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8000)


