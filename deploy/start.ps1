$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$envs = @("--env-file","..\.env","--env-file","..\frontend\digital_twin\.env","-f","docker-compose.yml")
docker compose @envs up -d
docker compose @envs ps
Write-Host "Frontend http://localhost:8502  Backend http://localhost:8010/docs  Twin http://localhost:5174"
