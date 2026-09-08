"""
Validates real HYSPLIT dust-source attribution against the independent
susceptibility grid (build_susceptibility_grid.py) -- two methods built
from entirely separate data (backward-trajectory transport modeling vs.
soil/land-cover susceptibility), so agreement between them is a genuine
cross-check, not circular.

For every flight with a completed real HYSPLIT run (hysplit_results/flights/
*/density_grid.json, topn strategy), computes what percentage of that
flight's own top-25%-density cells also fall in a susceptibility-confirmed
area (medium tier or higher) -- the same "corroboration %" the dashboard's
Results tab reports per-flight, run here in batch across every flight
already computed rather than one at a time.

Requires hysplit_results/susceptibility_grid.json to already exist (run
build_susceptibility_grid.py first if not).

Run (one-time/occasional, like build_susceptibility_grid.py itself):

    python validate_susceptibility_agreement.py
"""
import concurrent.futures
import csv
import glob
import json
from pathlib import Path

import numpy as np

DUST_FILES_DIR = Path(__file__).resolve().parent
RESULTS_DIR = DUST_FILES_DIR / "hysplit_results"
SUSCEPTIBILITY_PATH = RESULTS_DIR / "susceptibility_grid.json"
OUT_CSV = RESULTS_DIR / "susceptibility_agreement.csv"

# hysplit_results/ lives under OneDrive, whose file provider has been
# observed to block an individual open()/read() indefinitely (no exception,
# no return, for minutes at a stretch) while a sync backlog drains -- same
# issue run_all_flights.py's append_log() and build_dataset.py's CSV reads
# work around. Bounded via a worker thread so one stalled flight can't hang
# the whole batch; skipped (not fatal) if it still won't read in time.
_IO_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="onedrive-io")


def _load_json_bounded(path, timeout=20.0):
    def _read():
        with open(path) as f:
            return json.load(f)
    return _IO_EXECUTOR.submit(_read).result(timeout=timeout)

# Matches the dashboard's own convention (app.js gateDensityBySusceptibility
# + susceptibilityCorroborationPct, used by the Results tab) -- top 25% of
# a flight's OWN density distribution counts as "high density", same
# percentile threshold the AOD/surrogate-overlap agreement stats use.
DENSITY_PERCENTILE = 75


def load_susceptibility():
    susc = _load_json_bounded(SUSCEPTIBILITY_PATH, timeout=60.0)
    return {
        "lon": np.array(susc["lon"]), "lat": np.array(susc["lat"]),
        "tier": np.array(susc["tier"]), "medium": susc.get("tier_thresholds", {}).get("medium", 2),
    }


def gate_by_susceptibility(density, lon, lat, susc):
    """Zeroes out cells whose nearest susceptibility grid cell is below 'medium' tier."""
    lon_step = susc["lon"][1] - susc["lon"][0]
    lat_step = susc["lat"][1] - susc["lat"][0]
    si = np.clip(np.round((np.asarray(lat) - susc["lat"][0]) / lat_step).astype(int), 0, len(susc["lat"]) - 1)
    sj = np.clip(np.round((np.asarray(lon) - susc["lon"][0]) / lon_step).astype(int), 0, len(susc["lon"]) - 1)
    tier_at_cell = susc["tier"][np.ix_(si, sj)]
    return np.where(tier_at_cell >= susc["medium"], density, 0.0)


def corroboration_pct(density_json, susc):
    """% of this flight's own top-DENSITY_PERCENTILE cells that are also susceptibility-confirmed, or None if there are no cells to threshold."""
    density = np.array(density_json["density"])
    if density.size == 0:
        return None
    cut = np.percentile(density, DENSITY_PERCENTILE)
    high_mask = density >= cut
    total = int(high_mask.sum())
    if total == 0:
        return None
    gated = gate_by_susceptibility(density, density_json["lon"], density_json["lat"], susc)
    supported = int((gated[high_mask] > 0).sum())
    return 100 * supported / total


def main():
    if not SUSCEPTIBILITY_PATH.exists():
        raise SystemExit(f"{SUSCEPTIBILITY_PATH} doesn't exist -- run build_susceptibility_grid.py first")
    susc = load_susceptibility()

    paths = sorted(glob.glob(str(RESULTS_DIR / "flights" / "*" / "density_grid.json")))
    print(f"{len(paths)} flight(s) with a computed HYSPLIT density_grid.json")

    rows = []
    skipped = 0
    for i, path in enumerate(paths, start=1):
        flight_id = Path(path).parent.name
        try:
            density_json = _load_json_bounded(path)
            pct = corroboration_pct(density_json, susc)
        except Exception as e:
            print(f"  [{i}/{len(paths)}] skipping {flight_id}: {e}")
            skipped += 1
            continue
        if pct is None:
            skipped += 1
            continue
        if i % 50 == 0:
            print(f"  [{i}/{len(paths)}] ...")
        rows.append({"flight_id": flight_id, "susceptibility_agreement_pct": pct})

    if not rows:
        raise SystemExit("no flights produced a usable agreement percentage")

    pcts = np.array([r["susceptibility_agreement_pct"] for r in rows])
    print(f"processed {len(rows)} flight(s), skipped {skipped} (no usable high-density cells)")
    print(f"mean: {pcts.mean():.1f}%, median: {np.median(pcts):.1f}%, std: {pcts.std():.1f}%")
    print(f"min: {pcts.min():.1f}%, max: {pcts.max():.1f}%")
    print(f"25th pct: {np.percentile(pcts, 25):.1f}%, 75th pct: {np.percentile(pcts, 75):.1f}%")

    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["flight_id", "susceptibility_agreement_pct"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: -r["susceptibility_agreement_pct"]))
    print(f"wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
