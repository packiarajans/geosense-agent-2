# Cloud secrets checklist (names only -- never commit values)

| Variable | Where used | Used for | Stored in |
|---|---|---|---|
| GEMINI_API_KEY | frontend | LangGraph agent (AGENT_PROVIDER=gemini) | Secret Manager `gemini-api-key` |
| ANTHROPIC_API_KEY | frontend (optional) | Claude instead of Gemini (AGENT_PROVIDER=anthropic) | not deployed |
| DB_PASSWORD | backend | Neon PostGIS login | Secret Manager `neon-db-password` |
| DB_HOST / DB_PORT / DB_NAME / DB_USER / PGSSLMODE | backend | Neon connection (plain env vars, pooler host) | Cloud Run env vars |
| MAPILLARY_TOKEN | backend | street-view imagery | Secret Manager `mapillary-token` |
| CESIUM_ION_TOKEN, MAPILLARY_TOKEN (VITE_*) | 3D twin build | terrain / imagery (frontend/digital_twin/.env, baked at build) | local .env, gitignored |
| MCP_SERVER_URL | frontend | live backend URL | Cloud Run env var |
| TWIN_URL | frontend | live 3D twin URL | Cloud Run env var |
| EXTRA_CORS_ORIGINS | backend | allowed browser origins (the twin URL) | Cloud Run env var |

## GitHub Actions (Settings > Secrets and variables > Actions)

Keyless login with Workload Identity Federation (service-account keys are blocked by org policy, and not needed).

| Name | Kind | Value |
|---|---|---|
| GCP_PROJECT_ID | secret | the Google Cloud project id |
| GCP_SERVICE_ACCOUNT | secret | `github-actions-deployer@<project>.iam.gserviceaccount.com` |
| GCP_WIF_PROVIDER | secret | `projects/<number>/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| DEPLOY_ENABLED | variable | leave unset (CI only); `true` makes pushes deploy the frontend |
