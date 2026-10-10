# GeoSense Agent 2.0 – Phase 3 Runbook

Follows the sequence of **GeoSense_Phase3_Implementation_Manual.docx** exactly.
Every command runs from the project root in PowerShell:

```
cd F:\GeoSense_Agent
conda activate geosense2
```

| # | Manual section | Script | Week |
|---|---|---|---|
| 1 | D.3 | src/phase3_lidar/mapillary_download.py | Data collection |
| 2 | E.2.1 | src/phase3_lidar/pdal_pipeline.py | Week 1 |
| 3 | E.3.1 | src/phase3_lidar/lidar_features.py | Week 2 |
| 4 | E.3.2 | src/phase3_lidar/visualize_cloud.py | Week 2 |
| 5 | E.3.3 | src/phase3_lidar/sam_rooftop_extraction.py | Week 2 |
| 6 | E.4.1 | src/phase3_lidar/streetview_semantic.py | Week 3 |
| 7 | E.5.1 | src/phase3_lidar/lidar_analyzer.py | Week 4 |
| 8 | E.5.2 | src/phase3_lidar/unified_site_profile.py | Week 4 |
| 9 | G.1 | tests/test_phase3_modules.py | Week 4 |

---

## Setup (manual Parts B and C)

```
conda install -c conda-forge pdal python-pdal -y
pip install shapely requests python-dotenv "laspy[lazrs]" open3d rasterio geopandas pyproj opencv-python torch segment-anything transformers pillow pytest
mkdir data\lidar\raw, data\lidar\processed, data\streetview\images, outputs\point_clouds, outputs\reports, models\saved\sam -Force
Invoke-WebRequest https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth -OutFile models\saved\sam\sam_vit_b_01ec64.pth
```
`.env` must contain `MAPILLARY_TOKEN=MLY|...` (manual C.3).
LiDAR tile from OpenTopography saved as `data\lidar\raw\study_area.laz` (manual D.2).

---

## 1. D.3 – Download street imagery from Mapillary

