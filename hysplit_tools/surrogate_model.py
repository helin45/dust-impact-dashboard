"""
Trains a fast statistical surrogate for HYSPLIT's backward-dispersion output,
so the dashboard can offer an instant approximate dust-source density map as
an alternative to a real hycs_std run (which needs HYSPLIT installed, GDAS
met-data downloads, and can take a long time per release point).

Trains on every already-computed HYSPLIT release point's raw per-cell
concentration output (hysplit_results/flights/*/combined_raw.csv -- one row
per grid cell per period per release-point run, see postprocess_results.py)
rather than on the flight-level density_grid.json: pooling at that finer
grain gives far more training rows than one row per flight would (tens of
thousands vs. a few dozen).

The learned function is (release point + query offset + how far back in the
run) -> log10(concentration) -- see surrogate_features.build_features() for
the exact inputs, shared with surrogate_backtrack.py so training and
inference can never drift apart. Predicting at a query OFFSET from the
release point (rather than an absolute lat/lon) is what lets one model serve
release points anywhere in the region, not just the ones already computed.

IMPORTANT LIMITATION: this model has never seen actual meteorology (the GDAS
wind fields HYSPLIT itself uses) -- only the outcomes of whichever flights
already have a real HYSPLIT run. It can only reproduce the climatological /
seasonal transport pattern implicit in that training set, not the specific
weather on a new flight's actual day. It's a fast approximation for
exploration, not a substitute for the real simulation -- see
surrogate_backtrack.py and dashboard/README.md for how it's labelled in the
UI.

Re-run any time more flights get a real HYSPLIT computation (more training
data only helps, this always rebuilds from scratch) -- see run_all_flights.py
for batch-computing HYSPLIT across every flight in CSVFiles instead of one
at a time:

    python surrogate_model.py

Requires scikit-learn and joblib (see requirements.txt).
"""
import concurrent.futures as cf
import glob
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold, cross_validate

import surrogate_features

DUST_FILES_DIR = Path(__file__).resolve().parent
FLIGHTS_DIR = DUST_FILES_DIR / "hysplit_results" / "flights"
MODEL_PATH = DUST_FILES_DIR / "hysplit_results" / "surrogate_model.joblib"

# Below this, con2asc's own output is numerical underflow noise, not a real
# signal (density_utils.build_flight_density's weight>0 mask keeps these as
# "positive" but they contribute ~nothing to the KDE there either) -- floor
# the target here before taking log10 so the regressor spends its capacity
# on the part of the range that actually matters instead of chasing noise
# down near float64's exponent limit.
CONCENTRATION_FLOOR = 1e-20

# Matches flight_backtrack.METHOD_SURROGATE -- not imported directly to keep
# this script's own dependency set light, but MUST stay in sync with it.
# Any combined_raw.csv living under a "surrogate" folder is this script's own
# past output, not a real HYSPLIT run -- training on it would let the model
# learn from (and drift further from reality around) its own guesses.
SURROGATE_DIRNAME = "surrogate"


def _combined_raw_paths():
    paths = []
    for path in glob.glob(str(FLIGHTS_DIR / "**" / "combined_raw.csv"), recursive=True):
        rel_parts = Path(path).relative_to(FLIGHTS_DIR).parts
        if SURROGATE_DIRNAME in rel_parts:
            continue
        paths.append(path)
    return sorted(paths)


