"""Full GUI: score a folder, read the results, sweep for robustness.

    python gui.py                       start empty, pick a folder in the app
    python gui.py <image_directory>     score that folder on launch
    python gui.py predictions.json      open a finished result file

You say what you are uploading - labelled or unlabelled data - and that decides
what comes back. A labelled folder gives accuracy, charts and the failure list;
unlabelled data gives a verdict on each picture and nothing that pretends to be
a metric. The window is a front end over the same runner/sweep
code the CLI uses, so results are identical either way. Detailed progress still
goes to this terminal; the window shows finished results.

CLI equivalents:
    python detect.py <dir>       score a folder -> predictions.json (+ metrics)
    python robustness.py <dir>   transform sweep -> robustness_report.json

This file is only the launcher: parse the one optional argument, apply the
theme, open the window. Everything the window does lives in app/widgets/, and
everything it computes lives in app/ - PyQt6 is imported inside main() so that
importing this module costs nothing.
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

#: folder offered when the app starts with no argument
DEFAULT_DATA_DIR = "sample_data"

#: where the slider starts before anything is loaded. A finished run replaces it
#: with the detector's own calibrated operating point (~0.954 for the bundle),
#: which is also what Reset then returns to - see AppWindow._on_run_done.
DEFAULT_THRESHOLD = 0.5


def main() -> int:
    """Returns a process exit code: Qt's on a clean exit, 2 for a bad argument."""
    # imported here, not at module scope: a bad path should fail before the cost
    # of pulling in Qt, and this module stays importable without a display
    from PyQt6.QtWidgets import QApplication

    from app import theme
    from app.widgets.window import AppWindow

    # one optional positional argument, and its extension decides the mode:
    # a .json is a finished result to visualise, anything else is a folder to run
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    start_dir = start_json = None

    if arg and arg.lower().endswith(".json"):
        if not os.path.isfile(arg):
            print(f"error: no such file: {arg}", file=sys.stderr)
            return 2
        start_json = arg
    elif arg:
        if not os.path.isdir(arg):
            print(f"error: not a directory: {arg}", file=sys.stderr)
            return 2
        start_dir = arg

    QApplication.setApplicationName("AIGC Detector")
    QApplication.setOrganizationName("Hackathon")

    # sys.argv[:1]: our own argument is already parsed, and handing it to Qt
    # would have it try to interpret the path as a Qt option
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(theme.STYLESHEET)
    theme.apply_matplotlib_style()

    window = AppWindow(start_dir=start_dir, start_json=start_json,
                       threshold=DEFAULT_THRESHOLD)

    # prefill the picker with the sample folder when it exists, but don't run
    if start_dir is None and start_json is None:
        default = os.path.join(PROJECT_ROOT, DEFAULT_DATA_DIR)
        if os.path.isdir(default):
            window.upload_page.set_directory(default)

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
