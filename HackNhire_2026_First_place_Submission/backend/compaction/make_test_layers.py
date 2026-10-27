"""
make_test_layers.py - made-up input layers for the compaction step, for tests and a first try.

eudr_layers.py reads about 20 satellite layers (JRC forest 2020, Hansen tree loss, JRC TMF yearly
change, RADD radar alerts, ...). The real ones are big downloads, so this writes small made-up
versions into a folder, named the way eudr_layers.py finds them:

    JRC_GFC2020_V3_TEST.tif                    forest on 31 Dec 2020
    JRC_GFT2020_V1_TEST.tif                    forest type (naturally regenerating, primary, planted)
    Hansen_GFC-2025-v1.13_lossyear_TEST.tif    tree loss year (1-25 = 2001-2025)
    Hansen_GFC-2025-v1.13_datamask_TEST.tif    0 no data, 1 land, 2 water
    Hansen_GFC-2025-v1.13_treecover2000_TEST.tif
    JRC_TMF_AnnualChange_v1_<year>_TEST.tif    TMF class each year, 2020-2025
    rubber_map_TEST.tif                        rubber in 2020
    radd_alerts_TEST.tif, primary_forest_TEST.tif   RADD radar alerts (date_conf) and where RADD watches
    gfw_integrated_alerts_TEST.tif             near-real-time optical alerts (date_conf), some in 2026

The area is in Central Java, far from any real map, and every branch of the rule appears: confirmed,
probable and needs-review clearing (one source, RADD only, rubber-map land), damaged forest, gaps
inside a confirmed clearing, 2026 alerts, cleared non-forest, water and a no-data corner. The
bottom-right corner is plain farmland, as large parts of real maps are, so storage that leaves out
the most common value has something to leave out.

    python compaction/make_test_layers.py OUT_DIR [--size 600] [--seed 2026]

It prints the --bbox to give eudr_layers.py (or pipeline.py).
"""

import argparse
import datetime
import os

import numpy as np
import rasterio
from rasterio.transform import from_origin

X0, Y0, PIXEL = 110.0, -7.0, 1 / 12000      # JRC's 10 m lattice, in Central Java
RADD_EPOCH = datetime.date(2014, 12, 31)    # date_conf: confidence digit, then days after this


def date_conf(confidence: int, year: int) -> int:
    """A GFW date_conf value: 2 = low, 3 = high confidence (4 = highest, integrated alerts only)."""
    return confidence * 10000 + (datetime.date(year, 6, 1) - RADD_EPOCH).days


