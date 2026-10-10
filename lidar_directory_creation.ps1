# Define the directories to create
$dirs = @(
    "data\lidar",
    "data\lidar\raw",
    "data\lidar\processed",
    "data\streetview",
    "data\streetview\images",
    "data\streetview\results",
    "src\phase3_lidar",
    "models\saved\sam",
    "outputs\point_clouds"
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

Write-Host "`n✅ Directory structure created successfully!" -ForegroundColor Cyan
