"""
Backward dispersion runner for HYSPLIT.
Downloads GDAS met data, builds CONTROL/SETUP, runs hycs_std, plots the result with concplot.

Single manual run:  python backtrack.py
Batch / test matrix: see run_matrix.py, which imports run_backtrack() from here.
"""

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

IS_WINDOWS = platform.system() == "Windows"


def _exe(name):
    """
    Platform-appropriate executable name for a HYSPLIT/Ghostscript binary --
    Windows builds are named `name.exe`, Mac/Linux builds have no suffix.
    """
    return f"{name}.exe" if IS_WINDOWS else name


# ---------------- MACHINE CONFIG (fixed, doesn't vary per run) ----------------
# These are tied to where HYSPLIT / Ghostscript are actually installed and
# where HYSPLIT's scratch files live -- they only make sense on the machine
# that has HYSPLIT. Each can be overridden by an environment variable
# (HYSPLIT_DIR, GHOSTSCRIPT_EXE, HYSPLIT_MET_DIR, HYSPLIT_WORK_BASE); if the
# env var is unset, an OS-appropriate default is used. MET_DIR and WORK_BASE
# default to living under HYSPLIT_DIR, so pointing HYSPLIT_DIR at a custom
# install location is usually all that's needed.
if IS_WINDOWS:
    _HYSPLIT_DEFAULT = r"C:\hysplit"
    _GS_DEFAULT = r"C:\Program Files\gs\gs10.07.1\bin\gswin64c.exe"
else:
    # macOS/Linux: HYSPLIT itself needs a manual download (free registration
    # at ready.arl.noaa.gov/HYSPLIT.php, no Windows-only requirement despite
    # appearances); Ghostscript is `brew install ghostscript` on macOS.
    # ~/hysplit rather than /usr/local/hysplit -- on Apple Silicon Macs
    # /usr/local is root-owned, so the latter can't be created without sudo.
    _HYSPLIT_DEFAULT = os.path.expanduser("~/hysplit")
    _GS_DEFAULT = "/opt/homebrew/bin/gs"

HYSPLIT_DIR = os.environ.get("HYSPLIT_DIR", _HYSPLIT_DEFAULT)
GS_EXE = os.environ.get("GHOSTSCRIPT_EXE", _GS_DEFAULT)
# Keep WORK_BASE outside any cloud-synced folder -- a sync client that copies
# these files mid-write corrupts HYSPLIT's output.
MET_DIR = os.environ.get("HYSPLIT_MET_DIR", os.path.join(HYSPLIT_DIR, "metdata"))
WORK_BASE = os.environ.get("HYSPLIT_WORK_BASE", os.path.join(HYSPLIT_DIR, "working"))

# Unlike the four above, this is just an output folder inside this same
# repo -- derived from this file's own location (like DUST_FILES_DIR in the
# other scripts) so it isn't tied to one machine's home directory.
RESULTS_DIR = str(Path(__file__).resolve().parent / "hysplit_results")  # finished, small deliverables only -- copied here after each run completes

MET_BASE_URL = "https://www.ready.noaa.gov/data/archives/gdas1"

# ---------------- DEFAULTS for a single manual run (python backtrack.py) ----------------
DEFAULT_LAT = 24.45
DEFAULT_LON = 54.4
DEFAULT_HEIGHT_M = 500
DEFAULT_START = datetime(2022, 7, 16, 9)  # UTC
DEFAULT_RUNTIME_HOURS = -72  # negative = backward run
DEFAULT_SPECIES = "Dust"
DEFAULT_PARTICLE_DIAMETER_UM = 2.5  # 2.5 = PM2.5, 10 = coarse windblown dust
DEFAULT_PARTICLE_DENSITY = 2.5  # g/cc, typical mineral dust
# ------------------------------------------------------------------------------------------

# Fixed map extent (radius in km, centered on the release point) used for every
# frame of a GIF so the view doesn't zoom in/out between frames. I can't see
# the rendered output, so this is a guess -- tune it directly: rerun with a
# smaller number if there's too much empty space, larger if the plume still
# gets clipped near the edge in later frames.
DEFAULT_MAP_RADIUS_KM = 500


