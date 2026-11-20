"""Strict reads of live, atomically replaced progress snapshots.

Only observational files use this reader. Durable manifests, journals and result
bindings retain strict_load and their existing locking/verification contracts.
"""

from __future__ import annotations

import time
from pathlib import Path

from .common import GMError, strict_load


def read_live_json(path: Path, *, timeout: float = .25) -> dict:
    """Read one whole snapshot, retrying only short access/sharing conflicts.

    A not-yet-published snapshot means no progress detail. Malformed JSON and
    persistent access errors propagate; they never turn a failed job into success
    or allow supervision to reuse stale stage/deadline information indefinitely.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            # strict_load closes every handle before returning or retrying. The
            # writer retains its existing bounded atomic-replace retry as well.
            value = strict_load(path)
            if not isinstance(value, dict):
                raise GMError("LIVE_JSON_INVALID", "Progress snapshot must be an object")
            return value
        except FileNotFoundError:
            return {}
        except OSError as exc:
            # Delete-pending (303) can occur during replacement on Windows too.
            transient = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33, 303}
            remaining = deadline - time.monotonic()
            if not transient or remaining <= 0:
                raise
            time.sleep(min(.01, remaining))
