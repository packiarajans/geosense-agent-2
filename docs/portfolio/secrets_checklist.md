# Cloud secrets checklist (names only -- never commit values)

Confirm each variable name against your local `.env` before deploying.

| Variable | Service | Used for | Where it comes from |
|---|---|---|---|
| ANTHROPIC_API_KEY | frontend | LangGraph agent reasoning | console.anthropic.com |
| AGENT_MODEL | frontend | model id (default claude-sonnet-4-6) | optional |
| DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD | backend | PostGIS connection | your hosted PostGIS / Cloud SQL |
| MAPILLARY_TOKEN | backend | street-view imagery | mapillary.com/developer |
| CESIUM_ION_TOKEN | 3D twin build | terrain / imagery (in frontend/digital_twin/.env) | cesium.com/ion |
| MCP_SERVER_URL | frontend | live backend URL | Cloud Run service URL |
| TWIN_URL | frontend | live 3D twin URL | twin hosting URL |
| EXTRA_CORS_ORIGINS | backend | allowed browser origins | frontend + twin URLs |
| GCP_SA_KEY, GCP_PROJECT_ID | GitHub Actions | CI/CD deploy | GCP service account (delete local key.json after use) |
