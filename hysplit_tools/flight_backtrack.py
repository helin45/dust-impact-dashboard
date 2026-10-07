"""
Per-flight dust source density: for a given flight_id from the dashboard's
CSVFiles, samples release points along its actual recorded trajectory and
backward-traces each with HYSPLIT, to estimate where the dust it encountered
originated.

Point selection: the flight's phase-filtered trace (CLIMB/CRUISE/DESCENT/
LEVEL DESCENT, same convention as the dashboard's app.py) is split into
N_RELEASE_POINTS time buckets; the single peak-dust timestep in each bucket
becomes a release point (same "biggest event wins" idea as run_matrix.py's
pick_events(), just applied along one flight's timeline instead of across a
month). Each point's real recorded lat/lon/altitude/time is used -- this is
reconstructing one specific flight's exposure, not sweeping a fixed grid.

Run:
    python flight_backtrack.py <flight_id> [<flight_id> ...]

flight_id is the CSV filename stem, e.g. 2022_03_01_0205_06A041_QTR46M_A333_A7-AEG
(matches the dashboard's /api/flight/{flight_id} exactly).

Each successful point becomes its own HYSPLIT run (backtrack.run_backtrack_gif),
same as run_perimeter.py. Results land under
hysplit_results/flights/<flight_id>/<point_label>/, indexed in
hysplit_results/flights/<flight_id>/index.csv. Once all points for a flight
are done, postprocess_results.process_index() combines them and
build_flight_density() turns that into a compact density_grid.json the
dashboard reads directly (see app.py's /api/flight/{flight_id}/dust_source_density).

--strategy (topn/trigger/dense) picks WHICH release points to use; the
separate --method flag (hysplit/surrogate) picks HOW each one is traced --
hysplit (default) runs the real hycs_std simulation above, surrogate instead
evaluates surrogate_backtrack.py's trained-model estimate, near-instant and
requiring no HYSPLIT install, but an approximation learned only from
whichever flights already have a real HYSPLIT result -- see that module's
docstring before trusting it for anything beyond quick exploration. Results
for a non-default strategy and/or method get their own namespaced subfolder
(see flight_dir_for()) so they never mix with -- or overwrite -- each other.

N_RELEASE_POINTS has not been tuned against real run timings yet -- per
run_matrix.py's own guidance, time a single flight's batch before raising it.
"""

import argparse
import csv
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import backtrack
import density_utils
import postprocess_results

DUST_FILES_DIR = Path(__file__).resolve().parent
CSV_DIR = Path(os.environ.get("DUST_CSV_DIR") or DUST_FILES_DIR.parent / "CSVFiles").expanduser()  # DUST_CSV_DIR overrides the default repo-root CSVFiles/
FLIGHTS_DIR = os.path.join(backtrack.RESULTS_DIR, "flights")

PHASES = ["CLIMB", "CRUISE", "DESCENT", "LEVEL DESCENT"]  # matches dashboard/app.py's PHASES

N_RELEASE_POINTS = 10  # untimed so far -- time one flight's batch before raising this
RUNTIME_HOURS = -48  # matches run_matrix.py/run_perimeter.py's revised default

STRATEGY_TOPN = "topn"
STRATEGY_TRIGGER = "trigger"
STRATEGY_DENSE = "dense"
STRATEGIES = (STRATEGY_TOPN, STRATEGY_TRIGGER, STRATEGY_DENSE)

# Orthogonal to STRATEGIES above: strategy picks WHICH release points to use,
# method picks HOW each one is traced. METHOD_SURROGATE swaps out the real
# hycs_std run (backtrack.run_backtrack_gif) for surrogate_backtrack.py's
# trained-model estimate -- see that module's docstring for what it can and
# can't be trusted for. surrogate_backtrack is imported lazily (only inside
# run_flight(), when actually needed) so this module -- imported at startup
# by dashboard/app.py -- stays importable on a machine without scikit-learn/
# joblib installed.
METHOD_HYSPLIT = "hysplit"
METHOD_SURROGATE = "surrogate"
METHODS = (METHOD_HYSPLIT, METHOD_SURROGATE)

DENSE_BUCKET_SECONDS = 60  # ~1 release point per minute of elapsed flight time
DENSE_MAX_POINTS = 60  # safety cap -- an unusually long flight shouldn't silently balloon into hundreds of HYSPLIT runs

TRIGGER_STD_MULTIPLIER = 2.0  # a timestep counts as a "trigger" event once dust ingestion exceeds mean + this many std devs of the flight's own values
TRIGGER_MAX_POINTS = 30  # safety cap -- an unusually spiky flight shouldn't produce unbounded release points

