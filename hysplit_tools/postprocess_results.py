"""
Post-processing: combines every run's raw_data.csv (per-grid-cell dust
concentration, per period -- columns: period, DAY, HR, LAT, LON, Dust00100)
with that run's metadata from an index CSV into one tidy analysis table,
plus a compact per-run/per-period summary for quick comparison across a sweep.

Processes every index listed in INDEX_CSVS below in one run -- currently the
perimeter-point results and the OEJN storm/nominal matrix results. Each is
handled independently and just carries along whatever metadata columns that
particular index has (perimeter_point, or multiplier/regime/altitude_ft/etc).
An index that doesn't exist yet (e.g. you haven't run that batch) is skipped
with a message rather than erroring.

Outputs, written next to each INDEX_CSV:
  combined_raw.csv -- every run's raw_data.csv stacked, tagged with that
                       run's metadata (one row per grid cell per period per run).
                       This is the "interpret however you like" master table.
  run_summary.csv   -- one row per run per period: total mass, mass-weighted
                        centroid lat/lon, and max concentration -- a numeric
                        version of what the GIF shows visually, so you can
                        plot/compare storm progression across many runs at once
                        (e.g. group by altitude_ft and compare centroid drift).
"""

import os
from pathlib import Path

import pandas as pd

# Only used by the standalone `python postprocess_results.py` batch entry point
# (INDEX_CSVS / main() below). flight_backtrack.py calls process_index(index_csv)
# with an explicit path and never touches this.
DUST_FILES_DIR = str(Path(__file__).resolve().parent)

INDEX_CSVS = [
    os.path.join(DUST_FILES_DIR, "hysplit_results", "perimeter_index.csv"),
    os.path.join(DUST_FILES_DIR, "hysplit_results", "OEJN", "oejn_matrix_index.csv"),
]


def summarize_run(raw, meta_cols, row):
    conc_col = next(c for c in raw.columns if c.startswith("Dust"))

    def per_period(g):
        total = g[conc_col].sum()
        return pd.Series({
            "total_mass": total,
            "centroid_lat": (g["LAT"] * g[conc_col]).sum() / total if total else float("nan"),
            "centroid_lon": (g["LON"] * g[conc_col]).sum() / total if total else float("nan"),
            "max_conc": g[conc_col].max(),
            "n_grid_cells": len(g),
        })

    summary = raw.groupby("period").apply(per_period, include_groups=False).reset_index()
    for col in meta_cols:
        summary[col] = row[col]
    return summary


def process_index(index_csv):
    if not os.path.exists(index_csv):
        print(f"skipping {index_csv} (doesn't exist yet -- that batch hasn't been run)")
        return

    index = pd.read_csv(index_csv)
    # index CSVs are append-only (a rerun adds new rows rather than replacing
    # old ones, so a crash mid-batch never loses progress) -- so the same
    # run_label can appear multiple times across reruns. Only the latest
    # attempt reflects current reality.
    #
    # run_label alone isn't globally unique for the OEJN matrix index (it
    # omits multiplier/regime, so the same date+hour+altitude+particle-size
    # can legitimately appear under different sigma levels) -- include those
    # columns in the dedup key when present, so genuinely different runs
    # don't get collapsed into one.
    key_candidates = ["multiplier", "regime", "sweep", "date", "hour_utc", "altitude_ft", "particle_diameter_um"]
    dedup_cols = ["run_label"] + [c for c in key_candidates if c in index.columns]
    index = index.drop_duplicates(subset=dedup_cols, keep="last")
    ok = index[index["status"] == "ok"].copy()
    if ok.empty:
        print(f"{index_csv}: no successful runs yet")
        return

    meta_cols = [c for c in ok.columns if c not in ("status", "gif", "raw_csv", "result_png", "error")]

    combined_parts = []
    summary_parts = []
    skipped = 0
    for _, row in ok.iterrows():
        raw_path = row.get("raw_csv")
        if not isinstance(raw_path, str) or not os.path.exists(raw_path):
            skipped += 1  # e.g. a run made before the GIF/raw-CSV pipeline existed
            continue
        raw = pd.read_csv(raw_path)

        tagged = raw.copy()
        for col in meta_cols:
            tagged[col] = row[col]
        combined_parts.append(tagged)

        summary_parts.append(summarize_run(raw, meta_cols, row))

    out_dir = os.path.dirname(index_csv)
    if combined_parts:
        pd.concat(combined_parts, ignore_index=True).to_csv(
            os.path.join(out_dir, "combined_raw.csv"), index=False
        )
    if summary_parts:
        pd.concat(summary_parts, ignore_index=True).to_csv(
            os.path.join(out_dir, "run_summary.csv"), index=False
        )

    print(f"{index_csv}: {len(combined_parts)} runs combined, {skipped} skipped (no raw_csv)")
    print(f"  wrote {out_dir}\\combined_raw.csv and {out_dir}\\run_summary.csv")


def main():
    for index_csv in INDEX_CSVS:
        process_index(index_csv)


if __name__ == "__main__":
    main()
