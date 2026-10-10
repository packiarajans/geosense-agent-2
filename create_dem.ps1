param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$City,

    [Parameter(Mandatory = $false)]
    [double]$BufferKm = 5
)

$ErrorActionPreference = "Stop"

# ============================================================
# GENERIC CITY DEM DOWNLOADER
#
# Source  : Copernicus DEM GLO-30
# Resolution: ~30 m
# Output  : GeoTIFF
#
# Usage:
#
#   .\create_dem.ps1 Mumbai
#   .\create_dem.ps1 Chennai
#   .\create_dem.ps1 Delhi
#   .\create_dem.ps1 "New York"
#
# Optional buffer:
#
#   .\create_dem.ps1 Mumbai -BufferKm 10
#
# Requirements:
#   GDAL 3.x+
#   Internet connection
#
# AWS CLI NOT REQUIRED
# ============================================================


# ------------------------------------------------------------
# 1. Check GDAL
# ------------------------------------------------------------

Write-Host ""
Write-Host "============================================================"
Write-Host " GENERIC CITY DEM DOWNLOADER"
Write-Host "============================================================"
Write-Host ""

Write-Host "Checking GDAL..."

$gdal = Get-Command gdalinfo -ErrorAction SilentlyContinue

if (-not $gdal) {

    Write-Host ""
    Write-Host "ERROR: GDAL was not found in PATH." -ForegroundColor Red
    Write-Host ""
    Write-Host "Please run this script from:"
    Write-Host "QGIS OSGeo4W Shell"
    Write-Host "or add GDAL to your PATH."
    Write-Host ""

    exit 1
}

gdalinfo --version


# ------------------------------------------------------------
# 2. City name
# ------------------------------------------------------------

Write-Host ""
Write-Host "City:"
Write-Host $City

Write-Host ""
Write-Host "Buffer:"
Write-Host "$BufferKm km"


# ------------------------------------------------------------
# 3. Geocode city using Nominatim
# ------------------------------------------------------------

Write-Host ""
Write-Host "============================================================"
Write-Host " STEP 1 - GEOCODING CITY"
Write-Host "============================================================"

$encodedCity = [System.Uri]::EscapeDataString($City)

$url = "https://nominatim.openstreetmap.org/search" +
"?q=$encodedCity" +
"&format=json" +
"&limit=1" +
"&addressdetails=1"

$headers = @{
    "User-Agent" = "GeoSense-DEMScript/1.0"
}

Write-Host "Searching OpenStreetMap..."

