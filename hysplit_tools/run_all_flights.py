"""
Batch-computes real HYSPLIT dust-source density for as many flights in
CSVFiles/ (repo root) as possible.

Skips any flight that already has a density_grid.json for the requested
strategy -- interrupting and re-running this script is the expected way to
use it (a few hundred flights x ~10 HYSPLIT runs each, each needing GDAS
downloads, is a long unattended job), so it always picks up where it left
off instead of redoing already-computed flights. A flight this script can't
usably process -- missing/unexpected CSV columns, an unexpected filename, no
rows in a real flight phase -- is logged and skipped, never stops the batch;
appropriate given the current CSVFiles are known to have data-quality
problems (this script is meant to be re-run once cleaner replacement flight
data is in place, without needing to change anything here).

Run (from the machine with HYSPLIT installed):
    python run_all_flights.py                    # every not-yet-computed flight, topn/48h/10pts
    python run_all_flights.py --dry-run           # just report how many flights would run, and exit
    python run_all_flights.py --limit 20          # stop after 20 NEWLY computed flights this run
    python run_all_flights.py --max-hours 8        # stop starting new flights after 8 wall-clock hours
    python run_all_flights.py --shuffle            # random flight order instead of chronological --
                                                     # gives broader date/route coverage if interrupted
                                                     # partway through a huge CSVFiles folder
    python run_all_flights.py --hours 72 --points 15 --strategy topn

Progress and failures are appended to hysplit_results/batch_compute_log.csv
as they happen (one row per flight), so an in-progress run can be monitored,
and a finished one reviewed, without waiting on the whole batch.
"""
import argparse
import csv
import os
import random
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import backtrack
import flight_backtrack

DUST_FILES_DIR = Path(__file__).resolve().parent
CSV_DIR = Path(os.environ.get("DUST_CSV_DIR") or DUST_FILES_DIR.parent / "CSVFiles").expanduser()  # DUST_CSV_DIR overrides the default repo-root CSVFiles/
# Outside OneDrive (like backtrack.WORK_BASE, for the same reason -- see its
# comment): this log gets appended to once per flight, far more often than
# any other single file this batch touches, and OneDrive's file provider
# was observed to serve ETIMEDOUT on reads of a file still mid-upload,
# indefinitely, for as long as writes to it kept re-triggering sync churn.
LOG_PATH = Path(backtrack.WORK_BASE) / "batch_compute_log.csv"

LOG_FIELDS = ["timestamp_utc", "flight_id", "status", "elapsed_s", "strategy", "n_points", "runtime_hours", "error"]


def list_flight_ids():
    """
    Same convention as dashboard/build_dataset.py: every CSVFiles/*.csv
    except the per-aircraft-type "<TYPE>_Summary.csv" lookup files.
    """
    return sorted(p.stem for p in CSV_DIR.glob("*.csv") if not p.name.endswith("_Summary.csv"))


def already_computed(flight_id, strategy):
    path = Path(flight_backtrack.flight_dir_for(flight_id, strategy)) / "density_grid.json"
    return path.exists()


