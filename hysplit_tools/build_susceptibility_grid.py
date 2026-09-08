"""
Static dust-source-susceptibility layer for the dashboard's dust source
attribution tab -- adapts the "potential SDS source map" step from the
source-attribution literature (overlay thematic soil/land-cover layers,
classify cells into Low/Medium/High potential by how many layers agree)
using free, no-login public sources, since there's no local GIS soil/
land-cover database available. The paper's 4 named layers (sand dune
fields, aridisols, dried alluvial fans, degradation maps) don't have exact
public datasets, so each is approximated by the closest available layer:

  - Sand dune fields  -> ESA WorldCover 2021 (10m): bare/sparse-vegetation
    class, read over /vsicurl/ from the public AWS bucket at a decimated
    resolution (GDAL pulls overview data only, not full 10m tiles).
  - Aridisols          -> ISRIC SoilGrids WCS: sand content, 0-5cm mean,
    AND ISRIC WRB classification: Arenosols probability (sandy soils).
  - Dried alluvial fans -> ISRIC WRB classification: Fluvisols probability
    (soils formed on recent fluvial/alluvial deposits).
  - Degradation maps    -> ISRIC WRB classification: Solonchaks probability
    (saline soils -- a standard proxy for playas/sabkhas, a real desert
    dust source).

One-time/occasional offline build, not part of the live dashboard request
path -- like flight_backtrack.py, run manually:

    python build_susceptibility_grid.py

Writes hysplit_results/susceptibility_grid.json on the SAME 0.25-degree
grid convention as density_utils.build_density_grid / flight_pscf's
GRID_RES_DEG, so it lines up cell-for-cell with every flight's
density_grid.json / pscf_cwt_grid.json without reprojection.

Combining is a vote count across LAYER_BUILDERS against TIER_THRESHOLDS
(Low/Medium/High), not a hardcoded AND -- another layer can be added later
without touching the combine step or any downstream consumer.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.enums import Resampling

DUST_FILES_DIR = Path(__file__).resolve().parent
RESULTS_DIR = DUST_FILES_DIR / "hysplit_results"
OUT_PATH = RESULTS_DIR / "susceptibility_grid.json"

# Bounding box: Arabian Peninsula's known dust source regions (An Nafud,
# Rub' al Khali, Tigris-Euphrates floodplain) plus the wider Sahara-to-Iran
# corridor a 48h HYSPLIT backward trajectory can plausibly reach.
LON_MIN, LON_MAX = 25.0, 65.0
LAT_MIN, LAT_MAX = 8.0, 35.0
GRID_RES_DEG = 0.25  # matches density_utils.build_density_grid / flight_pscf.GRID_RES_DEG
CELLS_PER_DEG = round(1 / GRID_RES_DEG)  # integer grid-index math below assumes this divides evenly into 3 (WorldCover tile size)

SAND_PCT_THRESHOLD = 50  # SoilGrids raw units are g/kg -- divide by 10 for %.
# The textbook figure, from USDA NRCS Wind Erodibility Groups (WEG) -- the
# standard soil-science reference for wind-erosion susceptibility by texture
# -- is >=70% sand (WEG 1-2: sand/loamy sand textures, the most erodible
# classes). 50 is deliberately lower than that literature threshold: SoilGrids'
# 0-5cm sand estimate reads only ~50-56% even at the Rub' al Khali's core
# (verified against real coordinates during development) rather than the
# ~90%+ a literal sand sea implies -- sparse ground-truth training data in
# this region pulls its estimate toward the regional mean. Using the textbook
# 70% here would exclude confirmed real deserts because of that regional
# product bias, not because they aren't sandy. Combined with the independent
# WorldCover layer via SUSCEPTIBILITY_THRESHOLD, 50 still discriminates real
# sand/dune terrain from the surrounding gravel plains and mountains.
# Source: USDA NRCS Wind Erodibility Groups, e.g.
# https://efotg.sc.egov.usda.gov/references/Agency/SD/Archived_winderos_100415.pdf
WORLDCOVER_BARE_SPARSE_CLASS = 60

# WRB soil-classification probability layers (0-100%, same ISRIC WCS service
# as the sand layer above). Unlike SAND_PCT_THRESHOLD, this one has no
# literature threshold to fall back on -- there's no published convention for
# a fixed percentage cutoff on SoilGrids' WRB class-probability output.
# ISRIC's own recommended usage is instead an argmax ("MostProbable" class
# per cell, see their SoilGrids WRB documentation), which was investigated
# here as a more principled alternative to a fixed cutoff -- but spot-checks
# cross-referencing the MostProbable layer's own per-cell code against an
# independently-computed argmax over all 30 RSG probability layers did not
# agree (e.g. a cell where Fluvisols was clearly the highest-probability
# class by a wide margin didn't get MostProbable's Fluvisols code), and
# MostProbable's code encoding isn't documented anywhere it could be
# reconciled from. Not trustworthy enough to switch to, so this stays a
# calibrated pragmatic threshold, same reasoning as SAND_PCT_THRESHOLD: even
# the Rub' al Khali's core reads ~50% for its own best-matching WRB class
# (verified against real coordinates during development), so a high bar
# would exclude confirmed real deserts.
WRB_PROBABILITY_THRESHOLD = 25
WRB_LAYERS = {
    "arenosols": "Arenosols",  # sandy soils -- aridisols proxy
    "fluvisols": "Fluvisols",  # recent fluvial/alluvial deposits -- dried alluvial fans proxy
    "solonchaks": "Solonchaks",  # saline soils -- degradation/playa proxy
}

# Cells voted "susceptible" by >= this many of the 5 layers land in each tier.
# 0 = none, 1 = low, 2 = medium, 3 = high. This vote-count scheme itself has
# no fixed literature precedent either -- published dust-source-susceptibility
# mapping for this same region (e.g. Tigris-Euphrates basin, Iran-Iraq border
# studies) uses weighted linear combination or ML classifiers (Random Forest,
# SVM, etc.) rather than a simple "N of M layers agree" vote, with weights
# typically obtained from expert judgement calibrated against field surveys --
# not a fixed number either. This tier scheme is a deliberately simpler proxy,
# calibrated the same way (checked against known real source regions): the 3
# WRB probability layers largely compete for the same per-cell probability
# budget (a cell rarely has 2+ WRB classes both above WRB_PROBABILITY_THRESHOLD
# at once), so requiring 4-5 of 5 layers to agree left even the Rub' al
# Khali's core (which reads 3/5) below "high" -- lowered so the world's most
# iconic sand sea actually lands in the top tier.
TIER_THRESHOLDS = {"low": 1, "medium": 2, "high": 3}
TIER_NAMES = ["none", "low", "medium", "high"]

WORLDCOVER_BASE_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
WORLDCOVER_TILE_DEG = 3
WORLDCOVER_TILE_CELLS = WORLDCOVER_TILE_DEG * CELLS_PER_DEG  # cells per tile edge at our target resolution


def grid_axes():
    n_lon = round((LON_MAX - LON_MIN) * CELLS_PER_DEG)
    n_lat = round((LAT_MAX - LAT_MIN) * CELLS_PER_DEG)
    lon = LON_MIN + (np.arange(n_lon) + 0.5) * GRID_RES_DEG
    lat = LAT_MIN + (np.arange(n_lat) + 0.5) * GRID_RES_DEG
    return lon, lat


def _fetch_isric_wcs(map_file, coverage_id, n_lon, n_lat, label):
    """
    One WCS GetCoverage request, scaled server-side directly to
    (n_lon, n_lat) -- no need to fetch the native-resolution global raster.
    Shared by every ISRIC-backed layer (SoilGrids sand content, WRB soil
    classification probabilities -- same maps.isric.org service, different
    map files/coverage IDs).
    """
    params = {
        "map": map_file,
        "SERVICE": "WCS",
        "VERSION": "2.0.1",
        "REQUEST": "GetCoverage",
        "COVERAGEID": coverage_id,
        "FORMAT": "image/tiff",
        "SUBSET": [f"X({LON_MIN},{LON_MAX})", f"Y({LAT_MIN},{LAT_MAX})"],
        "SUBSETTINGCRS": "http://www.opengis.net/def/crs/EPSG/0/4326",
        "OUTPUTCRS": "http://www.opengis.net/def/crs/EPSG/0/4326",
        "SCALESIZE": f"X({n_lon}),Y({n_lat})",
    }
    print(f"fetching {label} ...")
    r = requests.get("https://maps.isric.org/mapserv", params=params, timeout=120)
    r.raise_for_status()

    tmp_path = RESULTS_DIR / f"_{coverage_id}_wcs_tmp.tif"
    tmp_path.write_bytes(r.content)
    try:
        with rasterio.open(tmp_path) as src:
            data = src.read(1).astype(float)
            data = np.flipud(data)  # WCS output is north-up (row 0 = LAT_MAX); flip to south-up, matching grid_axes()
    finally:
        tmp_path.unlink(missing_ok=True)
    return data


def build_sand_layer(n_lon, n_lat):
    sand_gkg = _fetch_isric_wcs("/map/sand.map", "sand_0-5cm_mean", n_lon, n_lat, "SoilGrids sand_0-5cm_mean")
    sand_pct = sand_gkg / 10.0  # SoilGrids raw units are g/kg
    return sand_pct > SAND_PCT_THRESHOLD


def build_wrb_layer(coverage_id, n_lon, n_lat):
    """WRB soil-classification probability layer, 0-100% already in native units."""
    prob_pct = _fetch_isric_wcs("/map/wrb.map", coverage_id, n_lon, n_lat, f"WRB {coverage_id} probability")
    return prob_pct > WRB_PROBABILITY_THRESHOLD


def _worldcover_tiles():
    """SW-corner-named 3deg tiles, e.g. ESA_WorldCover_10m_2021_v200_N24E045_Map.tif."""
    lon0 = int(np.floor(LON_MIN / WORLDCOVER_TILE_DEG) * WORLDCOVER_TILE_DEG)
    lon1 = int(np.floor((LON_MAX - 1e-9) / WORLDCOVER_TILE_DEG) * WORLDCOVER_TILE_DEG)
    lat0 = int(np.floor(LAT_MIN / WORLDCOVER_TILE_DEG) * WORLDCOVER_TILE_DEG)
    lat1 = int(np.floor((LAT_MAX - 1e-9) / WORLDCOVER_TILE_DEG) * WORLDCOVER_TILE_DEG)
    for lat in range(lat0, lat1 + 1, WORLDCOVER_TILE_DEG):
        for lon in range(lon0, lon1 + 1, WORLDCOVER_TILE_DEG):
            ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
            ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
            yield lon, lat, f"{WORLDCOVER_BASE_URL}/ESA_WorldCover_10m_2021_v200_{ns}{ew}_Map.tif"


def build_landcover_layer(n_lon, n_lat):
    """
    Reads each intersecting WorldCover tile decimated straight to our grid
    resolution (majority class per cell, via GDAL overviews over
    /vsicurl/ -- no full 10m tile ever downloaded), then places each tile's
    cells into the shared grid using integer grid-index arithmetic (both
    the WorldCover tile grid (3deg) and our target grid (0.25deg) are
    aligned to a common lattice anchored at 0deg, so tile edges always fall
    exactly on grid-cell boundaries -- no fractional-cell overlap to
    handle).
    """
    is_bare = np.zeros((n_lat, n_lon), dtype=bool)
    covered = np.zeros((n_lat, n_lon), dtype=bool)

    lon_min_idx = round(LON_MIN * CELLS_PER_DEG)
    lat_min_idx = round(LAT_MIN * CELLS_PER_DEG)

    for lon0, lat0, url in _worldcover_tiles():
        tile_lon_idx0 = lon0 * CELLS_PER_DEG
        tile_lat_idx0 = lat0 * CELLS_PER_DEG

        lon_overlap_start = max(tile_lon_idx0, lon_min_idx)
        lon_overlap_end = min(tile_lon_idx0 + WORLDCOVER_TILE_CELLS, lon_min_idx + n_lon)
        lat_overlap_start = max(tile_lat_idx0, lat_min_idx)
        lat_overlap_end = min(tile_lat_idx0 + WORLDCOVER_TILE_CELLS, lat_min_idx + n_lat)
        if lon_overlap_start >= lon_overlap_end or lat_overlap_start >= lat_overlap_end:
            continue  # tile computed from floor() but doesn't actually reach into the domain on this axis

        print(f"  worldcover tile N{lat0}E{lon0} ...")
        try:
            with rasterio.open(f"/vsicurl/{url}") as src:
                data = src.read(
                    1,
                    out_shape=(WORLDCOVER_TILE_CELLS, WORLDCOVER_TILE_CELLS),
                    resampling=Resampling.mode,
                )
        except rasterio.errors.RasterioIOError:
            print(f"    tile not found (likely all-ocean gap in WorldCover's grid) -- skipping")
            continue
        data = np.flipud(data)  # north-up -> south-up, matching grid_axes()
        tile_bare = data == WORLDCOVER_BARE_SPARSE_CLASS

        tile_col_lo, tile_col_hi = lon_overlap_start - tile_lon_idx0, lon_overlap_end - tile_lon_idx0
        tile_row_lo, tile_row_hi = lat_overlap_start - tile_lat_idx0, lat_overlap_end - tile_lat_idx0
        grid_col_lo, grid_col_hi = lon_overlap_start - lon_min_idx, lon_overlap_end - lon_min_idx
        grid_row_lo, grid_row_hi = lat_overlap_start - lat_min_idx, lat_overlap_end - lat_min_idx

        is_bare[grid_row_lo:grid_row_hi, grid_col_lo:grid_col_hi] = tile_bare[tile_row_lo:tile_row_hi, tile_col_lo:tile_col_hi]
        covered[grid_row_lo:grid_row_hi, grid_col_lo:grid_col_hi] = True

    n_missing = int((~covered).sum())
    if n_missing:
        print(f"  {n_missing} grid cells had no WorldCover tile (treated as not bare/sparse)")
    return is_bare


LAYER_BUILDERS = {
    "sand_gt_50pct": lambda n_lon, n_lat: build_sand_layer(n_lon, n_lat),
    "worldcover_bare_sparse": lambda n_lon, n_lat: build_landcover_layer(n_lon, n_lat),
    "arenosols": lambda n_lon, n_lat: build_wrb_layer(WRB_LAYERS["arenosols"], n_lon, n_lat),
    "fluvisols": lambda n_lon, n_lat: build_wrb_layer(WRB_LAYERS["fluvisols"], n_lon, n_lat),
    "solonchaks": lambda n_lon, n_lat: build_wrb_layer(WRB_LAYERS["solonchaks"], n_lon, n_lat),
}


def classify_tier(count):
    """count -> tier index (0=none, 1=low, 2=medium, 3=high), per TIER_THRESHOLDS."""
    tier = np.zeros_like(count)
    tier[count >= TIER_THRESHOLDS["low"]] = 1
    tier[count >= TIER_THRESHOLDS["medium"]] = 2
    tier[count >= TIER_THRESHOLDS["high"]] = 3
    return tier


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    lon, lat = grid_axes()
    n_lon, n_lat = len(lon), len(lat)

    layers = {name: builder(n_lon, n_lat) for name, builder in LAYER_BUILDERS.items()}
    count = np.zeros((n_lat, n_lon), dtype=int)
    for arr in layers.values():
        count += arr.astype(int)
    tier = classify_tier(count)

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "lon": lon.tolist(),
        "lat": lat.tolist(),
        "count": count.tolist(),
        "tier": tier.tolist(),
        "tier_names": TIER_NAMES,
        "tier_thresholds": TIER_THRESHOLDS,
        "layers": list(LAYER_BUILDERS.keys()),
        # Per-layer boolean grids (not just the combined count/tier above) --
        # lets the dashboard let a user pick a subset of layers and recompute
        # count/tier client-side, instead of only ever showing the fixed
        # all-layers combination baked in here.
        "layer_grids": {name: arr.astype(int).tolist() for name, arr in layers.items()},
    }
    OUT_PATH.write_text(json.dumps(out))
    for name, idx in zip(TIER_NAMES[1:], [1, 2, 3]):
        frac = (tier == idx).mean()
        print(f"  {name}: {frac:.1%} of cells")
    print(f"wrote {OUT_PATH} ({n_lon}x{n_lat} grid, {len(LAYER_BUILDERS)} layers)")


if __name__ == "__main__":
    main()
