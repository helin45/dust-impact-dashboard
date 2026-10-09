"""
FastAPI backend for the Dust Impact Dashboard.

Serves the static frontend and a small JSON API backed by DuckDB queries
against the Parquet files produced by build_dataset.py.

Run:
    uvicorn app:app --reload
Then open http://localhost:8000
"""

from pathlib import Path
import csv
import io
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import duckdb
import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Optional -- GeoTIFF export and the WorldCover overlay are the only two
# features that need it (see their endpoints below). Guarded the same way as
# earthaccess elsewhere in this file, since rasterio's compiled
# GDAL binding has been observed to fail to load entirely on some locked-down
# Windows machines (e.g. an Application Control policy blocking its DLLs) --
# that shouldn't take down the rest of the dashboard.
try:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin
    RASTERIO_IMPORT_ERROR = None
except ImportError as _exc:
    rasterio = Resampling = MemoryFile = from_origin = None
    RASTERIO_IMPORT_ERROR = str(_exc)

# Local-only fallback (see local_secrets.py) -- only fills in EARTHDATA_* if
# they aren't already set in the real environment, so an OS-level/shell
# override always wins.
try:
    import local_secrets
    os.environ.setdefault("EARTHDATA_USERNAME", local_secrets.EARTHDATA_USERNAME)
    os.environ.setdefault("EARTHDATA_PASSWORD", local_secrets.EARTHDATA_PASSWORD)
except ImportError:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent

# Loads EARTHDATA_USERNAME/EARTHDATA_PASSWORD (MERRA-2/AOD) and
# EUMETSAT_CONSUMER_KEY/EUMETSAT_CONSUMER_SECRET (SEVIRI) from a local .env
# file next to this script, if one exists -- see .env.example. Without this,
# those credentials would need re-exporting in whatever shell launches
# uvicorn every single time; a .env file survives restarts. override=False:
# a real environment variable the shell already set still wins, .env is
# only a fallback for what isn't already set.
from dotenv import load_dotenv
load_dotenv(SCRIPT_DIR / ".env", override=False)

DATA_DIR = SCRIPT_DIR / "data"
CSV_DIR = Path(os.environ.get("DUST_CSV_DIR") or SCRIPT_DIR.parent / "CSVFiles").expanduser()  # DUST_CSV_DIR overrides the default repo-root CSVFiles/
TIMESTEPS_PARQUET = DATA_DIR / "timesteps.parquet"
FLIGHTS_PARQUET = DATA_DIR / "flights.parquet"
# Written by build_dataset.py -- every flight excluded from timesteps.parquet
# and why. Read back as-is by /api/anomalous_flights below, not re-derived.
ANOMALOUS_FLIGHTS_PATH = DATA_DIR / "anomalous_flights.csv"
# Precomputed by hysplit_tools/flight_backtrack.py (offline, on the HYSPLIT machine) --
# this endpoint only ever reads a finished density_grid.json, never computes one.
DUST_FILES_DIR = SCRIPT_DIR.parent / "hysplit_tools"
DUST_SOURCE_DIR = DUST_FILES_DIR / "hysplit_results" / "flights"

sys.path.insert(0, str(DUST_FILES_DIR))
import density_utils  # noqa: E402 -- needs DUST_FILES_DIR on sys.path first
import flight_backtrack  # noqa: E402 -- reused only for flight_dir_for()'s strategy-namespacing logic below
import run_all_flights  # noqa: E402 -- reused only for list_flight_ids()/already_computed()/LOG_PATH below

try:
    # Same coastline data the offline matplotlib tool draws with (see
    # density_utils.plot_coastlines) -- read once at startup, filtered
    # per-request by /api/coastlines below instead of re-parsing the 1.4MB
    # arlmap file on every call. HYSPLIT's arlmap is coastlines only -- no
    # political borders, hence BORDER_SEGMENTS as a separate source below.
    COASTLINE_SEGMENTS = density_utils.read_arlmap()
except FileNotFoundError:
    COASTLINE_SEGMENTS = []  # no HYSPLIT install on this machine -- degrade gracefully
# Precomputed by hysplit_tools/build_susceptibility_grid.py -- same offline-only contract.
SUSCEPTIBILITY_PATH = DUST_SOURCE_DIR.parent / "susceptibility_grid.json"

# Natural Earth's free, no-login "admin-0 boundary lines (land)" dataset --
# country borders only, deliberately excluding coastlines (arlmap already
# covers those) so the two overlays don't duplicate lines. Downloaded once
# and cached to disk on first use; a machine with no network access at that
# moment just gets no border overlay (same degrade-gracefully contract as
# a missing arlmap), not a startup crash.
NE_BORDERS_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_boundary_lines_land.geojson"
NE_BORDERS_CACHE = DATA_DIR / "ne_50m_admin_0_boundary_lines_land.geojson"


