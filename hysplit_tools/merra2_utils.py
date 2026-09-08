"""
NASA MERRA-2 reanalysis dust-extinction lookups, used by the dashboard's
"Validate against MERRA-2" panel (dust source attribution tab) to
corroborate/validate HYSPLIT release points two ways, both against the same
variable (DUEXTTAU -- dust extinction AOD at 550nm, from the M2T1NXAER /
M2TMNXAER "aer" diagnostic collections) so they're directly comparable:

  - event corroboration: MERRA-2's own modeled dust loading at the release
    point's real lat/lon/hour -- does an independent transport model also
    see elevated dust there at that time?
  - source climatology: how that same location's DUEXTTAU compares to its
    own multi-year (default 2003-2022) climatological distribution for that
    calendar month -- is this an unusually dusty event, or normal for the
    place and season?

Requires a free NASA Earthdata Login account
(https://urs.earthdata.nasa.gov/users/new) with its username/password
exported as EARTHDATA_USERNAME / EARTHDATA_PASSWORD. Uses NASA's own
`earthaccess` client (https://earthaccess.readthedocs.io/) for auth, search
and remote xarray access against GES DISC, rather than hand-rolled OPeNDAP
constraint URLs.

Not imported at module load time by app.py's request path -- every
dashboard endpoint that uses this imports it lazily inside a try/except, so
a machine without `earthaccess`/`xarray` installed (or without credentials)
degrades to a "not configured" response instead of failing to start.

Not verified against a live Earthdata account during development (no
credentials were available on the build machine) -- if GES DISC's actual
granule/variable layout differs from what's assumed here, the exceptions
this raises should make that obvious rather than silently returning wrong
numbers. Anything that fails is caught by the caller per-point (see
app.py's /api/flight/{flight_id}/merra2_validation), so one bad lookup
doesn't blank the whole response.
"""

import os

import numpy as np

HOURLY_SHORT_NAME = "M2T1NXAER"   # tavg1_2d_aer_Nx -- hourly aerosol diagnostics
MONTHLY_SHORT_NAME = "M2TMNXAER"  # tavgM_2d_aer_Nx -- monthly-mean aerosol diagnostics
VARIABLE = "DUEXTTAU"             # dust extinction AOT, 550nm -- present in both collections, unitless

# Wind collections, verified to exist against NASA's public CMR (metadata
# search needs no auth; the actual granule data does) during development --
# short names and variable names not otherwise exercised against a live
# Earthdata account, same caveat as the DUEXTTAU lookups above.
WIND_PRESSURE_SHORT_NAME = "M2I3NPASM"  # inst3_3d_asm_Np -- 3-hourly instantaneous, 42 pressure levels
WIND_SURFACE_SHORT_NAME = "M2T1NXSLV"   # tavg1_2d_slv_Nx -- hourly single-level diagnostics
U_VAR, V_VAR = "U", "V"                 # eastward/northward wind, m/s, on M2I3NPASM's `lev` (hPa) dimension
U50M_VAR, V50M_VAR = "U50M", "V50M"     # 50m eastward/northward wind, m/s, on M2T1NXSLV (no `lev` dimension)

# MERRA-2's own record starts 1980; capped to a more computationally
# reasonable ~20-year window for the per-point climatology fetch (up to one
# granule search + open per year, per release point).
DEFAULT_CLIMATOLOGY_YEARS = range(2003, 2023)


def credentials_configured():
    return bool(os.environ.get("EARTHDATA_USERNAME")) and bool(os.environ.get("EARTHDATA_PASSWORD"))


def _login():
    import earthaccess
    auth = earthaccess.login(strategy="environment")
    if not auth.authenticated:
        raise RuntimeError(
            "NASA Earthdata login failed -- check EARTHDATA_USERNAME/EARTHDATA_PASSWORD "
            "(https://urs.earthdata.nasa.gov/users/new)"
        )
    return auth


def _open_point_dataset(short_name, lat, lon, temporal, pad_deg=0.75):
    """
    Searches for the granule(s) covering `temporal` (an (start, end)
    ISO-date-string pair) intersecting a small box around (lat, lon), and
    opens the first match as a lazy xarray Dataset via earthaccess -- no
    full file downloaded, just the point selected out of it below.
    """
    import earthaccess
    import xarray as xr
    results = earthaccess.search_data(
        short_name=short_name,
        temporal=temporal,
        bounding_box=(lon - pad_deg, lat - pad_deg, lon + pad_deg, lat + pad_deg),
    )
    if not results:
        return None
    files = earthaccess.open(results[:1])
    return xr.open_dataset(files[0])


