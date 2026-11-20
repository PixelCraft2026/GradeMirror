"""Durable single-machine job records, idempotency and recovery."""

from __future__ import annotations

import contextlib
import json
import msvcrt
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .common import (GMError, TERMINAL, VERSION, atomic_json, canonical_json,
                     intent_digest, normalize_request, reject_reparse, sha_bytes, sha_file,
                     strict_load, under, writable_path, public_backend)
from .winproc import current_identity, identity_alive


@contextlib.contextmanager
def locked(path: Path, *, timeout: float = 10, nonblocking: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b", buffering=0) as f:
        # Windows permits a byte-range lock beyond EOF. Initialize only after
        # acquiring it; two first callers must not write the empty file while
        # the other thread already holds that byte.
        start = time.monotonic()
        while True:
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if nonblocking or time.monotonic() - start > timeout:
                    raise GMError("LOCK_BUSY", str(path), 9)
                time.sleep(.025)
        try:
            if f.seek(0, 2) == 0:
                f.write(b"0")
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def _fsync_append(path: Path, row: dict):
    with path.open("ab") as f:
        f.write(canonical_json(row) + b"\n")
        f.flush()
        os.fsync(f.fileno())


class Store:
    def __init__(self, root: Path, entry: Path):
        self.root = writable_path(root)
        self.entry = entry.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        reject_reparse(self.root)
        self._launched = []

    def attempt(self, job_id: str, attempt_id: str):
        for value in (job_id, attempt_id):
            try:
                uuid.UUID(value)
            except (ValueError, AttributeError, TypeError):
                raise GMError("ADDRESS_INVALID", "Both UUID identifiers required")
        path = self.root / job_id / attempt_id
        reject_reparse(path)
        if not path.is_dir() or not (path / "spec.json").is_file():
            raise GMError("ADDRESS_NOT_FOUND", "Job/attempt pair not found")
        spec = strict_load(path / "spec.json")
        if spec.get("job_id") != job_id or spec.get("attempt_id") != attempt_id:
            raise GMError("ADDRESS_MISMATCH", "Attempt identity mismatch")
        return path, spec

    def _events(self, path: Path, repair_tail: bool = True):
        journal = path / "events.jsonl"
        raw = journal.read_bytes()
        rows, offset = [], 0
        lines = raw.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if not line.endswith(b"\n"):
                if i != len(lines) - 1:
                    raise GMError("JOURNAL_INVALID", "Middle partial record", 7)
                if repair_tail:
                    evidence = path / "journal-tail-fault.bin"
                    if not evidence.exists():
                        with evidence.open("xb") as f:
                            f.write(line)
                            f.flush(); os.fsync(f.fileno())
                    with journal.open("r+b") as f:
                        f.truncate(offset)
                        f.flush(); os.fsync(f.fileno())
                break
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError):
                raise GMError("JOURNAL_INVALID", "Malformed complete event", 7)
            if item.get("seq") != len(rows) + 1 or not isinstance(item.get("snapshot"), dict):
                raise GMError("JOURNAL_INVALID", "Sequence or snapshot invalid", 7)
            rows.append(item)
            offset += len(line)
        if not rows:
            raise GMError("JOURNAL_INVALID", "No acceptance event", 7)
        return rows

    def _project(self, path: Path):
        rows = self._events(path)
        status = dict(rows[-1]["snapshot"])
        atomic_json(path / "status.json", status)
        return rows, status

    def _append(self, path: Path, snapshot: dict, code: str):
        rows = self._events(path)
        event = {"protocol_version": VERSION, "job_id": snapshot["job_id"],
                 "attempt_id": snapshot["attempt_id"], "seq": len(rows) + 1,
                 "timestamp": time.time(), "state": snapshot["state"],
                 "stage": snapshot.get("stage"), "done": snapshot.get("done"),
                 "total": snapshot.get("total"), "unit": snapshot.get("unit"),
                 "message_code": code, "snapshot": snapshot}
        _fsync_append(path / "events.jsonl", event)
        atomic_json(path / "status.json", snapshot)
        return event

    def transition(self, job_id, attempt_id, state, code, **changes):
        path, _ = self.attempt(job_id, attempt_id)
        with locked(path / "record.lock"):
            _, status = self._project(path)
            if status["state"] in TERMINAL or status["state"] == "CANCEL_REQUESTED":
                return status
            status.update(changes)
            status["state"] = state
            self._append(path, status, code)
            return status

    def submit(self, request: dict, *, test_mode: bool = False, start: bool = True,
               test_start_deadline_s: float | None = None):
        if test_start_deadline_s is not None and not (test_mode and not start):
            raise GMError("TEST_OPTION_FORBIDDEN", "Test-only reservation control")
        norm = normalize_request(request, test_mode=test_mode)
        backend = "test_mock" if test_mode else public_backend(norm)
        digest = intent_digest(norm)
        key = norm.get("request_key")
        with locked(self.root / "index.lock"):
            if key:
                key_path = self.root / "keys" / (sha_bytes(key.encode("utf-8")) + ".json")
                if key_path.exists():
                    record = strict_load(key_path)
                    if record["request_key"] != key or record["intent_sha256"] != digest:
                        raise GMError("REQUEST_KEY_CONFLICT", "Same key, different intent")
                    path, spec = self.attempt(record["job_id"], record["attempt_id"])
                    status = self.reconcile(spec["job_id"], spec["attempt_id"])
                    return {"job_id": spec["job_id"], "attempt_id": spec["attempt_id"],
                            "state": status["state"], "idempotent_hit": True,
                            "intent_sha256": digest}
            job_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
            if Path(norm["output_path"]).exists():
                raise GMError("OUTPUT_EXISTS", norm["output_path"], 7)
            path = self.root / job_id / attempt_id
            path.mkdir(parents=True)
            spec = {"schema_version": VERSION, "job_id": job_id, "attempt_id": attempt_id,
                    "intent_sha256": digest, "request": norm, "accepted_at": time.time(),
                    "start_deadline": time.time() + (test_start_deadline_s if test_start_deadline_s is not None else 15),
                    "backend": backend}
            atomic_json(path / "spec.json", spec)
            status = {"job_id": job_id, "attempt_id": attempt_id, "state": "QUEUED",
                      "stage": "accepted", "error_code": None, "owner": None,
                      "worker": None, "result_lease": None, "exit_code": None,
                      "recovery_pending": False}
            _fsync_append(path / "events.jsonl", {"protocol_version": VERSION,
                "job_id": job_id, "attempt_id": attempt_id, "seq": 1,
                "timestamp": time.time(), "state": "QUEUED", "stage": "accepted",
                "done": None, "total": None, "unit": None,
                "message_code": "JOB_ACCEPTED", "snapshot": status})
            atomic_json(path / "status.json", status)
            if key:
                atomic_json(key_path, {"request_key": key, "intent_sha256": digest,
                                       "job_id": job_id, "attempt_id": attempt_id})
        if start:
            flags = subprocess.CREATE_NO_WINDOW
            # The supervisor dispatches only its fixed implementation and test mock.
            argv = [sys.executable, "-B", str(self.entry), "_supervise", "--store", str(self.root),
                    "--job", job_id, "--attempt", attempt_id]
            with (path / "supervisor.log").open("wb") as log:
                proc = subprocess.Popen(argv, cwd=self.entry.parent.parent, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=log,
                                        close_fds=True, creationflags=flags)
                self._launched.append(proc)
        return {"job_id": job_id, "attempt_id": attempt_id, "state": "QUEUED",
                "idempotent_hit": False, "intent_sha256": digest}

    def events(self, job_id, attempt_id, after_seq=0):
        path, _ = self.attempt(job_id, attempt_id)
        with locked(path / "record.lock"):
            rows = self._events(path)
        return [row for row in rows if row["seq"] > after_seq]

    def run(self, request: dict, *, test_mode: bool = False):
        receipt = self.submit(request, test_mode=test_mode)
        try:
            while True:
                status = self.reconcile(receipt["job_id"], receipt["attempt_id"])
                if status["state"] in TERMINAL:
                    return status
                time.sleep(.05)
        except KeyboardInterrupt:
            self.cancel(receipt["job_id"], receipt["attempt_id"])
            while True:
                status = self.reconcile(receipt["job_id"], receipt["attempt_id"])
                if status["state"] in TERMINAL:
                    return status
                time.sleep(.05)

    def _manifest(self, path: Path, spec: dict):
        manifest_path = path / "commit.json"
        if not manifest_path.exists():
            return None
        try:
            m = strict_load(manifest_path)
            if set(m) != {"state", "job_id", "attempt_id", "intent_sha256", "profile",
                          "recipe", "input_identity", "output_root", "artifacts"}:
                raise ValueError("manifest fields")
            req = spec["request"]
            if (m["state"] != "COMPLETE" or m["job_id"] != spec["job_id"] or
                m["attempt_id"] != spec["attempt_id"] or m["intent_sha256"] != spec["intent_sha256"] or
                m["profile"] != req["profile"] or m["recipe"] != req["recipe"] or
                m["input_identity"] != req["pair"] or m["output_root"] != req["output_path"]):
                raise ValueError("manifest binding")
            cleanup = strict_load(path / "cleanup.json")
            if cleanup.get("owned_processes_exited") is not True or not cleanup.get("worker") or not cleanup.get("launcher"):
                raise ValueError("owned process cleanup")
            root = Path(m["output_root"])
            reject_reparse(root)
            if not root.is_dir() or not isinstance(m["artifacts"], list) or not m["artifacts"]:
                raise ValueError("output root/artifacts")
            for artifact in m["artifacts"]:
                if set(artifact) != {"path", "bytes", "sha256", "artifact_kind"}:
                    raise ValueError("artifact fields")
                relative = Path(artifact["path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("artifact traversal")
                target = root / relative
                reject_reparse(target)
                if not under(target, root) or not target.is_file() or target.stat().st_size != artifact["bytes"] or sha_file(target) != artifact["sha256"]:
                    raise ValueError("artifact identity")
            return m
        except (OSError, ValueError, KeyError, TypeError, GMError) as e:
            raise GMError("COMMIT_INVALID", str(e), 7)

    def _result_projection(self, path, status, manifest=None):
        """Rebuild a disposable result projection under record.lock, without execution."""
        cleanup = status.get("cleanup_receipt")
        if cleanup is None and (path / "cleanup.json").is_file():
            cleanup = strict_load(path / "cleanup.json")
        expected = {"state": status["state"], "error_code": status.get("error_code"),
                    "artifact_manifest": manifest, "result_lease": status.get("result_lease"),
                    "cleanup_receipt": cleanup}
        try:
            if strict_load(path / "result.json") == expected:
                return
        except (OSError, ValueError, GMError):
            pass
        atomic_json(path / "result.json", expected)

    def _session_projection(self, path, status):
        """Session liveness is independent of a valid disk commit."""
        session = path / "gm003-session"
        lease_path = session / "lease.json"
        if not lease_path.is_file():
            return
        reject_reparse(lease_path)
        lease = strict_load(lease_path)
        from .lease import LEASE_FIELDS
        if not isinstance(lease, dict) or set(lease) != LEASE_FIELDS:
            raise GMError("RESULT_LEASE_INVALID", "Invalid persisted session lease", 7)
        if lease.get("state") in {"RESIDENT", "RELEASE_PENDING"}:
            for name in ("owner.json", "host-process.json"):
                identity_path = session / name
                if identity_path.is_file():
                    reject_reparse(identity_path)
                    if identity_alive(strict_load(identity_path)) is False:
                        lease = {**lease, "state": "LOST"}
                        atomic_json(lease_path, lease)
                        break
        if status.get("result_lease") != lease:
            status["result_lease"] = lease
            self._append(path, status, "RESULT_LEASE_" + lease["state"])

    def reconcile(self, job_id, attempt_id):
        path, spec = self.attempt(job_id, attempt_id)
        with locked(path / "record.lock"):
            _, status = self._project(path)
            if status["state"] in TERMINAL:
                self._session_projection(path, status)
                manifest = None
                if status["state"] == "SUCCEEDED":
                    try:
                        manifest = self._manifest(path, spec)
                        if manifest is None:
                            raise GMError("COMMIT_INVALID", "Missing committed manifest", 7)
                    except GMError:
                        status.update(state="FAILED", error_code="COMMIT_INVALID", exit_code=7,
                                      stage="integrity_check")
                        self._append(path, status, "COMMIT_INVALID_AFTER_SUCCESS")
                self._result_projection(path, status, manifest)
                return status
            owner = status.get("owner")
            if owner:
                alive = identity_alive(owner)
                if alive is True:
                    return status
                if alive is None:
                    status["recovery_pending"] = True
                    status["error_code"] = "OWNER_STATUS_UNKNOWN"
                    return status
            elif time.time() < spec["start_deadline"]:
                return status
            for member in (status.get("launcher"), status.get("worker")):
                if not member:
                    continue
                alive = identity_alive(member)
                if alive is None or alive is True:
                    status["recovery_pending"] = True
                    status["error_code"] = "OWNED_TREE_EXIT_PENDING" if alive else "OWNER_STATUS_UNKNOWN"
                    return status
            try:
                manifest = self._manifest(path, spec)
            except GMError:
                status.update(state="FAILED", error_code="COMMIT_INVALID", exit_code=7,
                              recovery_pending=False, stage="recovered")
                code = "STATE_RECOVERED_COMMIT_INVALID"
            else:
                if manifest:
                    status.update(state="SUCCEEDED", error_code=None, exit_code=0,
                                  recovery_pending=False, stage="recovered")
                    cleanup = strict_load(path / "cleanup.json")
                    atomic_json(path / "result.json", {"state": "SUCCEEDED", "error_code": None,
                        "artifact_manifest": manifest, "result_lease": None,
                        "cleanup_receipt": cleanup})
                    code = "STATE_RECOVERED_FROM_COMMIT"
                else:
                    interrupted = "SUBMISSION_INTERRUPTED" if owner is None and status.get("worker") is None else "INTERRUPTED"
                    status.update(state="FAILED", error_code=interrupted, exit_code=9,
                                  recovery_pending=False, stage="recovered")
                    code = "STATE_RECOVERED_" + interrupted
            self._append(path, status, code)
            self._session_projection(path, status)
            self._result_projection(path, status, manifest if status["state"] == "SUCCEEDED" else None)
            return status

    def cancel(self, job_id, attempt_id):
        path, _ = self.attempt(job_id, attempt_id)
        with locked(path / "record.lock"):
            _, status = self._project(path)
            if status["state"] in TERMINAL:
                return {**status, "cancel_disposition": "ALREADY_TERMINAL"}
            if (path / "commit.json").exists():
                try:
                    self._manifest(path, strict_load(path / "spec.json"))
                except GMError:
                    pass
                else:
                    return {**status, "cancel_disposition": "ALREADY_COMMITTED"}
            if status["state"] != "CANCEL_REQUESTED":
                immediate = status["state"] == "QUEUED" and status.get("owner") is None
                status.update(state="CANCELLED" if immediate else "CANCEL_REQUESTED",
                              stage="terminal" if immediate else "cancelling",
                              cancel_requested_at=time.time())
                if immediate:
                    status.update(error_code="CANCELLED", exit_code=8,
                                  cleanup_receipt={"owned_processes_exited": True})
                self._append(path, status, "CANCELLED_QUEUED" if immediate else "CANCEL_REQUESTED")
                with (path / "cancel.signal").open("xb") as f:
                    f.write(b"1\n")
                    f.flush(); os.fsync(f.fileno())
                if immediate:
                    atomic_json(path / "result.json", {"state": "CANCELLED", "error_code": "CANCELLED",
                        "artifact_manifest": None, "result_lease": None,
                        "cleanup_receipt": status["cleanup_receipt"]})
            return {**status, "cancel_disposition": "REQUESTED"}
