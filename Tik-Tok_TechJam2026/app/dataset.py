"""Dataset scanning and ground-truth label inference.

Three label sources are supported and auto-detected in this order:
  1. subfolder  - real/ vs ai/ (and synonyms) anywhere in the relative path
  2. manifest   - labels.csv / labels.json with path + label columns
  3. filename   - regex prefix on the file name, default ^(real|ai|fake)[_-]

Anything that yields no label stays None -> the app runs in inference-only mode.
A folder may be partly labeled; the unlabeled remainder is still scored, and the
metrics simply use the part that has ground truth.

Nothing here decodes an image. Scanning has to stay cheap enough for the GUI to
run it on every keystroke in the folder box, which is what lets the upload
screen say "1,200 images, 600 real / 600 AI" before a single one is scored.
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum

#: What counts as an image. Anything else in the tree is counted as skipped
#: rather than silently ignored, so a folder of RAW files does not look empty.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

#: Folder / filename / manifest tokens that resolve to a class. "0" and "1" are
#: in here because a manifest column and a numerically named folder both turn up
#: in the wild; _coerce_label handles the numeric case before it ever gets here.
REAL_TOKENS = {"real", "authentic", "natural", "human", "nature", "camera", "genuine", "0"}
AI_TOKENS = {"ai", "aigc", "fake", "generated", "synthetic", "gen", "sd", "midjourney", "1"}

#: manifest files looked for in the dataset root, in order of preference
MANIFEST_NAMES = ("labels.csv", "labels.json", "manifest.csv", "manifest.json", "ground_truth.csv")
#: column names a manifest might use for the image path, and for the label.
#: Matched case-insensitively, first hit wins - see _pick_key.
PATH_KEYS = ("image_path", "path", "file", "filename", "filepath", "image", "img")
LABEL_KEYS = ("label", "is_ai", "y", "target", "class", "gt", "ground_truth")

#: the third fallback: a class name at the very start of the file name. The
#: capture group is what gets looked up in the token sets above.
DEFAULT_FILENAME_REGEX = r"^(real|authentic|ai|aigc|fake|generated)[_\-.]"


class LabelMode(str, Enum):
    """How to label a scan.

    AUTO tries the three sources in order and takes the first that produces
    anything. NONE is not "we found nothing" - it is an instruction to ignore
    labels that are there, which is what the GUI sends when you said you were
    uploading plain images.
    """

    AUTO = "auto"
    SUBFOLDER = "subfolder"
    MANIFEST = "manifest"
    FILENAME = "filename"
    NONE = "none"


LABEL_MODE_TITLES = {
    LabelMode.AUTO: "Auto-detect",
    LabelMode.SUBFOLDER: "Subfolder names (real/ vs ai/)",
    LabelMode.MANIFEST: "Manifest file (csv/json)",
    LabelMode.FILENAME: "Filename prefix",
    LabelMode.NONE: "Unlabeled (inference only)",
}


@dataclass
class ImageItem:
    """One image on disk, plus whatever ground truth we could infer for it."""

    path: str                   # absolute path
    rel_path: str               # path relative to dataset root, forward slashes
    label: int | None = None    # 0 = real/authentic, 1 = AI-generated
    size_bytes: int = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class Dataset:
    """A scanned folder: the images, and where their labels came from.

    `items` is the index the whole app is keyed by - RunResult.scores is aligned
    with it position for position, so an item's position is its identity from
    the scan all the way to the table row.
    """

    root: str = ""
    items: list = field(default_factory=list)
    label_mode: LabelMode = LabelMode.NONE
    label_source_detail: str = ""   # human-readable, shown in the UI and the log
    skipped: int = 0                # non-image files passed over during the walk

    def __len__(self) -> int:
        return len(self.items)

    @property
    def n_real(self) -> int:
        return sum(1 for i in self.items if i.label == 0)

    @property
    def n_ai(self) -> int:
        return sum(1 for i in self.items if i.label == 1)

    @property
    def n_unlabeled(self) -> int:
        return sum(1 for i in self.items if i.label is None)

    @property
    def has_labels(self) -> bool:
        """True if *any* item is labeled - a partly labeled folder still counts."""
        return self.n_real + self.n_ai > 0

    def labeled_indices(self) -> list:
        """Positions of the labeled items, for anything that needs ground truth."""
        return [i for i, it in enumerate(self.items) if it.label is not None]


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #

def list_images(root: str):
    """Recursively collect image files. Returns (paths, skipped_non_images)."""
    paths = []
    skipped = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # pruning dirnames in place is what stops os.walk descending into
        # .git/, .ipynb_checkpoints/ and friends
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                paths.append(os.path.join(dirpath, fn))
            elif not fn.startswith("."):
                skipped += 1        # a real file we chose not to score
    # sorted so a run is reproducible and predictions.json has a stable order
    paths.sort()
    return paths, skipped


def scan_directory(root, mode=LabelMode.AUTO, manifest_path=None,
                   filename_regex=DEFAULT_FILENAME_REGEX) -> Dataset:
    """Walk a folder into a Dataset, labels included. No image is decoded."""
    root = os.path.abspath(root)
    paths, skipped = list_images(root)

    items = []
    for p in paths:
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        rel = os.path.relpath(p, root).replace("\\", "/")
        items.append(ImageItem(path=p, rel_path=rel, size_bytes=size))

    # forward slashes in rel_path throughout: it is compared against manifest
    # entries and shown in the table, and Windows separators would make both of
    # those platform-dependent
    ds = Dataset(root=root, items=items, skipped=skipped)
    apply_labels(ds, mode=mode, manifest_path=manifest_path, filename_regex=filename_regex)
    return ds


def apply_labels(ds: Dataset, mode=LabelMode.AUTO, manifest_path=None,
                 filename_regex=DEFAULT_FILENAME_REGEX) -> Dataset:
    """(Re)apply labels to an existing dataset, in place.

    Separate from scan_directory so the label source can be changed without
    paying for another directory walk. It clears first, so calling it twice is
    not cumulative.
    """
    for it in ds.items:
        it.label = None
    ds.label_mode = LabelMode.NONE
    ds.label_source_detail = "no labels found - inference only"

    if not ds.items:
        return ds

    if mode == LabelMode.AUTO:
        order = [LabelMode.SUBFOLDER, LabelMode.MANIFEST, LabelMode.FILENAME]
    elif mode == LabelMode.NONE:
        return ds
    else:
        order = [mode]

    for m in order:
        if m == LabelMode.SUBFOLDER:
            n = _label_by_subfolder(ds)
            detail = "subfolder names"
        elif m == LabelMode.MANIFEST:
            mp = manifest_path or _find_manifest(ds.root)
            if not mp:
                continue
            n = _label_by_manifest(ds, mp)
            detail = "manifest: " + os.path.basename(mp)
        else:
            n = _label_by_filename(ds, filename_regex)
            detail = "filename regex: " + filename_regex

        if n > 0:
            ds.label_mode = m
            ds.label_source_detail = "%s (%d/%d labeled)" % (detail, n, len(ds.items))
            return ds
        # this source produced nothing; undo its partial work before the next one
        # runs, or a half-applied manifest would pollute the filename pass
        for it in ds.items:
            it.label = None

    return ds


def _token_label(token: str):
    """Map one word to 0 (real), 1 (AI), or None if it says nothing."""
    t = token.strip().lower()
    if t in REAL_TOKENS:
        return 0
    if t in AI_TOKENS:
        return 1
    return None


def _label_by_subfolder(ds: Dataset) -> int:
    """Label from any directory component. Returns how many were labeled.

    Walks the path from the deepest folder outwards, so ai/landscapes/ beats a
    stray "real" further up. Depth is not fixed: real/ and ai/ may sit at the
    root or several levels down, which is what makes the documented layout a
    convention rather than a requirement.
    """
    n = 0
    for it in ds.items:
        parts = it.rel_path.split("/")[:-1]     # directories only, not the file
        for part in reversed(parts):            # nearest folder wins
            lab = _token_label(part)
            if lab is not None:
                it.label = lab
                n += 1
                break
    return n


def _label_by_filename(ds: Dataset, regex: str) -> int:
    """Label from a class name at the head of the file name (real_0001.jpg)."""
    try:
        pat = re.compile(regex, re.IGNORECASE)
    except re.error:
        return 0                    # a user-supplied regex must not crash a scan
    n = 0
    for it in ds.items:
        m = pat.match(it.name)
        if not m:
            continue
        # prefer the capture group; fall back to the whole match for a regex
        # written without one, then strip the separator it dragged along
        token = m.group(1) if m.groups() else m.group(0)
        lab = _token_label(re.sub(r"[_\-.]$", "", token))
        if lab is not None:
            it.label = lab
            n += 1
    return n


def _find_manifest(root: str):
    """First recognised manifest sitting in the dataset root, or None."""
    for name in MANIFEST_NAMES:
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return p
    return None


def _coerce_label(value):
    """Turn whatever a manifest cell holds into 0 / 1 / None.

    Handles the four spellings that actually appear: booleans, numbers (a
    probability column counts as AI at >= 0.5), the true/false/yes/no strings,
    and the class words in the token sets.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return 1 if float(value) >= 0.5 else 0
    s = str(value).strip().lower()
    if not s:
        return None
    if s in ("1", "true", "yes"):
        return 1
    if s in ("0", "false", "no"):
        return 0
    return _token_label(s)


