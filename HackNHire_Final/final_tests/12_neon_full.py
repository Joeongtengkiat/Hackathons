"""Read-only test of HackNHire-System-Earth against Neon, step 2: speed and fidelity, one map per line of
tests_neon\\full_args.txt. Nothing in the HackNHire-System-Earth folder is changed; every Neon connection is
read-only. Run neon_list.py first."""
import os
import shlex
from _run import J, echo, exists, here, now, run
REPO = r"C:\Users\joeon\.vscode\Python programming stuff\sc2001-project3\HackNHire-System-Earth"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
VENV = here(".neon_venv", "Scripts", "python.exe")
P = J("tests_neon", "progress.txt")
echo(P, f"full started {now()}")
lines = []
if exists("tests_neon", "full_args.txt"):
    with open(here("tests_neon", "full_args.txt"), encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
for line in lines:
    echo(P, f"  map {line} started {now()}")
    run([J("analysis", "neon_system_test.py"), REPO, "tests_neon", "--stage", "full", "--env", REPO + r"\neon.env",
         *shlex.split(line, posix=False)], J("tests_neon", "full_out.txt"), append=True, exe=VENV)
echo(P, f"full done {now()}")
