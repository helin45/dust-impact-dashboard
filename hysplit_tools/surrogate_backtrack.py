"""
Fast surrogate stand-in for backtrack.run_backtrack_gif(): instead of running
real HYSPLIT (hycs_std, which needs a local HYSPLIT install, GDAS met-data
downloads, and real wall-clock time per release point), evaluates the
regressor trained by surrogate_model.py over a candidate grid of (lon, lat,
period) around the release point and writes the same raw_data.csv shape a
real run produces (period, DAY, HR, LAT, LON, Dust00100), plus a matching
meta.json. That means flight_backtrack.py's existing
postprocess_results.process_index() and build_flight_density() consume this
completely unchanged -- the swap only happens at this one function.

IMPORTANT: this is a fast APPROXIMATION, not a physics simulation. It has
never seen this specific flight's actual meteorology (GDAS wind fields) --
only the climatological pattern implicit in whichever flights already have a
real HYSPLIT run (see surrogate_model.py's docstring). Treat its output as
"where dust from a release point like this one has tended to come from",
not "where this flight's dust specifically came from". Every result it
writes is tagged "surrogate": true in meta.json (and folded into
density_grid.json by flight_backtrack.build_flight_density) so the dashboard
can label it accordingly rather than presenting it as an equal alternative
to a real run.

Needs scikit-learn + joblib + a trained model at
hysplit_results/surrogate_model.joblib (run `python surrogate_model.py`
first -- this module only reads the trained model file, it doesn't train).
"""
import json
import os
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import surrogate_features

DUST_FILES_DIR = Path(__file__).resolve().parent
RESULTS_DIR = DUST_FILES_DIR / "hysplit_results"
MODEL_PATH = RESULTS_DIR / "surrogate_model.joblib"

# Matches backtrack.py's DEFAULT_SPECIES/DEFAULT_PARTICLE_DIAMETER_UM/
# DEFAULT_PARTICLE_DENSITY -- duplicated as plain constants here (rather than
# importing backtrack.py) so this module has zero dependency on anything
# HYSPLIT-machine-specific; it should work on a laptop with no HYSPLIT
# install at all.
DEFAULT_SPECIES = "Dust"
DEFAULT_PARTICLE_DIAMETER_UM = 2.5
DEFAULT_PARTICLE_DENSITY = 2.5

# How far out (degrees) around the release point to evaluate candidate cells,
# scaled by backward runtime -- a 120h backward run can plausibly trace to
# somewhere much farther away than a 24h one. ~0.6 deg/day is a generous
# multiple of typical mid-level wind speeds (~10-15 m/s -> ~1.5-2.5 deg/day
# of travel), deliberately erring wide: a candidate cell the model scores
# near-zero costs nothing (KEEP_THRESHOLD below drops it), whereas too tight
# a box would silently clip off a real plume the model wanted to place
# outside it.
DEG_PER_DAY = 0.6
MIN_RADIUS_DEG = 5.0
GRID_RES_DEG = 0.25  # matches density_utils.build_density_grid's own default cell size
# Candidate cells scored below this are dropped before writing raw_data.csv,
# mirroring the real pipeline's own sparsity (con2asc only reports nonzero
# cells) -- keeps the surrogate's output file small instead of one row per
# candidate cell regardless of how negligible.
KEEP_THRESHOLD = 1e-18

_MODEL_CACHE = None
_MODEL_CACHE_MTIME = None


def available():
    """
    Cheap existence check the dashboard can call to decide whether to offer
    the surrogate option at all, without importing sklearn/joblib (both
    optional dependencies -- see requirements.txt).
    """
    return MODEL_PATH.exists()


def model_info():
    """Small summary for the dashboard's surrogate-status panel, or None if not trained yet."""
    if not available():
        return None
    bundle = _load_model()
    return {
        "n_training_rows": bundle.get("n_training_rows"),
        "n_training_flights": bundle.get("n_training_flights"),
        "cv_mae": bundle.get("cv_mae"),
        "cv_mae_std": bundle.get("cv_mae_std"),
        "cv_rmse": bundle.get("cv_rmse"),
        "cv_rmse_std": bundle.get("cv_rmse_std"),
        "cv_r2": bundle.get("cv_r2"),
        "cv_r2_std": bundle.get("cv_r2_std"),
        "trained_utc": bundle.get("trained_utc"),
    }


def _load_model():
    # Cached by mtime, not just loaded once -- the dashboard's own process
    # stays running for hours/days while surrogate_model.py retrains in a
    # SEPARATE subprocess (see app.py's /api/surrogate/retrain), so a plain
    # "load once" cache would keep serving a stale model forever after a
    # retrain, even though a genuinely new .joblib is sitting on disk.
    global _MODEL_CACHE, _MODEL_CACHE_MTIME
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"no trained surrogate model at {MODEL_PATH} -- run `python surrogate_model.py` first"
        )
    mtime = MODEL_PATH.stat().st_mtime
    if _MODEL_CACHE is None or mtime != _MODEL_CACHE_MTIME:
        import joblib
        _MODEL_CACHE = joblib.load(MODEL_PATH)
        _MODEL_CACHE_MTIME = mtime
    return _MODEL_CACHE


