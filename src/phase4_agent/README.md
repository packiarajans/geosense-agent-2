# GeoSense Agent 2.0 - Phase 4: MCP Server, GeoAI Agent and 3D Digital Twin

Phase 4 turns the analysis modules from Phases 1-3 into one conversational system:

1. **MCP server** - exposes 7 geospatial analysis tools over REST (`/tools/<name>`) and the Model Context Protocol (`/mcp`).
2. **GeoAI agent** - a LangGraph agent that reads a plain-English question, picks tools, calls the server and writes an answer.
3. **3D Digital Twin** - a CesiumJS globe with flood-risk zones, LiDAR-derived 3D buildings, live street view and a camera that flies to the agent's results.
4. **Streamlit demo** - one page with a question box, the answer and the embedded twin.

```
 Streamlit (8501) --question--> LangGraph agent --HTTP--> MCP server (8000) --> 7 tools --> PostGIS / LiDAR / rasters
        |                                                       |
        |  embeds                                               +-- /agent/latest_result  <-- polled every 3 s
        v                                                                                          |
 CesiumJS twin (5173)  <---------------- flies camera + opens street view -------------------------+
```

## Coverage (what the data supports)

| Area | Tools with data |
|------|-----------------|
| Chennai | site scoring, flood-zone risk, similar sites, nearest hospital (OSM) |
| Manhattan, New York | LiDAR building profile, flood-zone risk (NOAA 2017 topobathy DEM) |
| Anywhere with Mapillary photos | street-view scene composition and walkability |

Outside these areas the tools return an explicit "no data" message rather than invented values.
Flood layers are **elevation-based estimates** (relative terciles or absolute elevation bands), not hydraulic models.

## The 7 tools

| Tool | What it returns |
|------|-----------------|
| `score_location` | Site-suitability score for a point (Chennai) |
| `classify_imagery` | Land-use class from imagery - **currently an "unavailable" stub** (GeoVision model not wired in) |
| `lidar_profile` | Building heights / footprints around a point (Manhattan LiDAR) |
| `streetview_scene` | Road / building / vegetation shares and a walkability score from Mapillary photos |
| `get_climate_risk` | Elevation and flood-zone class at a point (temperature / drought not available) |
| `get_demographics` | Distance to the nearest hospital (population density not available) |
| `find_similar_sites` | Most similar candidate sites to a point (Chennai) |

## Folder layout

```
src/phase4_agent/
  mcp_server.py          FastAPI + MCP server (port 8000)
  geoai_agent.py         LangGraph agent (plan -> call tools -> write answer), CLI and ask_agent()
  app.py                 Streamlit demo
  tools/                 one file per tool, plus _common.py (coverage checks, error wrapper)
  export_flood_geojson.py, export_lidar_geojson.py,
  make_floodzones_absolute.py, prep_nyc_dem.py   data preparation for the twin
  run_mcp_server.bat, run_agent.bat, run_app.bat launchers (Windows)
frontend/digital_twin/   Vite + CesiumJS project (port 5173); data in public/*.geojson
outputs/agent_logs/      agent JSONL logs and server logs (git-ignored)
```

## Setup

Python environment: the existing `geosense2` conda environment (Python with geopandas, rasterio, laspy, torch, transformers,
fastapi, uvicorn, `mcp`, langgraph, langchain-anthropic, httpx, streamlit, truststore).
Node.js 18+ for the twin.

### 1. Configure `F:\GeoSense_Agent\.env` (never commit this file)

```
DB_HOST=...  DB_PORT=...  DB_NAME=...  DB_USER=...  DB_PASSWORD=...   # PostGIS
MAPILLARY_TOKEN=...                      # street view tool
MCP_SERVER_URL=http://localhost:8000
AGENT_MODEL=claude-sonnet-5-5            # any model your key can use, e.g. claude-haiku-5-5 for cheaper tests
ANTHROPIC_API_KEY=sk-ant-api03-...       # Anthropic Console key (needs API credit)
ANTHROPIC_WORKSPACE_ID=...               # only if the key is not scoped to a workspace
```

