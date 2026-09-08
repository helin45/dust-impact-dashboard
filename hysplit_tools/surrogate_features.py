"""
Shared feature engineering for the HYSPLIT surrogate model -- used by both
surrogate_model.py (training, from real combined_raw.csv rows) and
surrogate_backtrack.py (inference, from a synthetic candidate grid), so the
two can never drift out of sync with each other (a classic way a trained
model silently stops matching what's fed to it at prediction time).

The model predicts concentration at a QUERY OFFSET from a release point
(delta_lat/delta_lon/distance/bearing), not at an absolute lat/lon -- that's
what lets one model serve release points anywhere in the region, not just
the ones it was trained on.
"""
import concurrent.futures as cf
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd


def _read_bounded(read_fn, timeout=20, attempts=2):
    """
    hysplit_results/ lives under OneDrive, whose file provider has been
    observed to evict a file back to cloud-only between one read and the
    next, then serve a truncated/empty read instead of raising -- a plain
    open() can come back with 0 bytes with no exception at all (this is
    what silently broke the susceptibility grid load below the first time).
    Each attempt gets its own ThreadPoolExecutor with shutdown(wait=False),
    not a "with" block (which would wait for a truly stalled thread on
    exit and undo the timeout) -- same fix used throughout this session's
    other OneDrive-backed readers (build_exact_wind_cache.py,
    surrogate_model.py, run_surrogate_comparison_batch.py).
    """
    last_err = None
    for attempt in range(attempts):
        ex = cf.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(read_fn)
        try:
            result = fut.result(timeout=timeout)
            ex.shutdown(wait=False)
            return result
        except cf.TimeoutError as e:
            ex.shutdown(wait=False)
            last_err = e
    raise TimeoutError(f"still unreadable after {attempts} attempt(s) of {timeout}s each") from last_err

FEATURE_COLS = [
    "release_lat", "release_lon", "release_alt_ft",
    "release_month_sin", "release_month_cos",
    "release_hour_sin", "release_hour_cos",
    "runtime_hours_abs", "period_frac",
    "delta_lat", "delta_lon", "distance_km", "bearing_sin", "bearing_cos",
    "wind_u_ms", "wind_v_ms", "wind_available",
    "cand_wind_u_ms", "cand_wind_v_ms", "cand_wind_available",
    "cand_susceptibility_tier",
]

# Precomputed by build_wind_climatology.py -- see that script's docstring
# for why this is a precomputed table rather than a live MERRA-2 lookup:
# a live lookup at inference time would cost the surrogate its "instant,
# offline, no extra credentials needed" property, since every prediction
# would then need Earthdata access too, not just training.
WIND_CLIMATOLOGY_PATH = Path(__file__).resolve().parent / "hysplit_results" / "wind_climatology.npz"
_WIND_CLIMATOLOGY_CACHE = None

# Precomputed by build_exact_wind_cache.py, one row per release point: the
# real MERRA-2 wind at that release point's own exact lat/lon/alt/time, not
# a pentad/grid average. Preferred over the climatology whenever a
# release_id is given and has an entry here, since it is the true value
# for that release point rather than a smoothed regional one. The
# climatology still covers every release point that has not gone through
# build_exact_wind_cache.py yet, or that has no release_id at all (a raw
# ad hoc lat/lon query).
EXACT_WIND_PATH = Path(__file__).resolve().parent / "hysplit_results" / "exact_wind_by_flight.csv"
_EXACT_WIND_CACHE = None

# Precomputed by build_susceptibility_grid.py -- a real, independent signal
# for WHERE dust actually tends to originate (soil type, land cover, WRB
# probabilities voted into a tier), rather than only geometry and
# meteorology. The query cell's own tier is looked up as a feature: unlike
# release point wind, this describes the destination of the query, not the
# origin, since it answers "is this candidate cell the kind of place dust
# comes from at all" independent of how the model thinks it got there.
SUSCEPTIBILITY_GRID_PATH = Path(__file__).resolve().parent / "hysplit_results" / "susceptibility_grid.json"
_SUSCEPTIBILITY_GRID_CACHE = None


