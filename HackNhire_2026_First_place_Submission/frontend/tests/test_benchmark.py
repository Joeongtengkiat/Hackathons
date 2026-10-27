import itertools
import threading
import unittest
from unittest import mock

from shapely.geometry import shape

import farmgen
import neon_service
from backend import common

EXTENT = (103.0, -2.3, 103.5, -1.8)


class FarmgenTest(unittest.TestCase):
    def test_farms_are_inside_valid_and_repeatable(self):
        features = farmgen.make_features(EXTENT, 20, 30, seed=7)
        self.assertEqual(features, farmgen.make_features(EXTENT, 20, 30, seed=7))
        self.assertNotEqual(features, farmgen.make_features(EXTENT, 20, 30, seed=8))
        self.assertEqual([f["geometry"]["type"] for f in features].count("Polygon"), 30)
        self.assertEqual([f["properties"]["id"] for f in features], list(range(1, 51)))
        for f in features:
            geom = shape(f["geometry"])
            self.assertTrue(geom.is_valid)
            west, south, east, north = geom.bounds
            self.assertTrue(EXTENT[0] < west and east < EXTENT[2] and EXTENT[1] < south and north < EXTENT[3])

    def test_outline_sizes_look_like_farms(self):
        for f in farmgen.make_features(EXTENT, 0, 40, seed=3):
            ha = shape(f["geometry"]).area * farmgen.M_PER_DEGREE ** 2 * 0.9993 / 10_000
            self.assertTrue(0.2 < ha < 40, ha)


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return []


class FakeConnection:
    closed = False

    def __init__(self):
        self.commits = 0

    def cursor(self):
        return FakeCursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def make_service():
    conn = FakeConnection()
    service = neon_service.NeonService(connect=lambda: conn)
    service.grid = common.Grid("test", 103.0, -1.8, 1 / 12000, 1 / 12000, 6000, 6000, 4326, True,
                               default_class=0, fingerprint="test")
    return service, conn


def timed_check(durations):
    """A fake approach whose runs take the given (fake) times in turn."""
    clock = itertools.count()
    ticks = iter(durations)

    def check(conn, grid, farms, *args):
        return [common.point_result(f, common.FOREST) if f.kind == "point"
                else common.exact_result(f, {common.FOREST: 1.0}, 1.0) for f in farms], \
            {"total_s": next(ticks), "tick": next(clock)}
    return check


class BenchmarkTest(unittest.TestCase):
    def test_median_of_three_and_nothing_written(self):
        service, conn = make_service()
        walls = iter([0.0, 3.0, 0.0, 1.0, 0.0, 2.0] * 8)  # perf_counter pairs: runs take 3, 1, 2 s
        with mock.patch.object(neon_service.stencil_approach, "check", timed_check([0.1] * 99)), \
                mock.patch.object(neon_service.polygon_approach, "check", timed_check([0.1] * 99)), \
                mock.patch.object(neon_service.time, "perf_counter", lambda: next(walls)):
            out = service.benchmark(sizes=(1, 10), repeat=3, explain=False)
        runs = out["series"]["stencils"]["point"]
        self.assertEqual([r["n"] for r in runs], [1, 10])
        self.assertEqual(runs[0]["seconds"], 2.0)  # the median of 3, 1 and 2
        self.assertEqual(out["agreement"]["outline"], [{"n": 1, "same": 1, "of": 1}, {"n": 10, "same": 10, "of": 10}])
        self.assertEqual(conn.commits, 0)

    def test_cancel_stops_between_runs(self):
        service, conn = make_service()
        cancel = threading.Event()
        seen = []

        def progress(done, total, text):
            seen.append(done)
            if done == 2:
                cancel.set()

        with mock.patch.object(neon_service.stencil_approach, "check", timed_check([0.1] * 99)), \
                mock.patch.object(neon_service.polygon_approach, "check", timed_check([0.1] * 99)):
            with self.assertRaises(neon_service.Cancelled):
                service.benchmark(sizes=(1, 10), repeat=3, explain=False, progress=progress, cancel=cancel)
        self.assertEqual(max(seen), 2)
        self.assertEqual(conn.commits, 0)
