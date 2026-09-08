"""Dark theme: palette constants, Qt stylesheet, matplotlib rcParams.

A neutral charcoal ramp - no blue or violet anywhere in the greys, which is
what makes most dark interfaces read cold. Surfaces sit one step above the
ground and carry a hairline; that pair is the only depth cue, since a Qt
stylesheet has no shadows to lean on.

Text contrast is measured against the ground rather than guessed:

    TEXT        14.9:1     anything you actually read
    TEXT_DIM     7.2:1     secondary lines, still comfortable
    TEXT_FAINT   5.1:1     labels and captions - the floor for information
    TEXT_MUTED   2.7:1     disabled only, and never the sole carrier of meaning

Fills that carry text are measured the same way - see the accent block below.

Colour is reserved for meaning: the accent marks the one primary action on a
screen, and real/AI keep their two hues everywhere they appear - charts, table,
gallery, preview. Everything else is greyscale and space.

Every value the stylesheet needs is named here. Nothing below hardcodes a hex,
so this block is the only thing to edit.

Three consumers, one source: STYLESHEET is applied once to the QApplication,
individual widgets read the constants for the few things a stylesheet cannot
express (a painted delegate, a status colour), and apply_matplotlib_style()
pushes the same palette into rcParams so the charts are part of the window
rather than a white rectangle inside it.
"""

from __future__ import annotations

# ---- ground and surfaces --------------------------------------------------
BG = "#121316"          # page ground
SURFACE = "#191A1E"     # header, panels, tables - one step up from the ground
CARD = "#191A1E"
RAISED = "#202227"      # buttons, hovered rows, tiles
CARD_HOVER = "#202227"
PRESSED = "#26282D"
TRACK = "#232529"       # inert fill: score-bar troughs, image letterbox
BORDER = "#2A2C32"
BORDER_STRONG = "#383B43"
BORDER_HOVER = "#474B54"

# ---- text -----------------------------------------------------------------
TEXT = "#E7E9EC"
TEXT_DIM = "#9CA3AC"
TEXT_FAINT = "#828892"
TEXT_MUTED = "#565B63"  # disabled; deliberately below reading contrast

# ---- accent ---------------------------------------------------------------
# The brand red splits in two, because one value cannot do both jobs on a dark
# ground: bright enough to read as a line against the ground is too bright to
# carry white text. ACCENT is the hue - marks, underlines, the slider, chart
# lines - and never has text on it. ACCENT_FILL is the button, and does.
ACCENT = "#FE2C55"           # 5.1:1 on the ground
ACCENT_TEXT = "#FF7089"      # accent as text, 7.0:1
ACCENT_SOFT = "#2A1B21"      # tinted fill behind a selected row

# the fill darkens on hover rather than brightening: white type on it only
# gets easier to read that way, and it still reads as pressing in
ACCENT_FILL = "#E5163F"      # white on it 4.7:1, itself 4.0:1 on the ground
ACCENT_FILL_HOVER = "#D50D36"
ACCENT_FILL_PRESSED = "#C90B31"
ACCENT_MUTED = "#4A2029"     # the accent button, disabled
ON_ACCENT = "#FFFFFF"        # text on a solid accent fill

# ---- status ---------------------------------------------------------------
SECONDARY = "#22D3C5"
WARN = "#E0A32E"
GOOD = "#3FB950"
BAD = "#F85149"


REAL_COLOR = "#22D3C5"       # authentic
AI_COLOR = "#FE2C55"         # generated

# ---- scrollbars -----------------------------------------------------------
SCROLL = "#34373D"
SCROLL_HOVER = "#454951"

# ---- geometry -------------------------------------------------------------
#: four radii for the whole app. One uniform soft corner on every surface was
#: the loudest thing about the old look; a card and a checkbox are not the
#: same kind of object and should not round the same way.
R_CARD = 10             # cards, panels, tiles
R_VIEW = 8              # tables and lists
R_CTRL = 6              # buttons, inputs
R_TINY = 4              # checkboxes, bars, small chrome

FONT_STACK = '"Segoe UI", "Inter", "SF Pro Text", system-ui, sans-serif'
MONO_STACK = '"Cascadia Mono", "Consolas", "SF Mono", monospace'


def contrast_text(fill: str) -> str:
    """Pick text that reads on an arbitrary fill (WCAG relative luminance).

    The verdict pills use the real/AI hues at full strength, and those two sit
    on opposite sides of the line - white reads on the red but not on the
    teal. Rather than hardcode one colour per pill, measure the fill.
    """
    # sRGB -> linear, then the standard luminance weighting. Averaging the raw
    # channels instead would call the teal dark and put white on it.
    channels = []
    for raw in (int(fill[i:i + 2], 16) / 255 for i in (1, 3, 5)):
        channels.append(raw / 12.92 if raw <= 0.04045
                        else ((raw + 0.055) / 1.055) ** 2.4)
    luminance = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    # 0.35, not 0.5: white text needs a darker fill than the midpoint suggests
    return BG if luminance > 0.35 else ON_ACCENT