def _nearest_value(ds, variable, lat, lon, when_utc=None):
    da = ds[variable]
    sel = da.sel(lat=lat, lon=lon, method="nearest")
    if when_utc is not None and "time" in sel.dims:
        sel = sel.sel(time=np.datetime64(when_utc.replace(tzinfo=None)), method="nearest")
    value = float(sel.values)
    actual_time = str(sel["time"].values) if "time" in sel.coords else None
    return value, actual_time


def event_value(lat, lon, when_utc):
    """MERRA-2 DUEXTTAU at (lat, lon), nearest hour to `when_utc` (tz-aware datetime)."""
    _login()
    day = when_utc.strftime("%Y-%m-%d")
    ds = _open_point_dataset(HOURLY_SHORT_NAME, lat, lon, temporal=(day, day))
    if ds is None:
        raise RuntimeError(f"no {HOURLY_SHORT_NAME} granule found for {day} near ({lat:.2f}, {lon:.2f})")
    value, actual_time = _nearest_value(ds, VARIABLE, lat, lon, when_utc)
    return {"value": value, "time_utc": actual_time}


def climatology(lat, lon, month, years=DEFAULT_CLIMATOLOGY_YEARS):
    """
    Monthly-mean DUEXTTAU at (lat, lon) for calendar `month` (1-12), one
    value per year in `years` -- each from that single year's own
    M2TMNXAER granule for that month. Returns the per-year values plus
    their mean/std, so the caller can compute a percentile against an event
    value without re-fetching.
    """
    _login()
    values = []
    for year in years:
        temporal = (f"{year}-{month:02d}-01", f"{year}-{month:02d}-28")
        try:
            ds = _open_point_dataset(MONTHLY_SHORT_NAME, lat, lon, temporal=temporal)
            if ds is None:
                continue
            value, _ = _nearest_value(ds, VARIABLE, lat, lon)
            values.append(value)
        except Exception:
            continue  # one missing/bad year shouldn't sink the whole climatology
    if not values:
        raise RuntimeError(f"no {MONTHLY_SHORT_NAME} granules found for month={month} near ({lat:.2f}, {lon:.2f})")
    arr = np.array(values)
    return {"n_years": len(values), "mean": float(arr.mean()), "std": float(arr.std()), "values": arr.tolist()}


def validate_point(lat, lon, when_utc, years=DEFAULT_CLIMATOLOGY_YEARS):
    """Combines event_value() + climatology() into the one per-point result the dashboard renders."""
    event = event_value(lat, lon, when_utc)
    clim = climatology(lat, lon, when_utc.month, years=years)
    percentile = None
    if clim["n_years"] >= 2:
        percentile = float((np.array(clim["values"]) < event["value"]).mean() * 100)
    return {
        "event_value": event["value"],
        "event_time_utc": event["time_utc"],
        "climatology_mean": clim["mean"],
        "climatology_std": clim["std"],
        "climatology_n_years": clim["n_years"],
        "climatology_percentile": percentile,
        "variable": VARIABLE,
    }


# ---------------------------------------------------------------------------
# Wind vectors -- corroborates a HYSPLIT release point's implied transport
# direction against an independent reanalysis's own wind field, rather than
# just its modeled dust loading (event_value/climatology above). HYSPLIT's
# own trajectories are driven by whichever met dataset flight_backtrack.py
# was configured with (typically GDAS), not MERRA-2 -- so agreement here is
# a genuine independent cross-check, not circular.
# ---------------------------------------------------------------------------

def _altitude_ft_to_pressure_hpa(alt_ft):
    """
    ICAO standard-atmosphere pressure for a given altitude, used only to
    pick the nearest of M2I3NPASM's 42 fixed pressure levels to a release
    point's alt_ft -- not a precise pressure reading for that point, just
    close enough to select the right level. Valid in the troposphere
    (< ~11km/36000ft); release points above that (rare -- most CoreDust
    ingestion happens at CRUISE/CLIMB/DESCENT well within this range) get
    the same formula extrapolated past its strict validity, a minor
    approximation given pressure levels are already a coarse-grained pick.
    """
    alt_m = alt_ft * 0.3048
    return 1013.25 * (1 - 2.25577e-5 * alt_m) ** 5.25588


