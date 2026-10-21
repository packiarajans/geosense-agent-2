# Phase 5 deployment notes (GeoSense Agent 2.0)

Phase 5 is finished: the three images are built, running on Google Cloud Run (Singapore) and released as v1.0.0.
Live links: https://packiarajans.github.io/geosense-agent-2/

## What is in `deploy/`
| File | Purpose |
|---|---|
| `Dockerfile.backend` | FastAPI + MCP server (`src.phase4_agent.mcp_server:app`) with the 7 tools; honours Cloud Run `PORT`; bakes in the demo data (LiDAR, U-Net, labelled sites) |
| `Dockerfile.frontend` | Streamlit app (`app.py` + `geoai_agent.py`); runs the LangGraph agent in-process; honours `PORT` |
| `Dockerfile.twin` | Builds the Vite/CesiumJS 3D twin and serves it with nginx; honours `PORT` |
| `docker-compose.yml` | Local stack: PostGIS + backend + frontend + twin |
| `start.ps1` / `stop.ps1` | Start and stop the local stack (they read `..\.env` and `..\frontend\digital_twin\.env`) |
| `requirements-backend.txt` / `-frontend.txt` / `-ml-extra.txt` | Split dependency lists |
| `deploy.fixed.yml`, `github-workflow-deploy.yml` | Copies of the CI workflow used while editing; the live one is `.github/workflows/deploy.yml` |

## Run locally in containers
Docker Desktop must be running. From `F:\GeoSense_Agent\deploy`:

    .\start.ps1      # frontend :8502, backend :8010/docs, twin :5174, database :5433
    .\stop.ps1       # stops the containers, keeps the database volume

Keys live in the git-ignored `.env` files; never write real values in committed files.

## Cloud deployment (Google Cloud Run, asia-southeast1)
| Service | Memory / CPU | Min / Max instances | Notes |
|---|---|---|---|
| geosense-backend | 4 Gi / 2 CPU | 0 / 3 | concurrency 4; reads Neon and Secret Manager |
| geosense-frontend | 1 Gi / 1 CPU | 0 / 2 | concurrency 10; session affinity |
| geosense-twin | 512 Mi | 0 / 2 | static page behind nginx |

- Images are stored in Artifact Registry `geosense-repo` (asia-south1).
- The backend image is built and pushed from the laptop because it copies `data/`, which is not in git. The frontend and twin images are built with Cloud Build.
- Database: Neon free PostgreSQL with PostGIS (Singapore), trimmed data. Cloud Run uses the pooler host and `PGSSLMODE=require`.
- Secrets in Secret Manager: `neon-db-password`, `mapillary-token`, `gemini-api-key`.
- Environment: `DB_*`, `AGENT_PROVIDER=gemini`, `GEMINI_MODEL=gemini-3.5-flash-lite`, `MCP_SERVER_URL`, `TWIN_URL`, `EXTRA_CORS_ORIGINS`.
- The agent runs inside the frontend container, so the Gemini key is attached to the frontend service.
- Services scale to zero, so the first request after idle takes 10 to 30 seconds.

## CI (`.github/workflows/deploy.yml`)
- Every push or pull request to `main`: byte-compile `src` and `tests`, then build the frontend image (no push). The backend image is not built in CI because it needs `data/`.
- Deploy job (builds and deploys the frontend to Cloud Run) is off until the repository variable `DEPLOY_ENABLED` is set to `true`.
- Sign-in uses Workload Identity Federation (pool `github-pool`, provider `github-provider`, service account `github-actions-deployer`); no key file is used. Repository secrets: `GCP_PROJECT_ID`, `GCP_SERVICE_ACCOUNT`, `GCP_WIF_PROVIDER`.
- The pytest suite needs a live server and local data, so it is not run in CI.

## Load test
`tests/test_load.py` sends 20 rounds of `/health` plus `get_climate_risk`. Set `BACKEND_URL` first. Last cloud result: 0 errors, average 0.31 s, p95 0.35 s (local average 0.11 s).

## Known limits
Cold starts; `classify_imagery` covers Velachery only; flood classes are elevation-based estimates; temperature and drought are not integrated; Gemini free-tier rate limits; walkability varies with the street images Mapillary returns.

## Changes made to existing files (originals in `backup/`)
- `src/phase4_agent/mcp_server.py`: CORS origins extendable via `EXTRA_CORS_ORIGINS`; `HOST`/`PORT` environment variables for the `__main__` block.
- `.gitignore`: allow `models/saved/site_scorer_model.pkl`; key and secret patterns; `neon_conn.txt`.
