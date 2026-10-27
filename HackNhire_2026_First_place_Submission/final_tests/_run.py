"""Shared helper for the Python versions of the batch files.

Every step runs from the datasets folder (the parent of this folder) with the same Python that runs
the script, exactly as the .bat files did with `cd /d "%~dp0"`. Outputs, logs and progress files go to
the same places as before. Add --dry-run to any script to print its steps without running them.
"""
import datetime
import glob
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the datasets folder
PY = sys.executable
DRY = "--dry-run" in sys.argv
J = os.path.join


def here(*p):
    """A path inside the datasets folder."""
    return J(ROOT, *p)


def now():
    return datetime.datetime.now().strftime("%a %d/%m/%Y %H:%M:%S")


def exists(*p):
    return os.path.exists(here(*p))


def note(path, text, mode="a"):
    """Write one line to a progress file (like `echo ... >> file`)."""
    print(f"  [{path}] {text}")
    if not DRY:
        os.makedirs(os.path.dirname(here(path)) or ROOT, exist_ok=True)
        with open(here(path), mode, encoding="utf-8") as fh:
            fh.write(text + "\n")


def run(args, log=None, append=False, cwd=None, exe=None):
    """Run `python <args>` (or `exe <args>`) and return its exit code. log: a file (relative to the
    datasets folder) that gets everything printed, like `> file 2>&1`; append=True is `>> file 2>&1`.
    Without a log the output shows in the terminal."""
    cmd = [exe or PY] + [str(a) for a in args]
    where = f"   {'>>' if append else '>'} {log}" if log else ""
    print("> " + " ".join(cmd) + where)
    if DRY:
        return 0
    cwd = cwd or ROOT
    if log:
        os.makedirs(os.path.dirname(here(log)) or ROOT, exist_ok=True)
        with open(here(log), "a" if append else "w", encoding="utf-8") as fh:
            fh.flush()
            return subprocess.call(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT)
    return subprocess.call(cmd, cwd=cwd)


def run_if_missing(marker, args, **kw):
    """Run only if `marker` (e.g. a results folder's summary.json) is not there yet."""
    if exists(marker):
        print(f"  skipped (already there): {marker}")
        return 0
    return run(args, **kw)


def echo(log, text, append=True):
    """`echo text >> log`."""
    note(log, text, "a" if append else "w")


def show(log):
    """`type log`: print a log file."""
    if not DRY and exists(log):
        with open(here(log), encoding="utf-8", errors="replace") as fh:
            print(fh.read())


def copy(src, dst_dir):
    print(f"  copy {src} -> {dst_dir}")
    if not DRY and exists(src):
        os.makedirs(here(dst_dir), exist_ok=True)
        shutil.copy2(here(src), here(dst_dir))


def move(src, dst):
    print(f"  move {src} -> {dst}")
    if not DRY:
        shutil.move(here(src), here(dst))


def files(pattern):
    return sorted(glob.glob(here(pattern)))


def wait_until(hour):
    """Sleep until the next hh:00 (the .bat used PowerShell for this)."""
    t = datetime.datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    if t < datetime.datetime.now():
        t += datetime.timedelta(days=1)
    print(f"Waiting until {t:%H:%M} ({t:%d %b}). Leave this running and the PC awake.")
    if not DRY:
        time.sleep(max(0, (t - datetime.datetime.now()).total_seconds()))
