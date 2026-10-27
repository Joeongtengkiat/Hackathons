# compaction/: step 1, satellite layers to compact layers

- **`eudr_layers.py`** is Joe's code, copied unchanged from
  [github.com/Joeongtengkiat/HacknHire](https://github.com/Joeongtengkiat/HacknHire)
  (`eudr_layers/eudr_layers.py`, version 1.1 of 14 Oct 2026, git blob `c794cd4`).
  - **Inputs:** about 20 satellite layers.
  - **Outputs:** written to a results folder:
    - `evidence.tif`: every EUDR decision fact per pixel, in 32 bits;
    - `outcome.tif`;
    - test plots, and its own check against the original layers (`--verify`).
  - **How it works:** the docstring at the top of the file explains the inputs, the rule, the bit
    layout and every option.
- **`make_test_layers.py`** writes made-up input layers, so you can try everything without the
  real downloads. It is used by the tests.

Normally you don't run these directly: `../pipeline.py` runs `eudr_layers.py`, then uploads its
results folder to Neon and checks the plots.

**To take a newer version from Joe:**
1. Replace `eudr_layers.py`.
2. Run the backend's tests: `venv\Scripts\python -m unittest discover -s tests -t .`
3. Run `venv\Scripts\python tests\smoke_pipeline.py`.

`database/layers.py` is the only glue. If the bit layout changes, maps already in Neon refuse to
be read with the new version, and you have to upload their results folders again. His versions
1.4 and later write `outcome.tif` in the 5 colours already, and `layers.class_map` uses such a file
as it is.
