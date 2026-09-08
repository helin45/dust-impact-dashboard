"""
Shared spatial-density plotting logic, used by probability_map.py,
probability_maps_by_sigma.py, and probability_map_app.py -- kept in one
place so the KDE/coastline code isn't duplicated across scripts.
"""

import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde

import backtrack

# Same coastline file concplot itself uses -- derived from backtrack.HYSPLIT_DIR
# (already picked per-OS there) rather than hardcoded, so this resolves
# correctly on whichever machine actually has HYSPLIT installed.
ARLMAP_PATH = os.path.join(backtrack.HYSPLIT_DIR, "graphics", "arlmap")


def read_arlmap(path=ARLMAP_PATH):
    """
    Parses HYSPLIT's ARL map format: repeating blocks of
      header line: "<level> <npoints>"
      npoints latitudes,  10 per line, fixed-width 6 chars each
      npoints longitudes, 10 per line, fixed-width 7 chars each (one wider,
      since longitude needs a 3rd integer digit)
    Returns a list of segments, each a list of (lat, lon) tuples.
    """
    with open(path) as f:
        lines = f.readlines()

    segments = []
    i = 0
    while i < len(lines):
        header = lines[i].split()
        if len(header) < 2:
            break
        npoints = int(header[1])
        i += 1
        n_data_lines = -(-npoints // 10)  # ceil

        lat_vals = []
        for _ in range(n_data_lines):
            row = lines[i].rstrip("\n")
            lat_vals.extend(float(row[j:j + 6]) for j in range(0, len(row), 6))
            i += 1

        lon_vals = []
        for _ in range(n_data_lines):
            row = lines[i].rstrip("\n")
            lon_vals.extend(float(row[j:j + 7]) for j in range(0, len(row), 7))
            i += 1

        segments.append(list(zip(lat_vals[:npoints], lon_vals[:npoints])))
    return segments


_ARLMAP_CACHE = None


def plot_coastlines(ax, lon_min, lon_max, lat_min, lat_max, arlmap_path=ARLMAP_PATH):
    """No-ops (with a one-time warning) if arlmap_path isn't reachable -- e.g.
    on a machine without a local HYSPLIT install -- rather than crashing the
    whole figure over a decorative overlay."""
    global _ARLMAP_CACHE
    if _ARLMAP_CACHE is None:
        try:
            _ARLMAP_CACHE = read_arlmap(arlmap_path)
        except FileNotFoundError:
            import warnings
            warnings.warn(f"Coastline file not found at {arlmap_path!r} -- skipping coastline overlay.")
            _ARLMAP_CACHE = []
    for seg in _ARLMAP_CACHE:
        lats = [p[0] for p in seg]
        lons = [p[1] for p in seg]
        if max(lons) < lon_min or min(lons) > lon_max or max(lats) < lat_min or min(lats) > lat_max:
            continue  # segment doesn't touch the plotted extent -- skip
        ax.plot(lons, lats, color="white", linewidth=0.6, alpha=0.8, zorder=3)


def build_density_grid(lat, lon, weight, grid_res_deg=0.25, pad_deg=2.0):
    kde = gaussian_kde(np.vstack([lon, lat]), weights=weight)
    lon_grid = np.arange(lon.min() - pad_deg, lon.max() + pad_deg, grid_res_deg)
    lat_grid = np.arange(lat.min() - pad_deg, lat.max() + pad_deg, grid_res_deg)
    grid_lon, grid_lat = np.meshgrid(lon_grid, lat_grid)
    density = kde(np.vstack([grid_lon.ravel(), grid_lat.ravel()])).reshape(grid_lon.shape)
    density = density / density.sum()  # normalize -- reads as "share of probability mass"
    return grid_lon, grid_lat, density


def render_density_figure(lat, lon, weight, title, grid_res_deg=0.25, pad_deg=2.0):
    """Returns a matplotlib Figure -- caller decides whether to savefig() or st.pyplot() it."""
    grid_lon, grid_lat, density = build_density_grid(lat, lon, weight, grid_res_deg, pad_deg)

    fig, ax = plt.subplots(figsize=(8, 8))
    cf = ax.contourf(grid_lon, grid_lat, density, levels=20, cmap="inferno")
    plot_coastlines(ax, grid_lon.min(), grid_lon.max(), grid_lat.min(), grid_lat.max())
    ax.scatter(lon, lat, s=1, c="cyan", alpha=0.15, label="weighted grid cells")
    ax.set_xlim(grid_lon.min(), grid_lon.max())
    ax.set_ylim(grid_lat.min(), grid_lat.max())
    fig.colorbar(cf, ax=ax, label="probability density (normalized)")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"{title} ({len(lat)} weighted points)")
    ax.set_aspect("equal")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    return fig


def load_unified_dataset(matrix_csv, perimeter_csv):
    """
    Combines the OEJN storm/nominal matrix data (varying month/regime/sigma/
    altitude/time-of-day at one fixed release point) with the 8-waypoint
    perimeter data (varying release location instead) into one dataframe
    with a common schema, so they can be filtered and pooled together.

    Columns that don't apply to one source are filled with NaN/placeholder:
      - perimeter rows get regime="perimeter", multiplier=NaN, perimeter_point=<waypoint>
      - matrix rows get perimeter_point=NaN
    Both get real date_parsed/month/hour_utc/altitude_ft/particle_diameter_um,
    derived from start_utc/height_m where the source CSV doesn't already have them.
    """
    frames = []

    if matrix_csv and os.path.exists(matrix_csv):
        m = pd.read_csv(matrix_csv)
        m["source"] = "matrix"
        m["date_parsed"] = pd.to_datetime(m["date"], dayfirst=True).dt.date
        m["month"] = pd.to_datetime(m["date"], dayfirst=True).dt.month
        m["perimeter_point"] = np.nan
        frames.append(m)

    if perimeter_csv and os.path.exists(perimeter_csv):
        p = pd.read_csv(perimeter_csv)
        p["source"] = "perimeter"
        p["regime"] = "perimeter"
        p["multiplier"] = np.nan
        start = pd.to_datetime(p["start_utc"])
        p["date_parsed"] = start.dt.date
        p["month"] = start.dt.month
        p["hour_utc"] = start.dt.hour
        p["altitude_ft"] = (p["height_m"] / 0.3048).round().astype(int)
        frames.append(p)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)
