"""metering.py - measures how much database work a check does, without changing the backend.

The backend's check(conn, ...) functions take a connection, so they are handed a MeteredConnection:
it passes everything through to the real one, and logs every query the approach sends.

Client side (what the app sees, over Neon):
  round trips  one per execute()
  rows, bytes  what came back (bytes = the raw values Neon sent, as libpq received them)
  seconds      execute + fetch, so network time is included
Server side (explain()): the approach's data queries are run again with
EXPLAIN (ANALYZE, BUFFERS) in a transaction that is always rolled back, giving the time spent
inside PostgreSQL and the 8 KB blocks it touched. This is a second, warm run.
"""

import json
import time
from dataclasses import dataclass

EXPLAIN = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "


@dataclass
class QueryStat:
    sql: str
    params: object
    kind: str        # "data", "setup" (does the table exist?) or "setting" (SET LOCAL)
    seconds: float
    rows: int
    bytes: int


def classify(sql):
    text = " ".join(str(sql).split()).lower()
    if text.startswith("set "):
        return "setting"
    if "to_regclass" in text or "pg_catalog" in text or "information_schema" in text:
        return "setup"
    return "data"


def value_bytes(value):
    """A rough size for a value that did not come from libpq (tests' fake cursors)."""
    if value is None:
        return 0
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, (list, tuple)):
        return sum(value_bytes(v) for v in value)
    return 8


def result_size(cur):
    """(rows, bytes) of the result libpq holds, or None when the cursor isn't a real one."""
    res = getattr(cur, "pgresult", None)
    if res is None or not hasattr(res, "get_value"):
        return None
    total = 0
    for r in range(res.ntuples):
        for c in range(res.nfields):
            value = res.get_value(r, c)
            total += 0 if value is None else len(value)
    return res.ntuples, total


class MeteredCursor:
    def __init__(self, cur, log):
        self._cur, self._log, self._stat, self._counted = cur, log, None, False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._cur.close()

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def execute(self, sql, params=None, **kwargs):
        t0 = time.perf_counter()
        self._cur.execute(sql, params, **kwargs)
        seconds = time.perf_counter() - t0
        size = result_size(self._cur)
        self._counted = size is not None
        rows, nbytes = size or (0, 0)
        self._stat = QueryStat(str(sql), params, classify(sql), seconds, rows, nbytes)
        self._log.append(self._stat)
        return self

    def _fetched(self, t0, rows):
        self._stat.seconds += time.perf_counter() - t0
        if not self._counted:  # a fake cursor: count what was handed back
            self._stat.rows += len(rows)
            self._stat.bytes += sum(value_bytes(r) for r in rows)

    def fetchall(self):
        t0 = time.perf_counter()
        rows = self._cur.fetchall()
        self._fetched(t0, rows)
        return rows

    def fetchone(self):
        t0 = time.perf_counter()
        row = self._cur.fetchone()
        self._fetched(t0, [] if row is None else [row])
        return row


class MeteredConnection:
    """Looks like the real connection; every cursor it hands out logs its queries in .log."""

    def __init__(self, conn):
        self._conn = conn
        self.log = []

    def cursor(self, *args, **kwargs):
        return MeteredCursor(self._conn.cursor(*args, **kwargs), self.log)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def summarise(log):
    data = [s for s in log if s.kind == "data"]
    return {
        "round_trips": len(log),
        "data_queries": len(data),
        "rows": sum(s.rows for s in data),
        "bytes": sum(s.bytes for s in data),
        "client_s": sum(s.seconds for s in log),
        "setup_s": sum(s.seconds for s in log if s.kind != "data"),
    }


def _plan_numbers(plan):
    if isinstance(plan, (str, bytes)):
        plan = json.loads(plan)
    top = plan[0]
    node = top["Plan"]
    return (top.get("Execution Time", 0.0), top.get("Planning Time", 0.0),
            node.get("Shared Hit Blocks", 0), node.get("Shared Read Blocks", 0))


def explain(conn, log):
    """Run the logged data queries again under EXPLAIN (ANALYZE, BUFFERS), after the same
    SET LOCAL settings, in one transaction that is always rolled back. Returns server totals."""
    out = {"server_exec_ms": 0.0, "planning_ms": 0.0, "shared_hit": 0, "shared_read": 0, "queries": 0}
    if not any(stat.kind == "data" for stat in log):
        return out
    try:
        with conn.cursor() as cur:
            for stat in log:
                if stat.kind == "setting":
                    cur.execute(stat.sql, stat.params)
            for stat in log:
                if stat.kind != "data":
                    continue
                cur.execute(EXPLAIN + stat.sql, stat.params)
                exec_ms, plan_ms, hit, read = _plan_numbers(cur.fetchone()[0])
                out["server_exec_ms"] += exec_ms
                out["planning_ms"] += plan_ms
                out["shared_hit"] += hit
                out["shared_read"] += read
                out["queries"] += 1
    finally:
        conn.rollback()
    return out