def _wind_direction_from_deg(u, v):
    """
    Meteorological convention: the compass direction the wind is blowing
    FROM (0=N, 90=E, 180=S, 270=W), given the eastward (u) and northward (v)
    components of the direction it's blowing TOWARD.
    """
    return float(np.degrees(np.arctan2(-u, -v)) % 360)


def open_wind_dataset(lat, lon, day):
    """
    Opens the one M2I3NPASM granule covering `day` (MERRA-2's daily files
    are global, so every release point on the same day shares one granule)
    for reuse across many wind_at_point() calls. Callers with several
    points on the same day should open once and pass `ds` through, rather
    than let wind_at_point() do its own search+open per point -- each
    search+open is its own round of CMR search + auth/redirect handshakes,
    pure overhead when it's fetching the same file every time.
    """
    _login()
    ds = _open_point_dataset(WIND_PRESSURE_SHORT_NAME, lat, lon, temporal=(day, day))
    if ds is None:
        raise RuntimeError(f"no {WIND_PRESSURE_SHORT_NAME} granule found for {day} near ({lat:.2f}, {lon:.2f})")
    return ds


def wind_at_point(lat, lon, alt_ft, when_utc, ds=None):
    """
    MERRA-2 U/V wind (m/s) at (lat, lon), the pressure level nearest alt_ft,
    and the 3-hourly instant nearest when_utc. Returns the raw components
    plus derived speed and from-direction, so a release point's wind can be
    compared against the bearing from it back toward the modeled source
    region: if they roughly agree, an independent reanalysis's wind field
    supports the direction HYSPLIT's own (different) met data implied.

    Pass an already-open `ds` (from open_wind_dataset) when looking up
    multiple points for the same day -- see open_wind_dataset's docstring.
    Opens its own if omitted, for standalone single-point use.
    """
    if ds is None:
        ds = open_wind_dataset(lat, lon, when_utc.strftime("%Y-%m-%d"))

    pressure_hpa = _altitude_ft_to_pressure_hpa(alt_ft)
    u_sel = ds[U_VAR].sel(lat=lat, lon=lon, lev=pressure_hpa, method="nearest")
    v_sel = ds[V_VAR].sel(lat=lat, lon=lon, lev=pressure_hpa, method="nearest")
    if "time" in u_sel.dims:
        target_time = np.datetime64(when_utc.replace(tzinfo=None))
        u_sel = u_sel.sel(time=target_time, method="nearest")
        v_sel = v_sel.sel(time=target_time, method="nearest")
    u, v = float(u_sel.values), float(v_sel.values)
    return {
        "u_ms": u, "v_ms": v,
        "speed_ms": float(np.hypot(u, v)),
        "direction_from_deg": _wind_direction_from_deg(u, v),
        "pressure_hpa": float(u_sel["lev"].values),
        "requested_alt_ft": alt_ft,
        "time_utc": str(u_sel["time"].values) if "time" in u_sel.coords else None,
    }


def surface_wind_at_point(lat, lon, when_utc):
    """
    MERRA-2 50m wind (U50M/V50M, m/s) at (lat, lon), nearest hour to
    when_utc -- a lighter single-level lookup (no pressure-level dimension
    to select) for checking whether near-surface wind speed at a modeled
    source location was plausibly strong enough for dust uplift, independent
    of any particular release point's own altitude. ~8-10 m/s is a commonly
    cited rough threshold for loose desert soils, not a precise physical
    constant -- actual uplift thresholds vary with soil type and moisture.
    """
    _login()
    day = when_utc.strftime("%Y-%m-%d")
    ds = _open_point_dataset(WIND_SURFACE_SHORT_NAME, lat, lon, temporal=(day, day))
    if ds is None:
        raise RuntimeError(f"no {WIND_SURFACE_SHORT_NAME} granule found for {day} near ({lat:.2f}, {lon:.2f})")
    u, time_utc = _nearest_value(ds, U50M_VAR, lat, lon, when_utc)
    v, _ = _nearest_value(ds, V50M_VAR, lat, lon, when_utc)
    return {
        "u_ms": u, "v_ms": v,
        "speed_ms": float(np.hypot(u, v)),
        "direction_from_deg": _wind_direction_from_deg(u, v),
        "time_utc": time_utc,
    }
