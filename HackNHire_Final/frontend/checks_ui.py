"""checks_ui.py - checking farms from the app: the Point tool, Open (a GeoJSON file, or farms saved
in Neon), Check (all farms or the selected one, with options) and the results of the last check.

A result belongs to the shape that was checked: if a farm is edited afterwards its result is
marked out of date (grey, dashed) until it is checked again, and a deleted farm loses its result.
"""

import json
import os

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QActionGroup, QFileDialog, QMenu, QMessageBox, QToolButton

from farms import features_from_file
from legend import METHODS, OUTCOME_STYLE, VERDICT_ORDER
from report import worst

GEOJSON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "backend", "data", "geojson")


def geometry_key(feature):
    """A text fingerprint of a farm's shape, to notice edits made after a check."""
    return json.dumps(feature.get("geometry"), sort_keys=True)


def stale_keys(checked, features):
    """checked = {key: geometry_key at check time}. Returns (stale keys, deleted keys)."""
    now = {f["properties"]["id"]: geometry_key(f) for f in features}
    stale = {k for k, g in checked.items() if k in now and now[k] != g}
    deleted = {k for k in checked if k not in now}
    return stale, deleted


def sort_rows(rows):
    """Worst verdict first, then by name."""
    return sorted(rows, key=lambda r: (VERDICT_ORDER.index(r["verdict"]), r["name"].lower()))


