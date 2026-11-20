"""Bounded identity barrier shared by packaged P3 transports."""
import threading
import time

from . import winproc as win
from .common import atomic_json, strict_load


class SolverHandshake:
    def __init__(self, run, launcher, job, deadline, cancelled, *, entry_file=None):
        self.run = run
        self.launcher = launcher
        self.job = job
        self.deadline = deadline
        self.cancelled = cancelled
        self.entry_file = entry_file
        self.entry_deadline = min(deadline, time.monotonic() + 120)
        self.ready = threading.Event()
        self.actual = None
        self.handle = None
        self.waited = False
        self.exit_code = None
        atomic_json(run / "solver-entry-window.json", dict(
            launcher=launcher, entry_deadline=self.entry_deadline, deadline=deadline))

    def check(self):
        if self.cancelled():
            raise InterruptedError("P3_CANCELLED")
        now = time.monotonic()
        if now >= self.deadline:
            raise TimeoutError("P3_CHILD_DEADLINE")
        if not self.ready.is_set() and now >= self.entry_deadline:
            raise TimeoutError("P3_CHILD_IDENTITY_TIMEOUT")

    def acknowledge_entry(self):
        self.check()
        if self.entry_file is None or self.handle is not None or not self.entry_file.is_file():
            return
        entry = strict_load(self.entry_file)
        if entry.get("role") != "solver":
            raise ValueError("P3_CHILD_ENTRY_ROLE")
        self._retain_identity(entry.get("identity"))
        self.check()
        atomic_json(self.run / "actual-retained.json",dict(identity=self.actual,retained=True))

    def retain(self, hello):
        self.check()
        identity = hello.get("identity")
        expected = win.identity_key(identity)
        runtime = {"numpy": "2.5.3", "scipy": "1.18.1"}
        if expected is None or hello.get("runtime") != runtime:
            raise ValueError("P3_CHILD_IDENTITY_MISMATCH")
        if self.entry_file is None:
            self._retain_identity(identity)
        elif not self.handle or win.identity_key(self.actual) != expected:
            raise ValueError("P3_CHILD_ENTRY_MISMATCH")
        self.check()
        atomic_json(self.run / "solver-active-identity.json", dict(
            launcher=self.launcher, worker=identity, worker_in_owned_job=True, handle_retained=True))
        self.check()
        self.ready.set()

    def _retain_identity(self, identity):
        expected = win.identity_key(identity)
        if expected is None:
            raise ValueError("P3_CHILD_IDENTITY_MISMATCH")
        pid = identity["pid"]
        handle = win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION | win.SYNCHRONIZE, False, pid)
        if not handle:
            raise RuntimeError("P3_CHILD_HANDLE_REQUIRED")
        try:
            if (win.identity_key(win._identity_from_handle(handle, pid)) != expected
                    or not self.job.member(pid)
                    or win.identity_key(win._identity_from_handle(handle, pid)) != expected):
                raise ValueError("P3_CHILD_IDENTITY_MISMATCH")
        except BaseException:
            win.close(handle)
            raise
        self.handle = handle
        self.actual = identity

    def wait(self):
        if not self.handle:
            return False
        code = win.DWORD()
        self.waited = (win.k.WaitForSingleObject(self.handle, 10000) == win.WAIT_OBJECT_0
                       and bool(win.k.GetExitCodeProcess(self.handle, win.c.byref(code))))
        if self.waited:
            self.exit_code = code.value
        return self.waited

    def receipt(self):
        return dict(identity=self.actual, actual_handle_wait=self.waited,
                    exit_code=self.exit_code, arrays_authorized=self.ready.is_set())

    def close(self):
        if self.handle:
            win.close(self.handle)
            self.handle = None
