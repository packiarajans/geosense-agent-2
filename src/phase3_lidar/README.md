# Phase 3 – Skyscraper LiDAR Runs (Manhattan & Hong Kong)

Singapore was ruled out: SLA's national LiDAR is not open data (OneMap 3D is view-only).
Manhattan (USGS, streamed) and Hong Kong (CEDD, open data) are the high-rise alternatives.

All commands run from the project root:

```
cd F:\GeoSense_Agent
conda activate geosense2
mkdir outputs\point_clouds -Force
```

The complete from-scratch command sequence is in `PHASE3_RUNBOOK.md` in this folder.
Use `--urban` on pdal_pipeline.py for every city-centre area (wide ground filter).

---

## A. Manhattan (USGS 3DEP – no download needed)

The pipeline streams only the bbox you ask for. Keep boxes small (<1 km²) at first:
Manhattan LiDAR is very dense.

### A1. Lower Manhattan / Financial District
```
python .\src\phase3_lidar\pdal_pipeline.py --usgs --bbox -74.015 40.703 -74.005 40.712 --name nyc_fidi --urban
python .\src\phase3_lidar\sam_rooftop_extraction.py --laz data\lidar\processed\nyc_fidi_classified.laz
python .\src\phase3_lidar\lidar_features.py --laz data\lidar\processed\nyc_fidi_classified.laz --cell 50
```

### A2. Midtown
```
python .\src\phase3_lidar\pdal_pipeline.py --usgs --bbox -73.990 40.750 -73.978 40.760 --name nyc_midtown --urban
python .\src\phase3_lidar\sam_rooftop_extraction.py --laz data\lidar\processed\nyc_midtown_classified.laz
python .\src\phase3_lidar\lidar_features.py --laz data\lidar\processed\nyc_midtown_classified.laz --cell 50
```

### A3. Upper East Side (mid-rise, for comparison)
```
python .\src\phase3_lidar\pdal_pipeline.py --usgs --bbox -73.962 40.772 -73.955 40.778 --name manhattan_ues --urban
python .\src\phase3_lidar\sam_rooftop_extraction.py --laz data\lidar\processed\manhattan_ues_classified.laz
```

Output CRS is set automatically to UTM 18N (EPSG:32618).

---

## B. Hong Kong (CEDD territory-wide airborne LiDAR, 2010 & 2020)

