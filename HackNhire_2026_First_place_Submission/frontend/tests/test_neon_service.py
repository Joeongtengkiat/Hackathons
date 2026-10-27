import unittest
import zlib

import numpy as np
import psycopg

import neon_service
from backend import common, stencil_approach


def make_grid(width=520, height=300, default=0):
    return common.Grid("test", 103.0, -1.8, 1 / 12000, 1 / 12000, width, height, 4326, True,
                       default_class=default, fingerprint="test")


def pages_for(grid, data):
    """Pack a colour array the way stencil_approach.build does: one zipped row per page and no
    row for a page that is all the default colour."""
    stored = [c for c in common.COLOURS if c != grid.default_class]
    rows = []
    for row_off in range(0, grid.height, common.PAGE):
        for col_off in range(0, grid.width, common.PAGE):
            page = data[row_off:row_off + common.PAGE, col_off:col_off + common.PAGE]
            layers = stencil_approach.pack_page(page, stored)
            if layers.any():
                rows.append((col_off // common.PAGE, row_off // common.PAGE,
                             zlib.compress(layers.tobytes(), 9)))
    return rows


class ClassesFromPagesTest(unittest.TestCase):
    def test_round_trip_with_edge_pages_and_an_empty_page(self):
        grid = make_grid()
        data = np.random.default_rng(2).integers(0, 6, (grid.height, grid.width)).astype(np.uint8)
        data[:256, :256] = 0  # all default colour: this page gets no row
        rows = pages_for(grid, data)
        self.assertEqual(len(rows), 5)  # 3 x 2 pages minus the empty one
        np.testing.assert_array_equal(neon_service.classes_from_pages(grid, rows), data)

    def test_default_colour_need_not_be_land(self):
        grid = make_grid(default=2)
        data = np.full((grid.height, grid.width), 2, np.uint8)
        data[10:20, 300:340] = 3
        np.testing.assert_array_equal(neon_service.classes_from_pages(grid, pages_for(grid, data)), data)


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.commits = self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class RunTest(unittest.TestCase):
    def setUp(self):
        self.made, self.statuses = [], []

        def connect():
            self.made.append(FakeConnection())
            return self.made[-1]

        self.service = neon_service.NeonService(connect=connect, on_status=self.statuses.append)

    def test_read_jobs_end_with_a_rollback(self):
        self.assertEqual(self.service._run(lambda conn: 42), 42)
        self.assertEqual((self.made[0].rollbacks, self.made[0].commits), (1, 0))

    def test_write_jobs_commit(self):
        self.service._run(lambda conn: None, write=True)
        self.assertEqual(self.made[0].commits, 1)

    def test_a_dropped_connection_reconnects_once(self):
        calls = []

        def work(conn):
            calls.append(conn)
            if len(calls) == 1:
                raise psycopg.OperationalError("server closed the connection unexpectedly")
            return "ok"

        self.assertEqual(self.service._run(work), "ok")
        self.assertEqual(len(self.made), 2)
        self.assertTrue(self.made[0].closed)
        self.assertEqual(self.statuses, ["Reconnecting to Neon…"])

    def test_gives_up_after_the_second_failure(self):
        def work(conn):
            raise psycopg.OperationalError("still down")

        with self.assertRaises(psycopg.OperationalError):
            self.service._run(work)
        self.assertEqual(len(self.made), 2)

    def test_other_errors_roll_back_and_propagate(self):
        def work(conn):
            raise ValueError("bad")

        with self.assertRaises(ValueError):
            self.service._run(work)
        self.assertEqual(self.made[0].rollbacks, 1)

    def test_backend_messages_propagate(self):
        def work(conn):
            raise SystemExit("No map in the database yet")

        with self.assertRaises(SystemExit):
            self.service._run(work)


class InfoTest(unittest.TestCase):
    def test_extent_of_the_stored_map(self):
        west, south, east, north = neon_service.map_extent(make_grid(6000, 6000))
        for got, want in ((west, 103.0), (south, -2.3), (east, 103.5), (north, -1.8)):
            self.assertAlmostEqual(got, want)

    def test_map_info(self):
        service = neon_service.NeonService(connect=lambda: None)
        service.grid = make_grid(10, 10)
        service.counts = np.array([75, 0, 25, 0, 0, 0])
        info = service.map_info()
        self.assertEqual([c["code"] for c in info["classes"]], [0, 2])
        self.assertAlmostEqual(info["classes"][1]["share"], 25.0)
        (south, west), (north, east) = info["bounds"]
        self.assertAlmostEqual(south, -1.8 - 10 / 12000)
        self.assertAlmostEqual(east, 103.0 + 10 / 12000)
        self.assertFalse(info["tiff"]["available"])
