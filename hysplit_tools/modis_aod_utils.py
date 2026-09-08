"""
Gridded MODIS/Terra Aerosol Optical Depth (AOD), used by the dashboard's
"% agreement with AOD" metric to quantify how much a flight's HYSPLIT
source-density result spatially overlaps with regions of elevated,
satellite-*retrieved* dust/aerosol loading -- a genuinely independent check
against real observations, distinct from merra2_utils.py's *modeled*
reanalysis comparison.

Source: MOD08_D3 v6.1 (MODIS/Terra Aerosol Cloud Water Vapor Ozone Daily L3
Global 1Deg CMG), variable Aerosol_Optical_Depth_Land_Ocean_Mean -- both
confirmed to exist via NASA's public CMR collection search during
development (https://cmr.earthdata.nasa.gov/search/collections.json?short_name=MOD08_D3),
but the actual fetch/regrid logic below has not been exercised against a
live Earthdata account (no credentials were available on the build
machine). Requires the same NASA Earthdata Login as merra2_utils.py
(EARTHDATA_USERNAME / EARTHDATA_PASSWORD) -- reuses that module's
credentials_configured()/_login() rather than duplicating them.

Not imported at module load time by app.py -- see merra2_utils.py's module
docstring for the same lazy-import/degrade-gracefully rationale.
"""

import numpy as np

import merra2_utils

SHORT_NAME = "MOD08_D3"
VERSION = "6.1"
VARIABLE = "Aerosol_Optical_Depth_Land_Ocean_Mean"

# Delegate credential checking to merra2_utils -- same Earthdata Login
# powers both, no separate signup/config for this feature.
credentials_configured = merra2_utils.credentials_configured


def fetch_aod_grid(lon_min, lon_max, lat_min, lat_max, date_str, pad_deg=1.0):
    """
    Fetches MOD08_D3's 1x1-degree gridded AOD for one UTC date (YYYY-MM-DD),
    subset to the given bbox. Returns (lon_1d, lat_1d, aod_2d) with
    aod_2d[i, j] at (lat_1d[i], lon_1d[j]), or None if no granule covers
    that date. NaN marks cells with no valid retrieval (cloud, sun-glint,
    some bright desert surfaces) -- MOD08_D3's fill value.
    """
    merra2_utils._login()
    import earthaccess
    import xarray as xr

    results = earthaccess.search_data(
        short_name=SHORT_NAME, version=VERSION,
        temporal=(date_str, date_str),
        bounding_box=(lon_min - pad_deg, lat_min - pad_deg, lon_max + pad_deg, lat_max + pad_deg),
    )
    if not results:
        return None
    files = earthaccess.open(results[:1])
    ds = xr.open_dataset(files[0])

    sub = ds[VARIABLE].sel(
        YDim=slice(lat_max + pad_deg, lat_min - pad_deg),  # MOD08_D3's YDim runs north (90) to south (-90)
        XDim=slice(lon_min - pad_deg, lon_max + pad_deg),
    )
    lon = sub["XDim"].values.astype(float)
    lat = sub["YDim"].values.astype(float)
    aod = sub.values.astype(float)
    aod = np.where(aod < 0, np.nan, aod)  # negative fill values (e.g. -9999, already scaled) -> no retrieval
    return lon, lat, aod


def compute_agreement(density_lon, density_lat, density_grid, aod_lon, aod_lat, aod_grid,
                       density_percentile=75, aod_threshold=0.3):
    """
    Regrids the coarser (~1 deg) AOD field onto the HYSPLIT density grid's
    own (~0.25 deg) lon/lat axes via nearest-neighbor -- downsampling the
    density grid instead would throw away most of HYSPLIT's spatial detail
    for no benefit -- thresholds both into binary masks, and returns
    overlap stats between them:

      - hysplit_mask: density cells at/above `density_percentile` of THIS
        flight's own density distribution ("HYSPLIT's modeled source
        region" for this specific result).
      - aod_mask: AOD cells at/above the fixed `aod_threshold` (0.3 is a
        commonly used elevated-dust/aerosol cutoff in the literature) --
        fixed rather than percentile-based, since it needs to mean the
        same physical thing across different flights/dates to be
        comparable.

    Jaccard (intersection/union) is the primary agreement number; the two
    directional percentages are included since they answer different
    questions ("of what HYSPLIT flagged, how much did AOD also see" vs.
    "of what AOD saw, how much did HYSPLIT also flag") and a single
    combined number can hide a lopsided result.
    """
    density_grid = np.asarray(density_grid, dtype=float)
    aod_grid = np.asarray(aod_grid, dtype=float)
    density_lon = np.asarray(density_lon, dtype=float)
    density_lat = np.asarray(density_lat, dtype=float)

    lon_idx = np.abs(np.subtract.outer(density_lon, aod_lon)).argmin(axis=1)
    lat_idx = np.abs(np.subtract.outer(density_lat, aod_lat)).argmin(axis=1)
    aod_on_density_grid = aod_grid[np.ix_(lat_idx, lon_idx)]

    valid = ~np.isnan(aod_on_density_grid)
    if not valid.any():
        return {"error": "no valid AOD retrievals over this flight's density-grid extent"}

    density_cut = np.nanpercentile(density_grid, density_percentile)
    hysplit_mask = (density_grid >= density_cut) & valid
    aod_mask = (aod_on_density_grid >= aod_threshold) & valid

    intersection = int(np.sum(hysplit_mask & aod_mask))
    union = int(np.sum(hysplit_mask | aod_mask))
    n_hysplit = int(np.sum(hysplit_mask))
    n_aod = int(np.sum(aod_mask))

    return {
        "n_valid_cells": int(valid.sum()),
        "n_hysplit_cells": n_hysplit,
        "n_aod_cells": n_aod,
        "intersection_cells": intersection,
        "jaccard_pct": (100 * intersection / union) if union else None,
        "pct_hysplit_in_aod": (100 * intersection / n_hysplit) if n_hysplit else None,
        "pct_aod_in_hysplit": (100 * intersection / n_aod) if n_aod else None,
        "density_percentile": density_percentile,
        "aod_threshold": aod_threshold,
    }