def _load_border_segments():
    if not NE_BORDERS_CACHE.exists():
        try:
            r = requests.get(NE_BORDERS_URL, timeout=30)
            r.raise_for_status()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            NE_BORDERS_CACHE.write_bytes(r.content)
        except requests.RequestException:
            return []
    try:
        geojson = json.loads(NE_BORDERS_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    segments = []
    for feature in geojson.get("features", []):
        geom = feature.get("geometry", {}) or {}
        lines = geom.get("coordinates", [])
        if geom.get("type") == "LineString":
            lines = [lines]
        for line in lines:
            # GeoJSON coordinates are [lon, lat]; flip to the (lat, lon)
            # tuple convention COASTLINE_SEGMENTS/_clip_segments_to_bbox use.
            segments.append([(lat, lon) for lon, lat in line])
    return segments


BORDER_SEGMENTS = _load_border_segments()

# flight_backtrack.py can be launched straight from the dashboard instead of
# manually from a terminal on the HYSPLIT machine (see
# /api/flight/{flight_id}/compute below). Only one at a time, system-wide --
# these are heavy (real HYSPLIT runs + met-data downloads), not something to
# ever run two of concurrently on one machine.
COMPUTE_JOBS = {"density": "flight_backtrack.py"}
CURRENT_JOB = None  # {"flight_id", "job", "process", "log_path", "started"} or None

PHASES = ["CLIMB", "CRUISE", "DESCENT", "LEVEL DESCENT", "LEVEL FLIGHT"]
MAX_ELAPSED_S = 4 * 3600  # ignore rare stragglers beyond 4h for the time-binned charts
TIME_BUCKET_S = 30
ALT_BUCKET_FT = 1000
HISTOGRAM_SAMPLE_CAP = 2_000_000
DEFAULT_TOP_N = 15
MAX_TOP_N = 100
# Rough, commonly cited threshold for wind-driven dust uplift over loose
# desert soils, not a precise physical constant. See merra2_utils.surface_wind_at_point.
UPLIFT_THRESHOLD_MS = 8.0
SINGLE_FLIGHT_COLS = ["phase", "Flight_Time_Seconds", "Lat", "Lon", "Alt_ft", "CoreDustIngested_g", "TAS_kn", "vertical_rate"]

# Static ICAO -> country/city/name lookup for the airports this dataset
# actually references (there are 11). Not a general-purpose airport database --
# extend this if new routes appear in future data.
AIRPORTS = {
    "OBBI": {"country": "Bahrain", "city": "Manama", "name": "Bahrain Intl", "lat": 26.2708, "lon": 50.6336},
    "OEDF": {"country": "Saudi Arabia", "city": "Dammam", "name": "King Fahd Intl", "lat": 26.4712, "lon": 49.7979},
    "OEJN": {"country": "Saudi Arabia", "city": "Jeddah", "name": "King Abdulaziz Intl", "lat": 21.6796, "lon": 39.1565},
    "OERK": {"country": "Saudi Arabia", "city": "Riyadh", "name": "King Khalid Intl", "lat": 24.9576, "lon": 46.6988},
    "OKKK": {"country": "Kuwait", "city": "Kuwait City", "name": "Kuwait Intl", "lat": 29.2267, "lon": 47.9689},
    "OMAA": {"country": "United Arab Emirates", "city": "Abu Dhabi", "name": "Abu Dhabi Intl", "lat": 24.4330, "lon": 54.6511},
    "OMDB": {"country": "United Arab Emirates", "city": "Dubai", "name": "Dubai Intl", "lat": 25.2532, "lon": 55.3657},
    "OMDW": {"country": "United Arab Emirates", "city": "Dubai", "name": "Al Maktoum Intl", "lat": 24.8967, "lon": 55.1614},
    "OMSJ": {"country": "United Arab Emirates", "city": "Sharjah", "name": "Sharjah Intl", "lat": 25.3286, "lon": 55.5172},
    "OOMS": {"country": "Oman", "city": "Muscat", "name": "Muscat Intl", "lat": 23.5933, "lon": 58.2844},
    "OTHH": {"country": "Qatar", "city": "Doha", "name": "Hamad Intl", "lat": 25.2609, "lon": 51.6138},
}

# ESA WorldCover 2021 (10m) discrete classification -- code -> (label, RGB).
# The product's own official legend (confirmed via its Google Earth Engine
# catalog entry, ESA_WorldCover_v200), not a hand-picked approximation --
# same "real legend, not an approximation" standard as the AOD colorbar
# elsewhere in this app. Shared with /api/worldcover_overlay below and
# mirrored in static/app.js (WORLDCOVER_LEGEND there) for the on-page swatch
# legend -- if this ever changes, update both.
WORLDCOVER_LEGEND = {
    10: ("Tree cover", (0, 100, 0)),
    20: ("Shrubland", (255, 187, 34)),
    30: ("Grassland", (255, 255, 76)),
    40: ("Cropland", (240, 150, 255)),
    50: ("Built-up", (250, 0, 0)),
    60: ("Bare / sparse vegetation", (180, 180, 180)),
    70: ("Snow and ice", (240, 240, 240)),
    80: ("Permanent water bodies", (0, 100, 200)),
    90: ("Herbaceous wetland", (0, 150, 160)),
    95: ("Mangroves", (0, 207, 117)),
    100: ("Moss and lichen", (250, 230, 160)),
}
# Same public, no-login S3 bucket and SW-corner 3deg tile naming
# build_susceptibility_grid.py's own _worldcover_tiles() uses.
WORLDCOVER_BASE_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
WORLDCOVER_TILE_DEG = 3

if not TIMESTEPS_PARQUET.exists() or not FLIGHTS_PARQUET.exists():
    raise SystemExit(
        f"Missing {TIMESTEPS_PARQUET} or {FLIGHTS_PARQUET}. "
        "Run `python build_dataset.py` first."
    )

con = duckdb.connect(database=":memory:")
con.execute("SET enable_progress_bar=false")
con.execute(f"CREATE VIEW flights AS SELECT * FROM read_parquet('{FLIGHTS_PARQUET.as_posix()}')")
con.execute(f"CREATE VIEW timesteps AS SELECT * FROM read_parquet('{TIMESTEPS_PARQUET.as_posix()}')")
db_lock = threading.Lock()

app = FastAPI(title="Dust Impact Dashboard API")


@app.middleware("http")
async def no_cache_headers(request, call_next):
    # This is a local, single-user dev tool whose static files change often --
    # never let the browser cache a stale copy of index.html/app.js/style.css.
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


class QueryFilters(BaseModel):
    aircraft_types: list[str]
    date_from: str
    date_to: str
    takeoff_start_min: int = 0
    takeoff_end_min: int = 1439
    phases: list[str]
    altitude_min: float | None = None
    altitude_max: float | None = None
    origin_icaos: list[str] | None = None
    destination_icaos: list[str] | None = None
    registrations: list[str] | None = None
    top_n: int = DEFAULT_TOP_N
    flights_limit: int = DEFAULT_TOP_N
    flights_order: str = "desc"


def empty_result():
    return {
        "n_flights": 0,
        "n_rows": 0,
        "total_dust_g": 0.0,
        "mean_dust_per_row": 0.0,
        "mean_dust_per_flight": 0.0,
        "pdf": {"overall": None},
        "dust_per_time": {"overall": {"t": [], "mean_dust": [], "n": []}, "by_phase": {}},
        "dust_per_time_in_phase": {"by_phase": {}},
        "cumulative_dust_per_time": {"t": [], "cumulative_mean_dust": []},
        "dust_per_altitude": {},
        "phase_boxplot": [],
        "dust_by_date": {"date": [], "total_dust": [], "mean_dust": [], "n_flights": []},
        "dust_by_route": [],
        "top_flights": [],
        "dust_by_hour": {"hour": [], "mean_dust": [], "total_dust": [], "n_flights": []},
    }


def make_histogram(values: np.ndarray, n_bins: int = 40):
    values = values[np.isfinite(values) & (values >= 0)]
    if values.size == 0:
        return None
    positive = values[values > 0]
    if positive.size < 2:
        return None
    lo = max(positive.min(), 1e-12)
    hi = np.percentile(positive, 99.5)
    if hi <= lo:
        hi = positive.max()
    if hi <= lo:
        return None
    edges = np.logspace(np.log10(lo), np.log10(hi), n_bins + 1)
    counts, edges = np.histogram(values, bins=edges, density=False)
    # Bins are equal-width in log10 space; report density per unit log10(value)
    # (density per unit *linear* value would blow up near the small end, since
    # those bins are extremely narrow in linear terms).
    log_bin_width = np.diff(np.log10(edges))
    density = counts / (values.size * log_bin_width)
    centers = np.sqrt(edges[:-1] * edges[1:])
    return {
        "bin_edges": edges.tolist(),
        "bin_centers": centers.tolist(),
        "density": density.tolist(),
        "counts": counts.tolist(),
        "n": int(values.size),
    }


@app.get("/api/filters")
def get_filters():
    with db_lock:
        aircraft_types = [r[0] for r in con.execute(
            "SELECT DISTINCT aircraft_type FROM flights ORDER BY aircraft_type"
        ).fetchall()]
        date_min, date_max = con.execute(
            "SELECT MIN(date), MAX(date) FROM flights"
        ).fetchone()
        n_flights = con.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
        registrations = [r[0] for r in con.execute(
            "SELECT DISTINCT registration FROM flights WHERE registration IS NOT NULL ORDER BY registration"
        ).fetchall()]
    return {
        "aircraft_types": aircraft_types,
        "date_min": str(date_min),
        "date_max": str(date_max),
        "phases": PHASES,
        "n_flights_total": n_flights,
        "registrations": registrations,
    }


@app.get("/api/airports")
def get_airports():
    with db_lock:
        rows = con.execute(
            """
            SELECT DISTINCT origin_icao AS icao FROM flights WHERE origin_icao IS NOT NULL
            UNION
            SELECT DISTINCT destination_icao AS icao FROM flights WHERE destination_icao IS NOT NULL
            """
        ).fetchall()
    codes = sorted(r[0] for r in rows)
    return [
        {"icao": code, **AIRPORTS.get(code, {"country": "Unknown", "city": code, "name": code, "lat": None, "lon": None})}
        for code in codes
    ]


@app.post("/api/query")
def query(filters: QueryFilters):
    actypes = [a for a in filters.aircraft_types if a]
    phases = [p for p in filters.phases if p in PHASES]
    if not actypes or not phases:
        return empty_result()
    if filters.registrations is not None and len(filters.registrations) == 0:
        return empty_result()
    top_n = max(1, min(filters.top_n, MAX_TOP_N))
    flights_limit = max(1, min(filters.flights_limit, MAX_TOP_N))
    flights_order_sql = "ASC" if filters.flights_order == "asc" else "DESC"

    actype_ph = ", ".join(["?"] * len(actypes))
    phase_ph = ", ".join(["?"] * len(phases))
    conditions = [
        f"fl.aircraft_type IN ({actype_ph})",
        "fl.date BETWEEN ? AND ?",
        "fl.takeoff_minutes BETWEEN ? AND ?",
        f"ts.phase IN ({phase_ph})",
    ]
    params = [
        *actypes,
        filters.date_from,
        filters.date_to,
        filters.takeoff_start_min,
        filters.takeoff_end_min,
        *phases,
    ]
    if filters.altitude_min is not None:
        conditions.append("ts.altitude_ft >= ?")
        params.append(filters.altitude_min)
    if filters.altitude_max is not None:
        conditions.append("ts.altitude_ft <= ?")
        params.append(filters.altitude_max)
    if filters.origin_icaos:
        codes = [c.upper() for c in filters.origin_icaos]
        conditions.append(f"fl.origin_icao IN ({', '.join(['?'] * len(codes))})")
        params.extend(codes)
    if filters.destination_icaos:
        codes = [c.upper() for c in filters.destination_icaos]
        conditions.append(f"fl.destination_icao IN ({', '.join(['?'] * len(codes))})")
        params.extend(codes)
    if filters.registrations:
        conditions.append(f"fl.registration IN ({', '.join(['?'] * len(filters.registrations))})")
        params.extend(filters.registrations)
    where_clause = " AND ".join(conditions)

    with db_lock:
        con.execute("DROP TABLE IF EXISTS tmp_filtered")
        con.execute(
            f"""
            CREATE TEMP TABLE tmp_filtered AS
            SELECT ts.flight_id, ts.phase, ts.elapsed_s, ts.altitude_ft, ts.dust_g, fl.date
            FROM timesteps ts
            JOIN flights fl USING (flight_id)
            WHERE {where_clause}
            """,
            params,
        )

        n_flights, n_rows, total_dust, mean_dust_row = con.execute(
            """
            SELECT COUNT(DISTINCT flight_id), COUNT(*),
                   COALESCE(SUM(dust_g), 0), COALESCE(AVG(dust_g), 0)
            FROM tmp_filtered
            """
        ).fetchone()

        if n_flights == 0:
            return empty_result()

        # --- PDF / histogram of dust ingested per timestep (overall + per phase) ---
        hist_df = con.execute(
            f"""
            SELECT phase, dust_g FROM tmp_filtered
            USING SAMPLE {HISTOGRAM_SAMPLE_CAP} ROWS
            """ if n_rows > HISTOGRAM_SAMPLE_CAP else
            "SELECT phase, dust_g FROM tmp_filtered"
        ).df()

        pdf_overall = make_histogram(hist_df["dust_g"].to_numpy())

        # --- dust ingested vs time already spent in the current phase segment ---
        # elapsed_s is a whole-flight clock (zeroed at the flight's first usable
        # row), not a per-phase one, so a phase entered partway through a flight
        # would otherwise start its line partway along the x-axis. This resets
        # the clock at the start of each contiguous run of the same phase
        # (a LAG-based run-length encode, standard DuckDB window-function idiom)
        # so every phase's line starts at time-in-phase = 0.
        phase_time_in_phase = con.execute(
            f"""
            WITH ordered AS (
                SELECT flight_id, phase, elapsed_s, dust_g,
                       LAG(phase) OVER (PARTITION BY flight_id ORDER BY elapsed_s) AS prev_phase
                FROM tmp_filtered
            ),
            segmented AS (
                SELECT flight_id, phase, elapsed_s, dust_g,
                       SUM(CASE WHEN prev_phase IS NULL OR phase != prev_phase THEN 1 ELSE 0 END)
                           OVER (PARTITION BY flight_id ORDER BY elapsed_s) AS seg_id
                FROM ordered
            ),
            timed AS (
                SELECT phase, dust_g,
                       elapsed_s - MIN(elapsed_s) OVER (PARTITION BY flight_id, seg_id) AS phase_elapsed_s
                FROM segmented
            )
            SELECT phase,
                   CAST(phase_elapsed_s / {TIME_BUCKET_S} AS INTEGER) * {TIME_BUCKET_S} AS bucket_s,
                   AVG(dust_g) AS mean_dust, COUNT(*) AS n
            FROM timed
            WHERE phase_elapsed_s BETWEEN 0 AND {MAX_ELAPSED_S}
            GROUP BY phase, bucket_s
            ORDER BY phase, bucket_s
            """
        ).df()

        # --- dust per elapsed-time bucket, overall ---
        overall_time = con.execute(
            f"""
            SELECT CAST(elapsed_s / {TIME_BUCKET_S} AS INTEGER) * {TIME_BUCKET_S} AS bucket_s,
                   AVG(dust_g) AS mean_dust, COUNT(*) AS n
            FROM tmp_filtered
            WHERE elapsed_s BETWEEN 0 AND {MAX_ELAPSED_S}
            GROUP BY bucket_s
            ORDER BY bucket_s
            """
        ).df()

        # --- dust per elapsed-time bucket, per phase ---
        phase_time = con.execute(
            f"""
            SELECT phase, CAST(elapsed_s / {TIME_BUCKET_S} AS INTEGER) * {TIME_BUCKET_S} AS bucket_s,
                   AVG(dust_g) AS mean_dust, COUNT(*) AS n
            FROM tmp_filtered
            WHERE elapsed_s BETWEEN 0 AND {MAX_ELAPSED_S}
            GROUP BY phase, bucket_s
            ORDER BY phase, bucket_s
            """
        ).df()

        # --- dust per altitude bucket, per phase ---
        alt_df = con.execute(
            f"""
            SELECT phase, CAST(altitude_ft / {ALT_BUCKET_FT} AS INTEGER) * {ALT_BUCKET_FT} AS alt_bucket,
                   AVG(dust_g) AS mean_dust, COUNT(*) AS n
            FROM tmp_filtered
            WHERE altitude_ft >= 0
            GROUP BY phase, alt_bucket
            ORDER BY alt_bucket
            """
        ).df()

        # --- dust per altitude bucket, overall (phases combined) ---
        alt_overall_df = con.execute(
            f"""
            SELECT CAST(altitude_ft / {ALT_BUCKET_FT} AS INTEGER) * {ALT_BUCKET_FT} AS alt_bucket,
                   AVG(dust_g) AS mean_dust, COUNT(*) AS n
            FROM tmp_filtered
            WHERE altitude_ft >= 0
            GROUP BY alt_bucket
            ORDER BY alt_bucket
            """
        ).df()

        # --- per-phase boxplot stats ---
        box_df = con.execute(
            """
            SELECT phase,
                   MIN(dust_g) AS min_v,
                   QUANTILE_CONT(dust_g, 0.25) AS q1,
                   QUANTILE_CONT(dust_g, 0.5) AS median,
                   QUANTILE_CONT(dust_g, 0.75) AS q3,
                   MAX(dust_g) AS max_v,
                   COUNT(*) AS n
            FROM tmp_filtered
            GROUP BY phase
            """
        ).df()

        # --- total/mean dust by date ---
        date_df = con.execute(
            """
            SELECT date, SUM(dust_g) AS total_dust, AVG(dust_g) AS mean_dust,
                   COUNT(DISTINCT flight_id) AS n_flights
            FROM tmp_filtered
            GROUP BY date
            ORDER BY date
            """
        ).df()

        # --- total/mean dust by route (origin -> destination) ---
        route_df = con.execute(
            f"""
            SELECT fl.origin_icao, fl.destination_icao,
                   SUM(t.dust_g) AS total_dust, AVG(t.dust_g) AS mean_dust,
                   COUNT(DISTINCT t.flight_id) AS n_flights
            FROM tmp_filtered t
            JOIN flights fl USING (flight_id)
            WHERE fl.origin_icao IS NOT NULL AND fl.destination_icao IS NOT NULL
            GROUP BY fl.origin_icao, fl.destination_icao
            ORDER BY total_dust DESC
            LIMIT {top_n}
            """
        ).df()

        # --- flight ranking by total dust (respecting the current phase filter) ---
        top_df = con.execute(
            f"""
            SELECT t.flight_id, fl.date, fl.takeoff_hhmm, fl.callsign, fl.registration,
                   fl.origin_icao, fl.destination_icao, fl.first_phase,
                   SUM(t.dust_g) AS total_dust, COUNT(*) AS n
            FROM tmp_filtered t
            JOIN flights fl USING (flight_id)
            GROUP BY t.flight_id, fl.date, fl.takeoff_hhmm, fl.callsign, fl.registration,
                     fl.origin_icao, fl.destination_icao, fl.first_phase
            ORDER BY total_dust {flights_order_sql}
            LIMIT {flights_limit}
            """
        ).df()

        # --- dust vs takeoff hour-of-day ---
        hour_df = con.execute(
            """
            SELECT CAST(fl.takeoff_minutes / 60 AS INTEGER) AS hour,
                   AVG(t.dust_g) AS mean_dust, SUM(t.dust_g) AS total_dust,
                   COUNT(DISTINCT t.flight_id) AS n_flights
            FROM tmp_filtered t
            JOIN flights fl USING (flight_id)
            GROUP BY hour
            ORDER BY hour
            """
        ).df()

    dust_per_time_by_phase = {}
    for ph in phases:
        sub = phase_time[phase_time["phase"] == ph]
        dust_per_time_by_phase[ph] = {
            "t": sub["bucket_s"].tolist(),
            "mean_dust": sub["mean_dust"].tolist(),
            "n": sub["n"].tolist(),
        }

    dust_per_time_in_phase_by_phase = {}
    for ph in phases:
        sub = phase_time_in_phase[phase_time_in_phase["phase"] == ph]
        dust_per_time_in_phase_by_phase[ph] = {
            "t": sub["bucket_s"].tolist(),
            "mean_dust": sub["mean_dust"].tolist(),
            "n": sub["n"].tolist(),
        }

    # Running sum of the per-bucket mean dust value -- approximates the average
    # flight's cumulative dust-ingested trajectory (each timestep already carries
    # an absolute mass, not a rate, so no extra time scaling is applied).
    cumulative = overall_time["mean_dust"].cumsum() if not overall_time.empty else overall_time["mean_dust"]

    dust_per_altitude = {
        "overall": {
            "alt": alt_overall_df["alt_bucket"].tolist(),
            "mean_dust": alt_overall_df["mean_dust"].tolist(),
            "n": alt_overall_df["n"].tolist(),
        },
    }
    for ph in phases:
        sub = alt_df[alt_df["phase"] == ph]
        dust_per_altitude[ph] = {
            "alt": sub["alt_bucket"].tolist(),
            "mean_dust": sub["mean_dust"].tolist(),
            "n": sub["n"].tolist(),
        }

    boxplot = []
    for _, row in box_df.iterrows():
        iqr = row["q3"] - row["q1"]
        boxplot.append({
            "phase": row["phase"],
            "min": max(row["min_v"], row["q1"] - 1.5 * iqr),
            "q1": row["q1"],
            "median": row["median"],
            "q3": row["q3"],
            "max": min(row["max_v"], row["q3"] + 1.5 * iqr),
            "n": int(row["n"]),
        })

    return {
        "n_flights": int(n_flights),
        "n_rows": int(n_rows),
        "total_dust_g": float(total_dust),
        "mean_dust_per_row": float(mean_dust_row),
        "mean_dust_per_flight": float(total_dust / n_flights),
        "pdf": {"overall": pdf_overall},
        "dust_per_time_in_phase": {"by_phase": dust_per_time_in_phase_by_phase},
        "dust_per_time": {
            "overall": {
                "t": overall_time["bucket_s"].tolist(),
                "mean_dust": overall_time["mean_dust"].tolist(),
                "n": overall_time["n"].tolist(),
            },
            "by_phase": dust_per_time_by_phase,
        },
        "cumulative_dust_per_time": {
            "t": overall_time["bucket_s"].tolist(),
            "cumulative_mean_dust": cumulative.tolist(),
        },
        "dust_per_altitude": dust_per_altitude,
        "phase_boxplot": boxplot,
        "dust_by_date": {
            "date": date_df["date"].astype(str).tolist(),
            "total_dust": date_df["total_dust"].tolist(),
            "mean_dust": date_df["mean_dust"].tolist(),
            "n_flights": date_df["n_flights"].tolist(),
        },
        "dust_by_route": [
            {
                "route": f"{row.origin_icao}→{row.destination_icao}",
                "origin": row.origin_icao,
                "destination": row.destination_icao,
                "total_dust": row.total_dust,
                "mean_dust": row.mean_dust,
                "n_flights": int(row.n_flights),
            }
            for row in route_df.itertuples()
        ],
        "top_flights": [
            {
                "flight_id": row.flight_id,
                "date": str(row.date)[:10],
                "takeoff_hhmm": row.takeoff_hhmm,
                "callsign": row.callsign,
                "registration": row.registration,
                "origin": row.origin_icao,
                "destination": row.destination_icao,
                "first_phase": row.first_phase,
                "total_dust": row.total_dust,
                "n": int(row.n),
            }
            for row in top_df.itertuples()
        ],
        "dust_by_hour": {
            "hour": hour_df["hour"].tolist(),
            "mean_dust": hour_df["mean_dust"].tolist(),
            "total_dust": hour_df["total_dust"].tolist(),
            "n_flights": hour_df["n_flights"].tolist(),
        },
    }


@app.get("/api/flights_on_date")
def flights_on_date(date: str, aircraft_types: str = ""):
    actypes = [a for a in aircraft_types.split(",") if a]
    with db_lock:
        if actypes:
            ph = ", ".join(["?"] * len(actypes))
            rows = con.execute(
                f"""
                SELECT flight_id, takeoff_hhmm, callsign, registration, aircraft_type,
                       origin_icao, destination_icao, n_rows, total_dust_g, first_phase
                FROM flights
                WHERE date = ? AND aircraft_type IN ({ph}) AND first_phase = 'CLIMB'
                ORDER BY takeoff_hhmm
                """,
                [date, *actypes],
            ).df()
        else:
            rows = con.execute(
                """
                SELECT flight_id, takeoff_hhmm, callsign, registration, aircraft_type,
                       origin_icao, destination_icao, n_rows, total_dust_g, first_phase
                FROM flights
                WHERE date = ? AND first_phase = 'CLIMB'
                ORDER BY takeoff_hhmm
                """,
                [date],
            ).df()
    # An all-NULL VARCHAR column (e.g. origin_icao on a date with only
    # A321 flights, which have no summary file) comes back from duckdb as
    # a float64 NaN column -- see _records_with_nan_as_none() below for why
    # that needs sweeping to None explicitly.
    records = _records_with_nan_as_none(rows)
    return records


@app.get("/api/route_flights")
def route_flights(origin: str, destination: str, aircraft_types: str = "",
                   date_from: str = "", date_to: str = ""):
    """
    Individual flights making up one route on the Routes view -- that view
    only ever shows route-level aggregates (total dust, flight count), so
    this answers "which specific flights" once you've clicked one, using
    the same aircraft-type/date-range filters currently set on that tab.
    Same first_phase = 'CLIMB' usability filter as every other flight-
    listing endpoint, and same NaN -> None cleanup as flights_on_date.
    """
    actypes = [a for a in aircraft_types.split(",") if a]
    conditions = ["first_phase = 'CLIMB'", "origin_icao = ?", "destination_icao = ?"]
    params = [origin, destination]
    if actypes:
        ph = ", ".join(["?"] * len(actypes))
        conditions.append(f"aircraft_type IN ({ph})")
        params.extend(actypes)
    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("date <= ?")
        params.append(date_to)
    with db_lock:
        rows = con.execute(
            f"""
            SELECT flight_id, date, takeoff_hhmm, callsign, registration, aircraft_type, total_dust_g
            FROM flights
            WHERE {' AND '.join(conditions)}
            ORDER BY total_dust_g DESC
            """,
            params,
        ).df()
    records = _records_with_nan_as_none(rows)
    for record in records:
        record["date"] = str(record["date"])[:10]
    return records


@app.get("/api/flight/{flight_id}")
def flight_detail(flight_id: str):
    with db_lock:
        meta = con.execute("SELECT * FROM flights WHERE flight_id = ?", [flight_id]).df()
    if meta.empty:
        raise HTTPException(status_code=404, detail="Unknown flight_id")
    meta = meta.iloc[0]

    csv_path = CSV_DIR / f"{flight_id}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="Raw CSV for this flight is missing")

    df = pd.read_csv(csv_path, usecols=SINGLE_FLIGHT_COLS)
    df = df[df["phase"].isin(PHASES)].sort_values("Flight_Time_Seconds").copy()
    if df.empty:
        elapsed = []
    else:
        elapsed = (df["Flight_Time_Seconds"] - df["Flight_Time_Seconds"].min()).tolist()

    return {
        "flight_id": flight_id,
        "date": str(meta["date"])[:10],
        "takeoff_hhmm": meta["takeoff_hhmm"],
        "callsign": meta["callsign"],
        "registration": meta["registration"],
        "aircraft_type": meta["aircraft_type"],
        "origin_icao": meta["origin_icao"],
        "destination_icao": meta["destination_icao"],
        "origin_iata": meta["origin_iata"],
        "destination_iata": meta["destination_iata"],
        "first_phase": meta["first_phase"],
        "total_dust_g": float(meta["total_dust_g"]),
        "n_rows": int(meta["n_rows"]),
        "trace": {
            "t": elapsed,
            "phase": df["phase"].tolist(),
            "lat": df["Lat"].tolist(),
            "lon": df["Lon"].tolist(),
            "altitude_ft": df["Alt_ft"].tolist(),
            "dust_g": df["CoreDustIngested_g"].tolist(),
            "tas_kn": df["TAS_kn"].tolist(),
            "vertical_rate": df["vertical_rate"].tolist(),
        },
    }


@app.get("/api/flight/{flight_id}/dust_source_density")
def flight_dust_source_density(flight_id: str, alt_min: float | None = None, alt_max: float | None = None,
                                strategy: str = flight_backtrack.STRATEGY_TOPN):
    """
    Serves the precomputed HYSPLIT backward-trajectory
    source density for one flight, if flight_backtrack.py has been run for
    it. Always 200 -- the frontend distinguishes "not computed yet" via the
    `computed` flag rather than an HTTP error, since that's an expected,
    common state, not a fault.

    `strategy` (topn/trigger/dense) selects which release-point strategy's
    results to serve -- each is computed and stored independently (see
    flight_backtrack.flight_dir_for()), since they represent genuinely
    different release-point sets, not variations to be pooled together.

    With alt_min/alt_max given, rebuilds the density grid from just the
    release points whose alt_ft falls in that band -- answering "where does
    the dust from this altitude range come from" -- rather than serving the
    fixed all-points grid. This re-KDEs combined_raw.csv (every run's raw
    per-cell concentration output, already computed) on the fly; no new
    HYSPLIT runs are needed since the underlying simulations already cover
    the whole flight.
    """
    if strategy not in flight_backtrack.STRATEGIES:
        raise HTTPException(400, f"unknown strategy {strategy!r}, expected one of {flight_backtrack.STRATEGIES}")
    flight_dir = Path(flight_backtrack.flight_dir_for(flight_id, strategy))
    path = flight_dir / "density_grid.json"
    if not path.exists():
        return {"computed": False, "flight_id": flight_id, "strategy": strategy}
    data = json.loads(path.read_text())

    if alt_min is None and alt_max is None:
        return {"computed": True, "strategy": strategy, **data}

    combined_path = flight_dir / "combined_raw.csv"
    if not combined_path.exists():
        return {"computed": True, "strategy": strategy, **data}  # density_grid.json exists but combined_raw.csv doesn't -- serve unfiltered rather than fail

    lo = alt_min if alt_min is not None else float("-inf")
    hi = alt_max if alt_max is not None else float("inf")
    band_points = [rp for rp in data["release_points"] if lo <= rp["alt_ft"] <= hi]

    combined = pd.read_csv(combined_path)
    combined = combined[(combined["alt_ft"] >= lo) & (combined["alt_ft"] <= hi)]
    conc_col = next((c for c in combined.columns if c.startswith("Dust")), None)
    weight = combined[conc_col] * combined["bin_total_dust_g"] if conc_col else pd.Series(dtype=float)
    mask = weight > 0
    if mask.sum() < 2:  # gaussian_kde needs enough points for a non-singular covariance
        return {
            "computed": True, "flight_id": flight_id, "strategy": strategy, "filtered": True,
            "alt_min": alt_min, "alt_max": alt_max, "insufficient_data": True,
            "n_points_used": len(band_points), "release_points": band_points,
        }
    combined, weight = combined[mask], weight[mask]
    grid_lon, grid_lat, density = density_utils.build_density_grid(
        combined["LAT"].to_numpy(), combined["LON"].to_numpy(), weight.to_numpy(),
    )
    return {
        "computed": True, "flight_id": flight_id, "strategy": strategy, "filtered": True,
        "alt_min": alt_min, "alt_max": alt_max,
        "runtime_hours": data.get("runtime_hours"),
        "n_points_used": len(band_points),
        "lon": grid_lon[0, :].tolist(), "lat": grid_lat[:, 0].tolist(),
        "density": density.tolist(), "release_points": band_points,
    }


@app.get("/api/flight/{flight_id}/dust_source_density/export")
def export_dust_source_density(flight_id: str, format: str = "csv",
                                strategy: str = flight_backtrack.STRATEGY_TOPN):
    """
    Downloadable export of a computed density grid, for actual GIS work
    instead of just a chart screenshot. `density` in density_grid.json is a
    (lat, lon)-shaped nested list -- lon/lat are each ascending 1D arrays of
    cell centers (see flight_backtrack.build_flight_density) -- so density[i][j]
    is the cell at (lat[i], lon[j]).

    `strategy` picks which computed result to export -- same selector as
    /dust_source_density, defaulting to topn.
    """
    if strategy not in flight_backtrack.STRATEGIES:
        raise HTTPException(400, f"unknown strategy {strategy!r}, expected one of {flight_backtrack.STRATEGIES}")
    path = Path(flight_backtrack.flight_dir_for(flight_id, strategy)) / "density_grid.json"
    if not path.exists():
        raise HTTPException(404, f"dust source density not yet computed for {flight_id!r} ({strategy})")
    if format not in ("csv", "geotiff"):
        raise HTTPException(400, f"format must be 'csv' or 'geotiff', got {format!r}")

    data = json.loads(path.read_text())
    lon = np.array(data["lon"])
    lat = np.array(data["lat"])
    density = np.array(data["density"])

    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["lon", "lat", "density"])
        lon_grid, lat_grid = np.meshgrid(lon, lat)
        writer.writerows(zip(lon_grid.ravel(), lat_grid.ravel(), density.ravel()))
        return Response(
            buf.getvalue(), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{flight_id}_dust_source_density.csv"'},
        )

    # geotiff -- cell size from consecutive-center spacing (grid is regular),
    # origin at the top-left CORNER (half a cell north/west of the first
    # center), row 0 = north per GeoTIFF convention, so density (row 0 =
    # south, matching lat's ascending order) needs flipping vertically.
    if rasterio is None:
        raise HTTPException(503, f"GeoTIFF export unavailable: rasterio failed to import on this machine ({RASTERIO_IMPORT_ERROR}). CSV export still works.")
    dlon = lon[1] - lon[0]
    dlat = lat[1] - lat[0]
    transform = from_origin(lon[0] - dlon / 2, lat[-1] + dlat / 2, dlon, dlat)
    arr = np.flipud(density).astype("float32")
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff", height=arr.shape[0], width=arr.shape[1],
            count=1, dtype="float32", crs="EPSG:4326", transform=transform,
        ) as dst:
            dst.write(arr, 1)
        tiff_bytes = memfile.read()
    return Response(
        tiff_bytes, media_type="image/tiff",
        headers={"Content-Disposition": f'attachment; filename="{flight_id}_dust_source_density.tif"'},
    )


