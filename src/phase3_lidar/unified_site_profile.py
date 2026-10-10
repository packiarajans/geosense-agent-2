# unified_site_profile.py
# Purpose: Merge LiDAR metrics + street-level semantics into one unified site profile
#
# Usage:
#   python src/phase3_lidar/unified_site_profile.py --lat 42.953 --lon -75.603            # study_area (rural NY)
#   python src/phase3_lidar/unified_site_profile.py --lat 40.7075 --lon -74.010 --laz data/lidar/processed/nyc_fidi_classified.laz
#   python src/phase3_lidar/unified_site_profile.py --lat 13.0418 --lon 80.2341          # Chennai: street view only
#
# If there is no LiDAR (or no street imagery) for the location, those fields are null and the
# reason is listed in 'notes' — the profile is still produced from whatever data exists.
import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from lidar_analyzer import PROCESSED_CLOUD_PATH, analyze_bbox
from streetview_semantic import analyze_location

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = PROJECT_ROOT / 'outputs' / 'reports'
MIN_COVERAGE_PCT = 50   # below this, warn that LiDAR metrics only describe part of the bbox


@dataclass
class EnhancedSiteProfile:
    latitude: float
    longitude: float
    # LiDAR
    lidar_data_coverage_pct: Optional[float] = None
    avg_building_height_m: Optional[float] = None
    p95_building_height_m: Optional[float] = None
    canopy_cover_pct: Optional[float] = None
    building_cover_pct: Optional[float] = None
    impervious_surface_pct: Optional[float] = None
    mean_ground_slope_deg: Optional[float] = None
    slope_risk_score: Optional[float] = None
    # Street view
    street_images_analyzed: int = 0
    scene_composition: dict = field(default_factory=dict)
    walkability_score: Optional[float] = None
    notes: list = field(default_factory=list)


def build_profile(lat, lon, bbox_radius_deg=0.005, laz_path=PROCESSED_CLOUD_PATH):
    p = EnhancedSiteProfile(latitude=lat, longitude=lon)

    # ---- LiDAR
    try:
        lidar = analyze_bbox(lat - bbox_radius_deg, lon - bbox_radius_deg,
                             lat + bbox_radius_deg, lon + bbox_radius_deg, laz_path=laz_path)
    except SystemExit as e:           # e.g. LAZ file missing / no CRS
        lidar = {'error': str(e)}
    if 'error' in lidar:
        extent = lidar.get('data_extent_latlon')
        p.notes.append(f"LiDAR: {lidar['error']}" + (f' (file covers {extent})' if extent else ''))
    else:
        p.lidar_data_coverage_pct = lidar.get('data_coverage_pct')
        p.avg_building_height_m = lidar.get('avg_building_height_m')
        p.p95_building_height_m = lidar.get('p95_building_height_m')
        p.canopy_cover_pct = lidar.get('canopy_cover_pct')
        p.building_cover_pct = lidar.get('building_cover_pct')
        p.impervious_surface_pct = lidar.get('impervious_surface_pct')
        p.mean_ground_slope_deg = lidar.get('mean_ground_slope_deg')
        p.slope_risk_score = lidar.get('slope_risk_score')
        cov = p.lidar_data_coverage_pct or 0
        if cov < MIN_COVERAGE_PCT:
            p.notes.append(f'LiDAR: only {cov:.0f}% of the bbox has data - metrics describe the '
                           f'covered part only (use a smaller --radius or a larger LiDAR area)')

    # ---- Street view
    try:
        street = analyze_location(lat, lon)
    except Exception as e:            # network / token / model problems shouldn't kill the profile
        street = {'error': f'{type(e).__name__}: {e}'}
    if 'error' in street:
        p.notes.append(f"Street view: {street['error']}")
    else:
        p.street_images_analyzed = street.get('images_analyzed', 0)
        p.scene_composition = street.get('scene_composition', {})
        p.walkability_score = street.get('walkability_score')
        if p.street_images_analyzed < 3:
            p.notes.append(f'Street view: only {p.street_images_analyzed} image(s) - low confidence')

    return asdict(p)


def main():
    ap = argparse.ArgumentParser(description='Unified LiDAR + street-view site profile.')
    ap.add_argument('--lat', type=float, default=42.953, help='default: centre of study_area (rural NY)')
    ap.add_argument('--lon', type=float, default=-75.603)
    ap.add_argument('--radius', type=float, default=0.005, help='LiDAR bbox half-size in degrees')
    ap.add_argument('--laz', default=str(PROCESSED_CLOUD_PATH), help='classified LAZ to analyse')
    ap.add_argument('--out', default=None, help='output JSON path (default: outputs/reports/...)')
    args = ap.parse_args()

    laz = Path(args.laz)
    if not laz.is_absolute():
        laz = PROJECT_ROOT / laz
    profile = build_profile(args.lat, args.lon, args.radius, laz)
    print(json.dumps(profile, indent=2))

    if args.out:
        out = Path(args.out)
        out = out if out.is_absolute() else PROJECT_ROOT / out
    elif (args.lat, args.lon) == (ap.get_default('lat'), ap.get_default('lon')):
        out = REPORT_DIR / 'enhanced_site_profile_example.json'   # manual E.5.2 file name
    else:
        tag = re.sub(r'[^0-9a-z]+', '_', f'{args.lat:.4f}_{args.lon:.4f}'.lower()).strip('_')
        out = REPORT_DIR / f'site_profile_{tag}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(profile, f, indent=2)
    print(f'Saved to {out}')


if __name__ == '__main__':
    main()
