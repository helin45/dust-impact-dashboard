# Dust Impact Dashboard

A local dashboard for exploring **core dust ingestion by aircraft engines** across
flight phase, altitude and time, built from per-flight recordings of engine and
flight data. It is designed to help identify and characterize the impact of
airborne dust (including dust storms) on aircraft engines, with a focus on the
Arabian Peninsula.

It was built for a University of Manchester School of Engineering internship
project. This repository contains only the **tooling**; no flight data is
included. To use it you need your own folder of per-flight CSVs (schema below),
and, for the optional dust-source-attribution features, your own local
[HYSPLIT](https://www.ready.noaa.gov/HYSPLIT.php) install.

## Quick start

You need Python 3.12 or newer and a folder of per-flight CSVs.

```bash
git clone <repo-url>
cd dust-impact-dashboard
# put your flight CSVs in CSVFiles/  (or use --csv-dir, below)
python run.py
```

On Windows you can double-click `start.bat` instead of running `python run.py`.

The first run creates a `.venv` folder, installs the requirements (a few
minutes), builds the dataset from your CSVs (how long depends on how many you
have), starts the server and opens <http://localhost:8000>. Later runs skip
anything already done: requirements are reinstalled only if
`dashboard/requirements.txt` changed, and the dataset is rebuilt only if your
CSVs changed. The first page load after starting can take around 30 seconds
while the data loads.

| option | what it does |
|---|---|
| `--csv-dir <folder>` | read flight CSVs from any folder instead of `CSVFiles/` (also settable with the `DUST_CSV_DIR` environment variable) |
| `--port <n>` | serve on a different port (default 8000) |
| `--no-browser` | do not open a browser tab |
| `--rebuild` | force rebuilding the dataset from the CSVs |
| `--reinstall` | force reinstalling the requirements |

Everything below is optional detail: the CSV format your data needs, the tabs,
and how to set up the HYSPLIT and satellite features.

## What's in the box

```
dust-impact-dashboard/
  run.py              one-command launcher (install, build dataset, start, open browser)
  start.bat           Windows double-click wrapper for run.py
  dashboard/          FastAPI app + static frontend, plus the dataset-prep scripts
    app.py              the web app
    build_dataset.py    turns a folder of flight CSVs into two compact Parquet files
    infer_routes_from_position.py   optional extra pass for flight routes
    static/             HTML/CSS/JS frontend (Plotly)
    requirements.txt
    .env.example        template for the optional API credentials
  hysplit_tools/      scripts the dashboard shells out to / imports for the
                      HYSPLIT backward-dispersion ("dust source attribution") tab
  CSVFiles/           <- your flight CSVs go here (git-ignored; run.py creates it if missing)
```

The dashboard runs fully **without** HYSPLIT or any NASA/EUMETSAT credentials:
the aggregate view, single-flight view, route map, flight comparison and data
quality tab all work from the Parquet files alone. The extra panels (dust
source attribution, MERRA-2 winds, satellite overlays) just report themselves as
unavailable until you set those pieces up.

## Tabs

| tab | what it is for |
|---|---|
| **Aggregate view** | filter the whole dataset (aircraft type, date, takeoff time, phase, altitude, location, registration) and see distributions, trends and rankings |
| **Single flight view** | one flight in detail: flight path, phase timeline, altitude, speed, dust profile; a search box jumps to a flight by callsign, registration or route |
| **Dust source attribution** | a HYSPLIT backward-dispersion map of where a flight's dust likely originated, with optional imagery, MERRA-2 wind vectors, MODIS AOD overlay and a soil/land-cover susceptibility mask; can compute a flight on demand |
| **Compare flights** | overlay 2 to 8 individual flights on the same charts |
| **Compare filters** | hold snapshots of the aggregate view under different filters and overlay them |
| **Routes** | a map of every route flown, coloured by total dust ingested |
| **Data quality** | every flight excluded from the rest of the dashboard and why, with the share of each aircraft type's flights excluded |
| **Data & validation** | how many flights have a real HYSPLIT result, and how well it agrees with the independent soil/land-cover susceptibility grid |

Every chart has an **Info** button explaining exactly what is plotted and how it
is calculated.

## Expected CSV schema

**One CSV per flight**, plus optionally one summary CSV per aircraft type. Put
them all in `CSVFiles/` (or the folder you pass to `--csv-dir`).

### Per-flight file

Filename format (8 underscore-separated fields):

```
YYYY_MM_DD_HHMM_hexcode_callsign_actype_registration.csv
```

e.g. `2022_03_01_0205_06A041_QTR46M_A333_A7-AEG.csv`
(date, takeoff time UTC, Mode-S hex, callsign, aircraft type, registration).
Files that don't parse to exactly 8 fields are skipped by `build_dataset.py`.

**Columns the tooling actually reads** (each row = one timestep, typically every
4 to 10 s):

| column | used for |
|---|---|
| `time` | timestamp string (release-point timing for HYSPLIT) |
| `phase` | flight phase; see values below |
| `Flight_Time_Seconds` | elapsed-time axis on every chart |
| `Lat`, `Lon` | flight path, route inference, HYSPLIT release points |
| `Alt_ft` | altitude filter / altitude charts |
| `CoreDustIngested_g` | **the core metric**: dust mass ingested by the engine core during that one timestep (not a running total) |
| `TAS_kn` | true airspeed (single-flight speed profile) |
| `vertical_rate` | climb/descent rate (single-flight profile) |

Any other columns are ignored, so a wider export is fine. 

**`phase` values:** `GROUND`, `CLIMB`, `LEVEL CLIMB`, `CRUISE`, `DESCENT`,
`LEVEL DESCENT`, `LEVEL FLIGHT`, `HOLD`, `UNKNOWN`. Only
`CLIMB, CRUISE, DESCENT, LEVEL DESCENT, LEVEL FLIGHT` are kept for the charts;
everything else (and any flight whose recording doesn't start in `CLIMB`) is
excluded by `build_dataset.py`, recorded in `dashboard/data/anomalous_flights.csv`
and shown on the Data quality tab.

### Optional per-aircraft-type summary

`<AIRCRAFT_TYPE>_Summary.csv` (e.g. `A333_Summary.csv`) with columns:

```
Flight_ID, origin_ICAO, destination_ICAO, origin_IATA, destination_IATA
```

`Flight_ID` matches the per-flight filename stem. This gives authoritative
routes. Without it, routes are inferred from the flight's first and last
position (see "Route inference" below).

## Manual setup (without the launcher)

```bash
python -m venv .venv && source .venv/bin/activate    # optional but recommended
pip install -r dashboard/requirements.txt

mkdir -p CSVFiles                 # then copy your per-flight CSVs into it

cd dashboard
python build_dataset.py          # -> data/timesteps.parquet, data/flights.parquet
uvicorn app:app --reload
```

Set `DUST_CSV_DIR` to read the CSVs from somewhere other than `CSVFiles/`. Re-run
`build_dataset.py` whenever your CSVs change.

## Prerequisites for the optional features

| | |
|---|---|
| **HYSPLIT** | NOAA ARL's dispersion model. Separate manual install, free registration at <https://www.ready.noaa.gov/HYSPLIT.php>. This repo only *runs* an existing install; it never bundles or downloads HYSPLIT itself. Needed only for the "Dust source attribution" tab. |
| **Ghostscript** | only if you want the PNG/GIF plots HYSPLIT's `concplot` produces; `brew install ghostscript` on macOS. |
| **NASA Earthdata login** | free, <https://urs.earthdata.nasa.gov/users/new>; for the MERRA-2 wind-vector and validation panels. |
| **EUMETSAT API key** | free, <https://api.eumetsat.int/api-key/>; for the SEVIRI dust-imagery lookup. |

## HYSPLIT setup

`hysplit_tools/backtrack.py` locates your HYSPLIT install from environment
variables, falling back to an OS default:

| env var | default (Windows) | default (macOS/Linux) |
|---|---|---|
| `HYSPLIT_DIR` | `C:\hysplit` | `~/hysplit` |
| `GHOSTSCRIPT_EXE` | `C:\Program Files\gs\gs10.07.1\bin\gswin64c.exe` | `/opt/homebrew/bin/gs` |
| `HYSPLIT_MET_DIR` | `<HYSPLIT_DIR>/metdata` | `<HYSPLIT_DIR>/metdata` |
| `HYSPLIT_WORK_BASE` | `<HYSPLIT_DIR>/working` | `<HYSPLIT_DIR>/working` |

If your install lives elsewhere, set `HYSPLIT_DIR` before starting the
dashboard and the other three follow automatically:

```bash
export HYSPLIT_DIR=/path/to/hysplit
```

`backtrack.py` expects the standard layout under `HYSPLIT_DIR`: `exec/`
(`hycs_std`, `con2asc`, `concplot`), `bdyfiles/`, `graphics/arlmap`. Keep
`HYSPLIT_WORK_BASE` **outside** any cloud-synced folder; a sync client that
copies scratch files mid-write corrupts HYSPLIT's output. GDAS met data is
downloaded on demand into `HYSPLIT_MET_DIR`. Runs and their outputs are written
under `hysplit_tools/hysplit_results/` (git-ignored).

To compute HYSPLIT results for many flights in one unattended batch, see the
docstring of `hysplit_tools/run_all_flights.py`.

## API credentials (optional)

Copy `dashboard/.env.example` to `dashboard/.env` and fill in whichever you need.
`.env` is git-ignored and is loaded automatically at startup; a real environment
variable set in your shell takes priority over the file.

| variable | for |
|---|---|
| `EARTHDATA_USERNAME`, `EARTHDATA_PASSWORD` | NASA Earthdata Login: MERRA-2 wind vectors, MODIS AOD and the validation panels. Without them those panels show "not configured"; the rest of the dashboard is unaffected. |
| `EUMETSAT_CONSUMER_KEY`, `EUMETSAT_CONSUMER_SECRET` | EUMETSAT Data Store: nearest SEVIRI dust-RGB scene lookup on the attribution tab. |

Nothing in this repo hardcodes a credential.

## Route inference

Flights from an aircraft type with no `<TYPE>_Summary.csv` get their origin and
destination from the first and last recorded position: `build_dataset.py` picks
the nearest known airport within 20 km. `infer_routes_from_position.py` is an
optional extra pass you can run after the build (30 km limit).

Both only know the **11 airports hardcoded in `app.py`'s `AIRPORTS` dict**
(mirrored in `build_dataset.py` and `infer_routes_from_position.py`), all on the
Arabian Peninsula: OBBI, OEDF, OEJN, OERK, OKKK, OMAA, OMDB, OMDW, OMSJ, OOMS,
OTHH. A position further than the limit from any of them is left as "route
unknown" rather than guessed wrong. **If your dataset covers a different region,
extend that dict first** (keep the three copies in sync) or the inference is
meaningless for your flights.

## Authorship and license

Written by Helin Taha during a School of Engineering research internship at the
University of Manchester. Copyright is held by the University of Manchester.

Released under the MIT License. See [LICENSE](LICENSE).