@app.get("/api/flight/{flight_id}/merra2_validation")
def flight_merra2_validation(flight_id: str, strategy: str = flight_backtrack.STRATEGY_TOPN):
    """
    Validates this flight's HYSPLIT release points against NASA's MERRA-2
    reanalysis (see hysplit_tools/merra2_utils.py), two ways at once since both
    compare the same DUEXTTAU (dust extinction AOD) variable:

      - event corroboration: MERRA-2's own modeled dust loading at each
        release point's real lat/lon/hour, independent of HYSPLIT/CoreDust.
      - source climatology: how that value compares to the location's own
        multi-year climatological distribution for that calendar month.

    Requires a computed HYSPLIT density grid for this flight+strategy (its
    release points, with real start_utc timestamps, are what gets looked
    up) and a configured NASA Earthdata login. Same always-200,
    `computed`/`configured`-flag contract as the other dust-source
    endpoints, distinguishing three states: not yet computed (run HYSPLIT
    first), not configured (no Earthdata credentials), or computed with
    per-point results -- a point whose own MERRA-2 lookup fails carries an
    "error" field instead of values, so one bad point doesn't blank the
    whole response.
    """
    flight_dir = Path(flight_backtrack.flight_dir_for(flight_id, strategy))
    density_path = flight_dir / "density_grid.json"
    if not density_path.exists():
        return {"computed": False, "flight_id": flight_id, "strategy": strategy}

    try:
        import merra2_utils
    except ImportError:
        return {
            "computed": True, "configured": False, "flight_id": flight_id,
            "reason": "merra2_utils dependencies (earthaccess, xarray) aren't installed on this machine.",
        }
    if not merra2_utils.credentials_configured():
        return {
            "computed": True, "configured": False, "flight_id": flight_id,
            "reason": "NASA Earthdata credentials aren't configured.",
            "setup": "Create a free account at https://urs.earthdata.nasa.gov/users/new, then set "
                     "EARTHDATA_USERNAME and EARTHDATA_PASSWORD before starting the dashboard.",
        }

    release_points = json.loads(density_path.read_text())["release_points"]
    results = []
    for rp in release_points:
        entry = {
            "point_label": rp["point_label"], "lat": rp["lat"], "lon": rp["lon"],
            "dust_ingested_g": rp["dust_ingested_g"],
        }
        try:
            when_utc = datetime.fromisoformat(rp["start_utc"])
            if when_utc.tzinfo is None:
                when_utc = when_utc.replace(tzinfo=timezone.utc)
            entry.update(merra2_utils.validate_point(rp["lat"], rp["lon"], when_utc))
        except Exception as e:
            entry["error"] = str(e)
        results.append(entry)

    return {"computed": True, "configured": True, "flight_id": flight_id, "strategy": strategy, "points": results}


