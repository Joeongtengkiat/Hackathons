"""One QApplication for every test that needs Qt, created when a test first asks for it.
QtWebEngineWidgets must be imported before any QApplication exists, so it is imported here."""

import importlib
import sys

from PyQt5.QtWidgets import QApplication

importlib.import_module("PyQt5.QtWebEngineWidgets")

_app = None  # kept here: an unreferenced QApplication is deleted at once, and widgets then crash


def qt_app():
    global _app
    _app = QApplication.instance() or QApplication(sys.argv[:1])
    return _app
