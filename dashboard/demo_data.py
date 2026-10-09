"""Synthetic demo flights for `python run.py --demo`.

Everything here is randomly generated. None of it is real flight or dust data;
it only exists so the dashboard can be tried without any flight CSVs.
"""

import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20220301
STEP_S = 8
DAYS = [datetime(2022, 3, 1) + timedelta(days=d) for d in range(14)]
STORM = {"2022-03-06": 3.0, "2022-03-07": 8.0, "2022-03-08": 4.0}

AIRPORTS = {
    "OBBI": (26.2708, 50.6336, "BAH"), "OEDF": (26.4712, 49.7979, "DMM"),
    "OEJN": (21.6796, 39.1565, "JED"), "OERK": (24.9576, 46.6988, "RUH"),
    "OKKK": (29.2267, 47.9689, "KWI"), "OMAA": (24.4330, 54.6511, "AUH"),
    "OMDB": (25.2532, 55.3657, "DXB"), "OMDW": (24.8967, 55.1614, "DWC"),
    "OMSJ": (25.3286, 55.5172, "SHJ"), "OOMS": (23.5933, 58.2844, "MCT"),
    "OTHH": (25.2609, 51.6138, "DOH"),
}
FLEET = {
    "A333": {"tails": ["DEMO-A1", "DEMO-A2", "DEMO-A3"], "min_km": 600, "max_km": 2500, "cruise_kn": 450, "dust": 1.0},
    "A321": {"tails": ["DEMO-B1", "DEMO-B2", "DEMO-B3", "DEMO-B4"], "min_km": 300, "max_km": 1200, "cruise_kn": 430, "dust": 0.7},
}
COLUMNS = ["time", "phase", "Flight_Time_Seconds", "Lat", "Lon", "Alt_ft", "CoreDustIngested_g", "TAS_kn", "vertical_rate"]


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _smooth(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _flight_rows(rng, start, origin, dest, kind, storm):
    o, d = AIRPORTS[origin], AIRPORTS[dest]
    dist = _km(o, d)
    n = int((dist / 700 * 3600 + 600) / STEP_S) + 1
    cruise_alt = min(38000, max(24000, 20000 + 10 * dist))
    c_end, lf_end, d_start, ld_start = 0.22, 0.26, 0.72, 0.93
    burst, rows, prev_alt = 0, [], 0.0
    for i in range(n):
        f = i / (n - 1)
        if f < c_end:
            phase, alt = "CLIMB", cruise_alt * _smooth(f / c_end)
        elif f < lf_end:
            phase, alt = "LEVEL FLIGHT", cruise_alt
        elif f < d_start:
            phase, alt = "CRUISE", cruise_alt + rng.uniform(-150, 150)
        elif f < ld_start:
            phase, alt = "DESCENT", cruise_alt + (3000 - cruise_alt) * _smooth((f - d_start) / (ld_start - d_start))
        else:
            phase, alt = "LEVEL DESCENT", 3000 * (1 - (f - ld_start) / (1 - ld_start))
        alt = max(0.0, alt)
        vrate = (alt - prev_alt) / STEP_S * 60 if i else 0.0
        prev_alt = alt
        if burst == 0 and storm > 1 and rng.random() < 0.002:
            burst = 25
        boost = 15.0 if burst else 1.0
        burst = max(0, burst - 1)
        dust = 3.0 * math.exp(-alt / 14000) * storm * boost * kind["dust"] * rng.lognormvariate(0, 0.5)
        tas = 200 + (kind["cruise_kn"] - 200) * _smooth(f / c_end) if f < c_end else (
            kind["cruise_kn"] if f < d_start else 160 + (kind["cruise_kn"] - 160) * (1 - _smooth((f - d_start) / (1 - d_start))))
        wiggle = 0.05 * math.sin(f * math.pi * 6)
        rows.append({
            "time": (start + timedelta(seconds=STEP_S * i)).strftime("%Y-%m-%d %H:%M:%S"),
            "phase": phase, "Flight_Time_Seconds": STEP_S * i,
            "Lat": round(o[0] + (d[0] - o[0]) * f + wiggle, 5), "Lon": round(o[1] + (d[1] - o[1]) * f, 5),
            "Alt_ft": round(alt, 1), "CoreDustIngested_g": round(dust, 4),
            "TAS_kn": round(tas + rng.uniform(-4, 4), 1), "vertical_rate": round(vrate),
        })
    return rows


def _write(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def generate(out_dir):
    """Write the demo flight CSVs (plus an A333 summary file) into out_dir; returns the flight count."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    summary, count = [], 0
    pairs = {k: [(a, b) for a in AIRPORTS for b in AIRPORTS
                 if a != b and v["min_km"] <= _km(AIRPORTS[a], AIRPORTS[b]) <= v["max_km"]]
             for k, v in FLEET.items()}

    def emit(day, hhmm, actype, origin, dest, rows, tail_i):
        nonlocal count
        count += 1
        kind = FLEET[actype]
        tail = kind["tails"][tail_i % len(kind["tails"])]
        stem = f"{day:%Y_%m_%d}_{hhmm}_DE{count:04X}_DEMO{count:02d}_{actype}_{tail}"
        _write(out_dir / f"{stem}.csv", rows)
        if actype == "A333":
            summary.append({"Flight_ID": stem, "origin_ICAO": origin, "destination_ICAO": dest,
                            "origin_IATA": AIRPORTS[origin][2], "destination_IATA": AIRPORTS[dest][2]})

    for day in DAYS:
        storm = STORM.get(f"{day:%Y-%m-%d}", 1.0)
        for _ in range(rng.choice([2, 3])):
            actype = rng.choice(["A333", "A321"])
            origin, dest = rng.choice(pairs[actype])
            start = day + timedelta(hours=rng.randint(0, 22), minutes=rng.choice([5, 20, 35, 50]))
            rows = _flight_rows(rng, start, origin, dest, FLEET[actype], storm)
            emit(day, f"{start:%H%M}", actype, origin, dest, rows, rng.randrange(8))

    # two deliberately bad flights, so the Data quality tab has something to show
    day = DAYS[3]
    start = day + timedelta(hours=10, minutes=10)
    bad = [{"time": (start + timedelta(seconds=STEP_S * i)).strftime("%Y-%m-%d %H:%M:%S"), "phase": "UNKNOWN",
            "Flight_Time_Seconds": STEP_S * i, "Lat": 25.26, "Lon": 51.61, "Alt_ft": 0.0,
            "CoreDustIngested_g": 0.0, "TAS_kn": 0.0, "vertical_rate": 0} for i in range(60)]
    emit(day, "1010", "A333", "OTHH", "OMDB", bad, 0)
    day = DAYS[9]
    start = day + timedelta(hours=18, minutes=30)
    origin, dest = rng.choice(pairs["A321"])
    full = _flight_rows(rng, start, origin, dest, FLEET["A321"], 1.0)
    emit(day, "1830", "A321", origin, dest, full[int(len(full) * 0.35):], 1)  # recording starts mid-flight

    with open(out_dir / "A333_Summary.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Flight_ID", "origin_ICAO", "destination_ICAO", "origin_IATA", "destination_IATA"])
        w.writeheader()
        w.writerows(summary)
    return count


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo_csv")
    print(f"wrote {generate(target)} synthetic flights to {target}")