```
python src/phase3_lidar/mapillary_download.py
```
Success check: `Found N images` then `Saved: ...` lines; .jpg files in `data\streetview\images\`.
Other area: `python src/phase3_lidar/mapillary_download.py --bbox MIN_LON MIN_LAT MAX_LON MAX_LAT --limit 20`

## 2. E.2.1 – PDAL preprocessing pipeline (Week 1)

```
python src/phase3_lidar/pdal_pipeline.py
```
Success check: `Processed N points. Output saved to ...\data\lidar\processed\study_area_classified.laz`

## 3. E.3.1 – Building height and canopy cover (Week 2)

```
python src/phase3_lidar/lidar_features.py
```
Success check: `Computed metrics for N grid cells of 50 m`; grid saved as
`data\lidar\processed\study_area_grid_50m.gpkg` / `.csv`.

## 4. E.3.2 – Visualise the classified point cloud (Week 2)

```
python src/phase3_lidar/visualize_cloud.py
```
Success check: `Saved colour-coded point cloud to ...\outputs\point_clouds\study_area_colored.ply`
(open in Open3D / CloudCompare: ground brown, trees green, buildings grey).

Optional – save the full classification into a LAZ (ASPRS codes, for QGIS / CloudCompare / ArcGIS):
```
python src/phase3_lidar/export_classified_laz.py
python src/phase3_lidar/export_classified_laz.py --laz data/lidar/processed/nyc_fidi_classified.laz
```
Success check: `... points written to ...\data\lidar\processed\study_area_classified_full.laz`
followed by per-class counts (2 ground, 3/4/5 low/medium/high vegetation, 6 building).

Optional (NYC high-rises) – fix building labels with official footprints (internet needed the first time):
```
python src/phase3_lidar/footprint_building_labels.py --laz data/lidar/processed/nyc_fidi_classified_full.laz
```
Success check: `Relabelled ... points to building (6)`, a before/after class table, `Height check: {...}`
and `Saved ...\nyc_fidi_footprint_check.gpkg (+ .csv)`. Open the GPKG in QGIS and style by `status`.

Optional – GeoSense digital twin (3D city model + analysis layers):
```
python src/phase3_lidar/build_digital_twin.py --laz data/lidar/processed/nyc_fidi_classified_bldg.laz
```
Success check: building / tree / grid counts and `Saved ...\outputs\digital_twin\nyc_fidi\nyc_fidi_twin.html`.
Open the HTML in Chrome/Edge (internet needed for the basemap and the deck.gl library).

## 5. E.3.3 – SAM rooftop detection + LiDAR height matching (Week 2)

```
python src/phase3_lidar/sam_rooftop_extraction.py
```
Success check: rooftop table + total building volume; `study_area_rooftops.gpkg` / `.csv` /
`_summary.json` in `data\lidar\processed\`. Slow on CPU (manual warning).

## 6. E.4.1 – Street semantic segmentation with SegFormer (Week 3)

```
python src/phase3_lidar/streetview_semantic.py
```
Success check: latitude, longitude, images_analyzed, scene_composition percentages and
walkability_score. Other locations:
```
python src/phase3_lidar/streetview_semantic.py --lat 13.0418 --lon 80.2341
python src/phase3_lidar/streetview_semantic.py --lat 40.7075 --lon -74.010 --max-images 20
```

## 7. E.5.1 – lidar_analyzer.py (Week 4)

```
python src/phase3_lidar/lidar_analyzer.py
```
Success check: avg_building_height_m, canopy_cover_pct, impervious_surface_pct, slope_risk_score
(plus coverage, p95 height, slope). A specific bbox:
`python src/phase3_lidar/lidar_analyzer.py --bbox 42.950 -75.608 42.955 -75.600`

## 8. E.5.2 – Unified enhanced site profile (Week 4)

```
python src/phase3_lidar/unified_site_profile.py
```
Success check: JSON profile printed and saved to `outputs\reports\enhanced_site_profile_example.json`.
Other location: `python src/phase3_lidar/unified_site_profile.py --lat 13.0418 --lon 80.2341`
(saved as `outputs\reports\site_profile_<lat>_<lon>.json`).

## 9. G.1 – Unit tests (Week 4)

```
python -m pytest tests/ -v
```
Success check: `tests/test_phase3_modules.py::test_lidar_analyzer_returns_dict PASSED` …
(the manual's 4 tests first, then the extra checks). Offline: `$env:GEOSENSE_OFFLINE=1` before running.

---

## Optional extensions (after the manual's 9 steps)

Skyscraper areas streamed from USGS – same scripts, with `--urban` and `--laz`:
```
python src/phase3_lidar/pdal_pipeline.py --usgs --bbox -74.015 40.703 -74.005 40.712 --name nyc_fidi --urban
python src/phase3_lidar/lidar_features.py --laz data/lidar/processed/nyc_fidi_classified.laz --cell 25
python src/phase3_lidar/visualize_cloud.py --laz data/lidar/processed/nyc_fidi_classified.laz
python src/phase3_lidar/sam_rooftop_extraction.py --laz data/lidar/processed/nyc_fidi_classified.laz
python src/phase3_lidar/lidar_analyzer.py --laz data/lidar/processed/nyc_fidi_classified.laz
python src/phase3_lidar/unified_site_profile.py --lat 40.7075 --lon -74.010 --radius 0.004 --laz data/lidar/processed/nyc_fidi_classified.laz
```
Whole-city image download: `python src/phase3_lidar/mapillary_anycity_download.py --city "Chennai" --country "India" --limit 200 --all-images`
Better street model: add `--model vistas` to streetview_semantic.py (~800 MB download once).
Hong Kong: see README.md section B.

## Where outputs go

| Output | Path |
|---|---|
| Street images | `data\streetview\images\` |
| Classified point cloud | `data\lidar\processed\study_area_classified.laz` |
| Grid metrics | `data\lidar\processed\study_area_grid_50m.gpkg` / `.csv` |
| Colour point cloud | `outputs\point_clouds\study_area_colored.ply` |
| nDSM + rooftops | `data\lidar\processed\study_area_ndsm.tif`, `study_area_rooftops.gpkg` / `.csv` / `_summary.json` |
| Site profile | `outputs\reports\enhanced_site_profile_example.json` |

Backups of every script version: `F:\GeoSense_Agent\backup\scripts\`