def bearing_deg(lat0, lon0, lat1, lon1):
    """Initial compass bearing (degrees, 0-360) from point 0 to point 1."""
    lat0r, lat1r = np.radians(lat0), np.radians(lat1)
    dlon = np.radians(np.asarray(lon1, dtype=float) - np.asarray(lon0, dtype=float))
    y = np.sin(dlon) * np.cos(lat1r)
    x = np.cos(lat0r) * np.sin(lat1r) - np.sin(lat0r) * np.cos(lat1r) * np.cos(dlon)
    return (np.degrees(np.arctan2(y, x)) + 360) % 360


def haversine_km(lat0, lon0, lat1, lon1):
    r_earth = 6371.0
    lat0r, lat1r = np.radians(lat0), np.radians(lat1)
    dlat = np.radians(np.asarray(lat1, dtype=float) - np.asarray(lat0, dtype=float))
    dlon = np.radians(np.asarray(lon1, dtype=float) - np.asarray(lon0, dtype=float))
    a = np.sin(dlat / 2) ** 2 + np.cos(lat0r) * np.cos(lat1r) * np.sin(dlon / 2) ** 2
    return 2 * r_earth * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _pressure_hpa_from_alt_ft(alt_ft):
    """
    ICAO standard-atmosphere pressure -- matches merra2_utils.py's own
    _altitude_ft_to_pressure_hpa exactly (duplicated, not imported: that
    module needs earthaccess/xarray, optional deps this one shouldn't
    require just to look up an already-precomputed table). Used only to
    pick the nearest of the climatology's stored pressure levels.
    """
    alt_m = np.asarray(alt_ft, dtype=float) * 0.3048
    return 1013.25 * (1 - 2.25577e-5 * alt_m) ** 5.25588


def _load_wind_climatology():
    """None if build_wind_climatology.py hasn't been run yet -- wind_at_release()
    below falls back to (0, 0, wind_available=0) in that case, same
    degrade-gracefully contract as every other optional data source in
    this codebase (MERRA-2 validation, AOD overlay, etc)."""
    global _WIND_CLIMATOLOGY_CACHE
    if _WIND_CLIMATOLOGY_CACHE is None and WIND_CLIMATOLOGY_PATH.exists():
        def read_it():
            data = np.load(WIND_CLIMATOLOGY_PATH)
            return {k: data[k] for k in data.files}
        _WIND_CLIMATOLOGY_CACHE = _read_bounded(read_it)
    return _WIND_CLIMATOLOGY_CACHE


def _load_exact_wind():
    """
    {release_id: (wind_u_ms, wind_v_ms)} from build_exact_wind_cache.py's
    output, or {} if that script has not been run yet. release_id is
    "{flight_id}__{point_label}", the same per release point granularity
    combined_raw.csv already carries (one flight can have several release
    points at different times/altitudes, e.g. climb vs cruise vs descent,
    so a single wind value per flight would be too coarse). Same degrade
    gracefully contract as _load_wind_climatology(): missing file means an
    empty lookup, not an error, since a release point with no entry here
    should just fall back to the climatology instead of failing outright.
    """
    global _EXACT_WIND_CACHE
    if _EXACT_WIND_CACHE is None:
        if EXACT_WIND_PATH.exists():
            df = _read_bounded(lambda: pd.read_csv(EXACT_WIND_PATH, dtype={"release_id": str}))
            _EXACT_WIND_CACHE = {
                row["release_id"]: (float(row["wind_u_ms"]), float(row["wind_v_ms"]))
                for _, row in df.iterrows()
                if pd.notna(row["wind_u_ms"]) and pd.notna(row["wind_v_ms"])
            }
        else:
            _EXACT_WIND_CACHE = {}
    return _EXACT_WIND_CACHE


def _load_susceptibility_grid():
    """None if build_susceptibility_grid.py hasn't been run yet -- same
    degrade gracefully contract as the wind climatology: a missing grid
    means tier 0 (none) everywhere, not an error."""
    global _SUSCEPTIBILITY_GRID_CACHE
    if _SUSCEPTIBILITY_GRID_CACHE is None and SUSCEPTIBILITY_GRID_PATH.exists():
        def read_it():
            with open(SUSCEPTIBILITY_GRID_PATH) as f:
                return json.load(f)
        raw = _read_bounded(read_it)
        _SUSCEPTIBILITY_GRID_CACHE = {
            "lon": np.asarray(raw["lon"], dtype=float),
            "lat": np.asarray(raw["lat"], dtype=float),
            "tier": np.asarray(raw["tier"], dtype=float),  # [lat][lon], 0=none .. 3=high
        }
    return _SUSCEPTIBILITY_GRID_CACHE