def _pick_key(keys, candidates):
    """First candidate present in `keys`, matched case-insensitively."""
    lower = {str(k).strip().lower(): k for k in keys}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


def _load_manifest_rows(manifest_path: str):
    """Read a manifest into a list of dict rows, whatever shape it arrived in.

    JSON may be a list of records, a wrapper object with that list under
    records/images/data/items, or a flat {path: label} mapping. CSV is read as
    utf-8-sig because Excel writes a BOM that would otherwise become part of the
    first column name and break _pick_key.
    """
    ext = os.path.splitext(manifest_path)[1].lower()
    if ext == ".json":
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in ("records", "images", "data", "items"):
                if key in data and isinstance(data[key], list):
                    return list(data[key])
            return [{"image_path": k, "label": v} for k, v in data.items()]
        return [r for r in data if isinstance(r, dict)]
    with open(manifest_path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _label_by_manifest(ds: Dataset, manifest_path: str) -> int:
    """Label from a csv/json manifest. Returns how many items matched a row."""
    try:
        rows = _load_manifest_rows(manifest_path)
    except Exception:
        return 0                    # unreadable manifest -> fall through to the
                                    # next source rather than failing the scan
    if not rows:
        return 0

    # the first row decides the schema; a manifest with inconsistent columns is
    # not something we can meaningfully guess at
    path_key = _pick_key(rows[0].keys(), PATH_KEYS)
    label_key = _pick_key(rows[0].keys(), LABEL_KEYS)
    if path_key is None or label_key is None:
        return 0

    base = os.path.dirname(os.path.abspath(manifest_path))
    lookup = {}
    for row in rows:
        raw = row.get(path_key)
        lab = _coerce_label(row.get(label_key))
        if raw is None or lab is None:
            continue
        raw = str(raw).strip().replace("\\", "/")
        # index under several spellings so relative and absolute paths both resolve
        abs_p = os.path.normcase(os.path.abspath(os.path.join(base, raw)))
        lookup[abs_p] = lab
        lookup[os.path.normcase(raw)] = lab
        lookup.setdefault(os.path.normcase(os.path.basename(raw)), lab)

    n = 0
    for it in ds.items:
        # most specific first: a basename shared by two subfolders must not win
        # over an exact path match
        for key in (os.path.normcase(os.path.abspath(it.path)),
                    os.path.normcase(it.rel_path),
                    os.path.normcase(it.name)):
            if key in lookup:
                it.label = lookup[key]
                n += 1
                break
    return n
