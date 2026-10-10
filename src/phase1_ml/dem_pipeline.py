import os
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text
from dotenv import load_dotenv

try:
    from src.phase1_ml.db_connection import get_engine
except ModuleNotFoundError:
    from db_connection import get_engine

load_dotenv()  # read DB_* / PG_BIN from the project .env

LOCAL_TZ = ZoneInfo("Asia/Kolkata")  # IST


def now_ist():
    """Timezone-aware current timestamp in IST."""
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Derive AOI name from the DEM file path
# ---------------------------------------------------------------------------
def get_aoi_name_from_path(dem_path: str) -> str:
    """
    "F:/Cities/chennai/dem/chennai_dem.tif" -> "chennai"
    Takes the filename, drops the extension, keeps the part before the first '_',
    and lowercases it (PostgreSQL folds unquoted identifiers to lowercase, so we
    keep everything lowercase to avoid case-sensitivity surprises downstream).
    """
    filename = os.path.splitext(os.path.basename(dem_path.strip()))[0]
    return filename.split("_")[0].lower()


def get_schema_name(aoi_name: str) -> str:
    """Schema name for an AOI (already lowercased by get_aoi_name_from_path)."""
    return aoi_name.lower()


# ---------------------------------------------------------------------------
# Load DEM into PostGIS
# ---------------------------------------------------------------------------
def reproject_to_4326(dem_path, target_srid=4326):
    """Reproject the DEM to EPSG:4326 with gdalwarp; return the new file path."""
    base, ext = os.path.splitext(dem_path)
    out_path = f"{base}_4326{ext}"
    cmd = [
        "gdalwarp", "-overwrite",
        "-t_srs", f"EPSG:{target_srid}",
        "-r", "bilinear",          # bilinear suits continuous elevation
        dem_path, out_path,
    ]
    warp = subprocess.run(cmd, capture_output=True, text=True)
    if warp.returncode != 0:
        print("gdalwarp reprojection failed")
        print(warp.stderr)
        return None
    print(f"Reprojected to EPSG:{target_srid} -> {os.path.basename(out_path)}")
    return out_path