def gdas_filename(date):
    week = min((date.day - 1) // 7 + 1, 5)
    return f"gdas1.{date.strftime('%b%y').lower()}.w{week}"


def ensure_met_files(start, hours):
    os.makedirs(MET_DIR, exist_ok=True)
    needed = set()
    end = start + timedelta(hours=hours)
    lo, hi = min(start, end), max(start, end)
    d = lo
    while d <= hi:
        needed.add(gdas_filename(d))
        d += timedelta(days=1)
    needed.add(gdas_filename(hi))

    files = []
    for name in sorted(needed):
        local_path = os.path.join(MET_DIR, name)
        if not os.path.exists(local_path):
            url = f"{MET_BASE_URL}/{name}"
            print(f"downloading {name} ...")
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            with open(local_path, "wb") as f:
                shutil.copyfileobj(r.raw, f)
        files.append(name)
    return files


def write_control(work_dir, met_files, lat, lon, height_m, start, runtime_hours,
                   species, particle_diameter_um, particle_density):
    lines = []
    lines.append(start.strftime("%y %m %d %H"))
    lines.append("1")  # number of starting locations
    lines.append(f"{lat} {lon} {height_m}")
    lines.append(str(runtime_hours))
    lines.append("0")  # vertical motion: model default
    lines.append("10000.0")  # top of model, meters
    lines.append(str(len(met_files)))
    for name in met_files:
        lines.append(MET_DIR + os.sep)
        lines.append(name)
    lines.append("1")  # number of pollutants
    lines.append(species)
    lines.append("1.0")  # emission rate, mass units/hr
    lines.append("1.0")  # emission duration, hours
    lines.append(start.strftime("%y %m %d %H %M"))  # release start
    lines.append("1")  # number of grids
    lines.append(f"{lat} {lon}")
    lines.append("0.25 0.25")
    lines.append("10.0 10.0")
    lines.append(work_dir + os.sep)
    lines.append("cdump")
    lines.append("1")  # number of vertical levels
    lines.append("100")

    end = start + timedelta(hours=runtime_hours)
    sample_start, sample_stop = min(start, end), max(start, end)
    lines.append("1")  # number of sampling periods
    lines.append(sample_start.strftime("%y %m %d %H %M"))
    lines.append(sample_stop.strftime("%y %m %d %H %M"))
    lines.append("00 24 00")  # daily frames; concplot -r2 sums them into one cumulative footprint
    lines.append("1")  # number of depositing pollutants
    lines.append(f"{particle_diameter_um} {particle_density} 1.0")  # diameter (um), density (g/cc), shape
    lines.append("0.0 1.0 0.0 0.0 0.0")  # dep velocity (computed), MW=1 turns on resistance scheme
    lines.append("0.0 8.0E-05 8.0E-05")  # wet removal: Henry's, in-cloud, below-cloud (NOAA suggested defaults)
    lines.append("0.0")  # radioactive decay half-life
    lines.append("0.0")  # resuspension factor

    with open(os.path.join(work_dir, "CONTROL"), "w") as f:
        f.write("\n".join(lines) + "\n")


def write_ascdata(work_dir):
    bdyfiles = os.path.join(HYSPLIT_DIR, "bdyfiles") + os.sep
    content = (
        "-90.0  -180.0  lat/lon of lower left corner (last record in file)\n"
        "1.0     1.0    lat/lon spacing in degrees between data points\n"
        "180     360    lat/lon number of data points\n"
        "2              default land use category\n"
        "0.2            default roughness length (meters)\n"
        f"'{bdyfiles}' directory location of data files\n"
    )
    with open(os.path.join(work_dir, "ASCDATA.CFG"), "w") as f:
        f.write(content)


def run_hysplit(work_dir):
    write_ascdata(work_dir)
    exe = os.path.join(HYSPLIT_DIR, "exec", _exe("hycs_std"))
    subprocess.run([exe], cwd=work_dir, check=True)


def busiest_period(work_dir):
    con2asc = os.path.join(HYSPLIT_DIR, "exec", _exe("con2asc"))
    subprocess.run([con2asc, "-icdump", "-t"], cwd=work_dir, check=True)
    # con2asc names per-period files chronologically (oldest first), but for a
    # backward run concplot numbers its frames from the release time backward,
    # i.e. the reverse order.
    frames = sorted((f for f in os.listdir(work_dir) if f.startswith("cdump_")), reverse=True)

    best_frame, best_points = 1, -1
    for i, name in enumerate(frames, start=1):
        with open(os.path.join(work_dir, name)) as f:
            next(f)  # header
            points = sum(1 for line in f if line.strip())
        if points > best_points:
            best_frame, best_points = i, points
    return best_frame, best_points


def make_plots(work_dir, out_png):
    concplot = os.path.join(HYSPLIT_DIR, "exec", _exe("concplot"))
    arlmap = os.path.join(HYSPLIT_DIR, "graphics", "arlmap")
    ps_path = os.path.join(work_dir, "concplot.ps")

    period, points = busiest_period(work_dir)
    subprocess.run(
        [concplot, "-icdump", f"-j{arlmap}", "-oconcplot.ps", f"-n{period}:{period}", "+m2"],
        cwd=work_dir, check=True,
    )
    subprocess.run(
        [GS_EXE, "-sDEVICE=png16m", "-r150", "-o", out_png, ps_path],
        check=True,
    )
    return period, points


def render_frame_png(work_dir, period_index, out_png, center_lat, center_lon,
                      map_radius_km=DEFAULT_MAP_RADIUS_KM):
    """
    Returns True if a frame was actually rendered. concplot exits non-zero
    with "All concentrations ZERO - no maps" for a period where the grid is
    genuinely empty everywhere (a real, non-error outcome for a short-pulse
    tracer) -- that case returns False instead of raising. Any other failure
    still raises.
    """
    concplot = os.path.join(HYSPLIT_DIR, "exec", _exe("concplot"))
    arlmap = os.path.join(HYSPLIT_DIR, "graphics", "arlmap")
    ps_name = f"concplot_{period_index:03d}.ps"
    result = subprocess.run(
        [concplot, "-icdump", f"-j{arlmap}", f"-o{ps_name}", f"-n{period_index}:{period_index}",
         "+m2",  # suppress the default red max-value square; keeps the open-star (-l, default ascii 73) marker
         f"-h{center_lat}:{center_lon}",  # hold map centered here instead of re-centering per frame
         f"-g0:{map_radius_km}"],  # 0 circles drawn, but fixes the extent to this radius on every frame
        cwd=work_dir, capture_output=True, text=True,
    )
    ps_path = os.path.join(work_dir, ps_name)
    if result.returncode != 0 or not os.path.exists(ps_path):
        if "ZERO" in result.stdout.upper():
            return False
        raise RuntimeError(
            f"concplot failed for period {period_index} (exit {result.returncode}): {result.stdout}"
        )

    subprocess.run(
        [GS_EXE, "-sDEVICE=png16m", "-r150", "-o", out_png, ps_path],
        check=True,
    )
    return True


def cdump_period_to_dataframe(work_dir, cdump_filename, period_label):
    """
    Parses one con2asc -t period file into a DataFrame, tagged with which
    period it is. con2asc's column layout can vary with how HYSPLIT was
    configured -- this reads whatever whitespace-delimited columns are
    actually there rather than assuming fixed names; check raw_data.csv
    once and rename columns here if you want friendlier labels than the
    ones con2asc wrote in its own header line.
    """
    with open(os.path.join(work_dir, cdump_filename)) as f:
        header_cols = f.readline().split()
        rows = [line.split() for line in f if line.strip()]

    colnames = header_cols if rows and len(header_cols) == len(rows[0]) else (
        [f"col_{i + 1}" for i in range(len(rows[0]))] if rows else header_cols
    )
    df = pd.DataFrame(rows, columns=colnames)
    for col in df.columns:
        # pandas 3.x removed errors="ignore" from to_numeric(); only replace
        # the column if every value actually converts, else leave as text.
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().all():
            df[col] = converted
    df.insert(0, "period", period_label)
    return df


def run_backtrack_gif(lat, lon, height_m, start, runtime_hours, result_dir,
                       species=DEFAULT_SPECIES,
                       particle_diameter_um=DEFAULT_PARTICLE_DIAMETER_UM,
                       particle_density=DEFAULT_PARTICLE_DENSITY,
                       gif_frame_ms=400,
                       map_radius_km=DEFAULT_MAP_RADIUS_KM,
                       extra_meta=None):
    """
    Like run_backtrack(), but renders every period HYSPLIT produced (not
    just the busiest one), stitches them into an animated GIF showing the
    backward trajectory's progression, and dumps the raw per-period
    concentration grid to one CSV. Saves directly under result_dir (the
    caller controls the full folder path/hierarchy) instead of
    RESULTS_DIR/run_label.

    Requires Pillow (pip install pillow).
    """
    from PIL import Image

    # Unique across the whole result tree, not just the leaf folder name --
    # two different multiplier/regime branches can otherwise pick the same
    # date+hour+altitude+particle-size combination and collide here.
    run_label = os.path.relpath(result_dir, RESULTS_DIR).replace(os.sep, "__")
    work_dir = os.path.join(WORK_BASE, run_label)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(result_dir, exist_ok=True)

    met_files = ensure_met_files(start, runtime_hours)
    write_control(work_dir, met_files, lat, lon, height_m, start, runtime_hours,
                  species, particle_diameter_um, particle_density)
    run_hysplit(work_dir)

    con2asc = os.path.join(HYSPLIT_DIR, "exec", _exe("con2asc"))
    subprocess.run([con2asc, "-icdump", "-t"], cwd=work_dir, check=True)
    # chronological, oldest first (see busiest_period() for why this is the
    # reverse of concplot's own period numbering)
    cdump_files = sorted(f for f in os.listdir(work_dir) if f.startswith("cdump_"))
    total_periods = len(cdump_files)

    frame_pngs = []
    raw_dfs = []
    empty_periods = []
    for period in range(1, total_periods + 1):
        png_path = os.path.join(result_dir, f"frame_{period:03d}.png")
        rendered = render_frame_png(work_dir, period, png_path, lat, lon, map_radius_km=map_radius_km)
        if rendered:
            frame_pngs.append(png_path)
        else:
            empty_periods.append(period)  # concentration genuinely zero everywhere this period

        cdump_name = cdump_files[total_periods - period]  # concplot period -> chronological filename
        raw_dfs.append(cdump_period_to_dataframe(work_dir, cdump_name, period_label=period))

    gif_path = None
    if frame_pngs:
        gif_path = os.path.join(result_dir, "progression.gif")
        images = [Image.open(p) for p in reversed(frame_pngs)]  # oldest -> newest
        images[0].save(gif_path, save_all=True, append_images=images[1:],
                        duration=gif_frame_ms, loop=0)

    raw_csv_path = os.path.join(result_dir, "raw_data.csv")
    pd.concat(raw_dfs, ignore_index=True).to_csv(raw_csv_path, index=False)

    meta = {
        "run_label": run_label,
        "lat": lat, "lon": lon, "height_m": height_m,
        "start_utc": start.isoformat(), "runtime_hours": runtime_hours,
        "species": species,
        "particle_diameter_um": particle_diameter_um,
        "particle_density": particle_density,
        "total_periods": total_periods,
        "empty_periods": empty_periods,
        "gif": gif_path, "raw_csv": raw_csv_path,
        **(extra_meta or {}),
    }
    with open(os.path.join(result_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2, default=str)

    return meta


def run_backtrack(lat, lon, height_m, start, runtime_hours, run_label,
                   species=DEFAULT_SPECIES,
                   particle_diameter_um=DEFAULT_PARTICLE_DIAMETER_UM,
                   particle_density=DEFAULT_PARTICLE_DENSITY,
                   extra_meta=None):
    """
    Runs one backward HYSPLIT dispersion and saves the result under
    RESULTS_DIR/<run_label>/. Each run gets its own work_dir so runs never
    clobber each other's CONTROL/cdump/concplot files.

    Returns the metadata dict (also written as meta.json in the result folder).
    """
    work_dir = os.path.join(WORK_BASE, run_label)
    os.makedirs(work_dir, exist_ok=True)

    met_files = ensure_met_files(start, runtime_hours)
    write_control(work_dir, met_files, lat, lon, height_m, start, runtime_hours,
                  species, particle_diameter_um, particle_density)
    run_hysplit(work_dir)

    result_dir = os.path.join(RESULTS_DIR, run_label)
    os.makedirs(result_dir, exist_ok=True)
    out_png = os.path.join(result_dir, "result.png")
    period, points = make_plots(work_dir, out_png)

    meta = {
        "run_label": run_label,
        "lat": lat, "lon": lon, "height_m": height_m,
        "start_utc": start.isoformat(), "runtime_hours": runtime_hours,
        "species": species,
        "particle_diameter_um": particle_diameter_um,
        "particle_density": particle_density,
        "busiest_frame": period, "busiest_frame_points": points,
        "result_png": out_png,
        **(extra_meta or {}),
    }

    # copy the small deliverables into OneDrive now that HYSPLIT has finished
    # writing them (safe -- the corruption risk is mid-write, not post-write).
    # cdump itself is left in WORK_BASE (outside OneDrive): it's the large
    # binary you'd need to re-plot without rerunning HYSPLIT, so keep it if
    # you expect to revisit a run, but there's no need to duplicate it here.
    for fname in ("CONTROL", "concplot.ps"):
        src = os.path.join(work_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(result_dir, fname))

    with open(os.path.join(result_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2, default=str)

    return meta


def main():
    run_label = f"manual_{DEFAULT_START:%Y%m%dT%H%M}"
    meta = run_backtrack(DEFAULT_LAT, DEFAULT_LON, DEFAULT_HEIGHT_M, DEFAULT_START,
                          DEFAULT_RUNTIME_HOURS, run_label)
    print(f"saved {meta['result_png']}")


if __name__ == "__main__":
    main()