def _bearing_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing (degrees, clockwise from North) from point 1 to point 2."""
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dlambda = np.radians(lon2 - lon1)
    y = np.sin(dlambda) * np.cos(phi2)
    x = np.cos(phi1) * np.sin(phi2) - np.sin(phi1) * np.cos(phi2) * np.cos(dlambda)
    return float(np.degrees(np.arctan2(y, x)) % 360)


def _angle_diff_deg(a, b):
    """Smallest angle (0-180) between two compass bearings."""
    return float(min(abs(a - b), 360 - abs(a - b)))


@app.get("/api/flight/{flight_id}/wind_vectors")
def flight_wind_vectors(flight_id: str, strategy: str = flight_backtrack.STRATEGY_TOPN):
    """
    MERRA-2 wind vector (speed + from-direction) at each HYSPLIT release
    point's real lat/lon/alt_ft/time -- an independent physical cross-check
    of the trajectory HYSPLIT itself drew using its own (typically GDAS,
    not MERRA-2) meteorological input. For each point this also reports the
    bearing from that point back to the density grid's own dust-weighted
    centroid ("where HYSPLIT thinks the source is, from here") and the
    angle between that bearing and MERRA-2's wind from-direction: a small
    angle means an independent reanalysis's wind agrees that air was
    plausibly arriving from the same direction HYSPLIT's own trajectory
    implies. Same always-200 computed/configured contract, with per-point
    "error" on individual lookup failures, as /merra2_validation above.
    """
    flight_dir = Path(flight_backtrack.flight_dir_for(flight_id, strategy))
    density_path = flight_dir / "density_grid.json"
    if not density_path.exists():
        return {"computed": False, "flight_id": flight_id, "strategy": strategy}

    try:
        import merra2_utils
    except ImportError:
        return {
            "computed": True, "configured": False, "flight_id": flight_id,
            "reason": "merra2_utils dependencies (earthaccess, xarray) aren't installed on this machine.",
        }
    if not merra2_utils.credentials_configured():
        return {
            "computed": True, "configured": False, "flight_id": flight_id,
            "reason": "NASA Earthdata credentials aren't configured.",
            "setup": "Create a free account at https://urs.earthdata.nasa.gov/users/new, then set "
                     "EARTHDATA_USERNAME and EARTHDATA_PASSWORD before starting the dashboard.",
        }

    data = json.loads(density_path.read_text())
    release_points = data["release_points"]

    # Dust-density-weighted centroid of the source grid -- "where HYSPLIT
    # thinks the source is", the reference point every release point's wind
    # gets checked against below.
    lon_grid, lat_grid = np.meshgrid(np.array(data["lon"]), np.array(data["lat"]))
    density = np.array(data["density"])
    total = density.sum()
    if total > 0:
        centroid_lon = float((lon_grid * density).sum() / total)
        centroid_lat = float((lat_grid * density).sum() / total)
    else:
        centroid_lon, centroid_lat = None, None

    # MERRA-2's daily files are global, so every release point on the same
    # day shares one granule -- open each day's dataset once and reuse it
    # across that day's points, instead of a fresh remote search+open per
    # point (10x the CMR/auth round-trips for one 10-point flight, the
    # dominant cost since points on one flight are almost always same-day).
    wind_datasets_by_day = {}

    results = []
    for rp in release_points:
        entry = {
            "point_label": rp["point_label"], "lat": rp["lat"], "lon": rp["lon"],
            "alt_ft": rp["alt_ft"], "dust_ingested_g": rp["dust_ingested_g"],
        }
        try:
            when_utc = datetime.fromisoformat(rp["start_utc"])
            if when_utc.tzinfo is None:
                when_utc = when_utc.replace(tzinfo=timezone.utc)
            day = when_utc.strftime("%Y-%m-%d")
            if day not in wind_datasets_by_day:
                wind_datasets_by_day[day] = merra2_utils.open_wind_dataset(rp["lat"], rp["lon"], day)
            wind = merra2_utils.wind_at_point(rp["lat"], rp["lon"], rp["alt_ft"], when_utc, ds=wind_datasets_by_day[day])
            entry.update(wind)
            if centroid_lat is not None:
                source_bearing = _bearing_deg(rp["lat"], rp["lon"], centroid_lat, centroid_lon)
                entry["source_bearing_deg"] = source_bearing
                entry["wind_source_angle_diff_deg"] = _angle_diff_deg(wind["direction_from_deg"], source_bearing)
        except Exception as e:
            entry["error"] = str(e)
        results.append(entry)

    # Surface uplift plausibility check: was near-surface wind at the
    # source region actually strong enough to raise dust, around the time
    # the backward trajectory implies air left there? One lookup for the
    # whole flight (at the centroid, at the highest-dust point's start time
    # shifted back by runtime_hours), not per release point -- this is a
    # single plausibility read on the source region itself, not something
    # that needs repeating per point.
    surface_uplift = None
    if centroid_lat is not None and release_points:
        try:
            peak_point = max(release_points, key=lambda rp: rp["dust_ingested_g"])
            peak_time = datetime.fromisoformat(peak_point["start_utc"])
            if peak_time.tzinfo is None:
                peak_time = peak_time.replace(tzinfo=timezone.utc)
            source_time = peak_time + timedelta(hours=data.get("runtime_hours") or 0)
            surf = merra2_utils.surface_wind_at_point(centroid_lat, centroid_lon, source_time)
            surface_uplift = {
                **surf,
                "reference_point": peak_point["point_label"],
                "source_time_utc": source_time.isoformat(),
                "threshold_ms": UPLIFT_THRESHOLD_MS,
                "plausible": surf["speed_ms"] >= UPLIFT_THRESHOLD_MS,
            }
        except Exception as e:
            surface_uplift = {"error": str(e)}

    return {
        "computed": True, "configured": True, "flight_id": flight_id, "strategy": strategy,
        "centroid_lat": centroid_lat, "centroid_lon": centroid_lon, "points": results,
        "surface_uplift": surface_uplift,
    }


@app.get("/api/flight/{flight_id}/aod_agreement")
def flight_aod_agreement(flight_id: str, strategy: str = flight_backtrack.STRATEGY_TOPN,
                          density_percentile: float = 75, aod_threshold: float = 0.3):
    """
    Quantifies spatial agreement between this flight's HYSPLIT source
    density result and real satellite-retrieved MODIS AOD (MOD08_D3), as a
    percent overlap between two thresholded masks -- see hysplit_tools/
    modis_aod_utils.py for the method and its caveats. `density_percentile`
    picks HYSPLIT's own "high density" cutoff (relative to this flight's
    own distribution); `aod_threshold` is a fixed AOD cutoff (needs to mean
    the same thing across flights to be comparable). Same
    computed/configured always-200 contract as the other dust-source
    endpoints.
    """
    flight_dir = Path(flight_backtrack.flight_dir_for(flight_id, strategy))
    density_path = flight_dir / "density_grid.json"
    if not density_path.exists():
        return {"computed": False, "flight_id": flight_id, "strategy": strategy}

    try:
        import modis_aod_utils
    except ImportError:
        return {
            "computed": True, "configured": False, "flight_id": flight_id,
            "reason": "modis_aod_utils dependencies (earthaccess, xarray) aren't installed on this machine.",
        }
    if not modis_aod_utils.credentials_configured():
        return {
            "computed": True, "configured": False, "flight_id": flight_id,
            "reason": "NASA Earthdata credentials aren't configured.",
            "setup": "Create a free account at https://urs.earthdata.nasa.gov/users/new, then set "
                     "EARTHDATA_USERNAME and EARTHDATA_PASSWORD before starting the dashboard.",
        }

    density = json.loads(density_path.read_text())
    lon, lat = density["lon"], density["lat"]
    release_points = density["release_points"]
    if not release_points:
        return {"computed": True, "configured": True, "flight_id": flight_id, "error": "no release points to date this flight by"}
    date_str = release_points[0]["start_utc"][:10]

    try:
        result = modis_aod_utils.fetch_aod_grid(min(lon), max(lon), min(lat), max(lat), date_str)
        if result is None:
            return {
                "computed": True, "configured": True, "flight_id": flight_id,
                "error": f"no MOD08_D3 granule found for {date_str}",
            }
        aod_lon, aod_lat, aod_grid = result
        agreement = modis_aod_utils.compute_agreement(
            lon, lat, density["density"], aod_lon, aod_lat, aod_grid,
            density_percentile=density_percentile, aod_threshold=aod_threshold,
        )
    except Exception as e:
        return {"computed": True, "configured": True, "flight_id": flight_id, "error": str(e)}

    return {
        "computed": True, "configured": True, "flight_id": flight_id, "strategy": strategy,
        "date": date_str, **agreement,
    }


@app.get("/api/flight/{flight_id}/seviri_scene")
def flight_seviri_scene(flight_id: str, strategy: str = flight_backtrack.STRATEGY_TOPN):
    """
    Looks up the nearest real SEVIRI full-disk scene (EUMETSAT Data Store)
    to this flight's single highest-dust HYSPLIT release point, as a link
    the user can open in EUMETSAT's own viewer to visually cross-check the
    HYSPLIT/MERRA-2 result against an actual satellite image. See dust
    files/eumetsat_utils.py -- this only searches metadata, it doesn't
    render the Dust RGB composite itself.

    Falls back to the flight's own peak dust_g timestep (read directly from
    its raw CSV) if no HYSPLIT density grid has been computed yet for this
    flight+strategy -- unlike merra2_validation, a satellite scene lookup
    doesn't depend on HYSPLIT's release points to be useful.
    """
    try:
        import eumetsat_utils
    except ImportError:
        return {"configured": False, "flight_id": flight_id,
                "reason": "eumetsat_utils dependencies aren't installed on this machine."}
    if not eumetsat_utils.credentials_configured():
        return {
            "configured": False, "flight_id": flight_id,
            "reason": "EUMETSAT Data Store credentials aren't configured.",
            "setup": "Create a free account at https://data.eumetsat.int/, generate an API key at "
                     "https://api.eumetsat.int/api-key/, then set EUMETSAT_CONSUMER_KEY and "
                     "EUMETSAT_CONSUMER_SECRET before starting the dashboard.",
        }

    flight_dir = Path(flight_backtrack.flight_dir_for(flight_id, strategy))
    density_path = flight_dir / "density_grid.json"
    if density_path.exists():
        release_points = json.loads(density_path.read_text())["release_points"]
        peak = max(release_points, key=lambda rp: rp["dust_ingested_g"])
        lat, lon = float(peak["lat"]), float(peak["lon"])
        when_utc = datetime.fromisoformat(peak["start_utc"])
        if when_utc.tzinfo is None:
            when_utc = when_utc.replace(tzinfo=timezone.utc)
    else:
        with db_lock:
            meta = con.execute("SELECT * FROM flights WHERE flight_id = ?", [flight_id]).df()
        if meta.empty:
            raise HTTPException(404, "Unknown flight_id")
        csv_path = CSV_DIR / f"{flight_id}.csv"
        if not csv_path.exists():
            raise HTTPException(404, "Raw CSV for this flight is missing")
        df = pd.read_csv(csv_path, usecols=["time", "phase", "Lat", "Lon", "CoreDustIngested_g"])
        df = df[df["phase"].isin(PHASES)]
        if df.empty:
            raise HTTPException(404, "No usable rows for this flight")
        peak = df.loc[df["CoreDustIngested_g"].idxmax()]
        lat, lon = float(peak["Lat"]), float(peak["Lon"])
        when_utc = pd.to_datetime(peak["time"]).to_pydatetime()
        if when_utc.tzinfo is None:
            when_utc = when_utc.replace(tzinfo=timezone.utc)

    try:
        scene = eumetsat_utils.nearest_scene(lat, lon, when_utc)
    except Exception as e:
        return {"configured": True, "flight_id": flight_id, "error": str(e)}
    if scene is None:
        return {
            "configured": True, "flight_id": flight_id, "found": False,
            "reason": f"No HRSEVIRI scene found within the search window of {when_utc.isoformat()}.",
        }
    return {"configured": True, "flight_id": flight_id, "found": True, "reference_time_utc": when_utc.isoformat(), **scene}


def _log_tail(log_path, n=5):
    if not log_path.exists():
        return []
    return log_path.read_text(errors="replace").splitlines()[-n:]


POINT_RE = re.compile(r"\[(\d+)/(\d+)\]")
PERCENT_RE = re.compile(r"Percent complete:\s*([\d.]+)")


def _log_progress(log_path):
    """
    Scans the whole log for the LAST "[N/10] <label>" line (printed once per
    release point by flight_backtrack.py's run_flight loop) and the LAST
    "Percent complete: X" line (printed by hycs_std itself). Percent resets
    to 0 at the start of every point since each is its own separate HYSPLIT
    run -- that's surfaced to the frontend as context, not treated as a bug.
    """
    if not log_path.exists():
        return {"point": None, "total_points": None, "percent": None}
    text = log_path.read_text(errors="replace")
    point_match = POINT_RE.findall(text)
    percent_match = PERCENT_RE.findall(text)
    point, total_points = (int(x) for x in point_match[-1]) if point_match else (None, None)
    percent = float(percent_match[-1]) if percent_match else None
    return {"point": point, "total_points": total_points, "percent": percent}


@app.post("/api/flight/{flight_id}/compute")
def start_compute(flight_id: str, job: str, hours: int | None = None, points: int | None = None,
                   strategy: str = flight_backtrack.STRATEGY_TOPN):
    """
    Launches flight_backtrack.py for flight_id in the background, so the
    dashboard doesn't require a terminal on the HYSPLIT machine for the
    common case of "just compute this one flight I'm already looking at".
    Non-blocking -- Popen() returns immediately, so this is safe to call
    from a sync endpoint.

    `hours` is the backward-trajectory length (positive number of hours,
    e.g. 72); `points` is how many highest-dust release points to evaluate
    (topn/dense strategies only -- ignored by trigger). Both optional -- the
    script defaults to its own RUNTIME_HOURS/N_RELEASE_POINTS constants when
    omitted. `strategy` (topn/trigger/dense) picks the release-point
    selection strategy -- see flight_backtrack.select_release_points().
    """
    global CURRENT_JOB
    if job not in COMPUTE_JOBS:
        raise HTTPException(400, f"unknown job {job!r}, expected one of {list(COMPUTE_JOBS)}")
    if strategy not in flight_backtrack.STRATEGIES:
        raise HTTPException(400, f"unknown strategy {strategy!r}, expected one of {flight_backtrack.STRATEGIES}")

    if CURRENT_JOB is not None and CURRENT_JOB["process"].poll() is None:
        return {
            "status": "busy",
            "running": {"flight_id": CURRENT_JOB["flight_id"], "job": CURRENT_JOB["job"]},
        }

    flight_dir = Path(flight_backtrack.flight_dir_for(flight_id, strategy))
    flight_dir.mkdir(parents=True, exist_ok=True)
    log_path = flight_dir / f"{job}_compute.log"
    cmd = [sys.executable, "-u", COMPUTE_JOBS[job], flight_id, "--strategy", strategy]
    if hours is not None:
        cmd += ["--hours", str(hours)]
    if points is not None:
        cmd += ["--points", str(points)]
    with open(log_path, "w") as log_file:
        process = subprocess.Popen(
            # -u: unbuffered stdout, so this script's own "[N/10] ..." print()s land in the
            # log in the order they actually happen relative to hycs_std/hyts_std's own
            # (unbuffered-by-default) writes to the same fd -- otherwise Python's default
            # block-buffering can delay them behind a point's HYSPLIT output, leaving
            # _log_progress() unable to tell which point is currently running.
            cmd, cwd=DUST_FILES_DIR, stdout=log_file, stderr=subprocess.STDOUT,
        )  # child inherits its own duplicated fd, safe to close ours once Popen() returns
    CURRENT_JOB = {"flight_id": flight_id, "job": job, "process": process, "log_path": log_path, "started": time.time()}
    return {"status": "started"}


@app.get("/api/compute_status")
def compute_status():
    """
    Polled by the frontend while a compute job is in flight. Reports the one
    system-wide job slot's state -- clears it on the poll *after* it
    resolves to done/error, not immediately, so whichever tab triggered it
    still gets to see the final status once before it goes back to idle.
    """
    global CURRENT_JOB
    if CURRENT_JOB is None:
        return {"status": "idle"}

    returncode = CURRENT_JOB["process"].poll()
    base = {
        "flight_id": CURRENT_JOB["flight_id"],
        "job": CURRENT_JOB["job"],
        "elapsed_s": time.time() - CURRENT_JOB["started"],
        "log_tail": _log_tail(CURRENT_JOB["log_path"]),
        **_log_progress(CURRENT_JOB["log_path"]),
    }
    if returncode is None:
        return {"status": "running", **base}

    result = {"status": "done" if returncode == 0 else "error", **base}
    CURRENT_JOB = None
    return result


SUSCEPTIBILITY_AGREEMENT_PATH = DUST_SOURCE_DIR.parent / "susceptibility_agreement.csv"


@app.get("/api/susceptibility_agreement")
def susceptibility_agreement():
    """
    Reads hysplit_tools/validate_susceptibility_agreement.py's output (run
    offline, like build_susceptibility_grid.py itself -- see that script)
    -- for every flight with a real HYSPLIT result, what percentage of its
    own high-density source region is independently supported by the
    susceptibility grid (soil/land-cover data, entirely separate from the
    transport modeling). Same always-200/`computed`-flag contract as the
    other dust-source endpoints.
    """
    if not SUSCEPTIBILITY_AGREEMENT_PATH.exists():
        return {"computed": False}
    with open(SUSCEPTIBILITY_AGREEMENT_PATH, newline="") as f:
        rows = [
            {"flight_id": row["flight_id"], "agreement_pct": float(row["susceptibility_agreement_pct"])}
            for row in csv.DictReader(f)
        ]
    pcts = [r["agreement_pct"] for r in rows]
    return {
        "computed": True,
        "n_flights": len(rows),
        "mean_agreement_pct": float(np.mean(pcts)) if pcts else None,
        "median_agreement_pct": float(np.median(pcts)) if pcts else None,
        "flights": sorted(rows, key=lambda r: -r["agreement_pct"]),
    }


@app.get("/api/dust_susceptibility")
def dust_susceptibility():
    """
    Serves the precomputed, region-wide source-susceptibility grid (soil +
    land-cover layers, not per-flight) if build_susceptibility_grid.py has
    been run. Same always-200/`computed`-flag contract as the two endpoints
    above -- the frontend overlays this once, independent of flight choice.
    """
    if not SUSCEPTIBILITY_PATH.exists():
        return {"computed": False}
    return {"computed": True, **json.loads(SUSCEPTIBILITY_PATH.read_text())}


def _records_with_nan_as_none(df):
    """
    Shared by the endpoints below -- DataFrame.to_dict(orient="records")
    leaves NaN in place (it isn't valid JSON; FastAPI would serialize it as
    the non-standard literal `NaN`, which strict JSON parsers reject), so
    sweep it to None per-cell after conversion. Done on the plain Python
    values from to_dict(), not on the DataFrame itself, since an
    all-numeric-NaN VARCHAR column (e.g. no summary file for that aircraft
    type) round-trips through pandas as float64 and DataFrame.where() can't
    fix that in place -- see /api/flights_on_date, which had this exact bug.
    """
    records = df.to_dict(orient="records")
    for record in records:
        for key, value in record.items():
            if isinstance(value, float) and math.isnan(value):
                record[key] = None
    return records


@app.get("/api/anomalous_flights")
def anomalous_flights():
    """
    Surfaces build_dataset.py's own data-quality record directly in the
    dashboard, instead of requiring someone to open data/anomalous_flights.csv
    by hand: every flight excluded from timesteps.parquet (so it can never
    appear in or skew any chart, table, or picker), and why -- either no
    usable (non-UNKNOWN-phase) rows at all, or its first recorded phase
    isn't CLIMB (the recording appears to start mid-flight). Nothing here is
    re-derived; it's exactly what build_dataset.py already decided, read
    back as-is. Always 200 -- `computed` false just means build_dataset.py
    hasn't been run yet (or hasn't produced this file, on an older run).
    """
    if not ANOMALOUS_FLIGHTS_PATH.exists():
        return {"computed": False}
    df = pd.read_csv(ANOMALOUS_FLIGHTS_PATH)
    records = _records_with_nan_as_none(df)
    n_no_usable_rows = sum(1 for r in records if not (r.get("n_rows") or 0))
    # Denominator for the per-type breakdown's percentage column -- total
    # flights of that type in flights.parquet (excluded + usable), not just
    # the excluded count above, so "6 excluded" reads very differently for a
    # type with 20 total flights vs. one with 5,000.
    with db_lock:
        totals_by_type = dict(con.execute(
            "SELECT aircraft_type, COUNT(*) FROM flights GROUP BY aircraft_type"
        ).fetchall())
    return {
        "computed": True,
        "n_flights": len(records),
        "n_no_usable_rows": n_no_usable_rows,
        "n_wrong_first_phase": len(records) - n_no_usable_rows,
        "flights": records,
        "totals_by_type": totals_by_type,
    }


@app.get("/api/batch_compute_progress")
def batch_compute_progress(strategy: str = flight_backtrack.STRATEGY_TOPN):
    """
    How much of CSVFiles has a real HYSPLIT result yet, for watching a
    run_all_flights.py batch progress without tailing a log file by hand.
    Two independent parts:

      - coverage: every flight_id in CSVFiles (same listing
        run_all_flights.py itself uses), checked against the same
        density_grid.json existence test that script uses to decide what's
        left to do -- works even if no batch has ever been run on this
        machine, since it's a direct filesystem check, not a log read.
      - recent activity: the tail of batch_compute_log.csv, if
        run_all_flights.py has been run here at least once. That log lives
        outside OneDrive (see run_all_flights.LOG_PATH / backtrack.WORK_BASE)
        specifically because OneDrive's sync churn was found to corrupt a
        file appended to this often -- so this can only ever report on
        activity from the machine open right now, not a batch running
        elsewhere.
    """
    flight_ids = run_all_flights.list_flight_ids()
    total = len(flight_ids)
    done = sum(
        1 for fid in flight_ids
        if run_all_flights.already_computed(fid, strategy)
    )

    log_path = run_all_flights.LOG_PATH
    n_ok = n_other = 0
    recent = []
    last_activity_utc = None
    if log_path.exists():
        with open(log_path, newline="") as f:
            log_rows = list(csv.DictReader(f))
        for row in log_rows:
            if row.get("status") == "ok":
                n_ok += 1
            else:
                n_other += 1
        recent = list(reversed(log_rows[-20:]))
        if log_rows:
            last_activity_utc = log_rows[-1].get("timestamp_utc")

    return {
        "strategy": strategy,
        "total_flights": total,
        "computed": done,
        "remaining": total - done,
        "log_exists": log_path.exists(),
        "log_attempts": n_ok + n_other,
        "log_ok": n_ok,
        "log_other": n_other,
        "last_activity_utc": last_activity_utc,
        "recent": recent,
    }


@app.get("/api/flight_search")
def flight_search(q: str, limit: int = 25):
    """
    Free-text jump-to-flight search across callsign, registration, ICAO
    codes, and the flight_id itself -- an alternative to narrowing aircraft
    type then date then picking from a dropdown, useful once there are
    hundreds of flights and you already know roughly what you're looking
    for. Same first_phase = 'CLIMB' usability filter /api/flights_on_date
    applies, so a search result is always a flight the picker could
    actually load. Substring match (case-insensitive), not a fuzzy search --
    q shorter than 2 characters returns nothing rather than a huge list.
    """
    q = q.strip()
    if len(q) < 2:
        return []
    like = f"%{q.upper()}%"
    with db_lock:
        rows = con.execute(
            """
            SELECT flight_id, date, takeoff_hhmm, callsign, registration, aircraft_type,
                   origin_icao, destination_icao, total_dust_g
            FROM flights
            WHERE first_phase = 'CLIMB' AND (
                UPPER(callsign) LIKE ? OR UPPER(registration) LIKE ? OR
                UPPER(origin_icao) LIKE ? OR UPPER(destination_icao) LIKE ? OR
                UPPER(flight_id) LIKE ?
            )
            ORDER BY date DESC, takeoff_hhmm DESC
            LIMIT ?
            """,
            [like, like, like, like, like, limit],
        ).df()
    records = _records_with_nan_as_none(rows)
    for record in records:
        record["date"] = str(record["date"])[:10]
    return records


def _worldcover_tile_urls(lon_min, lon_max, lat_min, lat_max):
    """Same SW-corner 3deg tile naming as build_susceptibility_grid.py's own _worldcover_tiles()."""
    lon0 = int(np.floor(lon_min / WORLDCOVER_TILE_DEG) * WORLDCOVER_TILE_DEG)
    lon1 = int(np.floor((lon_max - 1e-9) / WORLDCOVER_TILE_DEG) * WORLDCOVER_TILE_DEG)
    lat0 = int(np.floor(lat_min / WORLDCOVER_TILE_DEG) * WORLDCOVER_TILE_DEG)
    lat1 = int(np.floor((lat_max - 1e-9) / WORLDCOVER_TILE_DEG) * WORLDCOVER_TILE_DEG)
    for lat in range(lat0, lat1 + 1, WORLDCOVER_TILE_DEG):
        for lon in range(lon0, lon1 + 1, WORLDCOVER_TILE_DEG):
            ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
            ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
            yield lon, lat, f"{WORLDCOVER_BASE_URL}/ESA_WorldCover_10m_2021_v200_{ns}{ew}_Map.tif"