### 2. Configure the twin: `frontend/digital_twin/.env`

```
VITE_CESIUM_ION_TOKEN=...      # free token from cesium.com/ion
VITE_MAPILLARY_TOKEN=...       # same Mapillary token; used in the browser for the street-view panel
```

Install the twin's packages once: `cd frontend/digital_twin && npm install`.

## Running the demo (three processes, in this order)

| # | Process | How | URL |
|---|---------|-----|-----|
| 1 | MCP server | double-click `src\phase4_agent\run_mcp_server.bat` | http://localhost:8000/docs |
| 2 | 3D twin | `cd frontend\digital_twin` then `npm run dev` | http://localhost:5173 |
| 3 | Streamlit demo | double-click `src\phase4_agent\run_app.bat` | http://localhost:8501 |

CLI alternative to Streamlit: `run_agent.bat "Is the area at 13.0827, 80.2707 flood prone?"`.

## Using it

- **Ask the agent** in Streamlit, e.g. *"Is the area near 13.0827, 80.2707 in Chennai prone to flooding?"* The answer appears, the tools called are listed, and the twin flies to the location with a cyan pin and opens street view there.
- **Twin controls:** site switcher (Chennai / Manhattan UES / Lower Manhattan), flood layer on/off, NYC flood mode (absolute bands or terciles), LiDAR buildings on/off. **Click the globe** to open Mapillary street view at that point.
- **Call a tool directly** (Postman or PowerShell): `POST http://localhost:8000/tools/get_climate_risk` with `{"latitude": 40.7033, "longitude": -74.017}`.
- **Move the camera without the agent:**
  `Invoke-RestMethod -Method Post http://localhost:8000/agent/latest_result -ContentType 'application/json' -Body '{"latitude":40.7778,"longitude":-73.9565}'`

## Server endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | status and tool list |
| `GET /docs` | interactive API docs |
| `POST /tools/<tool_name>` | run one tool |
| `GET/POST /agent/latest_result` | latest agent location, polled by the twin |
| `/mcp` | MCP (streamable HTTP) for MCP clients such as Claude Desktop or MCP Inspector |

## Status and known limitations

- Server, 7 tools (6 working + 1 stub), twin, flood/LiDAR layers, camera fly-to and street view are built and were tested individually.
- The agent graph was verified with a stubbed model. A real-model run needs Anthropic API credit; the Streamlit end-to-end check is pending that.
- `classify_imagery` is not connected to the GeoVision model yet.
- The historical imagery year slider (2015 / 2018 / 2021 / 2024) is not built.
- Chennai has flood zones but no LiDAR buildings; street-view coverage in Chennai may be patchy (Mapillary is crowd-sourced).
- First `streetview_scene` call is slow (downloads photos and the segmentation model); later calls are faster.
- The TLS-inspecting network needs `truststore` (already injected in `mcp_server.py`) for Python HTTPS calls.

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| "Cannot reach the MCP server" in the agent | start `run_mcp_server.bat` first |
| "credit balance is too low" | add credit in the Anthropic Console on the organization that owns the key |
| "key is not scoped to a workspace" | set `ANTHROPIC_WORKSPACE_ID` in `.env` |
| Twin shows a blank/grey globe | missing or wrong `VITE_CESIUM_ION_TOKEN`; restart `npm run dev` after editing `.env` |
| Street-view panel says "Add VITE_MAPILLARY_TOKEN" | set it in `frontend/digital_twin/.env` and restart the twin |
| Python `CERTIFICATE_VERIFY_FAILED` | `pip install truststore` in `geosense2` (server already calls `inject_into_ssl()`) |
| Port 8000 already in use | stop the older server process using that port, then restart |
| Browser blocks calls to port 8000 (CORS) | the origin must be listed in the CORS block in `mcp_server.py` (5173 and 8501 are included) |

## Data sources

NOAA 2017 NYC topobathy DEM, NYC LiDAR (Manhattan Upper East Side and FiDi tiles), Chennai DEM and OpenStreetMap extract (South India),
Mapillary street-level imagery, Cesium Ion world terrain.
