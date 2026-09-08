"""The Qt window, screen by screen.

    window.py      the shell: two screens, the header, the threshold, run state
    upload.py      screen 1 - declare the data, drop or pick the folder
    loading.py     the working screen shown while a run is in flight
    pages.py       screen 2, labeled - insights, images, robustness
    gallery.py     screen 2, unlabeled - a verdict per image
    table.py       the predictions table model and its score-bar delegate
    charts.py      matplotlib canvases: histogram, ROC, confusion, degradation
    components.py  cards, chips, badges, the type scale

Two conventions hold across all of them. Nothing in here computes a result: the
pages call into app/ and render what comes back. And nothing in here stores one
either - every page is handed the AppWindow and re-reads its state on refresh(),
which is why the header slider can drive every view at once.
"""