class CheckController:
    def __init__(self, window):
        self.w = window
        window.checks = self
        self.rows = {}           # key -> row of the last check (or a row saved in Neon)
        self.checked = {}        # key -> geometry_key when it was checked
        self.stale = set()
        self.last = None
        self.pending_saved = None
        self.check_after_open = False

        # Point tool, next to the polygon tool (which is now called "Polygon")
        window.draw_act.setText("Polygon")
        window.draw_act.setToolTip("Polygon  (P)")
        self.point_act = window.action("Point", "point", ["O"], lambda: window.js("startPoint()"))
        self.point_act.setCheckable(True)
        window.toolbar.insertAction(window.edit_act, self.point_act)

        # Open ▾
        open_menu = QMenu(window)
        self.open_file_act = open_menu.addAction("GeoJSON file…", self.open_file)
        self.open_neon_act = open_menu.addAction("Farms saved in Neon", self.open_saved)
        open_btn = self.menu_button("Open", "open", open_menu)

        # Check ▾ (click = check all farms; the arrow opens the options)
        check_menu = QMenu(window)
        check_menu.setToolTipsVisible(True)
        self.check_all_act = window.action("Check all farms", "verify", ["Ctrl+Return", "Ctrl+Enter"], self.check_all)
        self.check_sel_act = window.action("Check selected farm", None, ["Ctrl+Shift+Return"], self.check_selected)
        check_menu.addAction(self.check_all_act)
        check_menu.addAction(self.check_sel_act)
        check_menu.addSeparator()
        self.point_mode = self.choice(check_menu, "Check points as", [
            ("The pixel under the point", "pixel"),
            ("A circle of the declared area (area_ha; 4 ha if none, as the EU system)", "circle")])
        self.rule = self.choice(check_menu, "Stencil rule", [
            ("Pixels whose centre is inside (as the evidence check)", "centre"),
            ("Every pixel an outline touches (cautious)", "touched")])
        check_menu.addSeparator()
        self.tiff_act = check_menu.addAction("Compare with the original TIFF")
        self.tiff_act.setCheckable(True)
        self.tiff_act.setEnabled(False)
        self.explain_act = check_menu.addAction("Measure server work (EXPLAIN ANALYZE)")
        self.explain_act.setCheckable(True)
        self.explain_act.setChecked(True)
        self.explain_act.setToolTip("Runs each approach's queries again under EXPLAIN (ANALYZE, BUFFERS), "
                                    "in a transaction that is rolled back, to see the work inside PostgreSQL.")
        self.save_act = check_menu.addAction("Save results to Neon")
        self.save_act.setCheckable(True)
        self.save_act.setToolTip("Writes the farms to plots and the answers to plot_results, like the "
                                 "backend's check command. A farm's name is its plot_id.")
        check_btn = QToolButton()
        check_btn.setDefaultAction(self.check_all_act)
        check_btn.setText("Check")
        check_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        check_btn.setMenu(check_menu)
        check_btn.setPopupMode(QToolButton.MenuButtonPopup)

        window.insert_tools(window.spacer_anchor, [None, open_btn, check_btn])
        window.listen("state", self.on_state)
        window.listen("geojson", self.on_geojson)
        window.listen("map_loaded", self.on_map_loaded)
        window.listen("map_switching", self.on_map_switching)

    # ----- UI helpers -----
    def menu_button(self, text, icon, menu):
        btn = QToolButton()
        btn.setText(text)
        btn.setIcon(self.w.icon(icon))
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setMenu(menu)
        btn.setPopupMode(QToolButton.InstantPopup)
        return btn

    def choice(self, menu, title, options):
        """A submenu of exclusive options; returns a function giving the chosen value."""
        sub = menu.addMenu(title)
        group = QActionGroup(sub)
        group.setExclusive(True)
        for i, (label, value) in enumerate(options):
            act = sub.addAction(label)
            act.setCheckable(True)
            act.setChecked(i == 0)
            act.setData(value)
            group.addAction(act)
        return lambda: group.checkedAction().data()

    def on_state(self, state):
        idle = state["mode"] == "idle"
        ready = self.w.map_info is not None
        has_farms = bool(self.w.geojson["features"])
        self.point_act.setEnabled(idle)
        self.point_act.setChecked(state["mode"] == "placing")
        self.check_all_act.setEnabled(idle and ready and has_farms)
        self.check_sel_act.setEnabled(idle and ready and state["selected"] is not None)
        self.open_neon_act.setEnabled(ready)

    def on_map_loaded(self, info):
        available = info["tiff"]["available"]
        self.tiff_act.setEnabled(available)
        self.tiff_act.setChecked(available)
        self.tiff_act.setToolTip(info["tiff"]["note"])
        self.on_state(self.w.state)

    def on_map_switching(self, old, new):
        """Results belong to the map they were checked against, so they go when the map changes."""
        self.rows.clear()
        self.checked.clear()
        self.stale.clear()
        self.last = None
        self.pending_saved = None
        self.publish()
        self.on_state(self.w.state)

    # ----- checking -----
    def check_all(self):
        self.run_check(self.w.geojson["features"])

    def check_selected(self):
        key = self.w.selected_id()
        self.run_check([f for f in self.w.geojson["features"] if f["properties"]["id"] == key])

    def run_check(self, features):
        if not features or self.w.map_info is None:
            return
        shapes = {f["properties"]["id"]: geometry_key(f) for f in features}
        self.w.statusBar().showMessage(f"Checking {len(features)} farm{'' if len(features) == 1 else 's'}…")
        self.w.run_job("check", "check", features, point_mode=self.point_mode(), rule=self.rule(),
                       with_tiff=self.tiff_act.isChecked(), save=self.save_act.isChecked(),
                       explain=self.explain_act.isChecked(),
                       done=lambda result: self.check_done(result, shapes))

    def check_done(self, result, shapes):
        for row in result["rows"]:
            self.rows[row["key"]] = row
            self.checked[row["key"]] = shapes.get(row["key"], "")
            self.stale.discard(row["key"])
        self.last = result
        self.publish()
        self.report(result)
        if result["skipped"]:
            names = "\n".join(f"• {s['name']}: {s['reason']}" for s in result["skipped"][:12])
            QMessageBox.information(self.w, "Some farms were not checked", names)
        self.w.emit("checked", result)

    def update_row(self, row, feature):
        """A fresh answer for one farm (from the focus view)."""
        self.rows[row["key"]] = row
        self.checked[row["key"]] = geometry_key(feature)
        self.stale.discard(row["key"])
        self.publish()

    def report(self, result):
        rows, s = result["rows"], result["summary"]
        if not rows:
            self.w.statusBar().showMessage("Nothing to check.", 6000)
            return
        counts = [(o, sum(1 for r in rows if r["outcome"] == o)) for o in OUTCOME_STYLE]
        parts = ", ".join(f"{n} {OUTCOME_STYLE[o][2].lower()}" for o, n in counts if n)
        times = ", ".join(f"{METHODS[m][0].lower()} {1000 * s['timing'][m]['total_s']:.0f} ms"
                          for m in ("stencils", "polygons"))
        saved = " · saved to Neon" if result["options"]["saved"] else ""
        self.w.statusBar().showMessage(f"Checked {len(rows)}: {parts} · {times} over Neon{saved}", 20000)

    # ----- results -> map and panel -----
    def sorted_rows(self):
        return [dict(row, stale=row["key"] in self.stale) for row in sort_rows(list(self.rows.values()))]

    def publish(self):
        self.w.map.call("setVerdicts", {str(k): {"verdict": r["verdict"], "stale": k in self.stale}
                                        for k, r in self.rows.items()})
        self.w.emit("results", self.sorted_rows())

    def on_geojson(self, geojson):
        features = geojson["features"]
        if self.check_after_open and features:
            self.check_after_open = False
            QTimer.singleShot(0, self.check_all)
        if self.pending_saved is not None:
            self.apply_saved(features)
        if not self.rows:
            return
        stale, deleted = stale_keys(self.checked, features)
        changed = stale != self.stale or bool(deleted)
        for key in deleted:
            self.rows.pop(key, None)
            self.checked.pop(key, None)
        names = {f["properties"]["id"]: f["properties"]["name"] for f in features}
        for key, row in self.rows.items():
            if key in names and row["name"] != names[key]:
                row["name"] = names[key]
                changed = True
        self.stale = stale
        if changed:
            self.publish()

    # ----- opening farms -----
    def open_file(self):
        folder = GEOJSON_DIR if os.path.isdir(GEOJSON_DIR) else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self.w, "Open farms", folder, "GeoJSON (*.geojson *.json)")
        if path:
            self.open_path(path)

    def open_path(self, path, check=False):
        """Add the farms in a GeoJSON file to the map (and check them all once they are on it)."""
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.w, "Couldn't open that file",
                                f"{os.path.basename(path)} isn't a readable GeoJSON file.\n\n{exc}")
            return
        features, skipped = features_from_file(data)
        self.check_after_open = bool(check and features)
        self.add_features(features, skipped, os.path.basename(path))

    def open_saved(self):
        self.w.run_job("saved_farms", "saved_farms", done=self.saved_loaded)

    def saved_loaded(self, result):
        if not result["features"]:
            QMessageBox.information(self.w, "No farms saved in Neon",
                                    "Neon's plots table is empty. Tick Check ▸ Save results to Neon "
                                    "before checking, or run the backend's check command, to store some.")
            return
        self.pending_saved = result["verdicts"]
        self.add_features(result["features"], [(s["name"], s["reason"]) for s in result["skipped"]], "Neon")

    def add_features(self, features, skipped, source):
        if features:
            self.w.map.call("addFeatures", {"type": "FeatureCollection", "features": features})
        text = f"Added {len(features)} farm{'' if len(features) == 1 else 's'} from {source}."
        if skipped:
            text += f" Skipped {len(skipped)}: " + "; ".join(f"{n} ({r})" for n, r in skipped[:5])
            QMessageBox.information(self.w, "Some farms were skipped", text)
        self.w.statusBar().showMessage(text, 10000)

    def apply_saved(self, features):
        """Show the verdicts stored in Neon for farms just loaded from Neon (until they are checked)."""
        saved, self.pending_saved = self.pending_saved, None
        for f in features:
            p = f["properties"]
            stored = saved.get(p["name"])
            if not stored or p["id"] in self.rows:
                continue
            verdict = worst([stored.get("stencils"), stored.get("polygons")])
            if verdict is None:
                continue
            self.rows[p["id"]] = {
                "key": p["id"], "name": p["name"], "kind": p["kind"], "area_ha": p["area_km2"] * 100,
                "verdict": verdict, "saved": True,
                "results": {m: {"verdict": stored[m]} for m in ("stencils", "polygons") if stored.get(m)},
                "agree": {"stencils_tiff": None, "polygons_tiff": None,
                          "stencils_polygons": stored.get("stencils") == stored.get("polygons")},
            }
            self.checked[p["id"]] = geometry_key(f)
        self.publish()
