# Define the directories to create
$dirs = @(
    "data",
    "data\raw",
    "data\processed",
    "data\shapefiles",
    "data\climate",
    "notebooks",
    "src",
    "src\phase1_ml",
    "src\phase2_dl",
    "src\phase3_lidar",
    "src\phase4_agent",
    "models",
    "models\saved",
    "tests",
    "docs",
    "outputs",
    "outputs\maps",
    "outputs\reports"
)

# Loop through and create each directory if it doesn't exist
foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) {
        New-Item -Path $dir -ItemType Directory | Out-Null
        Write-Host "Created: $dir" -ForegroundColor Green
    } else {
        Write-Host "Already exists: $dir" -ForegroundColor Yellow
    }
}

Write-Host "`n✅ Full directory structure created successfully!" -ForegroundColor Cyan