try {

    $result = Invoke-RestMethod `
        -Uri $url `
        -Headers $headers `
        -Method Get

}
catch {

    Write-Host ""
    Write-Host "ERROR: Unable to contact Nominatim." -ForegroundColor Red
    Write-Host $_.Exception.Message
    exit 1
}


if ($result.Count -eq 0) {

    Write-Host ""
    Write-Host "ERROR: City was not found:" -ForegroundColor Red
    Write-Host $City
    exit 1
}


$place = $result[0]

Write-Host ""
Write-Host "Location found:"
Write-Host $place.display_name


# ------------------------------------------------------------
# 4. Extract bounding box
# ------------------------------------------------------------

#
# Nominatim boundingbox order:
#
# [south, north, west, east]
#

$south = [double]$place.boundingbox[0]
$north = [double]$place.boundingbox[1]
$west = [double]$place.boundingbox[2]
$east = [double]$place.boundingbox[3]

$centerLat = ($south + $north) / 2


Write-Host ""
Write-Host "Original bounding box:"
Write-Host "South = $south"
Write-Host "North = $north"
Write-Host "West  = $west"
Write-Host "East  = $east"


# ------------------------------------------------------------
# 5. Add buffer
# ------------------------------------------------------------

#
# Approximate conversion:
#
# 1 degree latitude ≈ 111.32 km
#
# Longitude depends on latitude.
#

$latBuffer = $BufferKm / 111.32

$lonBuffer = $BufferKm / (
    111.32 * [math]::Cos(
        $centerLat * [math]::PI / 180
    )
)

$south = $south - $latBuffer
$north = $north + $latBuffer
$west = $west - $lonBuffer
$east = $east + $lonBuffer


Write-Host ""
Write-Host "Buffered bounding box:"
Write-Host "South = $south"
Write-Host "North = $north"
Write-Host "West  = $west"
Write-Host "East  = $east"


# ------------------------------------------------------------
# 6. Determine required 1° x 1° DEM tiles
# ------------------------------------------------------------

Write-Host ""
Write-Host "============================================================"
Write-Host " STEP 2 - FINDING DEM TILES"
Write-Host "============================================================"


function Format-LatTile {
    param(
        [int]$Lat
    )

    if ($Lat -ge 0) {
        return "N{0:D2}" -f $Lat
    }
    else {
        return "S{0:D2}" -f ([math]::Abs($Lat))
    }
}


function Format-LonTile {
    param(
        [int]$Lon
    )

    if ($Lon -ge 0) {
        return "E{0:D3}" -f $Lon
    }
    else {
        return "W{0:D3}" -f ([math]::Abs($Lon))
    }
}


$minLatTile = [math]::Floor($south)
$maxLatTile = [math]::Floor($north)

$minLonTile = [math]::Floor($west)
$maxLonTile = [math]::Floor($east)


$tiles = @()


for ($lat = $minLatTile; $lat -le $maxLatTile; $lat++) {

    for ($lon = $minLonTile; $lon -le $maxLonTile; $lon++) {

        $latName = Format-LatTile $lat
        $lonName = Format-LonTile $lon

        $tileName = "${latName}_00_${lonName}_00"

        $tiles += $tileName
    }
}


Write-Host ""
Write-Host "Required DEM tiles:"

foreach ($tile in $tiles) {
    Write-Host "  $tile"
}


# ------------------------------------------------------------
# 7. Create working directory
# ------------------------------------------------------------

$safeCity = $City -replace '[^a-zA-Z0-9_-]', '_'

$work = Join-Path `
(Get-Location) `
    "${safeCity}_DEM_Work"

New-Item `
    -ItemType Directory `
    -Force `
    -Path $work | Out-Null


# ------------------------------------------------------------
# 8. Download Copernicus DEM tiles
# ------------------------------------------------------------

Write-Host ""
Write-Host "============================================================"
Write-Host " STEP 3 - DOWNLOADING COPERNICUS GLO-30"
Write-Host "============================================================"


foreach ($tile in $tiles) {

    $tileFolder = "Copernicus_DSM_COG_10_${tile}_DEM"

    $fileName = "${tileFolder}.tif"

    $url =
    "https://copernicus-dem-30m.s3.amazonaws.com/" +
    "$tileFolder/" +
    "$fileName"

    $outputFile = Join-Path `
        $work `
        $fileName


    if (Test-Path $outputFile) {

        Write-Host ""
        Write-Host "Already exists:"
        Write-Host $fileName

        continue
    }


    Write-Host ""
    Write-Host "Downloading:"
    Write-Host $tile

    Write-Host $url


    try {

        Invoke-WebRequest `
            -Uri $url `
            -OutFile $outputFile `
            -UseBasicParsing

    }
    catch {

        Write-Host ""
        Write-Host "ERROR downloading tile:" -ForegroundColor Red
        Write-Host $tile
        Write-Host $_.Exception.Message

        Write-Host ""
        Write-Host "URL:"
        Write-Host $url

        exit 1
    }


    if (!(Test-Path $outputFile)) {

        Write-Host ""
        Write-Host "ERROR: Download failed." -ForegroundColor Red

        exit 1
    }


    Write-Host "Downloaded successfully." -ForegroundColor Green
}


# ------------------------------------------------------------
# 9. Find downloaded DEMs
# ------------------------------------------------------------

Write-Host ""
Write-Host "============================================================"
Write-Host " STEP 4 - VERIFYING DEM TILES"
Write-Host "============================================================"


$demFiles = Get-ChildItem `
    -Path $work `
    -Filter "*.tif"


if ($demFiles.Count -eq 0) {

    Write-Host ""
    Write-Host "ERROR: No DEM files found." -ForegroundColor Red

    exit 1
}


foreach ($file in $demFiles) {

    Write-Host $file.FullName
}


# ------------------------------------------------------------
# 10. Build VRT mosaic
# ------------------------------------------------------------

Write-Host ""
Write-Host "============================================================"
Write-Host " STEP 5 - BUILDING DEM MOSAIC"
Write-Host "============================================================"


$vrt = Join-Path `
    $work `
    "${safeCity}_GLO30.vrt"


$fileList = Join-Path `
    $work `
    "dem_files.txt"


$demFiles.FullName | Set-Content $fileList


gdalbuildvrt `
    $vrt `
    -input_file_list $fileList


if (!(Test-Path $vrt)) {

    Write-Host ""
    Write-Host "ERROR: VRT creation failed." -ForegroundColor Red

    exit 1
}


# ------------------------------------------------------------
# 11. Clip DEM to city bounding box
# ------------------------------------------------------------

Write-Host ""
Write-Host "============================================================"
Write-Host " STEP 6 - CREATING CITY DEM"
Write-Host "============================================================"


$output = Join-Path `
(Get-Location) `
    "${safeCity}_DEM.tif"


gdalwarp `
    -of GTiff `
    -te $west $south $east $north `
    -te_srs EPSG:4326 `
    -r bilinear `
    -co COMPRESS=DEFLATE `
    -co TILED=YES `
    -co BIGTIFF=IF_SAFER `
    $vrt `
    $output


if (!(Test-Path $output)) {

    Write-Host ""
    Write-Host "ERROR: DEM creation failed." -ForegroundColor Red

    exit 1
}


# ------------------------------------------------------------
# 12. Display DEM information
# ------------------------------------------------------------

Write-Host ""
Write-Host "============================================================"
Write-Host " DEM CREATED SUCCESSFULLY"
Write-Host "============================================================"

Write-Host ""
Write-Host "City:"
Write-Host $City

Write-Host ""
Write-Host "Output:"
Write-Host $output

Write-Host ""
Write-Host "Bounding Box:"
Write-Host "$west, $south, $east, $north"

Write-Host ""
Write-Host "DEM Information:"

gdalinfo $output |
Select-String `
    "Size is|Origin|Pixel Size|Coordinate System|Corner Coordinates"


Write-Host ""
Write-Host "============================================================"
Write-Host " DONE"
Write-Host "============================================================"