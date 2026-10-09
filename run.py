#!/usr/bin/env python3
"""One-command launcher: python run.py  (see README.md for options)."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASH = ROOT / "dashboard"
DEMO_DIR = ROOT / ".demo"
VENV = ROOT / ".venv"
REQ = DASH / "requirements.txt"
REQ_STAMP = VENV / ".requirements.sha256"
MIN_PY = (3, 12)
CORE_PACKAGES = {"fastapi", "uvicorn", "duckdb", "pandas", "numpy", "pyarrow",
                 "scipy", "matplotlib", "requests", "python-dotenv"}


def say(msg):
    print(f"[run] {msg}", flush=True)


def die(msg):
    print(f"\n[run] ERROR: {msg}\n", file=sys.stderr, flush=True)
    sys.exit(1)


def venv_python():
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def pip_install(*args):
    cmd = [str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check",
           "--progress-bar", "off", *args]
    return subprocess.call(cmd)


def ensure_venv():
    if venv_python().exists():
        return
    say("creating virtual environment in .venv (first run only)")
    venv.EnvBuilder(with_pip=True).create(VENV)


def ensure_requirements(force):
    """Returns True if packages were (re)installed."""
    digest = hashlib.sha256(REQ.read_bytes()).hexdigest()
    if not force and REQ_STAMP.exists() and REQ_STAMP.read_text().strip() == digest:
        return False
    say("installing requirements (first run takes a few minutes)")
    if pip_install("-r", str(REQ)) != 0:
        say("bulk install failed, retrying package by package (optional packages may be skipped)")
        failed_core, skipped = [], []
        for line in REQ.read_text().splitlines():
            pkg = line.split("#")[0].strip()
            if pkg and pip_install(pkg) != 0:
                (failed_core if pkg.lower() in CORE_PACKAGES else skipped).append(pkg)
        if failed_core:
            die(f"could not install required packages: {', '.join(failed_core)}")
        if skipped:
            say(f"skipped optional packages: {', '.join(skipped)} (the related panels will show 'not configured')")
    REQ_STAMP.write_text(digest)
    return True


def preflight():
    # Managed Windows PCs can block a freshly installed unsigned library on its first load only.
    code = "import matplotlib.pyplot, duckdb, pandas, numpy, scipy, pyarrow, requests, fastapi, uvicorn, dotenv"
    for attempt in (1, 2, 3):
        r = subprocess.run([str(venv_python()), "-c", code], capture_output=True, text=True)
        if r.returncode == 0:
            return
        last = (r.stderr.strip().splitlines() or ["unknown error"])[-1]
        if "Application Control" in r.stderr and attempt < 3:
            say("Windows Application Control blocked a newly installed library; waiting 30 s and retrying")
            time.sleep(30)
            continue
        die(f"a required package failed to import: {last}\n"
            "If this mentions Application Control or AppLocker, a security policy is blocking new compiled "
            "libraries: wait a minute and run again, or ask IT to allow this folder.")


def count_flights(csv_dir):
    return sum(1 for p in csv_dir.glob("*.csv") if not p.name.endswith("_Summary.csv"))


def resolve_csv_dir(arg):
    default = ROOT / "CSVFiles"
    chosen = arg or os.environ.get("DUST_CSV_DIR")
    csv_dir = Path(chosen).expanduser().resolve() if chosen else default
    if not csv_dir.is_dir():
        if chosen:
            die(f"CSV folder not found: {csv_dir}")
        csv_dir.mkdir()
        die(f"no flight data yet. Put your per-flight CSVs in:\n  {csv_dir}\n"
            "(or run: python run.py --csv-dir <folder>, or try: python run.py --demo). "
            "Expected CSV format: see README.md.")
    n = count_flights(csv_dir)
    if n == 0:
        die(f"no flight CSVs found in {csv_dir}. Expected CSV format: see README.md "
            "(or try: python run.py --demo).")
    return csv_dir, n


def prepare_demo(force):
    sys.path.insert(0, str(DASH))
    import demo_data
    csv_dir = DEMO_DIR / "csv"
    existing = list(csv_dir.glob("*.csv")) if csv_dir.is_dir() else []
    if force or not existing:
        for p in existing:
            p.unlink()
        n = demo_data.generate(csv_dir)
        say(f"generated {n} synthetic demo flights in {csv_dir}")
    return csv_dir, count_flights(csv_dir)


def dataset_stale(data_dir, csv_dir, n_csv):
    ts, fl, stamp_file = data_dir / "timesteps.parquet", data_dir / "flights.parquet", data_dir / ".build_stamp.json"
    if not (ts.exists() and fl.exists() and stamp_file.exists()):
        return True
    try:
        stamp = json.loads(stamp_file.read_text())
    except ValueError:
        return True
    if stamp.get("csv_dir") != str(csv_dir) or stamp.get("n_csv") != n_csv:
        return True
    built = min(ts.stat().st_mtime, fl.stat().st_mtime)
    newest = max((p.stat().st_mtime for p in csv_dir.glob("*.csv")), default=0)
    return newest > built


def ensure_dataset(data_dir, csv_dir, n_csv, env, force):
    if not force and not dataset_stale(data_dir, csv_dir, n_csv):
        say("dataset is up to date")
        return
    say(f"building dataset from {n_csv:,} flight CSVs (this can take a while for large folders)")
    if subprocess.call([str(venv_python()), "build_dataset.py"], cwd=DASH, env=env) != 0:
        die("build_dataset.py failed (see the messages above).")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ".build_stamp.json").write_text(json.dumps({"csv_dir": str(csv_dir), "n_csv": n_csv}))


def port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def open_browser_when_ready(proc, host, port):
    url = f"http://{host}:{port}"
    for _ in range(600):
        if proc.poll() is not None:
            return
        if port_in_use(host, port):
            say(f"dashboard is up: {url}")
            webbrowser.open(url)
            return
        time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser(description="Install, build and launch the Dust Impact Dashboard.")
    ap.add_argument("--csv-dir", help="folder of per-flight CSVs (default: CSVFiles/ next to this script, or $DUST_CSV_DIR)")
    ap.add_argument("--demo", action="store_true", help="run on built-in synthetic flights (no CSVs needed)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    ap.add_argument("--rebuild", action="store_true", help="force rebuilding the dataset (and regenerating demo flights)")
    ap.add_argument("--reinstall", action="store_true", help="force reinstalling the requirements")
    args = ap.parse_args()

    if sys.version_info < MIN_PY:
        die(f"Python {MIN_PY[0]}.{MIN_PY[1]} or newer is required (this is {sys.version.split()[0]}).")
    if args.demo and args.csv_dir:
        die("--demo and --csv-dir cannot be used together.")
    if args.demo:
        csv_dir, n_csv = prepare_demo(args.rebuild)
        data_dir = DEMO_DIR / "data"
        say("DEMO MODE: every flight is synthetic (randomly generated), not real data")
    else:
        csv_dir, n_csv = resolve_csv_dir(args.csv_dir)
        data_dir = DASH / "data"
    if port_in_use(args.host, args.port):
        die(f"port {args.port} is already in use (is the dashboard already running?). Try --port {args.port + 1}.")

    ensure_venv()
    if ensure_requirements(args.reinstall):
        preflight()
    env = {**os.environ, "DUST_CSV_DIR": str(csv_dir), "DUST_DATA_DIR": str(data_dir), "PYTHONUNBUFFERED": "1"}
    if args.demo:
        env["DUST_DEMO"] = "1"
    else:
        env.pop("DUST_DEMO", None)
    ensure_dataset(data_dir, csv_dir, n_csv, env, args.rebuild)

    say("starting server (the first page load can take ~30 s while data loads); Ctrl+C to stop")
    cmd = [str(venv_python()), "-m", "uvicorn", "app:app", "--host", args.host, "--port", str(args.port)]
    proc = subprocess.Popen(cmd, cwd=DASH, env=env)
    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, args=(proc, args.host, args.port), daemon=True).start()
    try:
        sys.exit(proc.wait())
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
