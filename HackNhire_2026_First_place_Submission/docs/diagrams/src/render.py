"""render.py - turns each figure's HTML in this folder into a PNG one folder up, with Microsoft Edge
(or Chrome) in headless mode, at 3x scale so the picture stays sharp in the memo.

    python docs/diagrams/src/render.py            # every fig*.html
    python docs/diagrams/src/render.py fig2       # just the files whose name starts with fig2

Each page is opened twice: once to measure its height (the page writes it into body[data-h]), then
once to take a screenshot of exactly that size."""

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
WIDTH = 680
SCALE = 3
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"]


def browser() -> str:
    for path in BROWSERS:
        if Path(path).exists():
            return path
    sys.exit("No Edge or Chrome found. Install one, or add its path to BROWSERS in render.py.")


def run(exe: str, *args: str) -> str:
    base = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            "--disable-extensions", "--run-all-compositor-stages-before-draw", "--virtual-time-budget=3000"]
    done = subprocess.run(base + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    return done.stdout


def height_of(exe: str, url: str) -> int:
    dom = run(exe, f"--window-size={WIDTH},2400", "--dump-dom", url)
    found = re.search(r'data-h="(\d+)"', dom)
    if not found:
        sys.exit(f"{url} did not report its height: add the measuring script to the page.")
    return int(found.group(1)) + 6   # a little air: the screenshot can lay text out a pixel taller


def main() -> None:
    exe = browser()
    prefix = sys.argv[1] if len(sys.argv) > 1 else "fig"
    pages = sorted(HERE.glob(f"{prefix}*.html"))
    if not pages:
        sys.exit(f"No HTML files starting with '{prefix}' in {HERE}.")
    for page in pages:
        url = page.as_uri()
        height = height_of(exe, url)
        png = OUT / (page.stem + ".png")
        run(exe, f"--window-size={WIDTH},{height}", f"--force-device-scale-factor={SCALE}",
            f"--screenshot={png}", url)
        print(f"{png.name}: {WIDTH} x {height} px (x{SCALE})")


if __name__ == "__main__":
    main()
