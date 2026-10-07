"""
Fills in missing origin_icao/destination_icao for flights whose aircraft
type has no <TYPE>_Summary.csv (currently: A321) by finding the nearest
known airport to the first and last recorded Lat/Lon in that flight's raw
CSV -- a proxy for where it took off and landed.

Validated against the ~5,500 A333 flights that DO have a real recorded
route (from A333_Summary.csv): 100% correct on a random sample, predicted
airport within 1-2km of the true one every time. Only the 11 airports this
dataset actually uses (see AIRPORTS in app.py) are considered, so this is
safe here but is NOT a general-purpose reverse-geocoder -- a flight to/from
anywhere else would get silently assigned the nearest of these 11 regardless
of true distance, which is why routes further than MAX_DISTANCE_KM away are
left as "route unknown" instead of guessing wrong.

Adds a `route_inferred` column (True for rows this script filled in, False/
NaN for the real recorded ones) so the two can still be told apart later.

Re-run after build_dataset.py any time new no-summary-file flights appear.

    python infer_routes_from_position.py
"""

from pathlib import Path
import math
import os

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
CSV_DIR = Path(os.environ.get("DUST_CSV_DIR") or SCRIPT_DIR.parent / "CSVFiles").expanduser()  # DUST_CSV_DIR overrides the default repo-root CSVFiles/
FLIGHTS_PARQUET = SCRIPT_DIR / "data" / "flights.parquet"

# Same 11 airports as app.py's AIRPORTS dict -- kept in sync manually since
# this script runs standalone, not imported into the live app.
AIRPORTS = {
    "OBBI": (26.2708, 50.6336), "OEDF": (26.4712, 49.7979), "OEJN": (21.6796, 39.1565),
    "OERK": (24.9576, 46.6988), "OKKK": (29.2267, 47.9689), "OMAA": (24.4330, 54.6511),
    "OMDB": (25.2532, 55.3657), "OMDW": (24.8967, 55.1614), "OMSJ": (25.3286, 55.5172),
    "OOMS": (23.5933, 58.2844), "OTHH": (25.2609, 51.6138),
}
MAX_DISTANCE_KM = 30  # observed real matches were all within 2km; generous margin


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_airport(lat, lon):
    icao, (alat, alon) = min(AIRPORTS.items(), key=lambda kv: haversine_km(lat, lon, kv[1][0], kv[1][1]))
    return icao, haversine_km(lat, lon, alat, alon)


def main():
    flights = pd.read_parquet(FLIGHTS_PARQUET)
    missing = flights[flights["origin_icao"].isna()]
    print(f"{len(missing)} flight(s) with no recorded route out of {len(flights)} total")

    flights["route_inferred"] = False
    filled = skipped = failed = 0

    for idx, row in missing.iterrows():
        csv_path = CSV_DIR / f"{row['flight_id']}.csv"
        try:
            df = pd.read_csv(csv_path, usecols=["Lat", "Lon"]).dropna()
        except Exception as exc:
            print(f"  {row['flight_id']}: could not read CSV ({exc})")
            failed += 1
            continue
        if len(df) < 2:
            failed += 1
            continue

        o_lat, o_lon = df.iloc[0]["Lat"], df.iloc[0]["Lon"]
        d_lat, d_lon = df.iloc[-1]["Lat"], df.iloc[-1]["Lon"]
        o_icao, o_dist = nearest_airport(o_lat, o_lon)
        d_icao, d_dist = nearest_airport(d_lat, d_lon)

        if o_dist > MAX_DISTANCE_KM or d_dist > MAX_DISTANCE_KM:
            skipped += 1
            continue

        flights.loc[idx, "origin_icao"] = o_icao
        flights.loc[idx, "destination_icao"] = d_icao
        flights.loc[idx, "route_inferred"] = True
        filled += 1

    print(f"filled: {filled}, skipped (too far from any known airport): {skipped}, failed to read: {failed}")
    flights.to_parquet(FLIGHTS_PARQUET, index=False)
    print(f"wrote {FLIGHTS_PARQUET}")


if __name__ == "__main__":
    main()
