"""
One-time (re-run as needed) preprocessing script.

Reads every per-flight CSV in ../CSVFiles, keeps only the columns the
dashboard needs, drops UNKNOWN-phase rows, and writes two compact
Parquet files into ./data/:

  - timesteps.parquet : one row per (flight, timestep) -- phase, altitude,
                         dust ingested, elapsed time since first known phase.
  - flights.parquet   : one row per flight -- metadata parsed from the
                         filename, plus origin/destination joined in from
                         any "<TYPE>_Summary.csv" file found alongside the
                         raw data, falling back to nearest-airport inference
                         from the flight's own recorded lat/lon (see
                         nearest_airport() below) for any aircraft type with
                         no summary file at all (e.g. A321 currently has
                         none, only A333 does).

Re-run this script whenever CSVFiles gains new flights or aircraft types:

    python build_dataset.py
"""

import concurrent.futures
import math
from pathlib import Path
import sys
import time

import pandas as pd

# CSVFiles/ lives under OneDrive, whose file provider has been observed to
# block an individual pd.read_csv() indefinitely (not just slowly -- no
# exception, no return, for well over an hour in one case) while a large
# sync backlog drains. A plain try/except+retry only helps once the call
# actually raises; this bounds the WAIT itself via a worker thread, same
# fix as app.py's _read_json_bounded. A read that times out is retried a
# few times (transient stalls usually clear within a minute or two) before
# being logged as a real failure and skipped -- never blocks the whole run.
_IO_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="onedrive-io")


def _read_csv_bounded(path, usecols, timeout=90.0, attempts=3):
    last_exc = None
    for attempt in range(attempts):
        future = _IO_EXECUTOR.submit(pd.read_csv, path, usecols=usecols)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            last_exc = TimeoutError(f"read timed out after {timeout}s (attempt {attempt + 1}/{attempts})")
        except Exception as exc:
            last_exc = exc
        if attempt < attempts - 1:
            time.sleep(5 * (attempt + 1))
    raise last_exc

SCRIPT_DIR = Path(__file__).resolve().parent
CSV_DIR = SCRIPT_DIR.parent / "CSVFiles"
OUT_DIR = SCRIPT_DIR / "data"

TIMESTEP_COLS = ["phase", "Flight_Time_Seconds", "Alt_ft", "CoreDustIngested_g"]
PHASES = {"CLIMB", "CRUISE", "DESCENT", "LEVEL DESCENT", "LEVEL FLIGHT"}

# Keep in sync with app.py's own AIRPORTS dict (same 11 ICAO codes; only the
# coordinates are needed here, not the display name/city/country) -- the
# region this dataset covers only ever touches these airports, confirmed by
# every A333 flight (which has authoritative Summary.csv routes) landing
# within 2.6km of one of them at both ends, across a 40-flight random sample.
AIRPORT_COORDS = {
    "OBBI": (26.2708, 50.6336), "OEDF": (26.4712, 49.7979), "OEJN": (21.6796, 39.1565),
    "OERK": (24.9576, 46.6988), "OKKK": (29.2267, 47.9689), "OMAA": (24.4330, 54.6511),
    "OMDB": (25.2532, 55.3657), "OMDW": (24.8967, 55.1614), "OMSJ": (25.3286, 55.5172),
    "OOMS": (23.5933, 58.2844), "OTHH": (25.2609, 51.6138),
}
# Generous margin above the 2.6km max observed on real, verified routes --
# still far below the hundreds of km between any two airports in this list,
# so it can't pick the WRONG one, only correctly decline to guess when a
# flight's recording doesn't start/end near any of them at all.
NEAREST_AIRPORT_MAX_KM = 20.0


def nearest_airport(lat, lon, max_km=NEAREST_AIRPORT_MAX_KM):
    """ICAO code of the closest known airport to (lat, lon), or None if none are within max_km."""
    best_icao, best_km = None, max_km
    for icao, (alat, alon) in AIRPORT_COORDS.items():
        p0, p1 = math.radians(lat), math.radians(alat)
        dp, dl = math.radians(alat - lat), math.radians(alon - lon)
        a = math.sin(dp / 2) ** 2 + math.cos(p0) * math.cos(p1) * math.sin(dl / 2) ** 2
        km = 2 * 6371.0 * math.asin(math.sqrt(a))
        if km < best_km:
            best_km, best_icao = km, icao
    return best_icao


