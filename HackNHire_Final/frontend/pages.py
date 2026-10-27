"""pages.py - the map page (Leaflet, in web/) and how Python talks to it.

Each page is an HTML file in web/. Before it is loaded, markers are filled in:
  /*QWEBCHANNEL*/       Qt's qwebchannel.js, which lets the page call Python
  /*CONFIG*/            window.CONFIG = {...}: legend colours, verdict words, farm colours
  /*INCLUDE:name.js*/   the contents of web/name.js
A page talks to Python with bridge.send(kind, json) and says bridge.ready() once it has started;
Python calls page functions with WebPage.call(fn, args...). Calls made before the page is ready
are queued. Console messages (including JavaScript errors) are printed to the terminal.
"""

import json
import os
import re
from collections import defaultdict

from PyQt5.QtCore import QFile, QIODevice, QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineView

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
BASE_URL = QUrl("https://localhost/")
_INCLUDE = re.compile(r"/\*INCLUDE:([\w.-]+)\*/")


def load_qwebchannel_js():
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.ReadOnly):
        raise RuntimeError("Could not load qwebchannel.js from Qt resources")
    js = bytes(f.readAll()).decode("utf-8")
    f.close()
    return js


def read_web_file(name):
    with open(os.path.join(WEB_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def build_page(html_name, config, qwebchannel_js):
    """The HTML of web/<html_name> with its includes, config and qwebchannel.js filled in."""
    html = _INCLUDE.sub(lambda m: read_web_file(m.group(1)), read_web_file(html_name))
    html = html.replace("/*CONFIG*/", "window.CONFIG = " + json.dumps(config) + ";")
    return html.replace("/*QWEBCHANNEL*/", qwebchannel_js)


class Bridge(QObject):
    """What a page can call: update(json) and state(json) (the map's drawing state),
    send(kind, json) (anything else) and ready() (the page has started)."""

    geojson_changed = pyqtSignal(dict)
    state_changed = pyqtSignal(dict)
    message = pyqtSignal(str, object)
    page_ready = pyqtSignal()

    @pyqtSlot(str)
    def update(self, payload):
        self.geojson_changed.emit(json.loads(payload))

    @pyqtSlot(str)
    def state(self, payload):
        self.state_changed.emit(json.loads(payload))

    @pyqtSlot(str, str)
    def send(self, kind, payload):
        self.message.emit(kind, json.loads(payload) if payload else None)

    @pyqtSlot()
    def ready(self):
        self.page_ready.emit()


class LoggingPage(QWebEnginePage):
    """Prints the page's console messages to the terminal, so JavaScript errors are visible."""

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name

    def javaScriptConsoleMessage(self, level, message, line, source):
        print(f"[{self.name}] {message} (line {line})", flush=True)


class WebPage(QObject):
    def __init__(self, name, html_name, config, qwebchannel_js, parent=None):
        super().__init__(parent)
        self.view = QWebEngineView()
        self.page = LoggingPage(name, self.view)
        self.view.setPage(self.page)
        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("bridge", self.bridge)
        self.page.setWebChannel(self.channel)
        self._ready = False
        self._queue = []
        self._handlers = defaultdict(list)
        self.bridge.page_ready.connect(self._on_ready)
        self.bridge.message.connect(self._dispatch)
        self.page.setHtml(build_page(html_name, config, qwebchannel_js), BASE_URL)

    def call(self, fn, *args):
        """Run fn(args...) in the page (arguments are sent as JSON)."""
        code = f"{fn}({', '.join(json.dumps(a) for a in args)});"
        if self._ready:
            self.page.runJavaScript(code)
        else:
            self._queue.append(code)

    def on(self, kind, handler):
        """handler(data) runs when the page calls send(kind, data)."""
        self._handlers[kind].append(handler)

    def _on_ready(self):
        self._ready = True
        for code in self._queue:
            self.page.runJavaScript(code)
        self._queue.clear()

    def _dispatch(self, kind, data):
        for handler in list(self._handlers.get(kind, [])):
            handler(data)
