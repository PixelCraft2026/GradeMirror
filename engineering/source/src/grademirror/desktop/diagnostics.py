"""Bounded asynchronous UI diagnostics, separate from operation receipts."""
from collections import deque
import os
from pathlib import Path
import threading


class DiagnosticWriter:
    MAX_PENDING = 2048

    def __init__(self, path):
        self.path = Path(path)
        self._pending = deque()
        self._condition = threading.Condition()
        self._closing = False
        self._dropped = 0
        self.error = None
        self._thread = threading.Thread(target=self._write, name="gm-ui-log", daemon=True)
        self._thread.start()

    def append(self, line):
        with self._condition:
            if self._closing:
                return
            if len(self._pending) == self.MAX_PENDING:
                self._pending.popleft()
                self._dropped += 1
            self._pending.append(line.encode("utf-8") + b"\n")
            self._condition.notify()

    def close(self, timeout=2.):
        with self._condition:
            self._closing = True
            self._condition.notify()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _write(self):
        while True:
            with self._condition:
                while not self._pending and not self._closing:
                    self._condition.wait()
                if not self._closing:
                    self._condition.wait_for(lambda:self._closing, timeout=.1)
                batch = list(self._pending)
                self._pending.clear()
                dropped, self._dropped = self._dropped, 0
                closing = self._closing
            if batch:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("ab") as stream:
                        if dropped:
                            stream.write(('{{"event":"diagnostics_dropped","count":{}}}\n'.format(dropped)).encode())
                        stream.writelines(batch)
                        stream.flush()
                        os.fsync(stream.fileno())
                except OSError as exc:
                    self.error = str(exc)
            if closing:
                return
