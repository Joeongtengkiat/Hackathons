import json
import unittest

import metering

PLAN = [{"Plan": {"Node Type": "Hash Join", "Shared Hit Blocks": 12, "Shared Read Blocks": 3},
         "Planning Time": 0.5, "Execution Time": 4.25}]


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def execute(self, sql, params=None):
        self.conn.sent.append(sql)
        if "boom" in sql:
            raise RuntimeError("query failed")
        if sql.startswith(metering.EXPLAIN):
            self.rows = [(json.dumps(PLAN),)]
        elif "to_regclass" in sql:
            self.rows = [(True,)]
        elif sql.startswith("SET"):
            self.rows = []
        else:
            self.rows = [(1, b"abcd"), (2, b"xy")]

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class FakeConnection:
    def __init__(self):
        self.sent, self.rollbacks = [], 0

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1


def approach(conn):
    """Like a backend check: a table check, a setting, then one data query."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", ("stencil_pages",))
        cur.fetchone()
    with conn.cursor() as cur:
        cur.execute("SET LOCAL jit = off")
        cur.execute("SELECT idx, blob FROM t WHERE idx = ANY(%s)", ([1, 2],))
        return cur.fetchall()


class MeteringTest(unittest.TestCase):
    def test_counts_round_trips_rows_and_bytes(self):
        conn = metering.MeteredConnection(FakeConnection())
        self.assertEqual(approach(conn), [(1, b"abcd"), (2, b"xy")])
        s = metering.summarise(conn.log)
        self.assertEqual((s["round_trips"], s["data_queries"], s["rows"]), (3, 1, 2))
        self.assertEqual(s["bytes"], 8 + 4 + 8 + 2)
        self.assertGreaterEqual(s["client_s"], s["setup_s"])

    def test_classify(self):
        self.assertEqual(metering.classify("SET LOCAL jit = off"), "setting")
        self.assertEqual(metering.classify("SELECT to_regclass(%s) IS NOT NULL"), "setup")
        self.assertEqual(metering.classify("\n  SELECT s.page_col FROM stencil_pages s"), "data")

    def test_explain_sums_plans_after_replaying_settings(self):
        conn = metering.MeteredConnection(FakeConnection())
        approach(conn)
        approach(conn)
        raw = FakeConnection()
        out = metering.explain(raw, conn.log)
        self.assertEqual(out["queries"], 2)
        self.assertAlmostEqual(out["server_exec_ms"], 8.5)
        self.assertEqual((out["shared_hit"], out["shared_read"]), (24, 6))
        self.assertTrue(raw.sent[0].startswith("SET LOCAL"))
        self.assertFalse(any("to_regclass" in s for s in raw.sent))
        self.assertEqual(raw.rollbacks, 1)

    def test_explain_rolls_back_when_a_query_fails(self):
        log = [metering.QueryStat("SELECT boom", None, "data", 0.0, 0, 0)]
        raw = FakeConnection()
        with self.assertRaises(RuntimeError):
            metering.explain(raw, log)
        self.assertEqual(raw.rollbacks, 1)
