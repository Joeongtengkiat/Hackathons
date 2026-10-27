"""Read-only test of HackNHire-System-Earth against Neon, step 1: set up a separate environment (.neon_venv) and
list the maps. Nothing in the HackNHire-System-Earth folder is changed; nothing is written to Neon."""
import os
from _run import J, echo, exists, here, now, run
REPO = r"C:\Users\joeon\.vscode\Python programming stuff\sc2001-project3\HackNHire-System-Earth"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
VENV = here(".neon_venv", "Scripts", "python.exe")
os.makedirs(here("tests_neon"), exist_ok=True)
if not exists(".neon_venv", "Scripts", "python.exe"):
    run(["-m", "venv", "--system-site-packages", ".neon_venv"], J("tests_neon", "setup.txt"))
run(["-m", "pip", "install", "-q", "psycopg[binary]>=3.1"], J("tests_neon", "setup.txt"), append=True, exe=VENV)
run(["-c", "import sys, psycopg, rasterio, shapely, pyproj, numpy; print(sys.version); "
     "print('psycopg', psycopg.__version__, 'rasterio', rasterio.__version__, 'shapely', shapely.__version__)"],
    J("tests_neon", "setup.txt"), append=True, exe=VENV)
run([J("analysis", "neon_system_test.py"), REPO, "tests_neon", "--stage", "list", "--env", REPO + r"\neon.env"],
    J("tests_neon", "list_out.txt"), exe=VENV)
echo(J("tests_neon", "progress.txt"), f"list done {now()}")
