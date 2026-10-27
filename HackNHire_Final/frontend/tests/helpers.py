"""helpers.py - shared test doubles: a scripted fake database connection, made-up TIFFs, and a
stand-in for the main window."""

from collections import defaultdict
from types import SimpleNamespace

import rasterio
from PyQt5.QtWidgets import QAction, QMainWindow, QToolBar
from rasterio.transform import from_origin


class Script:
    """A fake psycopg connection. Each query is answered by the first (fragment, rows) whose fragment
    appears in the SQL or in its parameters; everything executed is kept in .sql and .params."""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.sql, self.params = [], []
        self.commits = self.rollbacks = 0
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return ScriptCursor(self)

    def execute(self, sql, params=None):
        cur = ScriptCursor(self)
        cur.execute(sql, params)
        return cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def answer(self, sql, params):
        text = f"{sql} | {params!r}"
        for fragment, rows in self.answers:
            if fragment in text:
                return list(rows)
        return []


class ScriptCursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        self.conn.params.append(params)
        self.rows = self.conn.answer(sql, params)

    def executemany(self, sql, rows):
        for params in rows:
            self.execute(sql, params)

    def copy(self, sql):
        self.conn.sql.append(sql)
        return ScriptCopy()

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class ScriptCopy:
    """COPY ... FROM STDIN: the rows are accepted and dropped."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write_row(self, row):
        pass


def write_tiff(path, data, x0=110.0, y0=-7.0, pixel=1 / 12000, crs="EPSG:4326"):
    """Save a made-up single-band GeoTIFF of class codes (never a real map) and return its path."""
    with rasterio.open(path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=1,
                       dtype="uint8", crs=crs, transform=from_origin(x0, y0, pixel, pixel)) as dst:
        dst.write(data, 1)
    return path


class FakeEudr:
    """Records what the map layer was asked to do."""

    def __init__(self):
        self.calls = []

    def show_banner(self, text, error=False, button=None, action=None):
        self.calls.append(("banner", text, button))

    def clear(self, text, button=None, action=None):
        self.calls.append(("clear", text, button))

    def load(self, name):
        self.calls.append(("load", name))

    def renamed(self, info):
        self.calls.append(("renamed", info))


class FakeWindow(QMainWindow):
    """Just enough of app.MainWindow for the controllers: jobs, events and map calls are recorded."""

    def __init__(self):
        super().__init__()
        self.draw_act = QAction("Draw", self)
        self.toolbar = QToolBar()
        self.toolbar.addAction(self.draw_act)
        self.addToolBar(self.toolbar)
        self.eudr = FakeEudr()
        self.compare = SimpleNamespace(job=None)
        self.cancelled = 0
        self.worker = SimpleNamespace(cancel=self._cancel)
        self.map_info = None
        self.geojson = {"type": "FeatureCollection", "features": []}
        self.jobs, self.js_calls, self.map_calls, self.events = [], [], [], []
        self.map = SimpleNamespace(call=lambda fn, *args: self.map_calls.append((fn,) + args))
        self.listeners = defaultdict(list)

    def _cancel(self):
        self.cancelled += 1

    def action(self, text, icon=None, shortcuts=(), slot=None):
        act = QAction(text, self)
        if slot:
            act.triggered.connect(slot)
        return act

    def insert_tools(self, anchor, items):
        for item in items:
            if item is None:
                self.toolbar.insertSeparator(anchor)
            elif isinstance(item, QAction):
                self.toolbar.insertAction(anchor, item)
            else:
                self.toolbar.insertWidget(anchor, item)

    def listen(self, event, callback):
        self.listeners[event].append(callback)

    def emit(self, event, *args):
        self.events.append((event,) + args)
        for callback in list(self.listeners[event]):
            callback(*args)

    def run_job(self, kind, method, *args, **kwargs):
        self.jobs.append((kind, method, args, kwargs))
        return len(self.jobs)

    def js(self, code):
        self.js_calls.append(code)