def _load_pooled_rows():
    paths = _combined_raw_paths()
    if not paths:
        raise SystemExit(
            f"no combined_raw.csv files found under {FLIGHTS_DIR} -- run flight_backtrack.py for "
            "some flights first, this model can only learn from already-computed real HYSPLIT runs"
        )
    frames = []
    for i, path in enumerate(paths, start=1):
        if i == 1 or i % 20 == 0 or i == len(paths):
            print(f"  reading combined_raw.csv {i}/{len(paths)}", flush=True)
        # hysplit_results/ lives under OneDrive, whose file provider has been
        # observed to stall an individual read indefinitely (not just raise
        # an OSError) while a placeholder file downloads -- a plain retry
        # loop around pd.read_csv() never even triggers in that case, since
        # no exception occurs, it just blocks forever. Each attempt gets its
        # own ThreadPoolExecutor with shutdown(wait=False) (not a "with"
        # block, which would wait for the stalled thread on exit and undo
        # the timeout) so a stuck read is abandoned on schedule instead of
        # hanging the whole retrain -- same fix build_exact_wind_cache.py
        # uses for the same OneDrive behavior.
        df = None
        last_err = None
        for attempt in range(3):
            ex = cf.ThreadPoolExecutor(max_workers=1)
            fut = ex.submit(pd.read_csv, path)
            try:
                df = fut.result(timeout=30)
                ex.shutdown(wait=False)
                break
            except cf.TimeoutError as e:
                ex.shutdown(wait=False)
                last_err = e
                time.sleep(5 * (attempt + 1))
            except OSError as e:
                ex.shutdown(wait=False)
                last_err = e
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
        if df is None:
            print(f"  skipping {path} (still unreadable after retries: {last_err})")
            continue
        conc_col = next((c for c in df.columns if c.startswith("Dust")), None)
        if conc_col is None or df.empty:
            print(f"  skipping {path} (no Dust* column or empty)")
            continue
        frames.append(df.rename(columns={conc_col: "concentration"}))
    print(f"loaded {len(frames)}/{len(paths)} combined_raw.csv files")
    return pd.concat(frames, ignore_index=True)


def build_training_table(raw):
    """
    raw: pooled combined_raw.csv rows, tagged with that run's own
    release-point metadata. Column names kept exactly as combined_raw.csv
    has them rather than renamed here: "lat"/"lon" are the RELEASE point
    (lowercase), "LAT"/"LON" are the query grid cell con2asc reported a
    concentration for (uppercase) -- easy to mix up, so anyone cross
    checking against combined_raw.csv directly should see the same names
    used the same way here.

    Returns (features, target, groups) -- groups is flight_id per row, for
    GroupKFold below (a random split would leak correlated rows -- nearby
    grid cells from the same release point -- across train/val).
    """
    # format="ISO8601" (not a fixed strptime format): datetime.isoformat()
    # drops the fractional-second suffix when microseconds are exactly 0, so
    # a handful of rows lack it while the rest have ".NNNNNN" -- a single
    # fixed format can't parse both.
    start = pd.to_datetime(raw["start_utc"], utc=True, format="ISO8601")
    total_periods = pd.to_numeric(raw["total_periods"], errors="coerce").clip(lower=1)
    period_frac = pd.to_numeric(raw["period"], errors="coerce") / total_periods

    features = surrogate_features.build_features(
        release_lat=raw["lat"].to_numpy(dtype=float),
        release_lon=raw["lon"].to_numpy(dtype=float),
        release_alt_ft=raw["alt_ft"].to_numpy(dtype=float),
        release_month=start.dt.month.to_numpy(dtype=float),
        release_hour=(start.dt.hour + start.dt.minute / 60).to_numpy(dtype=float),
        runtime_hours=raw["runtime_hours"].to_numpy(dtype=float),
        period_frac=period_frac.to_numpy(dtype=float),
        cand_lat=raw["LAT"].to_numpy(dtype=float),
        cand_lon=raw["LON"].to_numpy(dtype=float),
        release_day_of_year=start.dt.dayofyear.to_numpy(dtype=float),
        # "{flight_id}__{point_label}", matching build_exact_wind_cache.py's
        # own key: one flight can have several release points at different
        # times/altitudes, so flight_id alone would be too coarse a key for
        # the exact wind lookup. point_label always exists here since it's
        # one of index.csv's own columns, carried through by
        # postprocess_results.process_index() into combined_raw.csv.
        release_id=(raw["flight_id"].astype(str) + "__" + raw["point_label"].astype(str)).to_numpy(),
    )
    concentration = pd.to_numeric(raw["concentration"], errors="coerce").fillna(0.0).clip(lower=CONCENTRATION_FLOOR)
    target = np.log10(concentration)
    groups = raw["flight_id"].astype(str).to_numpy()

    valid = features.notna().all(axis=1) & target.notna()
    if not valid.all():
        print(f"dropping {(~valid).sum()} rows with missing feature/target values")
    return features[valid].reset_index(drop=True), target[valid].reset_index(drop=True), groups[valid.to_numpy()]