INDEX_FIELDS = [
    "run_label", "status", "flight_id", "point_label", "lat", "lon", "height_m",
    "start_utc", "runtime_hours", "species", "particle_diameter_um", "particle_density",
    "elapsed_min", "alt_ft", "dust_ingested_g", "bin_total_dust_g",
    "total_periods", "empty_periods", "gif", "raw_csv", "error", "surrogate",
]


def _point_from_row(i, row, t0):
    return {
        "point_label": f"pt{i:02d}",
        "lat": float(row["Lat"]),
        "lon": float(row["Lon"]),
        "alt_ft": float(row["Alt_ft"]),
        "start": pd.to_datetime(row["time"]).to_pydatetime(),
        "elapsed_min": float((row["Flight_Time_Seconds"] - t0) / 60),
        "dust_ingested_g": float(row["CoreDustIngested_g"]),
    }


def _bucket_peaks(df, t0, n_buckets):
    """
    Splits df into n_buckets equal-time buckets and returns one release
    point per non-empty bucket: its highest-dust timestep ("biggest event
    wins", same idea as run_matrix.py's pick_events()). Shared by the topn
    and dense strategies -- they differ only in how n_buckets is chosen.
    """
    bins = pd.cut(df["Flight_Time_Seconds"], bins=n_buckets)
    points = []
    for i, (_, group) in enumerate(df.groupby(bins, observed=True), start=1):
        if group.empty:
            continue
        peak = group.loc[group["CoreDustIngested_g"].idxmax()]
        point = _point_from_row(i, peak, t0)
        point["bin_total_dust_g"] = float(group["CoreDustIngested_g"].sum())
        points.append(point)
    return points


def _trigger_events(df, t0):
    """
    Identifies real high-concentration events -- contiguous runs of
    timesteps where dust ingestion exceeds mean + TRIGGER_STD_MULTIPLIER*std
    of this flight's own values -- and returns one release point (the peak)
    per event, instead of always producing a fixed count regardless of
    whether the flight actually spiked. Variable count: zero for a flat
    profile, capped at TRIGGER_MAX_POINTS for a very spiky one.
    """
    values = df["CoreDustIngested_g"]
    threshold = values.mean() + TRIGGER_STD_MULTIPLIER * values.std()
    above = values > threshold
    if not above.any():
        return []

    event_id = (above != above.shift(fill_value=False)).cumsum()
    points = []
    for i, (_, group) in enumerate(df[above].groupby(event_id[above]), start=1):
        peak = group.loc[group["CoreDustIngested_g"].idxmax()]
        point = _point_from_row(i, peak, t0)
        point["bin_total_dust_g"] = float(group["CoreDustIngested_g"].sum())
        points.append(point)
        if len(points) >= TRIGGER_MAX_POINTS:
            break
    return points


