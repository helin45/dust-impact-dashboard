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

## What's in the box

```
dust-impact-dashboard/
  dashboard/          FastAPI app + static frontend, plus the two dataset-prep scripts
    app.py              the web app (uvicorn app:app)
    build_dataset.py    turns a folder of flight CSVs into two compact Parquet files
    infer_routes_from_position.py   optional: fills in routes by nearest-airport lookup
    static/            HTML/CSS/JS frontend (Plotly)
    requirements.txt
  hysplit_tools/      scripts the dashboard shells out to / imports for the
                      HYSPLIT backward-dispersion ("dust source attribution") tab
  CSVFiles/           <- you create this and put your flight CSVs here (git-ignored)
```

The dashboard runs fully **without** HYSPLIT or any NASA/EUMETSAT credentials:
the aggregate view, single-flight view, route map and flight comparison all work
from the Parquet files alone. The extra panels (dust source attribution, MERRA-2
winds, satellite overlays) just report themselves as unavailable until you set
those pieces up.

## Prerequisites

| | |
|---|---|
| **Python** | 3.12 or newer |
| **A folder of per-flight CSVs** | your own data; see the schema below |
| **HYSPLIT** (optional) | NOAA ARL's dispersion model. Separate manual install, free registration at <https://www.ready.noaa.gov/HYSPLIT.php>. This repo only *runs* an existing install; it never bundles or downloads HYSPLIT itself. Needed only for the "Dust source attribution" tab's **HYSPLIT** method. |
| **Ghostscript** (optional) | only if you want the PNG/GIF plots HYSPLIT's `concplot` produces; `brew install ghostscript` on macOS. |
| **NASA Earthdata login** (optional) | free, <https://urs.earthdata.nasa.gov/users/new>; for the MERRA-2 wind-vector and validation panels. |
| **EUMETSAT API key** (optional) | free, <https://api.eumetsat.int/api-key/>; for the SEVIRI dust-imagery lookup. |

## Expected CSV schema

**One CSV per flight**, plus optionally one summary CSV per aircraft type. Put
them all in `CSVFiles/` at the repo root.

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

Any other columns are ignored, so a wider export is fine. The upstream pipeline
this dashboard was built against also produces, per timestep:
`Mach`, `KCAS`, `StaticTemperature_K`, `Density_kg_m3`, `AircraftWeight_kg`,
`CoreMassFlowRate_kg_s`, `Dust_small`, `Dust_medium`, `Dust_large`; none of
these are required by anything in this repo.

**`phase` values:** `GROUND`, `CLIMB`, `LEVEL CLIMB`, `CRUISE`, `DESCENT`,
`LEVEL DESCENT`, `LEVEL FLIGHT`, `HOLD`, `UNKNOWN`. Only
`CLIMB, CRUISE, DESCENT, LEVEL DESCENT, LEVEL FLIGHT` are kept for the charts;
everything else (and any flight whose recording doesn't start in `CLIMB`) is
dropped by `build_dataset.py` and listed in `data/anomalous_flights.csv` as a
record of what was excluded and why.

### Optional per-aircraft-type summary

`<AIRCRAFT_TYPE>_Summary.csv` (e.g. `A333_Summary.csv`) with columns:

```
Flight_ID, origin_ICAO, destination_ICAO, origin_IATA, destination_IATA
```

`Flight_ID` matches the per-flight filename stem. This gives authoritative
routes. Without it, flights from that aircraft type show "route unknown" until
you run `infer_routes_from_position.py` (see below), which guesses origin and
destination from the first and last recorded position.

## Setup

```bash
git clone <repo-url>
cd dust-impact-dashboard

python -m venv .venv && source .venv/bin/activate    # optional but recommended
pip install -r dashboard/requirements.txt

mkdir -p CSVFiles                 # then copy your per-flight CSVs into it

cd dashboard
python build_dataset.py          # -> data/timesteps.parquet, data/flights.parquet
python infer_routes_from_position.py   # optional, see note below
uvicorn app:app --reload
```

Then open <http://localhost:8000>. Re-run `build_dataset.py` whenever
`CSVFiles/` gains new flights or aircraft types.

## HYSPLIT setup (optional, for the "Dust source attribution" HYSPLIT method)

`hysplit_tools/backtrack.py` locates your HYSPLIT install from environment
variables, falling back to an OS default:

| env var | default (Windows) | default (macOS/Linux) |
|---|---|---|
| `HYSPLIT_DIR` | `C:\hysplit` | `~/hysplit` |
| `GHOSTSCRIPT_EXE` | `C:\Program Files\gs\gs10.07.1\bin\gswin64c.exe` | `/opt/homebrew/bin/gs` |
| `HYSPLIT_MET_DIR` | `<HYSPLIT_DIR>/metdata` | `<HYSPLIT_DIR>/metdata` |
| `HYSPLIT_WORK_BASE` | `<HYSPLIT_DIR>/working` | `<HYSPLIT_DIR>/working` |

If your install lives elsewhere, set `HYSPLIT_DIR` before starting uvicorn and
the other three follow automatically:

```bash
export HYSPLIT_DIR=/path/to/hysplit
```

`backtrack.py` expects the standard layout under `HYSPLIT_DIR`: `exec/`
(`hycs_std`, `con2asc`, `concplot`), `bdyfiles/`, `graphics/arlmap`. Keep
`HYSPLIT_WORK_BASE` **outside** any cloud-synced folder; a sync client that
copies scratch files mid-write corrupts HYSPLIT's output. GDAS met data is
downloaded on demand into `HYSPLIT_MET_DIR`. Runs and their outputs are written
under `hysplit_tools/hysplit_results/` (git-ignored).

The **Surrogate model** method on the same tab is a trained approximation of
HYSPLIT and needs no HYSPLIT install; but it can only learn from flights that
already have real HYSPLIT results, so it's for quick exploration only. See the
docstring in `hysplit_tools/surrogate_backtrack.py`.

## Optional environment variables

| env var | for |
|---|---|
| `EARTHDATA_USERNAME`, `EARTHDATA_PASSWORD` | NASA Earthdata Login: MERRA-2 wind vectors and the wind/AOD validation panels. Without them those panels show "not configured"; the rest of the dashboard is unaffected. |
| `EUMETSAT_CONSUMER_KEY`, `EUMETSAT_CONSUMER_SECRET` | EUMETSAT Data Store: nearest SEVIRI dust-RGB scene lookup on the attribution tab. |

Set them in your shell before `uvicorn`, or put them in a `.env` you source
yourself (`.env` is git-ignored). Nothing in this repo hardcodes a credential;
every one is read via `os.environ`.

## Note on `infer_routes_from_position.py`

Run it **after** `build_dataset.py`. For every flight with no `<TYPE>_Summary.csv`
route, it assigns the nearest airport to the first and last recorded position.

It only knows the **11 airports hardcoded in `app.py`'s `AIRPORTS` dict**
(and mirrored in `build_dataset.py` and `infer_routes_from_position.py`): all
on the Arabian Peninsula: OBBI, OEDF, OEJN, OERK, OKKK, OMAA, OMDB, OMDW, OMSJ,
OOMS, OTHH. A position more than ~30 km from any of them is left as "route
unknown" rather than guessed wrong. **If your dataset covers a different region,
extend that `AIRPORTS` dict first** (keep the three copies in sync) or the
inference is meaningless for your flights.

## Authorship and license

Written by Helin Taha during a School of Engineering research internship at the
University of Manchester. Copyright is held by the University of Manchester.

Released under the MIT License. See [LICENSE](LICENSE).
