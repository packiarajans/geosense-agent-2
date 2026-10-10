# prep_nyc_dem.py
# Purpose: Build a pipeline-ready Manhattan DEM from the NOAA/NYC 2017 topobathy hydro-enforced 1-ft tiles.
#   - mosaics the downloaded tiles (EPSG:6539, ft) -> EPSG:32618 (m) at --res metres (area-average)
#   - converts vertical feet (US survey) -> metres
#   - masks everything outside the Manhattan shoreline polygon (removes river bathymetry)
# Output: F:/Cities/newyork/dem/newyork_dem.tif   (AOI/schema name = "newyork")
# Run: python src/phase4_agent/prep_nyc_dem.py --res 30
import argparse, glob, numpy as np, rasterio, geopandas as gpd
from pathlib import Path
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling
from rasterio.features import geometry_mask

CITY = Path('F:/Cities/newyork')
FT = 1200 / 3937  # US survey foot in metres
NODATA = -9999.0

def main(res):
    b = gpd.read_file(CITY / 'boundary' / 'nyc_boroughs.geojson')
    man = b[b.boroname == 'Manhattan'].to_crs(32618)
    minx, miny, maxx, maxy = man.total_bounds
    minx, miny = np.floor(minx / res) * res, np.floor(miny / res) * res
    maxx, maxy = np.ceil(maxx / res) * res, np.ceil(maxy / res) * res
    W, H = int((maxx - minx) / res), int((maxy - miny) / res)
    tf = from_origin(minx, maxy, res, res)
    dst = np.full((H, W), NODATA, dtype='float32')
    tiles = sorted(t for t in glob.glob(str(CITY / 'dem' / 'raw' / 'be_NYC_*.tif')) if Path(t).stat().st_size > 1e6)
    print(f'{len(tiles)} tiles -> grid {W}x{H} @ {res} m')
    for t in tiles:
        tmp = np.full((H, W), NODATA, dtype='float32')
        with rasterio.open(t) as src:
            reproject(rasterio.band(src, 1), tmp, src_transform=src.transform, src_crs=src.crs,
                      src_nodata=src.nodata if src.nodata is not None else -3.4028230607370965e+38,
                      dst_transform=tf, dst_crs='EPSG:32618', dst_nodata=NODATA,
                      resampling=Resampling.average)
        fill = (tmp != NODATA) & (dst == NODATA)
        dst[fill] = tmp[fill]
        print(f'  {Path(t).name}: {int(fill.sum()):,} cells')
    valid = dst != NODATA
    dst[valid] = dst[valid] * FT                      # ft -> m
    inside = ~geometry_mask(man.geometry, out_shape=(H, W), transform=tf)
    dst[~inside] = NODATA
    v = dst[dst != NODATA]
    print(f'valid cells {v.size:,}  elev m: min {v.min():.2f}  p33 {np.percentile(v,33.33):.2f}  p67 {np.percentile(v,66.67):.2f}  max {v.max():.2f}')
    out = CITY / 'dem' / 'newyork_dem.tif'
    with rasterio.open(out, 'w', driver='GTiff', height=H, width=W, count=1, dtype='float32',
                       crs='EPSG:32618', transform=tf, nodata=NODATA, compress='deflate') as d:
        d.write(dst, 1)
    print('wrote', out)

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--res', type=float, default=30)
    main(ap.parse_args().res)