def make_layers(folder: str, size: int = 600, seed: int = 2026) -> list[float]:
    """Write the made-up layers into folder. Returns the box [xmin, ymin, xmax, ymax], slightly inside
    the layers so it snaps to exactly size x size pixels."""
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(seed)
    shape = (size, size)
    yy, xx = np.mgrid[0:size, 0:size]
    s = size / 600                           # sizes below are for a 600-pixel map

    quiet = (yy >= 0.75 * size) & (xx >= 0.75 * size)   # plain farmland: nothing else happens here

    def blobs(n, rmin, rmax, within=None):
        out = np.zeros(shape, bool)
        for _ in range(n):
            if within is not None and within.any():
                rows, cols = np.nonzero(within)
                k = rng.integers(0, rows.size)
                cy, cx = rows[k], cols[k]
            else:
                cy, cx = rng.integers(0, size), rng.integers(0, size)
            r = rng.integers(max(2, int(rmin * s)), max(3, int(rmax * s)))
            out |= (yy - cy) ** 2 + (xx - cx) ** 2 < r * r
        return (out if within is None else out & within) & ~quiet

    water = (np.abs(yy - (size * 0.55 + 40 * s * np.sin(xx / (60 * s)))) < max(2, 4 * s)) & ~quiet
    no_data = (yy < 30 * s) & (xx < 40 * s)
    forest = blobs(14, 30, 90) & ~water
    primary = blobs(4, 40, 80, forest)
    planted = blobs(5, 10, 25, forest & ~primary)
    rubber = (blobs(10, 8, 30) & ~water) | blobs(3, 6, 14, forest)

    # Clearing after 2020 on 2020 forest: a year per patch, and which sources see it
    first_seen = np.zeros(shape, np.int16)              # 0 none, else the year
    tmf_sees, hansen_sees, radd_sees = (np.zeros(shape, bool) for _ in range(3))
    for kind, n in (("confirmed", 9), ("tmf only", 4), ("hansen only", 6), ("hansen+radd", 3), ("radd only", 3)):
        for patch in (blobs(1, 5, 18, forest & (first_seen == 0)) for _ in range(n)):
            year = int(rng.integers(2021, 2026))
            first_seen[patch] = year
            tmf_sees |= patch & (kind in ("confirmed", "tmf only"))
            hansen_sees |= patch & (kind in ("confirmed", "hansen only", "hansen+radd"))
            radd_sees |= patch & (kind in ("hansen+radd", "radd only"))
    confirmed = tmf_sees & hansen_sees
    gaps = confirmed & (rng.random(shape) < 0.06)        # pixels inside a clearing one source misses
    hansen_sees &= ~gaps
    degraded = blobs(4, 6, 16, forest & (first_seen == 0))
    alerts_2026 = blobs(4, 3, 8, forest & (first_seen == 0) & ~degraded)
    cleared_not_forest = blobs(8, 4, 14, ~forest & ~water)

    profile = dict(driver="GTiff", width=size, height=size, count=1, crs="EPSG:4326",
                   transform=from_origin(X0, Y0, PIXEL, PIXEL), compress="deflate", tiled=True,
                   blockxsize=256, blockysize=256)

    def write(name, arr):
        with rasterio.open(os.path.join(folder, name), "w", dtype=arr.dtype, **profile) as dst:
            dst.write(arr, 1)

    write("JRC_GFC2020_V3_TEST.tif", forest.astype(np.uint8))
    gft = np.where(forest, 1, 0).astype(np.uint8)
    gft[primary] = 10
    gft[planted] = 20
    write("JRC_GFT2020_V1_TEST.tif", gft)
    write("Hansen_GFC-2025-v1.13_datamask_TEST.tif", np.where(no_data, 0, np.where(water, 2, 1)).astype(np.uint8))
    write("Hansen_GFC-2025-v1.13_treecover2000_TEST.tif", np.where(forest, 85, 5).astype(np.uint8))

    loss = np.zeros(shape, np.uint8)
    loss[hansen_sees] = (first_seen[hansen_sees] - 2000).astype(np.uint8)
    loss[cleared_not_forest] = rng.integers(21, 26, int(cleared_not_forest.sum())).astype(np.uint8)
    prior = blobs(12, 4, 15, ~forest & ~water & ~cleared_not_forest)
    loss[prior] = rng.integers(1, 21, int(prior.sum())).astype(np.uint8)
    write("Hansen_GFC-2025-v1.13_lossyear_TEST.tif", loss)

    regrowth = blobs(6, 10, 30, ~forest & ~water)
    base = np.where(water, 5, np.where(forest, 1, np.where(regrowth, 4, 6))).astype(np.uint8)
    for year in range(2020, 2026):
        tmf = base.copy()
        tmf[tmf_sees & (first_seen <= year)] = 3     # deforested from its year on
        tmf[degraded & (year >= 2022)] = 2
        write(f"JRC_TMF_AnnualChange_v1_{year}_TEST.tif", tmf)

    write("rubber_map_TEST.tif", rubber.astype(np.uint8))
    write("primary_forest_TEST.tif", primary.astype(np.uint8))
    radd = np.zeros(shape, np.uint16)
    for year in range(2021, 2026):
        here = radd_sees & (first_seen == year)
        radd[here] = date_conf(3, year)
    low = blobs(3, 3, 8, forest & (first_seen == 0) & ~alerts_2026)
    radd[low] = date_conf(2, 2023)                       # low confidence: ignored by the rule
    radd[alerts_2026 & (xx % 2 == 0)] = date_conf(2, 2026)  # radar agrees with half of the 2026 alerts
    write("radd_alerts_TEST.tif", radd)
    integrated = np.zeros(shape, np.uint16)
    integrated[alerts_2026] = date_conf(3, 2026)
    write("gfw_integrated_alerts_TEST.tif", integrated)

    xmax, ymin = X0 + size * PIXEL, Y0 - size * PIXEL
    inset = PIXEL / 100
    return [X0 + inset, ymin + inset, xmax - inset, Y0 - inset]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", help="folder to write the layers into (created if needed)")
    ap.add_argument("--size", type=int, default=600, help="pixels across (default 600, about 5.5 km)")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    bbox = make_layers(args.out_dir, args.size, args.seed)
    print(f"Wrote made-up input layers to {args.out_dir}")
    print("--bbox " + " ".join(f"{v:.9f}" for v in bbox))


if __name__ == "__main__":
    main()