def parse_filename(stem: str):
    parts = stem.split("_")
    if len(parts) != 8:
        return None
    year, month, day, hhmm, hexcode, callsign, actype, registration = parts
    if len(hhmm) != 4 or not hhmm.isdigit():
        return None
    takeoff_minutes = int(hhmm[:2]) * 60 + int(hhmm[2:])
    return {
        "flight_id": stem,
        "date": f"{year}-{month}-{day}",
        "takeoff_minutes": takeoff_minutes,
        "takeoff_hhmm": f"{hhmm[:2]}:{hhmm[2:]}",
        "hexcode": hexcode,
        "callsign": callsign,
        "aircraft_type": actype,
        "registration": registration,
    }


def load_summary_lookups(csv_dir: Path, aircraft_types: set[str]) -> pd.DataFrame:
    frames = []
    for actype in sorted(aircraft_types):
        summary_path = csv_dir / f"{actype}_Summary.csv"
        if not summary_path.exists():
            print(f"  (no summary file for {actype}, skipping origin/destination enrichment)")
            continue
        try:
            df = pd.read_csv(
                summary_path,
                usecols=["Flight_ID", "origin_ICAO", "destination_ICAO", "origin_IATA", "destination_IATA"],
            )
        except Exception as exc:
            print(f"  WARNING: failed to read {summary_path.name}: {exc}")
            continue
        df = df.drop_duplicates(subset="Flight_ID", keep="first")
        df = df.rename(columns={
            "Flight_ID": "flight_id",
            "origin_ICAO": "origin_icao",
            "destination_ICAO": "destination_icao",
            "origin_IATA": "origin_iata",
            "destination_IATA": "destination_iata",
        })
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["flight_id", "origin_icao", "destination_icao", "origin_iata", "destination_iata"])
    return pd.concat(frames, ignore_index=True)


