"""The first screen: say what you are uploading, then pick the folder.

The old Run page inferred the kind of data from the folder and quietly went
whichever way the scan fell. Here the choice is explicit and enforced - you
declare labelled or unlabelled data, and the app either honours it or tells
you the folder cannot support it. That is what decides which results
screen you land on.

The enforcement is the peek: a ScanWorker reads the folder in the background as
soon as a path is typed, and _peek_text() turns what it found plus what you
declared into the sentence under the field and into whether Run is enabled. The
scan always runs with AUTO, so one scan answers both modes and switching the
tile re-reads it rather than rescanning.

The folder can arrive three ways - typed, browsed, or dropped - and all three
end at the same setText, so the peek and the enabling logic below never have to
know which one it was. Drops are handled by the page rather than by the
DropZone widget, so anywhere on this screen is a target and the zone is only
what says so.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget
)

from .. import theme as T
from ..dataset import IMAGE_EXTENSIONS, LabelMode
from ..detectors import available_detectors
from . import components as C
from .components import Card, Hint, SectionTitle

#: the two upload kinds - "Labelled data" and "Unlabelled data" on screen.
#: MODE_IMAGES maps to LabelMode.NONE, which is what makes "Unlabelled data"
#: ignore labels that happen to be there.
MODE_LABELED, MODE_IMAGES = range(2)

#: what the scanner tries, in order - quoted back when a labeled folder has none
LABEL_SOURCES = ("real/ and ai/ subfolders, a labels.csv manifest, "
                 "or real_ / ai_ filename prefixes")


class ModeTile(QFrame):
    """One of the two upload choices: a title, a line of consequence, a state.

    A QFrame has no :checked pseudo-state, so the selected look is painted from
    here rather than from the stylesheet.
    """

    clicked = pyqtSignal()

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setProperty("tile", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(3)

        self.title_label = QLabel(title)
        self.detail_label = QLabel(detail)
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: {C.FS_SMALL}px;"
            " background: transparent;")
        lay.addWidget(self.title_label)
        lay.addWidget(self.detail_label)
        self._paint()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        """Selection is set by the page, not toggled here - the two tiles are
        mutually exclusive and UploadPage.set_mode owns that."""
        self._checked = bool(checked)
        self._paint()

    def _paint(self):
        """Repaint for the current selection state."""
        if self._checked:
            self.setStyleSheet(
                'QFrame[tile="true"] { background-color: %s;'
                ' border: 1px solid %s; border-radius: %dpx; }'
                % (T.ACCENT_SOFT, T.ACCENT, T.R_CARD))
            color, weight = T.ACCENT_TEXT, 700
        else:
            self.setStyleSheet("")          # back to the stylesheet's resting look
            color, weight = T.TEXT, 600
        self.title_label.setStyleSheet(
            f"color: {color}; font-size: {C.FS_BODY}px; font-weight: {weight};"
            " background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class DropZone(QFrame):
    """The drop target for a folder, and a second way into the file dialog.

    It holds no state of its own: UploadPage owns the drag events - see
    UploadPage.dragEnterEvent - and calls set_message() to say what the zone
    should read at each moment. That keeps one place deciding whether a given
    drop is usable, instead of the zone guessing and the page deciding again.

    Like ModeTile, the look is painted here: a QFrame has no pseudo-state for
    "something is hovering over me", and a dashed border is not something the
    global stylesheet says anywhere else.
    """

    clicked = pyqtSignal()

    #: what the zone reads when nothing is being dragged over it
    IDLE_TITLE = "Drop a folder of images here"
    IDLE_DETAIL = "or click to browse — a predictions .json works too"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("dropzone", True)
        self.setMinimumHeight(78)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._state = "idle"               # idle | ok | bad

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(3)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_label = QLabel(self.IDLE_TITLE)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label = QLabel(self.IDLE_DETAIL)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setWordWrap(True)
        lay.addWidget(self.title_label)
        lay.addWidget(self.detail_label)
        self._paint()

    def set_message(self, state: str, title: str = None, detail: str = None):
        """Say what is about to happen. `state` is idle, ok or bad.

        `title=None` restores the resting wording, which is what a drag leaving
        the window has to fall back to.
        """
        self._state = state
        self.title_label.setText(title or self.IDLE_TITLE)
        self.detail_label.setText(self.IDLE_DETAIL if title is None
                                  else (detail or ""))
        self._paint()

    def _paint(self):
        """Repaint for the current state, and for enabled/disabled.

        Disabled is its own look rather than Qt's default fade: with no upload
        kind chosen this zone is not a control that failed, it is one that is
        not open yet, and the wording underneath says so.
        """
        if not self.isEnabled():
            border, fill, title = T.BORDER, "transparent", T.TEXT_MUTED
        elif self._state == "ok":
            border, fill, title = T.ACCENT, T.ACCENT_SOFT, T.ACCENT_TEXT
        elif self._state == "bad":
            border, fill, title = T.BAD, "transparent", T.BAD
        else:
            border, fill, title = T.BORDER_STRONG, "transparent", T.TEXT_DIM

        self.setStyleSheet(
            'QFrame[dropzone="true"] { background-color: %s;'
            ' border: 1px dashed %s; border-radius: %dpx; }'
            % (fill, border, T.R_CARD))
        self.title_label.setStyleSheet(
            f"color: {title}; font-size: {C.FS_BODY}px; font-weight: 600;"
            " background: transparent;")
        self.detail_label.setStyleSheet(
            f"color: {T.TEXT_MUTED if not self.isEnabled() else T.TEXT_FAINT};"
            f" font-size: {C.FS_SMALL}px; background: transparent;")

    def changeEvent(self, event):
        """setEnabled() is called from _sync, and the look has to follow it."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._paint()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.clicked.emit()
        super().mousePressEvent(event)


