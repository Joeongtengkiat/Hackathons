"""maps_ui.py - several maps in Neon: the Map dropdown, and Maps ▸ Rename / Delete map….

Each map lives in its own schema in Neon (backend/database/maps.py). At start-up the maps are
listed and one is shown: the --map flag, else the last one used, else the first. Choosing another
map loads it the same way. Each map keeps the farms drawn on it while the app is open, so switching
away and back brings them back; check results belong to one map, so they are cleared.

Renaming and deleting are the app's only writes to the map tables, and both ask first. The app
doesn't upload maps: they are ingested with the backend's ingest window (backend/ingest_app.py) or
pipeline.py, which build the evidence too.
"""

from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMenu,
                             QToolButton, QVBoxLayout)

from backend import common

INGEST_HINT = ("Ingest one with the backend's ingest window: "
               r"backend\venv\Scripts\python backend\ingest_app.py, then press Refresh.")


# ----- plain helpers (tested without a window) -----
def schema_or_problem(name):
    """(schema, "") for a usable map name, or (None, why it can't be used)."""
    try:
        return common.map_schema(name), ""
    except SystemExit as exc:
        return None, str(exc.code)


def name_problem(name, existing):
    """Why a new map can't have this name ("" if it can). existing: [(name, schema)] of stored maps."""
    name = (name or "").strip()
    schema, problem = schema_or_problem(name)
    if problem:
        return problem
    for other, other_schema in existing:
        if other == name:
            return f"There's already a map called '{other}'. Choose another name, or delete that map first."
        if other_schema == schema:
            return f"'{name}' is too close to '{other}': both would be stored as {schema}. Choose another name."
    return ""


def rename_problem(new, old, existing):
    """Why the map 'old' can't be renamed to 'new' ("" if it can). existing: [(name, schema)]."""
    if (new or "").strip() == old:
        return f"The map is already called '{old}'."
    return name_problem(new, [(n, s) for n, s in existing if n != old])


def choose_map(names, *preferred):
    """The first preferred name that is stored, else the first map; None if there are no maps."""
    for name in preferred:
        if name and name in names:
            return name
    return names[0] if names else None


def map_label(info):
    """A map's text in the dropdown: its name, plus a note if an approach is missing."""
    missing = [a for a, built in (("stencils", info["stencils"]), ("outlines", info["outlines"])) if not built]
    return info["name"] + (f"  (no {' or '.join(missing)})" if missing else "")


def map_tooltip(info):
    size = f"{info['width']:,} × {info['height']:,} px" if info.get("width") else ""
    parts = [info.get("source_file") or "", size, f"stored as {info['schema']}"]
    return " · ".join(p for p in parts if p)


def describe_text(info):
    """The delete dialog's line about what will go."""
    saved = info["saved_results"]
    text = f"Uses {info['bytes'] / 1e6:.1f} MB in Neon · {saved:,} saved result{'' if saved == 1 else 's'}"
    return text + (f" · from {info['source_file']}" if info.get("source_file") else "")


def confirmed(typed, name):
    """Delete is allowed only once the map's exact name has been typed."""
    return bool(name) and typed.strip() == name


