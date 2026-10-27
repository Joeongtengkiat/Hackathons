"""ZSTD baseline (report section 3.1): is the storage comparison fair? Re-compresses the same clipped original
layers of the Jambi box (source_data_bundle\\jambi_box\\inputs_clipped) with each codec and compares them with
our 3 evidence files (ZSTD).
Recorded result (16 Oct): DEFLATE + predictor 15.26 MB, DEFLATE 12.80 MB, ZSTD 12.50 MB, ZSTD + predictor
14.40 MB, best per file 12.43 MB; evidence 11.69 MB -> about 1.07x smaller than the originals with the same
codec. About 1 minute. Writes zstd_baseline.txt in the results folder."""
import glob
import os
import sys
import tempfile

from _common import DRY, J, OUT, ROOT

BUNDLE = J(ROOT, "source_data_bundle", "jambi_box")
VARIANTS = [("DEFLATE", 2), ("DEFLATE", None), ("ZSTD", None), ("ZSTD", 2)]


def main():
    if DRY:
        print(f"> re-compress {J(BUNDLE, 'inputs_clipped')} with {VARIANTS}; compare with {J(BUNDLE, 'encoded')}")
        return
    import rasterio
    files = sorted(glob.glob(J(BUNDLE, "inputs_clipped", "**", "*.tif"), recursive=True))
    if not files:
        sys.exit(f"No clipped layers in {BUNDLE}: run make_source_bundle first.")
    totals = {v: 0 for v in VARIANTS}
    best = 0
    tmp = tempfile.mkdtemp()
    out = J(tmp, "x.tif")
    for f in files:
        with rasterio.open(f) as s:
            data, prof = s.read(), s.profile.copy()
        sizes = []
        for codec, pred in VARIANTS:
            p = dict(prof)
            p.update(driver="GTiff", tiled=True, blockxsize=512, blockysize=512, compress=codec)
            p.pop("predictor", None)
            p.pop("photometric", None)
            if pred:
                p["predictor"] = pred
            with rasterio.open(out, "w", **p) as d:
                d.write(data)
            n = os.path.getsize(out)
            totals[(codec, pred)] += n
            sizes.append(n)
        best += min(sizes)
    evidence = sum(os.path.getsize(f) for f in glob.glob(J(BUNDLE, "encoded", "evidence_*.tif")))
    mb = lambda n: n / 2 ** 20
    zstd = totals[("ZSTD", None)]
    lines = [f"Original layers of the Jambi box: {len(files)} clipped files, re-compressed (512 x 512 tiles)", ""]
    lines += [f"  {c:8s} predictor {str(p):5s} {mb(t):7.2f} MB" for (c, p), t in totals.items()]
    lines += [f"  best codec per file      {mb(best):7.2f} MB", f"  our 3 evidence files (ZSTD) {mb(evidence):7.2f} MB", "",
              f"Evidence vs originals with the same codec (ZSTD): {zstd / evidence:.2f}x smaller",
              f"Evidence vs originals with the best codec per file: {best / evidence:.2f}x smaller"]
    os.makedirs(J(ROOT, OUT), exist_ok=True)
    with open(J(ROOT, OUT, "zstd_baseline.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nSaved to {J(OUT, 'zstd_baseline.txt')}")


if __name__ == "__main__":
    main()