def append_log(row):
    # hysplit_results/ lives under OneDrive, whose cloud-sync file provider
    # occasionally throws a transient TimeoutError on open() under the
    # sustained per-flight I/O this batch generates (observed ~1 in every
    # 4-5 flights) -- retried rather than left to propagate, since an
    # unhandled error here would kill the whole batch on a purely transient
    # hiccup even though every flight-level failure right above this is
    # already designed to be logged and skipped, never fatal.
    write_header = not LOG_PATH.exists()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(5):
        try:
            with open(LOG_PATH, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
                if write_header:
                    writer.writeheader()
                writer.writerow(row)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=int, default=None,
                         help="backward-trajectory length in hours (default: flight_backtrack's RUNTIME_HOURS)")
    parser.add_argument("--points", type=int, default=None,
                         help="release points per flight, topn/dense only (default: flight_backtrack's N_RELEASE_POINTS)")
    parser.add_argument("--strategy", choices=flight_backtrack.STRATEGIES, default=flight_backtrack.STRATEGY_TOPN,
                         help="release-point selection strategy (default: topn)")
    parser.add_argument("--limit", type=int, default=None,
                         help="stop after this many NEWLY computed flights this run (default: no limit -- "
                              "attempt every not-yet-computed flight)")
    parser.add_argument("--max-hours", type=float, default=None,
                         help="stop starting new flights once this many wall-clock hours have elapsed "
                              "(the flight already in progress still finishes)")
    parser.add_argument("--shuffle", action="store_true",
                         help="random flight order instead of chronological -- broader date/route coverage "
                              "if the batch gets interrupted partway through a large CSVFiles folder")
    parser.add_argument("--seed", type=int, default=0, help="shuffle order seed, for a reproducible order across resumed runs")
    parser.add_argument("--dry-run", action="store_true", help="report how many flights would be computed, then exit")
    args = parser.parse_args()

    runtime_hours = -abs(args.hours) if args.hours is not None else flight_backtrack.RUNTIME_HOURS
    n_points = args.points if args.points is not None else flight_backtrack.N_RELEASE_POINTS

    flight_ids = list_flight_ids()
    if not flight_ids:
        raise SystemExit(f"no flight CSVs found in {CSV_DIR}")
    if args.shuffle:
        random.Random(args.seed).shuffle(flight_ids)

    todo = [fid for fid in flight_ids if not already_computed(fid, args.strategy)]
    already_done = len(flight_ids) - len(todo)
    print(f"{len(flight_ids)} flight CSVs in {CSV_DIR}")
    print(f"{already_done} already computed for {args.strategy}/hysplit, {len(todo)} remaining")

    if args.dry_run:
        preview = ", ".join(todo[:10]) + (", ..." if len(todo) > 10 else "")
        print(f"--dry-run: would attempt {len(todo)} flight(s): {preview or '(none)'}")
        return

    start_time = time.time()
    n_ok = n_failed = n_attempted = 0
    for i, flight_id in enumerate(todo, start=1):
        if args.limit is not None and n_attempted >= args.limit:
            print(f"reached --limit {args.limit}, stopping ({len(todo) - n_attempted} flight(s) left for next run)")
            break
        if args.max_hours is not None and (time.time() - start_time) / 3600 >= args.max_hours:
            print(f"reached --max-hours {args.max_hours}, stopping ({len(todo) - n_attempted} flight(s) left for next run)")
            break

        print(f"[{i}/{len(todo)}] {flight_id}")
        t0 = time.time()
        try:
            ok, last_error = flight_backtrack.run_flight(
                flight_id, runtime_hours=runtime_hours, n_points=n_points,
                strategy=args.strategy,
            )
            status, error = ("ok", None) if ok else ("no_usable_points", last_error)
        except Exception as e:
            ok, status, error = False, "error", str(e)
            traceback.print_exc()
        elapsed = time.time() - t0
        n_attempted += 1
        if ok:
            n_ok += 1
        else:
            n_failed += 1
            print(f"  {flight_id}: {status} -- {error}")
        append_log({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(), "flight_id": flight_id,
            "status": status, "elapsed_s": round(elapsed, 1),
            "strategy": args.strategy, "n_points": n_points, "runtime_hours": runtime_hours, "error": error,
        })  # written after every flight, so an interrupted batch loses nothing and can just be re-run to resume

    print()
    print(f"batch finished: {n_ok} succeeded, {n_failed} failed/had no usable points this run "
          f"({n_attempted} attempted, {len(todo) - n_attempted} left for next run)")
    print(f"{already_done + n_ok} flight(s) now computed in total for {args.strategy}/hysplit")
    print(f"log: {LOG_PATH}")


if __name__ == "__main__":
    main()