def label(text, role=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    if role:
        widget.setProperty("role", role)
    return widget


# ----- dialogs -----
class DeleteDialog(QDialog):
    """Delete a stored map after typing its exact name. After exec_(), .deleted is {"name", "schema"}
    if a map was deleted."""

    def __init__(self, window, names, current=None):
        super().__init__(window)
        self.w = window
        self.job = None
        self.deleted = None
        self.setWindowTitle("Delete a map from Neon")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.combo = QComboBox()
        self.combo.addItems(names)
        if current in names:
            self.combo.setCurrentText(current)
        form.addRow("Map", self.combo)
        layout.addLayout(form)
        self.details = label("", "secondary")
        warning = label("This deletes the map's stencils, outlines and saved results from Neon, for good.",
                        "secondary")
        self.prompt = label("", "secondary")
        self.typed = QLineEdit()
        self.problem = label("", "error")
        for widget in (self.details, warning, self.prompt, self.typed, self.problem):
            layout.addWidget(widget)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.delete_btn = self.buttons.button(QDialogButtonBox.Ok)
        self.delete_btn.setText("Delete map")
        self.delete_btn.setProperty("role", "danger")
        self.buttons.accepted.connect(self.start)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.combo.currentTextChanged.connect(self.map_changed)
        self.typed.textChanged.connect(self.validate)
        self.map_changed(self.combo.currentText())

    def map_changed(self, name):
        self.prompt.setText(f"Type {name} to confirm:")
        self.typed.clear()
        self.problem.setText("")
        self.details.setText("Looking up what is stored…")
        self.w.run_job("describe_map", "describe_map", name, coalesce=True,
                       done=lambda info: self.show_details(name, info),
                       failed=lambda message: self.show_details(name, None, message))
        self.validate()

    def show_details(self, name, info, message=""):
        if name == self.combo.currentText():
            self.details.setText(describe_text(info) if info else f"Couldn't look it up: {message}")
        return True

    def validate(self):
        self.delete_btn.setEnabled(self.job is None and confirmed(self.typed.text(), self.combo.currentText()))

    def start(self):
        self.validate()
        if not self.delete_btn.isEnabled():
            return
        name = self.combo.currentText()
        self.job = self.w.run_job("delete_map", "delete_map", name, done=self.finished, failed=self.failed)
        self.combo.setEnabled(False)
        self.typed.setEnabled(False)
        self.validate()
        self.details.setText(f"Deleting {name}…")

    def finished(self, info):
        self.job = None
        self.deleted = info
        self.accept()

    def failed(self, message):
        self.job = None
        self.combo.setEnabled(True)
        self.typed.setEnabled(True)
        self.validate()
        self.problem.setText(f"The map wasn't deleted.\n{message}")
        return True

    def reject(self):
        if self.job is None:  # a delete under way is one quick transaction: let it finish
            super().reject()


class RenameDialog(QDialog):
    """Give a stored map a new name. After exec_(), .renamed is NeonService.rename_map's result
    ({"old", "name", "schema", "map"}) if a map was renamed."""

    def __init__(self, window, existing, current=None):
        super().__init__(window)
        self.w = window
        self.existing = existing          # [(name, schema)] of the maps stored
        self.job = None
        self.renamed = None
        self.setWindowTitle("Rename a map")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.combo = QComboBox()
        names = [n for n, _ in existing]
        self.combo.addItems(names)
        if current in names:
            self.combo.setCurrentText(current)
        form.addRow("Map", self.combo)
        self.name = QLineEdit()
        self.name.setMaxLength(common.MAP_NAME_MAX)
        form.addRow("New name", self.name)
        layout.addLayout(form)
        self.problem = label("", "error")
        layout.addWidget(self.problem)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.rename_btn = self.buttons.button(QDialogButtonBox.Ok)
        self.rename_btn.setText("Rename")
        self.buttons.accepted.connect(self.start)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.combo.currentTextChanged.connect(self.map_changed)
        self.name.textChanged.connect(self.validate)
        self.map_changed(self.combo.currentText())

    def map_changed(self, name):
        self.name.setText(name)
        self.name.selectAll()
        self.name.setFocus()

    def validate(self):
        new, old = self.name.text(), self.combo.currentText()
        problem = rename_problem(new, old, self.existing) if new.strip() else ""
        self.show_problem("" if new.strip() == old else problem)  # unchanged yet: nothing to say
        self.rename_btn.setEnabled(self.job is None and bool(new.strip()) and not problem)

    def show_problem(self, text):
        """The error line, shown only when there is one; the dialog grows to fit it."""
        self.problem.setText(text)
        self.problem.setVisible(bool(text))
        self.adjustSize()

    def start(self):
        self.validate()
        if not self.rename_btn.isEnabled():
            return
        self.job = self.w.run_job("rename_map", "rename_map", self.combo.currentText(),
                                  self.name.text().strip(), done=self.finished, failed=self.failed)
        self.combo.setEnabled(False)
        self.name.setEnabled(False)
        self.validate()

    def finished(self, info):
        self.job = None
        self.renamed = info
        self.accept()

    def failed(self, message):
        self.job = None
        self.combo.setEnabled(True)
        self.name.setEnabled(True)
        self.validate()
        self.show_problem(f"The map wasn't renamed.\n{message}")
        return True

    def reject(self):
        if self.job is None:  # a rename under way is one quick transaction: let it finish
            super().reject()


# ----- the controller -----
class MapsController:
    """The Map dropdown and the Maps menu: decides which map is shown, and switches maps."""

    def __init__(self, window, preferred=None, settings=None):
        self.w = window
        self.preferred = preferred        # --map NAME (start-up only)
        self.settings = settings if settings is not None else QSettings("HackNHire", "EUDR farm checker")
        self.maps = []                    # from NeonService.list_maps
        self.current = None               # the map shown, or being loaded
        self.farms = {}                   # map name -> the farms drawn on it (while the app is open)

        self.combo = QComboBox()
        self.combo.setMinimumWidth(170)
        self.combo.setEnabled(False)
        self.combo.setToolTip("The map in Neon that is shown, and that farms are checked against")
        self.combo.activated.connect(self.chosen)  # the user's choices only
        self.rename_act = window.action("Rename map…", slot=self.rename)
        self.rename_act.setEnabled(False)
        self.delete_act = window.action("Delete map…", slot=self.delete)
        self.delete_act.setEnabled(False)
        self.refresh_act = window.action("Refresh list", slot=self.refresh)
        menu = QMenu(window)
        menu.addAction(self.rename_act)
        menu.addAction(self.delete_act)
        menu.addSeparator()
        menu.addAction(self.refresh_act)
        button = QToolButton()
        button.setText("Maps")
        button.setToolTip("Rename, delete or refresh the maps stored in Neon (maps are ingested with "
                          "backend/ingest_app.py)")
        button.setMenu(menu)
        button.setPopupMode(QToolButton.InstantPopup)
        window.insert_tools(window.draw_act, [QLabel("Map"), self.combo, button, None])
        window.listen("map_loaded", self.on_loaded)
        self.start()

    def names(self):
        return [m["name"] for m in self.maps]

    # ----- listing -----
    def start(self, select=None):
        """List the maps in Neon, then show one: select, else --map, else the last used, else the first."""
        if self.w.map_info is None and self.current is None:
            self.w.eudr.show_banner("Looking for maps in Neon…")
        self.w.run_job("maps", "list_maps", coalesce=True,
                       done=lambda found: self.listed(found, select), failed=self.list_failed)

    def refresh(self):
        self.start(select=self.current)

    def listed(self, found, select=None):
        self.maps = list(found)
        names = self.names()
        self.combo.blockSignals(True)
        self.combo.clear()
        for info in self.maps:
            self.combo.addItem(map_label(info), info["name"])
            self.combo.setItemData(self.combo.count() - 1, map_tooltip(info), Qt.ToolTipRole)
        self.combo.blockSignals(False)
        self.combo.setEnabled(bool(names))
        self.rename_act.setEnabled(bool(names))
        self.delete_act.setEnabled(bool(names))
        if self.preferred and self.preferred not in names:
            self.w.statusBar().showMessage(f"There's no map called '{self.preferred}' in Neon.", 8000)
        name = choose_map(names, select, self.preferred, self.settings.value("map", "", type=str))
        self.preferred = None
        if name is None:
            self.current = None
            self.w.eudr.clear(f"No maps in Neon yet. {INGEST_HINT}", button="Refresh", action=self.refresh)
            return
        self.sync_combo(name)
        if name != self.current:
            self.switch_to(name)

    def list_failed(self, message):
        if self.w.map_info is None:
            self.w.eudr.show_banner(f"Could not list the maps in Neon. {message}", error=True, action=self.start)
        else:
            self.w.statusBar().showMessage(f"Could not list the maps in Neon: {message.splitlines()[0]}", 8000)
        return True

    def sync_combo(self, name):
        index = self.combo.findData(name)
        if index >= 0:
            self.combo.setCurrentIndex(index)

    # ----- switching -----
    def chosen(self, index):
        self.switch_to(self.combo.itemData(index))

    def switch_to(self, name):
        """Show another map: keep this map's farms, clear its results, load the new one."""
        if name is None or name == self.current:
            return
        old = self.current
        if old is not None:
            features = self.w.geojson["features"]
            if features:
                self.farms[old] = features
            else:
                self.farms.pop(old, None)
        self.current = name
        self.sync_combo(name)
        self.w.map_info = None
        self.w.emit("map_switching", old, name)
        if old is not None:
            self.w.js("clearAll()")
        self.w.eudr.load(name)

    def on_loaded(self, info):
        name = info["map_version"]
        if name != self.current:  # an earlier choice finishing late: the latest one follows
            return
        self.settings.setValue("map", name)
        features = self.farms.pop(name, None)
        if features:
            self.w.map.call("addFeatures", {"type": "FeatureCollection", "features": features})

    # ----- rename and delete -----
    def rename(self):
        if not self.maps:
            return
        dialog = RenameDialog(self.w, [(m["name"], m["schema"]) for m in self.maps], self.current)
        if dialog.exec_() != QDialog.Accepted or not dialog.renamed:
            return
        old, new = dialog.renamed["old"], dialog.renamed["name"]
        if old in self.farms:
            self.farms[new] = self.farms.pop(old)
        if old == self.current:  # same map, new name: it stays on screen with its farms and results
            self.current = new
            self.settings.setValue("map", new)
            if dialog.renamed.get("map"):
                self.w.eudr.renamed(dialog.renamed["map"])
        self.w.statusBar().showMessage(f"Renamed '{old}' to '{new}'.", 8000)
        self.start(select=self.current)

    def delete(self):
        if not self.maps:
            return
        dialog = DeleteDialog(self.w, self.names(), self.current)
        if dialog.exec_() != QDialog.Accepted or not dialog.deleted:
            return
        name = dialog.deleted["name"]
        self.farms.pop(name, None)
        self.w.statusBar().showMessage(f"Deleted '{name}' from Neon.", 8000)
        if name == self.current:  # it was on screen: its farms and results go with it
            self.current = None
            self.w.map_info = None
            self.w.emit("map_switching", name, None)
            self.w.js("clearAll()")
            self.w.eudr.clear(f"Deleted '{name}'. Loading another map…")
        self.start()