def main():
    if not CSV_DIR.exists():
        sys.exit(f"CSV directory not found: {CSV_DIR}")

    files = sorted(
        f for f in CSV_DIR.glob("*.csv")
        if not f.name.endswith("_Summary.csv")
    )
    print(f"Found {len(files)} flight CSVs in {CSV_DIR}")

    timestep_frames = []
    flight_rows = []
    failures = []

    start = time.time()
    for i, path in enumerate(files, 1):
        meta = parse_filename(path.stem)
        if meta is None:
            failures.append((path.name, "unexpected filename format"))
            continue

        try:
            raw = _read_csv_bounded(path, usecols=TIMESTEP_COLS + ["Lat", "Lon"])
        except Exception as exc:
            failures.append((path.name, f"read error: {exc}"))
            continue
        raw = raw.sort_values("Flight_Time_Seconds")

        # From the RAW (not phase-filtered) recording, since the true first/
        # last recorded point -- the best proxy for where the flight actually
        # started/ended -- can fall in an UNKNOWN or ground-phase row that
        # the phase filter below would otherwise have already dropped.
        if len(raw) > 0:
            meta["inferred_origin_icao"] = nearest_airport(raw["Lat"].iloc[0], raw["Lon"].iloc[0])
            meta["inferred_destination_icao"] = nearest_airport(raw["Lat"].iloc[-1], raw["Lon"].iloc[-1])
        else:
            meta["inferred_origin_icao"] = meta["inferred_destination_icao"] = None

        df = raw[raw["phase"].isin(PHASES)].sort_values("Flight_Time_Seconds").copy()
        n_rows = len(df)
        if n_rows == 0:
            flight_rows.append({**meta, "n_rows": 0, "duration_s": 0.0, "total_dust_g": 0.0, "first_phase": None})
            continue

        min_sec = df["Flight_Time_Seconds"].min()
        elapsed_s = (df["Flight_Time_Seconds"] - min_sec).astype("float32")
        total_dust = float(df["CoreDustIngested_g"].sum())
        first_phase = df["phase"].iloc[0]

        # A flight whose first recorded phase isn't CLIMB indicates a truncated
        # or otherwise unreliable recording (it appears to start mid-flight) --
        # exclude it from timesteps entirely so it can't skew any analysis.
        # It's still kept in flights.parquet (and anomalous_flights.csv) as a
        # record of what was excluded and why.
        if first_phase == "CLIMB":
            timestep_frames.append(pd.DataFrame({
                "flight_id": meta["flight_id"],
                "phase": df["phase"].astype("category").values,
                "elapsed_s": elapsed_s.values,
                "altitude_ft": df["Alt_ft"].astype("float32").values,
                "dust_g": df["CoreDustIngested_g"].astype("float32").values,
            }))

        flight_rows.append({
            **meta,
            "n_rows": n_rows,
            "duration_s": float(elapsed_s.max()),
            "total_dust_g": total_dust,
            "first_phase": first_phase,
        })

        if i % 500 == 0 or i == len(files):
            elapsed = time.time() - start
            print(f"  processed {i}/{len(files)} files ({elapsed:.1f}s elapsed)")

    if not timestep_frames:
        sys.exit("No usable flight data found -- aborting.")

    print("Concatenating timestep data...")
    timesteps = pd.concat(timestep_frames, ignore_index=True)
    timesteps["flight_id"] = timesteps["flight_id"].astype("category")
    timesteps["phase"] = timesteps["phase"].astype("category")

    flights = pd.DataFrame(flight_rows)
    flights["date"] = pd.to_datetime(flights["date"]).dt.date

    aircraft_types = set(flights["aircraft_type"].unique())
    print(f"Aircraft types found: {sorted(aircraft_types)}")
    lookups = load_summary_lookups(CSV_DIR, aircraft_types)
    if not lookups.empty:
        flights = flights.merge(lookups, on="flight_id", how="left")
    for col in ("origin_icao", "destination_icao", "origin_iata", "destination_iata"):
        if col not in flights.columns:
            flights[col] = None

    # Fallback for any flight with no Summary.csv match at all (currently
    # every A321 flight -- only A333 has a summary file): nearest-airport
    # inference from the flight's own first/last recorded lat/lon (see
    # nearest_airport() above), computed per-flight in the loop above.
    # route_inferred tags which flights got this fallback rather than a
    # verified Summary.csv route, in case a consumer wants to tell them
    # apart. IATA codes aren't inferrable this way (only coordinates are
    # known here) and stay null for these flights.
    origin_was_null = flights["origin_icao"].isna()
    destination_was_null = flights["destination_icao"].isna()
    flights["origin_icao"] = flights["origin_icao"].fillna(flights["inferred_origin_icao"])
    flights["destination_icao"] = flights["destination_icao"].fillna(flights["inferred_destination_icao"])
    flights["route_inferred"] = (
        (origin_was_null & flights["origin_icao"].notna())
        | (destination_was_null & flights["destination_icao"].notna())
    )
    flights = flights.drop(columns=["inferred_origin_icao", "inferred_destination_icao"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timesteps_path = OUT_DIR / "timesteps.parquet"
    flights_path = OUT_DIR / "flights.parquet"
    timesteps.to_parquet(timesteps_path, index=False)
    flights.to_parquet(flights_path, index=False)

    # Data-quality flag: a flight is anomalous if it has no usable rows at all,
    # or if its first recorded (non-UNKNOWN) phase isn't CLIMB -- i.e. the
    # recording appears to start mid-flight.
    anomalous = flights[flights["first_phase"] != "CLIMB"].copy()
    anomalous_path = OUT_DIR / "anomalous_flights.csv"
    anomalous.sort_values("date").to_csv(anomalous_path, index=False)

    print()
    print("Done.")
    print(f"  flights processed : {len(flights)}")
    print(f"  timestep rows     : {len(timesteps):,}")
    print(f"  timesteps.parquet : {timesteps_path} ({timesteps_path.stat().st_size / 1e6:.1f} MB)")
    print(f"  flights.parquet   : {flights_path} ({flights_path.stat().st_size / 1e6:.1f} MB)")
    print(f"  anomalous flights : {len(anomalous)} (no data, or first phase != CLIMB) -> {anomalous_path}")
    if failures:
        print(f"  WARNING: {len(failures)} files failed to process:")
        for name, reason in failures[:20]:
            print(f"    - {name}: {reason}")
        if len(failures) > 20:
            print(f"    ... and {len(failures) - 20} more")


if __name__ == "__main__":
    main()
