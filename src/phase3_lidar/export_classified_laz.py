# export_classified_laz.py
# Purpose: Write the full ground / vegetation / building classification back into a LAZ file
#          using standard ASPRS LAS class codes, so QGIS, CloudCompare, PDAL, ArcGIS etc. can
#          filter and style by class. (pdal_pipeline.py only writes ground = 2 / unclassified = 1;
#          classify_points() in lidar_features.py otherwise keeps building/vegetation in memory.)
#
# Usage (run after pdal_pipeline.py):
#   python src/phase3_lidar/export_classified_laz.py
#   python src/phase3_lidar/export_classified_laz.py --laz data/lidar/processed/nyc_fidi_classified.laz
#
# Output: data/lidar/processed/<name>_classified_full.laz   (input file is never modified)
#
# Class mapping (ASPRS LAS 1.4):
#   classify_points() label 0 (ground/low) -> keeps the SMRF class (2 ground, 1 unclassified)
#   label 1 (vegetation)                   -> 3 low veg (HAG < 2 m), 4 medium (2-5 m), 5 high (>= 5 m)
#   label 2 (building)                     -> 6 building
import argparse
from pathlib import Path

import laspy
import numpy as np

from lidar_features import classify_points, load_classified_cloud

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAZ = PROJECT_ROOT / 'data' / 'lidar' / 'processed' / 'study_area_classified.laz'

MEDIUM_VEG_M = 2.0   # vegetation HAG at/above this = medium vegetation (class 4)
HIGH_VEG_M = 5.0     # vegetation HAG at/above this = high vegetation (class 5)

CLASS_NAMES = {1: 'unclassified', 2: 'ground', 3: 'low vegetation',
               4: 'medium vegetation', 5: 'high vegetation', 6: 'building'}


def asprs_classes(labels, hag, original):
    """Map classify_points() labels (0/1/2) + HAG to ASPRS class codes."""
    cls = np.asarray(original, dtype='uint8').copy()
    veg = labels == 1
    cls[veg] = 3
    cls[veg & (hag >= MEDIUM_VEG_M)] = 4
    cls[veg & (hag >= HIGH_VEG_M)] = 5
    cls[labels == 2] = 6
    return cls


def export(input_laz, output_laz):
    x, y, z, hag, num_returns, return_num = load_classified_cloud(input_laz)
    labels = classify_points(hag, num_returns, return_num)

    las = laspy.read(str(input_laz))
    if len(las.points) != len(labels):
        raise SystemExit('Point count mismatch between reads - aborting.')
    las.classification = asprs_classes(labels, hag, las.classification)

    Path(output_laz).parent.mkdir(parents=True, exist_ok=True)
    las.write(str(output_laz))

    counts = np.bincount(np.asarray(las.classification), minlength=7)
    total = len(labels)
    print(f'{total:,} points written to {output_laz}')
    for code, name in CLASS_NAMES.items():
        if counts[code]:
            print(f'  class {code} {name:<18} {counts[code]:>12,}  ({100 * counts[code] / total:.1f}%)')


def main():
    ap = argparse.ArgumentParser(description='Write ground/vegetation/building ASPRS classes into a LAZ.')
    ap.add_argument('--laz', default=str(DEFAULT_LAZ), help='classified LAZ from pdal_pipeline.py')
    ap.add_argument('--out', default=None,
                    help='output .laz (default: <same folder>/<name>_classified_full.laz)')
    args = ap.parse_args()
    laz = Path(args.laz)
    laz = laz if laz.is_absolute() else PROJECT_ROOT / laz
    if not laz.exists():
        raise SystemExit(f'LAZ not found: {laz}')
    name = laz.stem.replace('_classified', '')
    out = Path(args.out) if args.out else laz.parent / f'{name}_classified_full.laz'
    out = out if out.is_absolute() else PROJECT_ROOT / out
    if out.resolve() == laz.resolve():
        raise SystemExit('Output would overwrite the input - choose a different --out.')
    export(laz, out)


if __name__ == '__main__':
    main()
