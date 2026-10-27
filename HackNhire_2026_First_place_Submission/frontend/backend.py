"""backend.py - makes the backend's own modules importable, the same way compare/compare.py does.

    from backend import common, polygon_approach, protected_areas, stencil_approach

Everything the app knows about the map and the EUDR rules comes from these modules, so the app
always gives the same answers as the command-line tools. The app changes the database only through
these modules: maps.upload / maps.delete (asked for in the Maps menu) and common.save_results
(Check ▸ Save results to Neon).

DATABASE_URL: a value set in the terminal wins; then the app's own frontend/.env, if it has one; then
common.connect()'s usual search (the folder you run from, backend/.env).
"""

import os
import sys

FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(os.path.dirname(FRONTEND_DIR), "backend", "database")
if DATABASE_DIR not in sys.path:
    sys.path.insert(0, DATABASE_DIR)

import common  # noqa: E402

# frontend/.env (see .env.example), read with the backend's own reader so both parse it the same way
if not os.environ.get("DATABASE_URL"):
    for _name in (".env", ".env.txt"):  # Windows Notepad sometimes saves .env as .env.txt
        if os.path.isfile(os.path.join(FRONTEND_DIR, _name)):
            common._read_env_file(os.path.join(FRONTEND_DIR, _name))
            break

import maps  # noqa: E402
import polygon_approach  # noqa: E402
import protected_areas  # noqa: E402
import stencil_approach  # noqa: E402

__all__ = ["common", "maps", "polygon_approach", "protected_areas", "stencil_approach"]