@app.get("/api/worldcover_overlay")
def worldcover_overlay(lon_min: float, lon_max: float, lat_min: float, lat_max: float):
    """
    ESA WorldCover 2021 (10m) land-cover classification for the given
    bounding box, rendered server-side as a color-coded PNG the frontend
    drops straight into a Plotly layout image -- same visual slot as the
    true-color/terrain/land-surface-temperature background toggles, except
    those come pre-rendered from NASA GIBS's snapshot API and WorldCover has
    no equivalent public snapshot service, so this builds one from the same
    raw GeoTIFF tiles build_susceptibility_grid.py already reads (public,
    no-login S3 bucket, read decimated over /vsicurl/ -- GDAL pulls
    overview data only, never a full 10m tile).

    Output capped at OUT_MAX_PX per side -- this is a quick-look overlay,
    not a precision export (the CSV/GeoTIFF buttons elsewhere in this app
    exist for that); a flight's bbox can span tens of degrees, which at
    native 10m resolution would be tens of thousands of pixels per side.
    Cells with no covering tile (ocean, or a real gap in WorldCover's own
    grid) are fully transparent (alpha 0) rather than colored, so they read
    as "no data" instead of a false class.
    """
    if rasterio is None:
        raise HTTPException(503, f"WorldCover overlay unavailable: rasterio failed to import on this machine ({RASTERIO_IMPORT_ERROR}).")
    OUT_MAX_PX = 900
    span_lon, span_lat = lon_max - lon_min, lat_max - lat_min
    if span_lon <= 0 or span_lat <= 0:
        raise HTTPException(400, "lon_max must be > lon_min and lat_max must be > lat_min")
    px_per_deg = OUT_MAX_PX / max(span_lon, span_lat)
    out_w = max(1, round(span_lon * px_per_deg))
    out_h = max(1, round(span_lat * px_per_deg))

    classes = np.zeros((out_h, out_w), dtype=np.uint8)
    covered = np.zeros((out_h, out_w), dtype=bool)
    # Output pixel grid is north-up (row 0 = lat_max), the usual image
    # convention -- matches how the frontend places it (imageBase's
    # y: latMax, yanchor: "top" in app.js).
    for lon0, lat0, url in _worldcover_tile_urls(lon_min, lon_max, lat_min, lat_max):
        tile_lon_max, tile_lat_max = lon0 + WORLDCOVER_TILE_DEG, lat0 + WORLDCOVER_TILE_DEG
        ov_lon_min, ov_lon_max = max(lon0, lon_min), min(tile_lon_max, lon_max)
        ov_lat_min, ov_lat_max = max(lat0, lat_min), min(tile_lat_max, lat_max)
        if ov_lon_min >= ov_lon_max or ov_lat_min >= ov_lat_max:
            continue  # tile computed from floor() but doesn't actually reach into the requested bbox on this axis

        col_lo = round((ov_lon_min - lon_min) * px_per_deg)
        col_hi = round((ov_lon_max - lon_min) * px_per_deg)
        row_lo = round((lat_max - ov_lat_max) * px_per_deg)
        row_hi = round((lat_max - ov_lat_min) * px_per_deg)
        if col_hi <= col_lo or row_hi <= row_lo:
            continue

        try:
            with rasterio.open(f"/vsicurl/{url}") as src:
                data = src.read(
                    1,
                    out_shape=(row_hi - row_lo, col_hi - col_lo),
                    resampling=Resampling.mode,
                )
        except rasterio.errors.RasterioIOError:
            continue  # no tile here -- likely an all-ocean gap in WorldCover's own grid
        classes[row_lo:row_hi, col_lo:col_hi] = data
        covered[row_lo:row_hi, col_lo:col_hi] = True

    rgb = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    known = np.zeros((out_h, out_w), dtype=bool)
    for code, (_, color) in WORLDCOVER_LEGEND.items():
        cell_mask = classes == code
        rgb[cell_mask] = color
        known |= cell_mask
    # A covered tile can still carry a raw value outside WORLDCOVER_LEGEND
    # (WorldCover's own no-data code, 0, inside an otherwise-covered tile) --
    # transparent like a genuinely missing tile, not opaque black, since
    # that's not a real class either.
    alpha = np.where(covered & known, 255, 0).astype(np.uint8)

    with MemoryFile() as memfile:
        with memfile.open(driver="PNG", height=out_h, width=out_w, count=4, dtype="uint8") as dst:
            dst.write(rgb[:, :, 0], 1)
            dst.write(rgb[:, :, 1], 2)
            dst.write(rgb[:, :, 2], 3)
            dst.write(alpha, 4)
        png_bytes = memfile.read()
    return Response(png_bytes, media_type="image/png")


