"""Public result operations for the fixed GM-003 group15 development route.

This layer owns no pixels or algorithm runtime.  It validates the committed
attempt and a live ResultSession before sending bounded local IPC commands.
"""

from __future__ import annotations

import time
from multiprocessing import AuthenticationError
from pathlib import Path

from .common import (GM003_CAPABILITY, GMError, atomic_json, reject_reparse,
                     sha_file, strict_load, under)
from .gm003_ipc import connect, recv_json, send_json
from .lease import validate_lease_handle
from .store import locked
from .winproc import identity_alive


_SESSION = "gm003-session"


def _committed_attempt(store, job_id: str, attempt_id: str):
    path, spec = store.attempt(job_id, attempt_id)
    if spec.get("backend") != GM003_CAPABILITY:
        raise GMError("RESULT_UNAVAILABLE", "This attempt has no GM-003 result", 4)
    status = store.reconcile(job_id, attempt_id)
    if status["state"] != "SUCCEEDED":
        raise GMError("RESULT_UNAVAILABLE", "A committed GM-003 result is required", 4)
    return path, spec, status


def _session_paths(path: Path):
    session = path / _SESSION
    reject_reparse(session)
    if not session.is_dir():
        raise GMError("RESULT_NOT_RESIDENT", "No current ResultSession", 4)
    for name in ("lease.json", "session-spec.json", "owner.json", "host-process.json"):
        reject_reparse(session / name)
    return session


def _live_session(path: Path, status: dict, binding: dict):
    session = _session_paths(path)
    lease = strict_load(session / "lease.json")
    validate_lease_handle(lease, binding=binding)
    owner = strict_load(session / "owner.json")
    host = strict_load(session / "host-process.json")
    if _identity_alive(owner) is not True or _identity_alive(host) is not True:
        raise GMError("RESULT_NOT_RESIDENT", "ResultSession owner or host exited", 4)
    session_spec = strict_load(session / "session-spec.json")
    if (session_spec.get("mode") != "group15"
            or session_spec.get("session_id") != lease["owner_instance_id"]
            or session_spec.get("project_revision") != lease["project_revision"]
            or not isinstance(session_spec.get("pipe_address"), list)
            or len(session_spec["pipe_address"]) != 2
            or session_spec["pipe_address"][0] != "127.0.0.1"
            or type(session_spec["pipe_address"][1]) is not int
            or not 0 < session_spec["pipe_address"][1] < 65536
            or not isinstance(session_spec.get("auth_hex"), str)
            or len(session_spec["auth_hex"]) != 64):
        raise GMError("RESULT_LEASE_INVALID", "ResultSession spec does not bind the lease", 7)
    try:
        bytes.fromhex(session_spec["auth_hex"])
    except ValueError as exc:
        raise GMError("RESULT_LEASE_INVALID", "Invalid ResultSession authentication", 7) from exc
    return session, session_spec, lease


def _identity_alive(identity):
    if (not isinstance(identity, dict) or type(identity.get("pid")) is not int
            or type(identity.get("creation_time")) is not int
            or not isinstance(identity.get("image_path"), str)):
        raise GMError("RESULT_LEASE_INVALID", "Malformed ResultSession process identity", 7)
    return identity_alive(identity)


def _call(session_spec: dict, command: str, **fields):
    try:
        with connect(session_spec["pipe_address"], session_spec["auth_hex"], timeout_s=5) as conn:
            send_json(conn, {"command": command, **fields})
            result = recv_json(conn)
    except (OSError, EOFError, ValueError, AuthenticationError) as exc:
        raise GMError("RESULT_NOT_RESIDENT", "ResultSession IPC is unavailable", 4) from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        code = result.get("error_code") if isinstance(result, dict) else None
        if code == "RESULT_NOT_RESIDENT":
            raise GMError("RESULT_NOT_RESIDENT", "ResultSession rejected stale identity", 4)
        raise GMError("RESULT_SESSION_ERROR", str(code or "Invalid ResultSession response"), 9)
    return result




def status(store, job_id: str, attempt_id: str):
    path, _, attempt_status = _committed_attempt(store, job_id, attempt_id)
    lease = attempt_status.get("result_lease")
    host_liveness = "NOT_ALLOCATED"
    effective = lease
    if lease is not None:
        session = path / _SESSION
        reject_reparse(session)
        states = []
        for name in ("owner.json", "host-process.json"):
            identity_path = session / name
            reject_reparse(identity_path)
            identity = strict_load(identity_path) if identity_path.is_file() else None
            states.append(_identity_alive(identity) if identity is not None else False)
        host_liveness = "ALIVE" if all(value is True for value in states) else (
            "UNKNOWN" if any(value is None for value in states) else "EXITED")
        if lease["state"] == "RESIDENT" and (host_liveness != "ALIVE" or
                                                 lease["expires_at"] <= time.time()):
            effective = {**lease, "state": "LOST"}
    return {"job_id": job_id, "attempt_id": attempt_id, "state": attempt_status["state"],
            "committed": True, "result_lease": effective,
            "algorithm_retired": True, "resident": False, "binding": None,
            "host_liveness": host_liveness, "exit_code": 0}


def preview(store, job_id: str, attempt_id: str, binding: dict, *, alpha: float):
    from .engine.profiles import retired_error
    raise retired_error()




def export(store, job_id: str, attempt_id: str, binding: dict, *, alpha: float,
           output_path: Path):
    from .engine.profiles import retired_error
    raise retired_error()


def release(store, job_id: str, attempt_id: str, binding: dict):
    path, _, attempt_status = _committed_attempt(store, job_id, attempt_id)
    with locked(path / "result-operation.lock"):
        session, session_spec, _ = _live_session(path, attempt_status, binding)
        owner = strict_load(session / "owner.json")
        host = strict_load(session / "host-process.json")
        result = _call(session_spec, "RELEASE", **binding)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            receipt_path = session / "owner-cleanup.json"
            if (receipt_path.is_file() and _identity_alive(owner) is False
                    and _identity_alive(host) is False):
                receipt = strict_load(receipt_path)
                if (receipt.get("actual_wait") is True and
                        receipt.get("owned_tree_empty") is True and
                        receipt.get("session_id") == binding["owner_instance_id"]):
                    break
            time.sleep(.05)
        else:
            raise GMError("SESSION_CLOSE_WAIT_TIMEOUT", "Host/owner actual exit unconfirmed", 9)
        projected = store.reconcile(job_id, attempt_id)
        if projected.get("result_lease", {}).get("state") != "RELEASED":
            raise GMError("RESULT_LEASE_INVALID", "Release state was not recorded", 7)
        return {"job_id": job_id, "attempt_id": attempt_id,
                "released": result.get("released") is True, "result_lease": projected["result_lease"],
                "owner_cleanup": receipt, "exit_code": 0}


def replay(store, job_id: str, attempt_id: str):
    from .engine.profiles import retired_error
    raise retired_error()