def load_dem_to_postgis(
    dem_path,
    db_name=os.getenv("DB_NAME", "geosense_db"),
    user=os.getenv("DB_USER", "postgres"),
    password=os.getenv("DB_PASSWORD", "postgres"),
    port=os.getenv("DB_PORT", "5432"),
    host=os.getenv("DB_HOST", "localhost"),
    pg_bin=os.getenv("PG_BIN", r"C:\Program Files\PostgreSQL\18\bin"),
    reproject=True,
    aoi_override=None,
):
    # aoi_override lets the orchestrator force the AOI/schema to the chosen city
    # regardless of the DEM filename; otherwise derive it from the filename.
    aoi_name = aoi_override.lower() if aoi_override else get_aoi_name_from_path(dem_path)
    schema_name = get_schema_name(aoi_name)          # "chennai"
    table_name = f"{schema_name}_dem"                # "chennai_dem" (lowercase)

    env = {**os.environ, "PGPASSWORD": password}
    raster2pgsql = os.path.join(pg_bin, "raster2pgsql.exe")
    psql = os.path.join(pg_bin, "psql.exe")

    if not os.path.isfile(dem_path):
        raise FileNotFoundError(f"DEM not found: {dem_path}")
    if not os.path.isfile(raster2pgsql):
        raise FileNotFoundError(f"raster2pgsql.exe not found: {raster2pgsql}")
    if not os.path.isfile(psql):
        raise FileNotFoundError(f"psql.exe not found: {psql}")

    load_path = dem_path
    if reproject:
        load_path = reproject_to_4326(dem_path, target_srid=4326)
        if load_path is None:
            return None

    psql_base = [
        psql, "-h", host, "-p", port, "-U", user, "-d", db_name,
        "-v", "ON_ERROR_STOP=1",
    ]

    # Make sure PostGIS raster support and the AOI schema exist. This is
    # idempotent, so re-running is safe. postgis_raster is what lets the
    # raster type / raster2pgsql output load at all.
    setup_sql = (
        "CREATE EXTENSION IF NOT EXISTS postgis; "
        "CREATE EXTENSION IF NOT EXISTS postgis_raster; "
        f"CREATE SCHEMA IF NOT EXISTS {schema_name};"
    )
    setup_res = subprocess.run(
        psql_base + ["-c", setup_sql],
        capture_output=True, text=True, env=env,
    )
    if setup_res.returncode != 0:
        print("Could not enable PostGIS raster / create schema")
        print(setup_res.stderr)
        return None
    print(f"PostGIS raster ready; schema ready: {schema_name}")

    # raster2pgsql -> SQL, then pipe into psql
    raster_cmd = [
        raster2pgsql, "-d", "-s", "4326", "-I", "-C", "-M",
        load_path, f"{schema_name}.{table_name}",
    ]
    raster = subprocess.run(raster_cmd, capture_output=True, text=True)
    if raster.returncode != 0:
        print("raster2pgsql failed")
        print(raster.stderr)
        return None

    result = subprocess.run(
        psql_base, input=raster.stdout,
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        print("Error loading DEM")
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        return None

    print(f"DEM loaded into PostGIS as {schema_name}.{table_name}")
    return aoi_name


# ---------------------------------------------------------------------------
# Build flood-zone polygons from the loaded DEM
# ---------------------------------------------------------------------------
def create_floodzones(engine, aoi_name, mode="relative"):
    """Build flood-risk zones from the DEM (terciles by default)."""
    schema = get_schema_name(aoi_name)
    dem_table = f"{schema}.{schema}_dem"
    flood_table = f"{schema}.{schema}_floodzones"
    flood_table_bare = f"{schema}_floodzones"

    if mode == "relative":
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT (pvc).value AS value
                FROM (
                    SELECT ST_Quantile(ST_Union(rast), ARRAY[0.3333, 0.6667]) AS pvc
                    FROM {dem_table}
                ) t
            """)).fetchall()
        qs = sorted(float(r[0]) for r in rows)
        if len(qs) < 2:
            raise RuntimeError(f"Could not compute terciles from {dem_table} "
                               f"(got {qs}). Check the DEM has valid data.")
        q33, q67 = qs[0], qs[1]
        reclass_expr = f"[-10000-{q33}):1, [{q33}-{q67}):2, [{q67}-100000]:3"
        print(f"Relative flood bands (terciles): high < {q33:.1f} m <= medium "
              f"< {q67:.1f} m <= low")
    else:
        reclass_expr = "[0-10):1, [10-20):2, [20-9999]:3"
        print("Absolute flood bands: high 0-10 m, medium 10-20 m, low 20+ m")

    with engine.begin() as conn:
        conn.execute(
            text(f"""
                DROP TABLE IF EXISTS {flood_table};
                CREATE TABLE {flood_table} AS
                SELECT (dp).geom AS geometry, (dp).val AS risk_code
                FROM (
                    SELECT ST_DumpAsPolygons(rast) AS dp
                    FROM (
                        SELECT ST_Reclass(rast, 1, :reclass, '8BUI', 0) AS rast
                        FROM {dem_table}
                    ) reclassed
                ) dumped;
            """),
            {"reclass": reclass_expr},
        )
        conn.execute(text(f"""
            UPDATE {flood_table}
            SET geometry = ST_SetSRID(geometry, 4326)
            WHERE ST_SRID(geometry) <> 4326;
        """))
        conn.execute(text(f"ALTER TABLE {flood_table} ADD COLUMN risk_level VARCHAR;"))
        conn.execute(text(f"""
            UPDATE {flood_table}
            SET risk_level = CASE risk_code::int
                WHEN 1 THEN 'high'
                WHEN 2 THEN 'medium'
                WHEN 3 THEN 'low'
                ELSE 'unknown'
            END;
        """))
        conn.execute(text(f"""
            CREATE INDEX IF NOT EXISTS {flood_table_bare}_geom_idx
            ON {flood_table} USING GIST (geometry);
        """))
        conn.execute(text(f"ALTER TABLE {flood_table} ADD COLUMN created_at TIMESTAMPTZ;"))
        conn.execute(text(f"UPDATE {flood_table} SET created_at = :ts;"),
                     {"ts": now_ist()})

    print(f"Flood zone polygons created as {flood_table}")
    return flood_table


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser(
        description="Load a DEM into PostGIS and derive flood zones.")
    _ap.add_argument("--dem", required=True,
                     help="Path to the DEM GeoTIFF; AOI name is the filename's "
                          "first token (e.g. chennai_dem.tif -> chennai).")
    _ap.add_argument("--mode", choices=["relative", "absolute"], default="relative",
                     help="Flood-zone banding (default: relative terciles).")
    _ap.add_argument("--city", default=None,
                     help="Force the AOI/schema name (else taken from filename).")
    _args = _ap.parse_args()

    aoi_name = load_dem_to_postgis(dem_path=_args.dem, aoi_override=_args.city)
    if aoi_name:
        engine = get_engine()
        create_floodzones(engine, aoi_name, mode=_args.mode)
    else:
        print("Stopping: DEM load did not complete.")