class UploadPage(QWidget):
    """Declare the data, point at it, run it."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.peek = None                   # Dataset from the last folder scan
        self.busy = False                  # a run is in flight: refuse drops
        # the page, not the zone, is the drop target - see dragEnterEvent
        self.setAcceptDrops(True)
        # None until you choose. Neither tile is preselected: defaulting to
        # "Labelled data" would quietly make the choice this screen exists to ask.
        self.mode = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 0, 28, 28)
        outer.setSpacing(0)
        outer.addStretch(1)

        column = QWidget()
        column.setMaximumWidth(560)
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(14)

        col.addLayout(self._build_brand())
        col.addWidget(self._build_card())
        col.addWidget(Hint(
            "The same run from a terminal:  python detect.py <folder>"))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(column)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)                # sits a little above centre

        self._sync()

    # -- construction ------------------------------------------------------
    def _build_brand(self) -> QHBoxLayout:
        """The mark and the one-line description above the card."""
        brand = QHBoxLayout()
        brand.setSpacing(11)
        mark = QLabel()                    # the app mark, drawn not typeset
        mark.setFixedSize(26, 26)
        mark.setStyleSheet(f"background-color: {T.ACCENT}; border-radius: 8px;")

        words = QVBoxLayout()
        words.setSpacing(1)
        title = QLabel("AIGC Detector")
        title.setStyleSheet(
            f"color: {T.TEXT}; font-size: 17px; font-weight: 700;"
            " letter-spacing: -0.3px;")
        words.addWidget(title)
        words.addWidget(Hint("Find AI-generated images, and see how well that "
                             "detection holds up."))

        brand.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        brand.addLayout(words, 1)
        return brand

    def _build_card(self) -> QWidget:
        """The whole form, top to bottom: kind, folder, model, actions."""
        card = Card(padding=20)
        lay = card.layout()
        lay.setSpacing(12)

        lay.addWidget(SectionTitle("What are you uploading"))
        self.tiles = [
            ModeTile("Labelled data",
                     "A folder holding real/ and ai/. You get accuracy, AUC, "
                     "ROC and the robustness sweep."),
            ModeTile("Unlabelled data",
                     "Any folder of images. You get a verdict per image - "
                     "AI-generated or authentic."),
        ]
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        for i, tile in enumerate(self.tiles):
            tile.clicked.connect(lambda idx=i: self.set_mode(idx))
            tiles.addWidget(tile, 1)       # equal weight, not equal wordiness
        lay.addLayout(tiles)

        lay.addSpacing(4)
        self.folder_title = SectionTitle("Folder")
        lay.addWidget(self.folder_title)
        self.drop_zone = DropZone()
        self.drop_zone.clicked.connect(self.browse_folder)
        lay.addWidget(self.drop_zone)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Choose an image folder...")
        # QLineEdit takes URL drops itself and would paste a file:// string;
        # refusing them here lets the event reach the page instead
        self.path_edit.setAcceptDrops(False)
        self.path_edit.textChanged.connect(self._on_path_changed)
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self.browse_folder)
        src = QHBoxLayout()
        src.setSpacing(8)
        src.addWidget(self.path_edit, 1)
        src.addWidget(self.browse_btn)
        lay.addLayout(src)

        self.peek_label = Hint("")
        lay.addWidget(self.peek_label)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {T.BORDER};")
        lay.addSpacing(4)
        lay.addWidget(divider)
        lay.addSpacing(4)

        lay.addWidget(SectionTitle("Model"))
        self.detector_combo = QComboBox()
        for cls in available_detectors():
            suffix = ("   ·   no checkpoint"
                      if cls.requires_weights and not cls.is_ready() else "")
            self.detector_combo.addItem(cls.display_name + suffix, cls.name)
            self.detector_combo.setItemData(self.detector_combo.count() - 1,
                                            cls.description,
                                            Qt.ItemDataRole.ToolTipRole)
        self.detector_combo.currentIndexChanged.connect(self._sync)
        lay.addWidget(self.detector_combo)

        self.weights_row = QWidget()
        self.weights_row.setProperty("bare", True)   # it sits on the card
        wr = QHBoxLayout(self.weights_row)
        wr.setContentsMargins(0, 0, 0, 0)
        wr.setSpacing(8)
        self.weights_edit = QLineEdit()
        self.weights_edit.setAcceptDrops(False)   # as above: let the page have it
        self.weights_edit.setPlaceholderText(
            "models/model.pt — leave empty for the default")
        weights_browse = QPushButton("Browse...")
        weights_browse.clicked.connect(self.browse_weights)
        wr.addWidget(self.weights_edit, 1)
        wr.addWidget(weights_browse)
        lay.addWidget(self.weights_row)

        lay.addSpacing(6)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.run_btn = QPushButton("Run")
        self.run_btn.setProperty("accent", True)
        self.run_btn.setMinimumWidth(112)
        self.run_btn.clicked.connect(lambda: self.app.start_run())
        self.open_btn = QPushButton("Open predictions.json")
        self.open_btn.setProperty("link", True)
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.setToolTip("Visualise a result file produced earlier")
        self.open_btn.clicked.connect(self.browse_json)
        actions.addWidget(self.run_btn)
        actions.addWidget(self.open_btn)
        actions.addStretch(1)
        lay.addLayout(actions)

        self.status = Hint("")
        lay.addWidget(self.status)
        return card

    # -- state -------------------------------------------------------------
    @property
    def directory(self) -> str:
        # strip('"') because a path pasted from Explorer's "copy as path"
        # arrives wrapped in quotes
        return self.path_edit.text().strip().strip('"')

    @property
    def detector_name(self) -> str:
        return self.detector_combo.currentData()

    @property
    def weights(self) -> str:
        return self.weights_edit.text().strip().strip('"') or None

    @property
    def label_mode(self) -> LabelMode:
        """NONE for unlabelled data, so a labelled folder is still scored blind."""
        return LabelMode.NONE if self.mode == MODE_IMAGES else LabelMode.AUTO

    def set_mode(self, mode: int):
        """Select one of the two tiles and re-read the peek under that choice."""
        self.mode = mode
        for i, tile in enumerate(self.tiles):
            tile.setChecked(i == mode)
        self._sync()

    def set_directory(self, path: str):
        self.path_edit.setText(path)

    # -- browsing ----------------------------------------------------------
    def browse_folder(self):
        """Pick the image folder. Starts where the field currently points."""
        start = self.directory or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select image folder", start)
        if path:
            self.path_edit.setText(path)

    def browse_json(self):
        start = self.directory or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "Open predictions", start,
                                              "JSON (*.json)")
        if path:
            self.app.load_predictions_file(path)

    def browse_weights(self):
        start = self.weights_edit.text().strip() or os.getcwd()
        path, _ = QFileDialog.getOpenFileName(
            self, "Select checkpoint", start,
            "Checkpoints (*.pt *.pth);;All files (*)")
        if path:
            self.weights_edit.setText(path)

    def set_busy(self, busy: bool, message: str = ""):
        """Lock the form while a run is in flight, and unlock it after.

        Unlocking goes through _sync() rather than enabling everything, so a
        failed run does not leave Run clickable on a folder that cannot support
        the declared mode.
        """
        self.busy = busy
        for w in (self.detector_combo, self.weights_row, self.path_edit,
                  self.browse_btn, self.open_btn, self.drop_zone):
            w.setEnabled(not busy)
        for tile in self.tiles:
            tile.setEnabled(not busy)
        self.run_btn.setText("Running…" if busy else "Run")
        self.status.setText(message)
        if busy:
            self.run_btn.setEnabled(False)
        else:
            self._sync()

    # -- drag and drop -----------------------------------------------------
    #
    # Anywhere on this page is a target, not just the dashed rectangle: the
    # events are handled here and the DropZone is only what advertises them.
    # A drag that cannot be used is still accepted so that the zone can say
    # why - refusing it in dragEnterEvent would leave the pointer showing a
    # bare "no" and no sentence anywhere on screen.

    def _dropped_paths(self, mime) -> list:
        """The local paths in a drag, ignoring anything that is not a file."""
        if not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def _classify_drop(self, paths):
        """What a drop would do: (kind, path, title, detail).

        `kind` is "dir", "json" or None; a None kind carries the reason in
        `title` so the same call can drive both the hover message and what the
        drop itself reports. Dropping loose image files resolves to the folder
        they sit in, because a folder is the unit this app scores.
        """
        if not paths:
            return None, None, "That is not a file", ""
        if self.busy:
            return None, None, "A run is already going", "Wait for it to finish."

        dirs = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if os.path.isfile(p)]
        jsons = [p for p in files if p.lower().endswith(".json")]
        images = [p for p in files
                  if os.path.splitext(p)[1].lower() in IMAGE_EXTENSIONS]

        # a result file is not a folder and needs no upload kind - the Open
        # button beside Run is ungated the same way
        if len(paths) == 1 and jsons:
            return ("json", jsons[0], "Open this result file",
                    os.path.basename(jsons[0]))

        if dirs:
            if len(paths) > 1:
                return None, None, "One folder at a time", ""
            if self.mode is None:
                return None, None, "Choose a kind above first", ""
            return ("dir", dirs[0], "Score this folder",
                    os.path.basename(os.path.normpath(dirs[0])) or dirs[0])

        if images:
            parents = {os.path.dirname(p) for p in images}
            if len(parents) > 1:
                return None, None, "Those images are in different folders", ""
            if self.mode is None:
                return None, None, "Choose a kind above first", ""
            parent = parents.pop()
            plural = "" if len(images) == 1 else "s"
            return ("dir", parent, "Score the folder these sit in",
                    f"{os.path.basename(parent) or parent} — dropped "
                    f"{len(images)} image{plural}, the whole folder is scored")

        return None, None, "Not a folder of images", "Drop a folder, or a predictions .json."

    def dragEnterEvent(self, event):
        paths = self._dropped_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        kind, _, title, detail = self._classify_drop(paths)
        self.drop_zone.set_message("ok" if kind else "bad", title, detail)
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        # Qt does not carry the enter decision forward on its own, and without
        # this the drop never arrives
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.drop_zone.set_message("idle")
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        kind, path, title, detail = self._classify_drop(
            self._dropped_paths(event.mimeData()))
        self.drop_zone.set_message("idle")
        if kind == "dir":
            event.acceptProposedAction()
            # normpath: a dropped URL arrives with forward slashes, and this
            # text is what the field shows back to a Windows user
            self.set_directory(os.path.normpath(path))   # _on_path_changed follows
        elif kind == "json":
            event.acceptProposedAction()
            self.app.load_predictions_file(path)
        else:
            event.ignore()
            # _sync owns this line, so it clears itself on the next keystroke
            self.status.setText(
                " ".join(x for x in (title.rstrip(".") + ".", detail) if x))

    # -- folder peek -------------------------------------------------------
    def _on_path_changed(self):
        """Every keystroke: drop the stale peek, then rescan if it is a folder."""
        self.peek = None
        self._sync()
        if os.path.isdir(self.directory):
            self.app.peek_directory(self.directory)

    def set_peek(self, dataset):
        """Report what a background scan found, before anything is scored."""
        if dataset is None or dataset.root != os.path.abspath(self.directory or ""):
            return                          # a later folder won the race
        self.peek = dataset
        self._sync()

    def _peek_text(self):
        """(message, colour, ok) for the current folder under the current mode.

        The scan always runs with AUTO, so one scan answers both modes and
        switching the tile re-reads it rather than rescanning.
        """
        if self.mode is None:
            return "Pick one of the two above to begin.", T.TEXT_FAINT, False
        if not self.directory:
            return "", T.TEXT_DIM, False
        if self.peek is None:
            return ("Reading the folder…" if os.path.isdir(self.directory)
                    else "That folder does not exist."), T.TEXT_FAINT, False
        if not len(self.peek):
            return "No images in this folder.", T.WARN, False

        count = f"{len(self.peek):,} images"
        if self.mode == MODE_IMAGES:
            note = ("labels present but ignored — you asked for unlabelled data"
                    if self.peek.has_labels else "no labels — verdicts only")
            return f"{count}   ·   {note}", T.TEXT_DIM, True
        if not self.peek.has_labels:
            return (f"{count}, but no labels. Looked for {LABEL_SOURCES}.\n"
                    'Fix the folder, or choose "Unlabelled data" to score it '
                    'anyway.',
                    T.BAD, False)
        source = self.peek.label_source_detail.split(" (")[0]
        return (f"{count}   ·   {self.peek.n_real:,} real / {self.peek.n_ai:,} AI"
                f"   ·   {source}", T.TEXT_DIM, True)

    def _sync(self):
        """Single place that decides what is enabled and what the screen says.

        Everything - choosing a tile, typing a path, a peek landing, changing
        the model - ends up here, so the state can never be assembled two
        different ways.
        """
        cls = next((c for c in available_detectors()
                    if c.name == self.detector_name), None)
        self.weights_row.setVisible(bool(cls and cls.requires_weights))

        chosen = self.mode is not None
        for w in (self.folder_title, self.path_edit, self.browse_btn,
                  self.drop_zone):
            w.setEnabled(chosen and not self.busy)
        self.drop_zone.set_message(
            "idle", None if chosen else "Choose a kind above first",
            None if chosen else "then drop the folder here")

        message, color, ok = self._peek_text()
        self.peek_label.setText(message)
        self.peek_label.setStyleSheet(
            f"color: {color}; font-size: {C.FS_SMALL}px; background: transparent;")
        self.run_btn.setEnabled(ok)
        self.status.setText(
            "Choose a folder." if chosen and not self.directory else "")