def _clip_segments_to_bbox(segments, lon_min, lon_max, lat_min, lat_max):
    """
    Shared by /api/coastlines and /api/borders: clips a list of [(lat, lon), ...]
    polyline segments down to just the points actually inside the box
    (breaking into separate sub-runs wherever it exits/re-enters), not just
    a whole-segment bounding-box overlap test -- source segments can be very
    long (a whole coastline/border as one continuous polyline), so a
    real-world segment that merely brushes the box would otherwise drag in
    far-off points and blow the plotted extent out to a much wider area
    than the box actually asked for. Returns one lon/lat pair with `null`
    separators between segments, the standard Plotly technique for drawing
    disconnected polylines as a single trace.
    """
    lon, lat = [], []
    for seg in segments:
        seg_lats = [p[0] for p in seg]
        seg_lons = [p[1] for p in seg]
        if max(seg_lons) < lon_min or min(seg_lons) > lon_max or max(seg_lats) < lat_min or min(seg_lats) > lat_max:
            continue  # segment doesn't touch the requested extent at all -- skip entirely

        run_open = False
        for seg_lat, seg_lon in zip(seg_lats, seg_lons):
            inside = lon_min <= seg_lon <= lon_max and lat_min <= seg_lat <= lat_max
            if inside:
                if not run_open and lon:
                    lon.append(None)
                    lat.append(None)
                lon.append(seg_lon)
                lat.append(seg_lat)
                run_open = True
            else:
                run_open = False
    return {"lon": lon, "lat": lat}


@app.get("/api/coastlines")
def coastlines(lon_min: float, lon_max: float, lat_min: float, lat_max: float):
    """
    Coastline reference lines for the given bounding box, from the same
    HYSPLIT arlmap data the offline matplotlib tool draws with (see
    density_utils.plot_coastlines) -- filtered from the cached full-world
    parse (COASTLINE_SEGMENTS) rather than reading the file per request.
    """
    return _clip_segments_to_bbox(COASTLINE_SEGMENTS, lon_min, lon_max, lat_min, lat_max)


@app.get("/api/borders")
def borders(lon_min: float, lon_max: float, lat_min: float, lat_max: float):
    """
    Political (country) border reference lines for the given bounding box,
    from Natural Earth's free 50m admin-0 boundary-lines dataset (see
    BORDER_SEGMENTS above) -- separate from /api/coastlines because
    HYSPLIT's arlmap basemap only carries coastlines, not political
    borders. Same bbox-clipping technique and response shape as
    /api/coastlines.
    """
    return _clip_segments_to_bbox(BORDER_SEGMENTS, lon_min, lon_max, lat_min, lat_max)


app.mount("/", StaticFiles(directory=str(SCRIPT_DIR / "static"), html=True), name="static")
