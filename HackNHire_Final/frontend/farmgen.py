"""farmgen.py - repeatable made-up farms for the benchmark: points, and outlines shaped like real
plots (jittered polygons of about 0.5 to 20 ha), spread over the map. Same seed, same farms."""

import math
import random

M_PER_DEGREE = 111_320.0


def make_features(extent, n_points, n_outlines, seed=2026, margin_deg=0.006):
    """GeoJSON features inside extent = (west, south, east, north), kept margin_deg from the edges
    so no outline crosses the map's border. Points first, then outlines; ids run from 1."""
    west, south, east, north = extent
    rng = random.Random(seed)
    features = []

    def centre():
        return (rng.uniform(west + margin_deg, east - margin_deg),
                rng.uniform(south + margin_deg, north - margin_deg))

    for i in range(n_points):
        x, y = centre()
        features.append({"type": "Feature", "properties": {"id": len(features) + 1, "name": f"Point {i + 1}"},
                         "geometry": {"type": "Point", "coordinates": [x, y]}})
    for i in range(n_outlines):
        x, y = centre()
        hectares = math.exp(rng.uniform(math.log(0.5), math.log(20.0)))  # many small, a few big
        radius_m = math.sqrt(hectares * 10_000 / math.pi)
        corners = rng.randint(5, 9)
        start = rng.uniform(0, 2 * math.pi)
        ring = []
        for k in range(corners):
            angle = start + 2 * math.pi * (k + rng.uniform(-0.25, 0.25)) / corners
            r = radius_m * rng.uniform(0.75, 1.2)
            ring.append([x + r * math.cos(angle) / (M_PER_DEGREE * math.cos(math.radians(y))),
                         y + r * math.sin(angle) / M_PER_DEGREE])
        ring.append(ring[0])
        features.append({"type": "Feature", "properties": {"id": len(features) + 1, "name": f"Farm {i + 1}"},
                         "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return features