def main():
    print(f"scanning {FLIGHTS_DIR} for combined_raw.csv files...")
    raw = _load_pooled_rows()
    features, target, groups = build_training_table(raw)
    n_flights = len(set(groups))
    print(f"training rows: {len(features)}, distinct flights: {n_flights}")

    model = HistGradientBoostingRegressor(
        loss="squared_error",
        max_depth=8,
        max_iter=300,
        learning_rate=0.05,
        l2_regularization=1.0,
        random_state=0,
    )

    cv_mae = cv_mae_std = None
    if n_flights >= 3:
        # GroupKFold, not a plain/random KFold -- a random split would put
        # different periods/grid-cells from the SAME flight's SAME release
        # point in both train and val. Those rows are highly correlated
        # (same release point, nearby query cells), so a random split makes
        # the reported score look far better than how the model will
        # actually do on a flight it's never seen any part of.
        n_splits = min(5, n_flights)
        cv = GroupKFold(n_splits=n_splits)
        # One cross_validate call rather than three separate cross_val_score
        # calls -- same CV splits, same fitted folds, just scored three ways,
        # so this doesn't triple the actual fitting work.
        scores = cross_validate(
            model, features, target, groups=groups, cv=cv,
            scoring=["neg_mean_absolute_error", "neg_root_mean_squared_error", "r2"], n_jobs=-1,
        )
        cv_mae, cv_mae_std = float(-scores["test_neg_mean_absolute_error"].mean()), float(scores["test_neg_mean_absolute_error"].std())
        cv_rmse, cv_rmse_std = float(-scores["test_neg_root_mean_squared_error"].mean()), float(scores["test_neg_root_mean_squared_error"].std())
        cv_r2, cv_r2_std = float(scores["test_r2"].mean()), float(scores["test_r2"].std())
        print(f"{n_splits}-fold grouped-by-flight CV (log10 concentration): "
              f"MAE {cv_mae:.3f} +/- {cv_mae_std:.3f}, RMSE {cv_rmse:.3f} +/- {cv_rmse_std:.3f}, "
              f"R2 {cv_r2:.3f} +/- {cv_r2_std:.3f}")
    else:
        cv_rmse = cv_rmse_std = cv_r2 = cv_r2_std = None
        print("too few distinct flights for cross-validation (need >= 3) -- skipping")

    model.fit(features, target)

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": model,
        "feature_cols": surrogate_features.FEATURE_COLS,
        # Predictions are clipped to this range at inference -- keeps a
        # release point far outside the training distribution from
        # extrapolating to an absurd concentration instead of just a
        # low-confidence flat one.
        "target_log_range": (float(target.min()), float(target.max())),
        "n_training_rows": int(len(features)),
        "n_training_flights": int(n_flights),
        "concentration_floor": CONCENTRATION_FLOOR,
        "cv_mae": cv_mae,
        "cv_mae_std": cv_mae_std,
        "cv_rmse": cv_rmse,
        "cv_rmse_std": cv_rmse_std,
        "cv_r2": cv_r2,
        "cv_r2_std": cv_r2_std,
        "trained_utc": datetime.now(timezone.utc).isoformat(),
    }, MODEL_PATH)
    print(f"wrote {MODEL_PATH}")


if __name__ == "__main__":
    main()
