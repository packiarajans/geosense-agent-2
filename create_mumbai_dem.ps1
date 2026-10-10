$ErrorActionPreference = "Stop"

# ============================================================
# Mumbai DEM - Copernicus GLO-30
# AOI: 72.75E–73.10E, 18.85N–19.35N
# Requires: GDAL 3.12+ and PowerShell
# AWS CLI is NOT required
# ============================================================

$work = Join-Path $PWD "Mumbai_DEM_Work"
New-Item -ItemType Directory -Force $work | Out-Null

Write-Host "============================================="
Write-Host " Mumbai DEM - Copernicus GLO-30"
Write-Host "============================================="

Write-Host "`nChecking GDAL..."
gdalinfo --version

# ------------------------------------------------------------
# Copernicus GLO-30 tile URLs
# ------------------------------------------------------------

$tiles = @(
    "N18_00_E072_00",
    "N18_00_E073_00",
    "N19_00_E072_00",
    "N19_00_E073_00"
)

foreach ($tile in $tiles) {

    $url = "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_${tile}_DEM/Copernicus_DSM_COG_10_${tile}_DEM.tif"

    $outfile = Join-Path $work "${tile}.tif"

    if (Test-Path $outfile) {
        Write-Host "`nAlready downloaded: $tile"
        continue
    }

    Write-Host "`nDownloading $tile ..."
    Write-Host $url

    Invoke-WebRequest `
        -Uri $url `
        -OutFile $outfile `
        -UseBasicParsing

    if (!(Test-Path $outfile)) {
        throw "Download failed: $tile"
    }

    Write-Host "Downloaded successfully: $outfile"
}

# ------------------------------------------------------------
# Verify downloaded files
# ------------------------------------------------------------

Write-Host "`n============================================="
Write-Host " Verifying DEM tiles"
Write-Host "============================================="

$files = Get-ChildItem $work -Filter "*.tif"

if ($files.Count -eq 0) {
    throw "No DEM GeoTIFFs were downloaded."
}

foreach ($file in $files) {
    Write-Host $file.FullName
}

# ------------------------------------------------------------
# Create VRT mosaic
# ------------------------------------------------------------

Write-Host "`n============================================="
Write-Host " Building DEM mosaic"
Write-Host "============================================="

$vrt = Join-Path $work "Mumbai_Copernicus_GLO30.vrt"

gdalbuildvrt `
    $vrt `
    $files.FullName

if (!(Test-Path $vrt)) {
    throw "VRT creation failed."
}

# ------------------------------------------------------------
# Clip Mumbai
# ------------------------------------------------------------

Write-Host "`n============================================="
Write-Host " Clipping Mumbai DEM"
Write-Host "============================================="

$output = Join-Path $PWD "Mumbai_DEM.tif"

gdalwarp `
    -of GTiff `
    -te 72.75 18.85 73.10 19.35 `
    -te_srs EPSG:4326 `
    -r bilinear `
    -co COMPRESS=DEFLATE `
    -co TILED=YES `
    -co BIGTIFF=IF_SAFER `
    $vrt `
    $output

if (!(Test-Path $output)) {
    throw "Mumbai DEM creation failed."
}

# ------------------------------------------------------------
# Display result
# ------------------------------------------------------------

Write-Host "`n============================================="
Write-Host " SUCCESS!"
Write-Host "============================================="
Write-Host ""
Write-Host "Mumbai DEM:"
Write-Host $output
Write-Host ""

Write-Host "DEM information:"
gdalinfo $output | Select-String `
    "Size is|Coordinate System|Origin|Pixel Size|Corner Coordinates"

Write-Host ""
Write-Host "============================================="
Write-Host " Mumbai_DEM.tif created successfully"
Write-Host "============================================="