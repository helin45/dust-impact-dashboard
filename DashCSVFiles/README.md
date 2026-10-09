# DashCSVFiles

Put your per-flight CSV files in this folder, then run `python run.py` from the
repository root. Only this note is tracked by git; your data never is.

**One CSV per flight**, named

    YYYY_MM_DD_HHMM_hexcode_callsign_actype_registration.csv

for example `2022_03_01_0205_06A041_QTR46M_A333_A7-AEG.csv`.

**Columns the dashboard reads:** `time`, `phase`, `Flight_Time_Seconds`, `Lat`,
`Lon`, `Alt_ft`, `CoreDustIngested_g`, `TAS_kn`, `vertical_rate`. Any other
columns are ignored.

**Optional:** one `<AIRCRAFT_TYPE>_Summary.csv` per aircraft type (for example
`A333_Summary.csv`) with the columns `Flight_ID`, `origin_ICAO`,
`destination_ICAO`, `origin_IATA`, `destination_IATA`, to give flights their
routes.

The full description of the format, the phase values and the setup steps is in
the main README at the repository root.
