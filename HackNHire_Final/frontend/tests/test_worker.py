import unittest

from PyQt5.QtCore import QEventLoop, QTimer

import worker
from neon_service import Cancelled
from tests.qt_app import qt_app


class FakeService:
    def __init__(self, status):
        self.status = status

    def echo(self, value):
        return {"value": value}

    def boom(self):
        raise ValueError("bad input")

    def leave(self):
        raise SystemExit("Run the build first.")

    def slow(self, progress=None, cancel=None):
        for i in range(500):
            progress(i, 500, "working")
            if cancel.wait(0.01):
                raise Cancelled()
        return "finished"

    def close(self):
        pass


class WorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.worker = worker.Worker(FakeService)
        self.events = []
        self.worker.done.connect(lambda i, kind, result: self.events.append(("done", kind, result)))
        self.worker.failed.connect(lambda i, kind, message: self.events.append(("failed", kind, message)))
        self.worker.start()

    def tearDown(self):
        self.worker.stop()
        self.worker.wait(3000)

    def wait_for(self, count, timeout_ms=5000):
        loop, timer = QEventLoop(), QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: loop.quit() if len(self.events) >= count else None)
        timer.start()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec_()
        timer.stop()

    def test_results_and_errors_come_back_in_order(self):
        self.worker.submit("a", "echo", (1,))
        self.worker.submit("b", "boom")
        self.worker.submit("c", "leave")
        self.wait_for(3)
        self.assertEqual(self.events, [("done", "a", {"value": 1}),
                                       ("failed", "b", "ValueError: bad input"),
                                       ("failed", "c", "Run the build first.")])

    def test_cancel_stops_a_long_job(self):
        self.worker.submit("s", "slow", progress=True)
        QTimer.singleShot(150, self.worker.cancel)
        self.wait_for(1)
        self.assertEqual(self.events, [("failed", "s", "Cancelled.")])

    def test_a_newer_request_replaces_a_waiting_one(self):
        self.worker.submit("s", "slow", progress=True)  # keeps the thread busy
        self.worker.submit("view", "echo", (1,), coalesce=True)
        self.worker.submit("view", "echo", (2,), coalesce=True)  # replaces the waiting one
        QTimer.singleShot(150, self.worker.cancel)
        self.wait_for(3)
        self.assertEqual(self.events, [("failed", "view", ""),
                                       ("failed", "s", "Cancelled."),
                                       ("done", "view", {"value": 2})])

    def test_progress_reaches_the_window(self):
        seen = []
        self.worker.progress.connect(lambda i, kind, done, total, text: seen.append((done, total, text)))
        self.worker.submit("s", "slow", progress=True)
        QTimer.singleShot(150, self.worker.cancel)
        self.wait_for(1)
        self.assertEqual(seen[0], (0, 500, "working"))
