$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$envs = @("--env-file","..\.env","--env-file","..\frontend\digital_twin\.env","-f","docker-compose.yml")
docker compose @envs down
Write-Host "Stopped. Database data kept (pgdata volume)."
