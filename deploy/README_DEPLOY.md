# Phase 5 deployment notes (GeoSense Agent 2.0)

Scaffolded 2026-10-14. **Nothing here has been built or run yet** -- Docker was not exercised when these files were written.

## What is in `deploy/`
| File | Purpose |
|---|---|
| `Dockerfile.backend` | FastAPI + MCP server (`src.phase4_agent.mcp_server:app`), honours Cloud Run `PORT` |
| `Dockerfile.frontend` | Streamlit app (`app.py` + `geoai_agent.py`), honours `PORT` |
| `docker-compose.yml` | PostGIS + backend + frontend for local testing |
| `requirements-backend.txt` / `-frontend.txt` / `-ml-extra.txt` | Split dependency lists (instead of one `pip freeze`) |

## Run locally in containers
From `F:\GeoSense_Agent` (Docker Desktop running; `.env` must contain `ANTHROPIC_API_KEY` and `DB_PASSWORD`):

    docker compose -f deploy/docker-compose.yml --env-file .env up --build

Then open http://localhost:8000/docs and http://localhost:8501.

## Gaps the manual does not cover (decide before Week 2)
1. **Database.** The app reads PostGIS schemas `chennai` and `newyork`/Manhattan (DEM, flood zones, OSM, labelled sites). The compose DB starts empty. Restore your local DB into it with `pg_dump` / `pg_restore`; for the cloud you need a hosted PostGIS (e.g. Cloud SQL) loaded the same way.
2. **`data/` is git-ignored and not in the image.** LiDAR `.laz`, the Velachery Sentinel-2 tile and processed CSVs live only on your drive. Locally compose mounts `../data`. In the cloud, either upload the needed files to a bucket and read from there, or deploy a reduced demo where those tools return their honest "no data" error.
3. **3D digital twin** (`frontend/digital_twin`, Vite on port 5173) is a separate app that Streamlit embeds via `TWIN_URL`. It needs its own hosting (static build on Cloud Run/nginx, Netlify, etc.), and its URL must be added to `EXTRA_CORS_ORIGINS` on the backend. Not scaffolded yet.
4. **Agent runs inside the frontend container**, so `ANTHROPIC_API_KEY` goes on the frontend service, not (only) the backend as in the manual.
5. **Models.** Only `models/saved/site_scorer_model.pkl` (~0.7 MB) is baked in; `.gitignore` was adjusted to allow committing it. The SAM checkpoint (375 MB) is excluded. U-Net weights for `classify_imagery` were not located -- check where they load from.
6. **Heavy ML extras** (torch, segmentation-models-pytorch, transformers) are optional via `--build-arg INSTALL_ML_EXTRA=true`; expect a ~2 GB larger image and more Cloud Run memory than 2Gi.
7. **PDAL** is intentionally not installed; add it only if the LiDAR path imports it.

## Cloud Run (Week 2) -- do not run until billing is enabled and you have agreed
Set secrets with Secret Manager rather than `--set-env-vars`. Env needed:
backend: `DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, MAPILLARY_TOKEN, EXTRA_CORS_ORIGINS`
frontend: `ANTHROPIC_API_KEY, MCP_SERVER_URL, TWIN_URL, AGENT_MODEL`

## CI (`.github/workflows/deploy.yml`)
CI compiles the code and builds both images on every push/PR. The Cloud Run deploy job stays off until the repo variable `DEPLOY_ENABLED=true` and secrets `GCP_SA_KEY`, `GCP_PROJECT_ID` exist. The existing pytest suite needs a live server and local data, so it is not run in CI.

## Changes made to existing files (originals in `backup/`)
- `src/phase4_agent/mcp_server.py`: CORS origins extendable via `EXTRA_CORS_ORIGINS`; `HOST`/`PORT` env for the `__main__` block (local default unchanged: 127.0.0.1:8000).
- `.gitignore`: `models/` -> allow `models/saved/site_scorer_model.pkl`; added key/secret patterns.