#: The whole application stylesheet, applied once to the QApplication. Widgets
#: opt into variants with Qt properties rather than one-off setStyleSheet calls:
#: `accent` (the primary button), `chip`, `tab`, `tile`, `link`, and `bare` for
#: a container that must not paint its own ground.
STYLESHEET = f"""
* {{
    font-family: {FONT_STACK};
    font-size: 13px;
    color: {TEXT};
}}

QWidget {{
    background-color: {BG};
}}

QMainWindow, QDialog {{
    background-color: {BG};
}}

/* labels sit on cards as often as on the page ground - never let one paint
   its own rectangle, or it carries the wrong background around with it */
QLabel {{
    background: transparent;
}}

/* Same trap one level up: a QWidget that exists only to hold a layout still
   matches the rule above and paints the ground. That is invisible while it
   sits on the ground and obvious the moment it sits on a header or a card,
   so any such container is marked bare and paints nothing. Children do not
   carry the property, so their own rules are untouched. */
QWidget[bare="true"] {{
    background: transparent;
}}
QSlider {{
    background: transparent;
}}

/* ---------- Results header ---------- */
QFrame#header {{
    background-color: {SURFACE};
    border: none;
    border-bottom: 1px solid {BORDER};
}}
QFrame#header QLabel {{
    background: transparent;
}}

/* Tabs live in the header, so they are buttons rather than a QTabBar - an
   underline and a weight change, no chrome of their own */
QPushButton[tab="true"] {{
    padding: 9px 2px;
    margin-right: 22px;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    background-color: transparent;
    color: {TEXT_DIM};
    font-size: 13px;
    font-weight: 500;
}}
QPushButton[tab="true"]:hover {{
    color: {TEXT};
}}
QPushButton[tab="true"]:checked {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
    font-weight: 600;
}}
QPushButton[tab="true"]:disabled {{
    color: {TEXT_MUTED};
}}

/* Borderless text button - a link, for the secondary way into the app */
QPushButton[link="true"] {{
    border: none;
    background: transparent;
    padding: 4px 2px;
    color: {TEXT_DIM};
    font-weight: 500;
}}
QPushButton[link="true"]:hover {{
    color: {ACCENT_TEXT};
    background: transparent;
}}
QPushButton[link="true"]:disabled {{
    color: {TEXT_MUTED};
    background: transparent;
}}

/* The two upload choices. A QFrame has no :checked, so the selected look is
   applied from Python - only the resting and hover states live here */
QFrame[tile="true"] {{
    background-color: {RAISED};
    border: 1px solid {BORDER};
    border-radius: {R_CARD}px;
}}
QFrame[tile="true"]:hover {{
    border-color: {BORDER_HOVER};
}}
QFrame[tile="true"] QLabel {{
    background: transparent;
}}

/* ---------- Buttons ---------- */
QPushButton {{
    background-color: {RAISED};
    border: 1px solid {BORDER_STRONG};
    border-radius: {R_CTRL}px;
    padding: 7px 14px;
    color: {TEXT};
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {PRESSED};
    border-color: {BORDER_HOVER};
}}
QPushButton:pressed {{
    background-color: {TRACK};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    background-color: {SURFACE};
    border-color: {BORDER};
}}
QPushButton[accent="true"] {{
    background-color: {ACCENT_FILL};
    border: 1px solid {ACCENT_FILL};
    color: {ON_ACCENT};
    font-weight: 600;
}}
QPushButton[accent="true"]:hover {{
    background-color: {ACCENT_FILL_HOVER};
    border-color: {ACCENT_FILL_HOVER};
}}
QPushButton[accent="true"]:pressed {{
    background-color: {ACCENT_FILL_PRESSED};
    border-color: {ACCENT_FILL_PRESSED};
}}
QPushButton[accent="true"]:disabled {{
    background-color: {ACCENT_MUTED};
    border-color: {ACCENT_MUTED};
    color: {TEXT_MUTED};
}}
QPushButton[chip="true"] {{
    border-radius: 13px;
    padding: 5px 14px;
    background-color: {RAISED};
    border: 1px solid {BORDER_STRONG};
    color: {TEXT_DIM};
}}
QPushButton[chip="true"]:hover {{
    background-color: {PRESSED};
    color: {TEXT};
}}
QPushButton[chip="true"]:checked {{
    background-color: {TEXT};
    border-color: {TEXT};
    color: {BG};
    font-weight: 600;
}}

/* ---------- Inputs ---------- */
/* inputs recede into the ground rather than lifting off it, so the field
   reads as a hole to type into and the buttons beside it stay the raised
   things on the row */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {BG};
    border: 1px solid {BORDER_STRONG};
    border-radius: {R_CTRL}px;
    padding: 7px 10px;
    selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT};
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{
    border-color: {BORDER_HOVER};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background-color: {SURFACE};
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {RAISED};
    border: 1px solid {BORDER_STRONG};
    border-radius: {R_CTRL}px;
    selection-background-color: {PRESSED};
    selection-color: {TEXT};
    outline: none;
    padding: 4px;
}}

QRadioButton {{
    spacing: 9px;
    background: transparent;
    padding: 2px 0;
}}
QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 8px;
    border: 1px solid {BORDER_STRONG};
    background-color: {BG};
}}
QRadioButton::indicator:hover {{
    border-color: {ACCENT};
}}
QRadioButton::indicator:checked {{
    border: 4px solid {ACCENT};
    background-color: {BG};
}}

QCheckBox {{
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: {R_TINY}px;
    border: 1px solid {BORDER_STRONG};
    background-color: {BG};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox:disabled {{
    color: {TEXT_MUTED};
}}
QCheckBox::indicator:disabled {{
    background-color: {SURFACE};
    border-color: {BORDER};
}}

/* ---------- Slider ---------- */
QSlider::groove:horizontal {{
    height: 4px;
    background: {TRACK};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {TEXT};
    border: none;
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background: {ON_ACCENT};
}}

/* ---------- Tabs ---------- */
QTabWidget::pane {{
    border: none;
    background-color: {BG};
}}
QTabBar {{
    background-color: transparent;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {TEXT_DIM};
    padding: 10px 20px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
    font-weight: 600;
}}

/* ---------- Views ---------- */
QListView, QTableView, QTreeView {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {R_VIEW}px;
    outline: none;
    gridline-color: {BORDER};
}}
QTableView::item {{
    padding: 5px 6px;
    border: none;
}}
QTableView::item:hover {{
    background-color: {RAISED};
}}
QTableView::item:selected {{
    background-color: {ACCENT_SOFT};
    color: {TEXT};
}}
QListView::item:selected {{
    background-color: transparent;
}}
QHeaderView {{
    background-color: transparent;
}}
QHeaderView::section {{
    background-color: {SURFACE};
    color: {TEXT_FAINT};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px;
    font-size: 11px;
    font-weight: 600;
}}
QHeaderView::section:hover {{
    color: {TEXT_DIM};
}}
QTableCornerButton::section {{
    background-color: {SURFACE};
    border: none;
}}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {SCROLL};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {SCROLL_HOVER};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {SCROLL};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {SCROLL_HOVER};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* ---------- Misc ---------- */
QProgressBar {{
    background-color: {TRACK};
    border: none;
    border-radius: {R_TINY}px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: {R_TINY}px;
}}
QStatusBar {{
    background-color: {SURFACE};
    border-top: 1px solid {BORDER};
    color: {TEXT_DIM};
}}
QStatusBar::item {{
    border: none;
}}
QSplitter::handle {{
    background-color: transparent;
}}
QSplitter::handle:horizontal {{
    width: 14px;
}}
QToolTip {{
    background-color: {RAISED};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    padding: 6px 9px;
    border-radius: {R_CTRL}px;
    font-size: 12px;
}}
QScrollArea {{
    border: none;
    background-color: transparent;
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: {R_CARD}px;
    margin-top: 14px;
    padding-top: 12px;
    background-color: {SURFACE};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {TEXT_FAINT};
    font-size: 11px;
    font-weight: 600;
}}
QMessageBox {{
    background-color: {SURFACE};
}}
QMessageBox QLabel {{
    background-color: transparent;
}}
"""


def apply_matplotlib_style() -> None:
    """Make matplotlib figures blend into the dark UI.

    Called once at startup, before any canvas exists. Imported here rather than
    at module scope so the CLI - which imports theme.py transitively - does not
    pay for matplotlib.
    """
    import matplotlib

    matplotlib.rcParams.update(
        {
            "figure.facecolor": CARD,
            "axes.facecolor": CARD,
            "savefig.facecolor": CARD,
            "axes.edgecolor": BORDER_STRONG,
            "axes.labelcolor": TEXT_FAINT,
            "axes.titlecolor": TEXT,
            "axes.titlesize": 10,
            "axes.titleweight": "600",
            "axes.labelsize": 9,
            "axes.grid": True,
            "grid.color": BORDER,
            "grid.linewidth": 0.8,
            "grid.alpha": 1.0,
            "text.color": TEXT,
            "xtick.color": TEXT_FAINT,
            "ytick.color": TEXT_FAINT,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.frameon": False,
            "legend.fontsize": 8,
            "legend.labelcolor": TEXT_DIM,
            "font.size": 9,
            # off because every canvas passes layout="constrained" instead,
            # which is the one that copes with these small panels
            "figure.autolayout": False,
        }
    )