def _build_candidate_grid(lat, lon, runtime_hours, n_periods):
    days = abs(runtime_hours) / 24
    radius = max(MIN_RADIUS_DEG, DEG_PER_DAY * days)
    lon_vals = np.arange(lon - radius, lon + radius, GRID_RES_DEG)
    lat_vals = np.arange(lat - radius, lat + radius, GRID_RES_DEG)
    grid_lon, grid_lat = np.meshgrid(lon_vals, lat_vals)
    grid_lon, grid_lat = grid_lon.ravel(), grid_lat.ravel()

    n_cells = len(grid_lon)
    periods = np.arange(1, n_periods + 1)
    cand_lon = np.tile(grid_lon, n_periods)
    cand_lat = np.tile(grid_lat, n_periods)
    cand_period = np.repeat(periods, n_cells)
    return cand_lat, cand_lon, cand_period


def run_backtrack_surrogate(lat, lon, height_m, start, runtime_hours, result_dir,
                             species=DEFAULT_SPECIES,
                             particle_diameter_um=DEFAULT_PARTICLE_DIAMETER_UM,
                             particle_density=DEFAULT_PARTICLE_DENSITY,
                             gif_frame_ms=None, map_radius_km=None,
                             extra_meta=None):
    """
    Signature matches backtrack.run_backtrack_gif() -- including accepting
    (and ignoring) gif_frame_ms/map_radius_km -- so flight_backtrack.py's
    run_flight() can call either interchangeably. No GIF is rendered: there's
    no physical time-stepped simulation here to animate.

    Returns the same meta dict shape run_backtrack_gif() does, plus
    "surrogate": True so downstream consumers (and the dashboard) can tell
    which kind of result they're looking at.
    """
    bundle = _load_model()
    model, feature_cols = bundle["model"], bundle["feature_cols"]
    log_floor, log_ceil = bundle["target_log_range"]

    alt_ft = height_m / 0.3048
    n_periods = max(1, round(abs(runtime_hours) / 24))
    # flight_id/point_label live in extra_meta (flight_backtrack.py's
    # run_flight() passes them there so this function's signature stays
    # identical to backtrack.run_backtrack_gif()'s, letting run_flight()
    # call either interchangeably), not as their own top level arguments.
    # release_id is None when either is missing, e.g. an ad hoc query from
    # run_surrogate.py's CLI with no known flight; build_features()/
    # wind_at_release() fall back to the climatology for those, with a
    # warning.
    meta = extra_meta or {}
    release_id = None
    if meta.get("flight_id") and meta.get("point_label"):
        release_id = f"{meta['flight_id']}__{meta['point_label']}"

    cand_lat, cand_lon, cand_period = _build_candidate_grid(lat, lon, runtime_hours, n_periods)
    features = surrogate_features.build_features(
        release_lat=lat, release_lon=lon, release_alt_ft=alt_ft,
        release_month=start.month, release_hour=start.hour + start.minute / 60,
        runtime_hours=runtime_hours, period_frac=cand_period / n_periods,
        cand_lat=cand_lat, cand_lon=cand_lon,
        release_day_of_year=start.timetuple().tm_yday,
        release_id=release_id,
    )[feature_cols]
    pred_log = np.clip(model.predict(features), log_floor, log_ceil)
    concentration = 10 ** pred_log

    keep = concentration >= KEEP_THRESHOLD
    os.makedirs(result_dir, exist_ok=True)
    kept_period = cand_period[keep]
    raw = pd.DataFrame({
        "period": kept_period,
        # DAY/HR aren't read by anything downstream (build_flight_density
        # only uses LAT/LON/Dust*/bin_total_dust_g) -- these exist purely so
        # raw_data.csv keeps the same columns a real run's file has, in case
        # something inspects them directly. "roughly this many days back
        # from release, same hour of day" is close enough for that.
        "DAY": [(start - timedelta(hours=24 * (int(p) - 1))).timetuple().tm_yday for p in kept_period],
        "HR": [start.hour] * int(keep.sum()),
        "LAT": np.round(cand_lat[keep], 2),
        "LON": np.round(cand_lon[keep], 2),
        "Dust00100": concentration[keep],
    })
    raw_csv_path = os.path.join(result_dir, "raw_data.csv")
    raw.to_csv(raw_csv_path, index=False)

    run_label = os.path.relpath(result_dir, RESULTS_DIR).replace(os.sep, "__")
    meta = {
        "run_label": run_label,
        "lat": lat, "lon": lon, "height_m": height_m,
        "start_utc": start.isoformat(), "runtime_hours": runtime_hours,
        "species": species, "particle_diameter_um": particle_diameter_um, "particle_density": particle_density,
        "total_periods": n_periods, "empty_periods": [],
        "gif": None, "raw_csv": raw_csv_path, "surrogate": True,
        "n_candidate_cells": int(len(cand_lat)), "n_kept_cells": int(keep.sum()),
        **(extra_meta or {}),
    }
    with open(os.path.join(result_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2, default=str)
    return meta
