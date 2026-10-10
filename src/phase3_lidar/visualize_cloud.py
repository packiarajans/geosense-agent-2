# visualize_cloud.py
# Purpose: Colour-code the classified point cloud and save it as a viewable PLY file
#
# Usage:
#   python src/phase3_lidar/visualize_cloud.py
#   python src/phase3_lidar/visualize_cloud.py --laz data/lidar/processed/nyc_fidi_classified.laz
# Output: outputs/point_clouds/<name>_colored.ply  (open in CloudCompare / MeshLab)
# Colours: ground = brown, vegetation = green, building = grey
import argparse
from pathlib import Path

import numpy as np
import open3d as o3d

from lidar_features import classify_points, load_classified_cloud

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAZ = PROJECT_ROOT / 'data' / 'lidar' / 'processed' / 'study_area_classified.laz'
PLY_DIR = PROJECT_ROOT / 'outputs' / 'point_clouds'

# row index = label: 0 ground (brown), 1 vegetation (green), 2 building (grey)
COLOR_TABLE = np.array([[0.55, 0.35, 0.15], [0.10, 0.60, 0.10], [0.60, 0.60, 0.60]])


def save_colored_ply(input_laz, output_ply):
    x, y, z, hag, num_returns, return_num = load_classified_cloud(input_laz)
    labels = classify_points(hag, num_returns, return_num)
    points = np.column_stack((x, y, z))
    colors = COLOR_TABLE[labels]                     # vectorised lookup (was a per-point loop)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    pcd.colors = o3d.utility.Vector3dVector(colors)
    Path(output_ply).parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(output_ply), pcd):   # returns False on failure
        raise SystemExit(f'Open3D could not write {output_ply}')
    counts = np.bincount(labels, minlength=3)
    print(f'{len(labels):,} points: ground {counts[0]:,} | vegetation {counts[1]:,} | building {counts[2]:,}')
    print(f'Saved colour-coded point cloud to {output_ply}')


def main():
    ap = argparse.ArgumentParser(description='Colour-coded PLY from a classified LAZ.')
    ap.add_argument('--laz', default=str(DEFAULT_LAZ))
    ap.add_argument('--out', default=None,
                    help='output .ply (default: outputs/point_clouds/<name>_colored.ply)')
    args = ap.parse_args()
    laz = Path(args.laz)
    laz = laz if laz.is_absolute() else PROJECT_ROOT / laz
    if not laz.exists():
        raise SystemExit(f'LAZ not found: {laz}')
    name = laz.stem.replace('_classified', '')
    out = Path(args.out) if args.out else PLY_DIR / f'{name}_colored.ply'
    out = out if out.is_absolute() else PROJECT_ROOT / out
    save_colored_ply(laz, out)


if __name__ == '__main__':
    main()