def select_release_points(flight_id, n_points=N_RELEASE_POINTS, strategy=STRATEGY_TOPN):
    """
    Picks release points along a flight's real trajectory for backward
    HYSPLIT tracing. Three strategies -- all return the same point-dict
    shape, so nothing downstream needs to know which one produced a point:

      - "topn" (default): n_points fixed time buckets, peak per bucket --
        the original behavior, always exactly n_points (fewer if the flight
        is short/sparse -- empty buckets are skipped).
      - "dense": same bucketing, but the bucket count is derived from flight
        duration / DENSE_BUCKET_SECONDS (~1 point/minute) instead of a fixed
        n_points -- ignores n_points.
      - "trigger": release points only at real statistical dust-ingestion
        spikes (see _trigger_events()) -- a variable count, possibly zero,
        ignores n_points.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}, expected one of {STRATEGIES}")

    csv_path = CSV_DIR / f"{flight_id}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"no CSV for flight_id {flight_id!r} at {csv_path}")

    df = pd.read_csv(
        csv_path,
        usecols=["time", "phase", "Flight_Time_Seconds", "Lat", "Lon", "Alt_ft", "CoreDustIngested_g"],
    )
    df = df[df["phase"].isin(PHASES)].sort_values("Flight_Time_Seconds").reset_index(drop=True)
    if df.empty:
        return []

    t0 = df["Flight_Time_Seconds"].min()

    if strategy == STRATEGY_TRIGGER:
        return _trigger_events(df, t0)

    if strategy == STRATEGY_DENSE:
        duration_s = df["Flight_Time_Seconds"].max() - t0
        n_buckets = min(DENSE_MAX_POINTS, max(1, round(duration_s / DENSE_BUCKET_SECONDS)))
        return _bucket_peaks(df, t0, n_buckets)

    return _bucket_peaks(df, t0, n_points)


def append_to_index(index_csv, row):
    os.makedirs(os.path.dirname(index_csv), exist_ok=True)
    write_header = not os.path.exists(index_csv)
    with open(index_csv, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INDEX_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def flight_dir_for(flight_id, strategy=STRATEGY_TOPN, method=METHOD_HYSPLIT):
    """
    Where this flight+strategy+method's results live. topn+hysplit keeps the
    original flat layout (FLIGHTS_DIR/<flight_id>/) for backward
    compatibility with already-computed flights; a non-default strategy
    and/or method each add their own namespaced subfolder (nested when both
    are non-default, e.g. <flight_id>/trigger/surrogate/). Without this,
    every strategy/method would share the same index.csv/combined_raw.csv/
    density_grid.json -- and since point_label always starts back at pt01
    for each one, their points would dedup-collide with each other, silently
    mixing release points (or real vs. surrogate results) into one density
    map instead of keeping each combination isolated and correctly
    attributable.
    """
    base = os.path.join(FLIGHTS_DIR, flight_id)
    if strategy != STRATEGY_TOPN:
        base = os.path.join(base, strategy)
    if method != METHOD_HYSPLIT:
        base = os.path.join(base, method)
    return base


def build_flight_density(flight_id, strategy=STRATEGY_TOPN, method=METHOD_HYSPLIT):
    """
    Combines this flight's HYSPLIT (or surrogate) runs into a density grid,
    weighted by concentration * bin_total_dust_g -- so a release point that
    genuinely ingested more dust counts for more in the aggregate picture,
    not just every point counting equally. Writes density_grid.json next to
    combined_raw.csv, read directly by the dashboard's FastAPI endpoint.
    """
    flight_dir = flight_dir_for(flight_id, strategy, method)
    combined_path = os.path.join(flight_dir, "combined_raw.csv")
    if not os.path.exists(combined_path):
        print(f"{flight_id}: no combined_raw.csv -- no successful runs to build a density map from")
        return

    combined = pd.read_csv(combined_path)
    conc_col = next(c for c in combined.columns if c.startswith("Dust"))
    weight = combined[conc_col] * combined["bin_total_dust_g"]
    mask = weight > 0
    if not mask.any():
        print(f"{flight_id}: no positive-weight grid cells -- skipping density map")
        return
    combined, weight = combined[mask], weight[mask]

    grid_lon, grid_lat, density = density_utils.build_density_grid(
        combined["LAT"].to_numpy(), combined["LON"].to_numpy(), weight.to_numpy(),
    )

    index = pd.read_csv(os.path.join(flight_dir, "index.csv"))
    # Dedup by run_label BEFORE filtering to status=="ok" -- a point that
    # failed on a later rerun must not be resurrected by a stale "ok" row
    # from an earlier attempt (filtering first would keep that earlier "ok"
    # row, since dedup would then only ever see rows that already passed the
    # filter).
    latest = index.drop_duplicates(subset=["run_label"], keep="last")
    ok = latest[latest["status"] == "ok"]
    release_points = [
        {
            "point_label": row["point_label"],
            "lat": row["lat"],
            "lon": row["lon"],
            "alt_ft": row["alt_ft"],
            "start_utc": row["start_utc"],
            "dust_ingested_g": row["dust_ingested_g"],
        }
        for _, row in ok.iterrows()
    ]

    out = {
        "flight_id": flight_id,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "n_points_used": len(ok),
        "runtime_hours": int(ok["runtime_hours"].iloc[0]) if len(ok) else RUNTIME_HOURS,
        "lon": grid_lon[0, :].tolist(),
        "lat": grid_lat[:, 0].tolist(),
        "density": density.tolist(),
        "release_points": release_points,
        # Belt-and-suspenders alongside the folder namespacing above -- lets
        # a consumer that only has this one JSON file in hand (e.g. an
        # export, or a future direct file read) still tell it apart from a
        # real HYSPLIT result without needing to know which endpoint/method
        # query param it was fetched with.
        "surrogate": method == METHOD_SURROGATE,
    }
    with open(os.path.join(flight_dir, "density_grid.json"), "w") as f:
        json.dump(out, f)
    print(f"{flight_id}: wrote density_grid.json ({len(ok)} release points)")


def run_flight(flight_id, runtime_hours=RUNTIME_HOURS, n_points=N_RELEASE_POINTS, strategy=STRATEGY_TOPN,
               method=METHOD_HYSPLIT):
    """
    Returns (any_ok, last_error): any_ok is False if the flight had no
    usable rows or every release point failed. Each point's own exception
    is caught below (so one bad point doesn't lose the others), but that
    means this function -- and the process running it -- would otherwise
    always exit 0 even when nothing was actually computed. The caller uses
    this return value to report a real failure via a non-zero exit code
    instead, since the dashboard's /api/flight/{id}/compute can only tell
    success from failure via this process's return code.

    `method` picks the per-point runner: METHOD_HYSPLIT (default) runs real
    HYSPLIT via backtrack.run_backtrack_gif(); METHOD_SURROGATE evaluates
    surrogate_backtrack.py's trained model instead -- see its module
    docstring for what that trades away. surrogate_backtrack is imported
    here, not at module level, so this module stays importable without
    scikit-learn/joblib installed (dashboard/app.py imports it unconditionally
    at startup for flight_dir_for()/select_release_points()).
    """
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}, expected one of {METHODS}")
    if method == METHOD_SURROGATE:
        import surrogate_backtrack
        runner = surrogate_backtrack.run_backtrack_surrogate
    else:
        runner = backtrack.run_backtrack_gif

    points = select_release_points(flight_id, n_points=n_points, strategy=strategy)
    if not points:
        print(f"{flight_id}: no usable (phase-filtered) rows, or no {strategy} events -- skipping")
        return False, f"no usable rows or no {strategy} events found"

    flight_dir = flight_dir_for(flight_id, strategy, method)
    index_csv = os.path.join(flight_dir, "index.csv")
    # Keeps the existing (flight_id, strategy, point_label) run_label format
    # for the default hysplit method unchanged -- already-computed flights'
    # index.csv/combined_raw.csv rows were written with that exact format, so
    # a rerun of the same flight+strategy must keep producing matching labels.
    label_parts = [flight_id, strategy] + ([] if method == METHOD_HYSPLIT else [method])
    any_ok = False
    last_error = None
    for i, pt in enumerate(points, start=1):
        run_label = "__".join(label_parts + [pt["point_label"]])
        result_dir = os.path.join(flight_dir, pt["point_label"])
        print(f"[{i}/{len(points)}] {run_label}")
        try:
            meta = runner(
                lat=pt["lat"], lon=pt["lon"], height_m=pt["alt_ft"] * 0.3048,
                start=pt["start"], runtime_hours=runtime_hours, result_dir=result_dir,
                extra_meta={
                    "flight_id": flight_id,
                    "point_label": pt["point_label"],
                    "elapsed_min": pt["elapsed_min"],
                    "alt_ft": pt["alt_ft"],
                    "dust_ingested_g": pt["dust_ingested_g"],
                    "bin_total_dust_g": pt["bin_total_dust_g"],
                },
            )
            meta["status"] = "ok"
            any_ok = True
        except Exception as e:
            meta = {"run_label": run_label, "status": "failed", "error": str(e)}
            last_error = str(e)
            traceback.print_exc()
        append_to_index(index_csv, meta)  # written after every run, so a crash mid-batch loses nothing

    postprocess_results.process_index(index_csv)
    build_flight_density(flight_id, strategy=strategy, method=method)
    return any_ok, last_error


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("flight_ids", nargs="+", help="flight_id(s), i.e. CSV filename stem(s)")
    parser.add_argument("--hours", type=int, default=None,
                         help="backward-trajectory length in hours, positive (default: script's RUNTIME_HOURS)")
    parser.add_argument("--points", type=int, default=None,
                         help="release-point count for topn/dense strategies -- ignored by trigger "
                              "(default: script's N_RELEASE_POINTS)")
    parser.add_argument("--strategy", choices=STRATEGIES, default=STRATEGY_TOPN,
                         help="release-point selection strategy (default: topn)")
    parser.add_argument("--method", choices=METHODS, default=METHOD_HYSPLIT,
                         help="hysplit runs the real hycs_std simulation (default); surrogate evaluates "
                              "the trained model from surrogate_model.py instead -- fast, but an "
                              "approximation, see surrogate_backtrack.py's docstring")
    args = parser.parse_args()

    runtime_hours = -abs(args.hours) if args.hours is not None else RUNTIME_HOURS
    n_points = args.points if args.points is not None else N_RELEASE_POINTS

    failures = {}
    for flight_id in args.flight_ids:
        print(f"=== {flight_id} ===")
        ok, last_error = run_flight(flight_id, runtime_hours=runtime_hours, n_points=n_points,
                                     strategy=args.strategy, method=args.method)
        if not ok:
            failures[flight_id] = last_error or "unknown failure"
    if failures:
        # Process every flight_id first (so one total failure never loses
        # progress on the others), only raise at the end -- but still raise,
        # so a caller checking the exit code sees this as a real failure
        # rather than a silent no-op.
        summary = "; ".join(f"{fid}: {err}" for fid, err in failures.items())
        raise SystemExit(f"{len(failures)}/{len(args.flight_ids)} flight(s) had zero successful release points -- {summary}")


if __name__ == "__main__":
    main()