### B1. Download (manual, one time)
1. Open the CEDD Spatial Data Portal: https://sdportal.cedd.gov.hk/#/en/
   (info: https://www.ginfo.cedd.gov.hk/geoopendata/eng/LIDAR.aspx ,
   https://data.gov.hk/en-data/dataset/hk-cedd-csu-lidar)
2. Read the Terms & Conditions, then order the 2020 LiDAR tiles for your area.
   Good high-rise areas: Central, Admiralty, Wan Chai, Tsim Sha Tsui, Mong Kok.
3. Save the LAS/LAZ tile(s) into `F:\GeoSense_Agent\data\lidar\raw\`, e.g. `hk_central.laz`

### B2. Check the file's coordinate system
```
pdal info --metadata data\lidar\raw\hk_central.laz
```
- If it is in a metre-based grid (e.g. HK1980 Grid, EPSG:2326) -> no `--epsg` needed.
- If it is in lat/lon degrees -> add `--epsg 32650` (UTM 50N) in B3.

### B3. Process
```
python .\src\phase3_lidar\pdal_pipeline.py --input data\lidar\raw\hk_central.laz --name hk_central --urban
python .\src\phase3_lidar\sam_rooftop_extraction.py --laz data\lidar\processed\hk_central_classified.laz
python .\src\phase3_lidar\lidar_features.py --laz data\lidar\processed\hk_central_classified.laz --cell 50
```
Tiles can be large. If processing is slow, crop first:
```
pdal translate data\lidar\raw\hk_central.laz data\lidar\raw\hk_central_small.laz crop --filters.crop.bounds="([xmin, xmax], [ymin, ymax])"
```
(bounds in the file's own coordinates)

Optional: annotated HK LiDAR for training labels – https://zenodo.org/records/13311916

---

## C. Viewing results

| Output | Where | Open with |
|---|---|---|
| Classified cloud | `data\lidar\processed\<name>_classified.laz` | CloudCompare |
| Full-class cloud (ASPRS 2/3/4/5/6) | `data\lidar\processed\<name>_classified_full.laz` | QGIS / CloudCompare |
| Height map (nDSM) | `data\lidar\processed\<name>_ndsm.tif` | QGIS |
| Rooftops + volume | `data\lidar\processed\<name>_rooftops.gpkg` / `.csv` | QGIS |
| Grid metrics | `data\lidar\processed\<name>_grid_50m.gpkg` / `.csv` | QGIS |
| Colour PLY | `outputs\point_clouds\...ply` | CloudCompare / MeshLab |

`visualize_cloud.py --laz <file>` writes `outputs\point_clouds\<name>_colored.ply`.
`export_classified_laz.py --laz <file>` writes `<name>_classified_full.laz` with ground (2), low/medium/high
vegetation (3/4/5) and building (6) stored in the LAS Classification field.

`footprint_building_labels.py --laz <name>_classified_full.laz` (NYC; needs internet on first run) downloads
official NYC building footprints for the cloud extent, relabels points inside footprints (HAG >= 2.5 m) as
building – fixing glass towers labelled high vegetation – and writes `<name>_classified_bldg.laz` plus
`<name>_footprint_check.gpkg/.csv` (LiDAR roof height vs footprint height per building).
Other cities: `--footprints <file.gpkg> --height-field <col> --height-units m`. `--strict` also turns
building points outside every footprint back into vegetation.

`build_digital_twin.py --laz <name>_classified_bldg.laz` (or `_classified_full.laz`) builds the GeoSense
digital twin in `outputs\digital_twin\<name>\`: `<name>_twin.html` (3D viewer – stepped LiDAR building blocks,
tree columns, 50 m analysis grid, site profiles; click for details) plus `_buildings/_trees/_grid.gpkg` for
QGIS 3D / ArcGIS Pro / Cesium ion. With footprints run first, buildings also show BIN, official height and the
height-check status ("Colour by footprint height check").

---

## D. Skyscraper issues – status after fine-tuning (2026-09-29)

1. Big roofs classified as ground -> FIXED: `--urban` (SMRF window 60 m).
2. Noise points (-250 m HAG) -> FIXED: provider noise classes dropped, filters.elm, HAG kept in [-2, 600] m.
3. Glass towers counted as trees -> FIXED: cells > 35 m are buildings unless extremely rough.
4. Fake slope in flat cities -> FIXED: 5 m ground DEM + spike removal (65 m window).
5. Merged rooftops -> FIXED: SAM segments split where heights jump >= 3 m.
6. Forest passed as rooftops -> FIXED: LiDAR building fraction >= 0.6 required (use --laz).
7. Tall-building volume: sum of per-pixel heights; overhangs are still not modelled.
8. Point density: city LiDAR can be 10–50 M points/km²; start with small boxes.

## E. Fine-tuning done (2026-09-29) / still open

Done: rooftop split + capture check, forest filter, walkability (Vistas option, panorama split,
rescaled score with pedestrians/crosswalks), faster visualize_cloud.py with auto folder,
5 m slope DEM, low-coverage warning, noise + urban ground filter, skyscraper rule.

Still open:
- Calibrate walkability weights against places with known walkability
- Impervious surface needs imagery or LiDAR intensity (currently building cover only)

## F. Backups (2026-09-29)
All original scripts are in `F:\GeoSense_Agent\backup\scripts\`:
- pdal_pipeline_backup_20260929.py, pdal_pipeline_old_backup.py – original pdal_pipeline.py
- pdal_pipeline_backup_20260929_v2.py – version before the USGS dataset-selection fix
- streetview_semantic_backup_20260929.py – original streetview_semantic.py
- lidar_features_backup_20260929.py – original lidar_features.py
- lidar_analyzer_backup_20260929.py – original lidar_analyzer.py
- unified_site_profile_backup_20260929.py – original unified_site_profile.py
- mapillary_anycity_download_backup_20260929.py – earlier any-city version (had a stray first line)
- mapillary_download_newyork.py – superseded by mapillary_anycity_download.py
Doc backups: `F:\GeoSense_Agent\backup\docs\`
To restore one: `copy backup\scripts\<name>_backup_20260929.py src\phase3_lidar\<name>.py`
- *_backup_20260929_final.py – every script as it was before the full fine-tuning pass
  (pdal_pipeline, lidar_features, lidar_analyzer, unified_site_profile, sam_rooftop_extraction,
  visualize_cloud, streetview_semantic, mapillary_download, test_phase3_modules)
