"""worker.py - one background thread that owns the NeonService and runs jobs one at a time.

A psycopg connection must never be used by two threads at once, so every database job goes
through this queue, and the window never waits: results come back as Qt signals.
"""

import threading
import traceback
from collections import deque
from dataclasses import dataclass, field

from PyQt5.QtCore import QThread, pyqtSignal

from neon_service import Cancelled


@dataclass
class Job:
    id: int
    kind: str
    method: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    progress: bool = False


class Worker(QThread):
    done = pyqtSignal(int, str, object)              # job id, kind, result
    failed = pyqtSignal(int, str, str)               # job id, kind, message ("" = replaced)
    progress = pyqtSignal(int, str, int, int, str)   # job id, kind, done, total, text
    status = pyqtSignal(str)                         # short status text from the service
    started_job = pyqtSignal(int, str)               # job id, kind

    def __init__(self, make_service, parent=None):
        super().__init__(parent)
        self._make_service = make_service
        self._jobs = deque()
        self._cond = threading.Condition()
        self._stopping = False
        self._cancel = threading.Event()
        self._next_id = 1
        self.service = None

    def submit(self, kind, method, args=(), kwargs=None, progress=False, coalesce=False):
        """Queue service.<method>(*args, **kwargs). coalesce=True: a waiting job of the same kind
        is dropped (it gets failed(id, kind, "")), e.g. outlines for a view already panned away."""
        with self._cond:
            job = Job(self._next_id, kind, method, tuple(args), dict(kwargs or {}), progress)
            self._next_id += 1
            dropped = [j for j in self._jobs if coalesce and j.kind == kind]
            for old in dropped:
                self._jobs.remove(old)
            self._jobs.append(job)
            self._cond.notify()
        for old in dropped:
            self.failed.emit(old.id, old.kind, "")
        return job.id

    def cancel(self):
        """Ask the running job to stop (long jobs check this between steps)."""
        self._cancel.set()

    def stop(self):
        with self._cond:
            self._stopping = True
            self._jobs.clear()
            self._cond.notify()
        self._cancel.set()

    def run(self):
        self.service = self._make_service(self.status.emit)
        try:
            while True:
                with self._cond:
                    while not self._jobs and not self._stopping:
                        self._cond.wait()
                    if self._stopping:
                        return
                    job = self._jobs.popleft()
                self._cancel.clear()
                self.started_job.emit(job.id, job.kind)
                self._execute(job)
        finally:
            self.service.close()

    def _execute(self, job):
        kwargs = dict(job.kwargs)
        if job.progress:
            kwargs["progress"] = lambda done, total, text: self.progress.emit(
                job.id, job.kind, int(done), int(total), str(text))
            kwargs["cancel"] = self._cancel
        try:
            result = getattr(self.service, job.method)(*job.args, **kwargs)
        except Cancelled:
            self.failed.emit(job.id, job.kind, "Cancelled.")
        except SystemExit as exc:  # the backend's own "how to fix it" messages
            self.failed.emit(job.id, job.kind, str(exc.code or "Stopped."))
        except Exception as exc:  # noqa: BLE001 - shown to the user instead of crashing the app
            traceback.print_exc()
            self.failed.emit(job.id, job.kind, f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(job.id, job.kind, result)
