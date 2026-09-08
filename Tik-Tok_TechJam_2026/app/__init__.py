"""The pipeline every entry point shares.

detect.py, robustness.py and gui.py are thin front ends; everything that
produces a number lives here, which is what makes the CLI and the window
incapable of disagreeing.

    dataset.py     walk a folder, infer ground truth
    runner.py      scan -> load detector -> score, with terminal logging
    metrics.py     pure functions over (y_true, scores, threshold)
    transforms.py  the post-processing degradations, five severities each
    sweep.py       the robustness grid, shared by the CLI and the GUI
    export.py      predictions.json / csv / run report
    detectors/     the model backends, behind a small plugin interface
    theme.py       palette and stylesheet
    widgets/       the Qt window

Import direction is one-way: widgets/ may import from here, and nothing here
imports from widgets/. That is what keeps detect.py and robustness.py runnable
on a machine with no PyQt6 installed.
"""