def susceptibility_tier_at(lat, lon):
    """
    Nearest-cell susceptibility tier (0 none, 1 low, 2 medium, 3 high) for
    each (lat, lon), from build_susceptibility_grid.py's precomputed grid.
    Scalar in, scalar out; array in, array out, like every other lookup in
    this module. 0 wherever the grid hasn't been built yet or the point
    falls outside its coverage.
    """
    lat_in = np.asarray(lat, dtype=float)
    scalar_input = lat_in.ndim == 0
    lat_a = np.atleast_1d(lat_in)
    lon_a = np.atleast_1d(np.asarray(lon, dtype=float))

    grid = _load_susceptibility_grid()
    if grid is None:
        tier = np.zeros(lat_a.shape)
    else:
        lat_idx = np.argmin(np.abs(grid["lat"][None, :] - lat_a[:, None]), axis=1)
        lon_idx = np.argmin(np.abs(grid["lon"][None, :] - lon_a[:, None]), axis=1)
        tier = grid["tier"][lat_idx, lon_idx]

    return float(tier[0]) if scalar_input else tier


def _climatology_wind_at(lat, lon, alt_ft, day, hour):
    """
    Precomputed-climatology wind lookup only, no exact-wind cache, no
    warning -- the building block wind_at_release() below wraps with the
    exact-cache-first logic for release points, and build_features() also
    calls this directly for the query cell, which has no release_id/exact
    cache entry of its own (exact wind is a per-flight release-point
    observation, not something that exists for an arbitrary candidate
    cell). Always array in, array out; callers handle scalar unwrapping.
    """
    clim = _load_wind_climatology()
    if clim is None:
        return np.zeros(lat.shape), np.zeros(lat.shape), np.zeros(lat.shape)

    lats_ref, lons_ref = clim["lats"], clim["lons"]
    levels_ref, hours_ref = clim["pressure_levels_hpa"], clim["hours_utc"]
    n_pentads = clim["mean_u"].shape[0]

    pentad_idx = np.clip((day.astype(int) - 1) // 5, 0, n_pentads - 1)
    hour_idx = np.argmin(np.abs(hours_ref[None, :] - (hour[:, None] % 24)), axis=1)
    lat_idx = np.argmin(np.abs(lats_ref[None, :] - lat[:, None]), axis=1)
    lon_idx = np.argmin(np.abs(lons_ref[None, :] - lon[:, None]), axis=1)
    pressure = _pressure_hpa_from_alt_ft(alt_ft)
    lev_idx = np.argmin(np.abs(levels_ref[None, :] - pressure[:, None]), axis=1)

    u = clim["mean_u"][pentad_idx, hour_idx, lat_idx, lon_idx, lev_idx]
    v = clim["mean_v"][pentad_idx, hour_idx, lat_idx, lon_idx, lev_idx]
    count = clim["count"][pentad_idx, hour_idx, lat_idx, lon_idx, lev_idx]
    avail = ((count > 0) & ~np.isnan(u)).astype(float)
    u = np.where(avail > 0, u, 0.0)
    v = np.where(avail > 0, v, 0.0)
    return u, v, avail


def _warn_climatology_fallback(lat, lon, alt_ft, day, hour, release_id, fallback_mask):
    """
    Fires once per wind_at_release() call that used the climatology for at
    least one release point, not once per point, so a training run over
    thousands of pooled rows from the same handful of flights emits one
    readable message instead of a wall of repeats.
    """
    n_fallback = int(fallback_mask.sum())
    n_total = len(fallback_mask)
    idx = int(np.flatnonzero(fallback_mask)[0])
    point_desc = (
        f"lat {lat[idx]:.2f}, lon {lon[idx]:.2f}, alt {alt_ft[idx]:.0f} ft, "
        f"day of year {int(day[idx])}, hour {hour[idx]:.1f} UTC"
    )
    if release_id is not None:
        rid_arr = np.broadcast_to(np.atleast_1d(np.asarray(release_id, dtype=object)), lat.shape)
        example_rid = rid_arr[idx]
        detail = (
            f"no exact MERRA-2 wind cached for {n_fallback}/{n_total} release point(s) "
            f"in this call (example: {example_rid}, {point_desc}). Run "
            f"build_exact_wind_cache.py to add missing release points, or check "
            f"EARTHDATA_USERNAME/EARTHDATA_PASSWORD if that keeps failing."
        )
    else:
        detail = (
            f"no release_id was given for {n_fallback}/{n_total} release point(s) in this "
            f"call (example: {point_desc}), so no exact wind lookup was possible."
        )
    warnings.warn(f"surrogate wind: falling back to the climatology, {detail}", stacklevel=3)


def wind_at_release(release_lat, release_lon, release_alt_ft, release_day_of_year, release_hour,
                     release_id=None):
    """
    (wind_u_ms, wind_v_ms, wind_available) for each release point. Scalar
    in, scalar out; array in, array out, matching how every other
    release_* argument to build_features() broadcasts, since wind is a
    property of the release point alone, not the query cell.

    Tries the exact per-release-point wind first (see _load_exact_wind())
    when release_id is given, then falls back to the precomputed
    climatology for any release point that has neither a release_id nor a
    cached exact value. A fallback to the climatology emits a warning (see
    _warn_climatology_fallback()) with the count and an example, so a
    training run or a live query surfaces this rather than silently using
    a coarser value with no indication.

    wind_available is 0 (and u/v are 0, not NaN, so the model sees a
    consistent numeric value rather than needing NaN handling) wherever
    neither source has a value for that release point. The model can learn
    to weight wind_u_ms/wind_v_ms down when wind_available is 0.
    """
    lat_in = np.asarray(release_lat, dtype=float)
    scalar_input = lat_in.ndim == 0
    lat = np.atleast_1d(lat_in)
    lon = np.atleast_1d(np.asarray(release_lon, dtype=float))
    alt_ft = np.broadcast_to(np.atleast_1d(np.asarray(release_alt_ft, dtype=float)), lat.shape)
    day = np.broadcast_to(np.atleast_1d(np.asarray(release_day_of_year, dtype=float)), lat.shape)
    hour = np.broadcast_to(np.atleast_1d(np.asarray(release_hour, dtype=float)), lat.shape)

    exact_u = np.full(lat.shape, np.nan)
    exact_v = np.full(lat.shape, np.nan)
    if release_id is not None:
        exact_cache = _load_exact_wind()
        rid_arr = np.broadcast_to(np.atleast_1d(np.asarray(release_id, dtype=object)), lat.shape)
        for i, rid in enumerate(rid_arr):
            hit = exact_cache.get(str(rid))
            if hit is not None:
                exact_u[i], exact_v[i] = hit
    has_exact = ~np.isnan(exact_u)

    clim_u, clim_v, clim_avail = _climatology_wind_at(lat, lon, alt_ft, day, hour)

    fallback_mask = ~has_exact
    if fallback_mask.any():
        _warn_climatology_fallback(lat, lon, alt_ft, day, hour, release_id, fallback_mask)

    u = np.where(has_exact, exact_u, clim_u)
    v = np.where(has_exact, exact_v, clim_v)
    avail = np.where(has_exact, 1.0, clim_avail)

    if scalar_input:
        return float(u[0]), float(v[0]), float(avail[0])
    return u, v, avail


def wind_at_cell(cand_lat, cand_lon, alt_ft, day, hour):
    """
    Climatology-only wind at a query cell, using the release point's own
    altitude/day/hour (the transport event's timing, not the query cell's,
    since a candidate cell has no time of its own). This describes typical
    conditions AT the destination rather than the origin, which
    wind_at_release() already covers -- together the two let the model
    reason about whether flow at the release point is even consistent with
    reaching this candidate cell, not just what the release point's own
    weather was.

    No exact-per-flight equivalent exists for an arbitrary candidate cell
    (the exact-wind cache is keyed to real release points only), so this is
    always the climatology, with no fallback warning: climatology being the
    only source here is the normal case, not a per-call anomaly worth
    flagging the way a release point missing its exact wind is.

    Scalar in, scalar out; array in, array out, matching wind_at_release().
    """
    lat_in = np.asarray(cand_lat, dtype=float)
    scalar_input = lat_in.ndim == 0
    lat = np.atleast_1d(lat_in)
    lon = np.atleast_1d(np.asarray(cand_lon, dtype=float))
    alt_ft_b = np.broadcast_to(np.atleast_1d(np.asarray(alt_ft, dtype=float)), lat.shape)
    day_b = np.broadcast_to(np.atleast_1d(np.asarray(day, dtype=float)), lat.shape)
    hour_b = np.broadcast_to(np.atleast_1d(np.asarray(hour, dtype=float)), lat.shape)

    u, v, avail = _climatology_wind_at(lat, lon, alt_ft_b, day_b, hour_b)

    if scalar_input:
        return float(u[0]), float(v[0]), float(avail[0])
    return u, v, avail


def build_features(release_lat, release_lon, release_alt_ft, release_month, release_hour,
                    runtime_hours, period_frac, cand_lat, cand_lon, release_day_of_year,
                    release_id=None):
    """
    Every argument broadcasts against the others via pandas/numpy, so callers
    can pass a mix of scalars (one fixed release point, inference over many
    candidate cells) and equal-length arrays (one row per training example,
    each with its own release point). release_month is 1-12, release_hour is
    a fractional UTC hour (0-24); both are cyclic-encoded (sin/cos) so e.g.
    December and January, or 23:00 and 01:00, read as close together instead
    of far apart on a raw numeric scale. release_day_of_year (1-365/366) is
    used for the wind lookup: the exact per-release-point cache is keyed by
    day, and the climatology fallback's pentad lookup needs it too, since
    month/hour alone don't carry enough resolution for either.

    release_id (optional, scalar or array matching release_lat's shape,
    "{flight_id}__{point_label}") is passed straight through to
    wind_at_release() so it can prefer that release point's own exact
    MERRA-2 wind over the climatology when available. Omit it for an ad hoc
    query with no known release point (e.g. a raw lat/lon from
    run_surrogate.py's CLI); the climatology is used for those, with a
    warning explaining why.

    Returns a DataFrame with exactly FEATURE_COLS as columns, in that order,
    ready to hand to the model's .predict()/.fit().
    """
    release_lat = np.asarray(release_lat, dtype=float)
    release_lon = np.asarray(release_lon, dtype=float)
    cand_lat = np.asarray(cand_lat, dtype=float)
    cand_lon = np.asarray(cand_lon, dtype=float)

    month_angle = 2 * np.pi * (np.asarray(release_month, dtype=float) - 1) / 12
    hour_angle = 2 * np.pi * np.asarray(release_hour, dtype=float) / 24
    bearing = np.radians(bearing_deg(release_lat, release_lon, cand_lat, cand_lon))
    distance_km = haversine_km(release_lat, release_lon, cand_lat, cand_lon)
    wind_u, wind_v, wind_available = wind_at_release(
        release_lat, release_lon, release_alt_ft, release_day_of_year, release_hour,
        release_id=release_id,
    )
    cand_wind_u, cand_wind_v, cand_wind_available = wind_at_cell(
        cand_lat, cand_lon, release_alt_ft, release_day_of_year, release_hour,
    )
    cand_susceptibility_tier = susceptibility_tier_at(cand_lat, cand_lon)

    df = pd.DataFrame({
        "release_lat": release_lat, "release_lon": release_lon, "release_alt_ft": release_alt_ft,
        "release_month_sin": np.sin(month_angle), "release_month_cos": np.cos(month_angle),
        "release_hour_sin": np.sin(hour_angle), "release_hour_cos": np.cos(hour_angle),
        "runtime_hours_abs": np.abs(np.asarray(runtime_hours, dtype=float)), "period_frac": period_frac,
        "delta_lat": cand_lat - release_lat, "delta_lon": cand_lon - release_lon,
        "distance_km": distance_km, "bearing_sin": np.sin(bearing), "bearing_cos": np.cos(bearing),
        "wind_u_ms": wind_u, "wind_v_ms": wind_v, "wind_available": wind_available,
        "cand_wind_u_ms": cand_wind_u, "cand_wind_v_ms": cand_wind_v,
        "cand_wind_available": cand_wind_available,
        "cand_susceptibility_tier": cand_susceptibility_tier,
    })
    return df[FEATURE_COLS]
