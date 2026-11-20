"""One short-lived supervisor per accepted attempt."""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from .common import GMError, GM003_CAPABILITY, atomic_json, sha_file, strict_load, writable_path
from .store import Store, locked
from .winproc import OwnedJob, current_identity, identity_alive


def _terminal(store, job_id, attempt_id, state, code, error=None, exit_code=0, **extra):
    path, _ = store.attempt(job_id, attempt_id)
    with locked(path / "record.lock"):
        _, current = store._project(path)
        if current["state"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return current
        current.update(state=state, stage="terminal", error_code=error,
                       exit_code=exit_code, result_lease=None, **extra)
        store._append(path, current, code)
        atomic_json(path / "result.json", {"state": state, "error_code": error,
                     "artifact_manifest": None, "result_lease": None,
                     "cleanup_receipt": extra.get("cleanup_receipt")})
        return current


def _cancelled(path):
    return (path / "cancel.signal").exists()


def _cleanup_failure(job, path, owner, launcher, worker):
    if job is None:
        return strict_load(path / "cleanup.json") if (path / "cleanup.json").exists() else {
            "owned_processes_exited": True, "processes_started": False}
    receipt = {"owned_processes_exited": False, "forced_termination": True,
               "owner": owner, "launcher": launcher, "worker": worker}
    try:
        job.terminate()
        receipt["worker_exit_code"] = job.wait()
        if not job.wait_empty(10000):
            raise GMError("OWNED_TREE_WAIT_TIMEOUT", "Descendant did not exit", 9)
        receipt.update(owned_processes_exited=True,
            peak_job_memory_bytes=job.peak_job_memory_bytes,
            peak_tree_rss_bytes=job.peak_tree_rss_bytes,
            rss_sample_complete=job.rss_sample_complete)
    finally:
        job.close()
        atomic_json(path / "cleanup.json", receipt)
    return receipt


def run_supervisor(store_root: Path, entry: Path, job_id: str, attempt_id: str):
    store = Store(store_root, entry)
    path, spec = store.attempt(job_id, attempt_id)
    try:
        with locked(path / "execution.lock", nonblocking=True):
            if spec["backend"] == GM003_CAPABILITY:
                from .gm003_supervisor import run_gm003
                run_gm003(store, path, spec, entry, job_id, attempt_id)
            else:
                _run_owned_supervisor(store, path, spec, entry, job_id, attempt_id)
    except GMError as error:
        if error.code != "LOCK_BUSY":
            raise
        # A duplicate never owns this attempt and must not publish a failure.


def _run_owned_supervisor(store, path, spec, entry, job_id, attempt_id):
    job = None
    heavy_context = None
    owner = launcher = worker = None
    try:
        owner = current_identity()
        if not owner or owner.get("unknown"):
            raise GMError("OWNER_IDENTITY_UNKNOWN", "Cannot bind supervisor identity", 9)
        claimed = store.transition(job_id, attempt_id, "PREFLIGHT", "SUPERVISOR_OWNED",
                                   owner=owner, stage="preflight")
        if claimed["state"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return
        if spec["backend"] != "test_mock":
            _terminal(store, job_id, attempt_id, "FAILED", "BACKEND_NOT_IMPLEMENTED",
                      "BACKEND_NOT_IMPLEMENTED", 4)
            return
        policy = spec["request"]["resource_policy"]
        if policy["ram_limit_bytes"] <= 64 * 1024 * 1024:
            raise GMError("RESOURCE_LIMIT_TOO_LOW", "Mock control tree requires 64 MiB", 5)
        deadline = time.monotonic() + policy["wall_limit_s"]
        store.transition(job_id, attempt_id, "WAITING_RESOURCE", "WAITING_RESOURCE", stage="mutex")
        wait_start = time.monotonic()
        while True:
            if _cancelled(path):
                _terminal(store, job_id, attempt_id, "CANCELLED", "CANCELLED_WAITING",
                          "CANCELLED", 8, cleanup_receipt={"owned_processes_exited": True})
                return
            try:
                heavy_context = locked(store.root / "heavy.lock", nonblocking=True)
                heavy_context.__enter__()
                break
            except GMError:
                heavy_context = None
                if time.monotonic() - wait_start > policy["wait_limit_s"]:
                    raise GMError("RESOURCE_WAIT_TIMEOUT", "Mutex wait expired", 5)
                time.sleep(.05)
        if _cancelled(path):
            _terminal(store, job_id, attempt_id, "CANCELLED", "CANCELLED_BEFORE_RUN",
                      "CANCELLED", 8, cleanup_receipt={"owned_processes_exited": True})
            return
        job = OwnedJob(int(policy["ram_limit_bytes"]) - 64 * 1024 * 1024)
        launcher_file = entry.parent.parent / "tests" / "helpers" / "mock_launcher.py"
        if not launcher_file.is_file():
            raise GMError("TEST_WORKER_MISSING", str(launcher_file), 9)
        launcher = job.spawn([sys.executable, "-B", str(launcher_file), str(path)], str(entry.parent.parent))
        handshake_deadline = time.monotonic() + 3
        worker = None
        while time.monotonic() < handshake_deadline:
            if (path / "worker.json").exists():
                candidate = strict_load(path / "worker.json")
                if (candidate.get("pid") != launcher["pid"] and
                    identity_alive(candidate) is True and job.member(candidate["pid"])):
                    worker = {**candidate, "job_member": True}
                    break
                raise GMError("WORKER_HANDSHAKE_INVALID", "Identity or membership failed", 9)
            if job.poll() is not None:
                break
            time.sleep(.02)
        if not worker:
            raise GMError("WORKER_HANDSHAKE_FAILED", "Worker did not join job", 9)
        store.transition(job_id, attempt_id, "RUNNING", "WORKER_STARTED", worker=worker,
                         launcher=launcher, stage="mock_compute")
        cancelled_at = None
        worker_exit = None
        forced = False
        stage_start = time.monotonic()
        while True:
            worker_exit = job.poll()
            job.memory_peak()
            if job.peak_tree_rss_bytes > policy["ram_limit_bytes"]:
                job.terminate(); worker_exit = job.wait()
                raise GMError("RAM_LIMIT", "Owned tree RSS limit exceeded", 5)
            if worker_exit is not None:
                break
            if _cancelled(path):
                if cancelled_at is None:
                    cancelled_at = time.monotonic()
                if time.monotonic() - cancelled_at >= 5:
                    job.terminate()
                    forced = True
                    worker_exit = job.wait()
                    break
            if time.monotonic() >= deadline:
                job.terminate()
                worker_exit = job.wait()
                raise GMError("WALL_TIMEOUT", "Mock wall limit expired", 5)
            if time.monotonic() - stage_start >= policy["stage_timeout_s"]:
                job.terminate()
                worker_exit = job.wait()
                raise GMError("STAGE_TIMEOUT", "Mock stage limit expired", 5)
            time.sleep(.025)
        # Closing the container terminates and waits on any descendant that outlived the root.
        peak = job.memory_peak()
        if worker_exit is not None:
            job.wait()
        if not job.wait_empty(1000):
            job.terminate()
            forced = True
            if not job.wait_empty(10000):
                raise GMError("OWNED_TREE_WAIT_TIMEOUT", "Descendant did not exit", 9)
        tree_rss = job.peak_tree_rss_bytes
        rss_complete = job.rss_sample_complete
        job.close()
        job = None
        receipt = {"worker_exit_code": worker_exit, "owned_processes_exited": True,
                   "forced_termination": forced, "peak_job_memory_bytes": peak,
                   "peak_tree_rss_bytes": tree_rss,
                   "rss_sample_complete": rss_complete,
                   "owner": owner, "launcher": launcher, "worker": worker}
        atomic_json(path / "cleanup.json", receipt)
        if _cancelled(path):
            _terminal(store, job_id, attempt_id, "CANCELLED", "CANCELLED_AFTER_WAIT",
                      "CANCELLED", 8, cleanup_receipt=receipt)
            return
        if worker_exit != 0:
            raise GMError("WORKER_CRASH", f"worker exit {worker_exit}", 9)
        crash_at = spec["request"]["extensions"].get("mock", {}).get("crash_at")
        if crash_at == "before_commit":
            os._exit(97)
        staging = path / "staging" / "artifact.txt"
        if not staging.is_file() or staging.stat().st_size > min(256 * 1024, policy["output_budget_bytes"]):
            raise GMError("MOCK_ARTIFACT_INVALID", "Missing/oversize worker artifact", 7)
        store.transition(job_id, attempt_id, "COMMITTING", "COMMITTING", stage="commit")
        with locked(path / "record.lock"):
            _, current = store._project(path)
            if current["state"] == "CANCEL_REQUESTED":
                current.update(state="CANCELLED", stage="terminal", error_code="CANCELLED", exit_code=8,
                               cleanup_receipt=receipt)
                store._append(path, current, "CANCELLED_BEFORE_COMMIT")
                atomic_json(path / "result.json", {"state": "CANCELLED", "error_code": "CANCELLED",
                    "artifact_manifest": None, "result_lease": None, "cleanup_receipt": receipt})
                return
            req = spec["request"]
            out = writable_path(Path(req["output_path"]))
            if out.exists():
                raise GMError("OUTPUT_EXISTS", str(out), 7)
            out.mkdir(parents=True, exist_ok=False)
            artifact = out / "artifact.txt"
            with staging.open("rb") as src, artifact.open("xb") as dst:
                for block in iter(lambda: src.read(65536), b""):
                    dst.write(block)
                dst.flush(); os.fsync(dst.fileno())
            manifest = {"state": "COMPLETE", "job_id": job_id, "attempt_id": attempt_id,
                        "intent_sha256": spec["intent_sha256"], "profile": req["profile"],
                        "recipe": req["recipe"], "input_identity": req["pair"],
                        "output_root": str(out), "artifacts": [{"path": "artifact.txt",
                            "bytes": artifact.stat().st_size, "sha256": sha_file(artifact),
                            "artifact_kind": "mock_metadata"}]}
            atomic_json(path / "commit.json", manifest)
            if crash_at == "after_commit":
                os._exit(98)
            current.update(state="SUCCEEDED", stage="terminal", error_code=None,
                           exit_code=0, cleanup_receipt=receipt, peak_tree_ram_bytes=tree_rss)
            store._append(path, current, "COMMIT_SUCCEEDED")
            atomic_json(path / "result.json", {"state": "SUCCEEDED", "error_code": None,
                "artifact_manifest": manifest, "result_lease": None, "cleanup_receipt": receipt})
            if crash_at == "after_terminal":
                os._exit(99)
    except GMError as e:
        receipt = _cleanup_failure(job, path, owner, launcher, worker)
        _terminal(store, job_id, attempt_id, "FAILED", e.code, e.code, e.exit_code,
                  cleanup_receipt=receipt)
    except BaseException:
        with (path / "supervisor-failure.txt").open("w", encoding="utf-8") as f:
            f.write(traceback.format_exc()[:16384])
            f.flush(); os.fsync(f.fileno())
        receipt = _cleanup_failure(job, path, owner, launcher, worker)
        _terminal(store, job_id, attempt_id, "FAILED", "SUPERVISOR_ERROR", "SUPERVISOR_ERROR", 9,
                  cleanup_receipt=receipt)
        raise
    finally:
        if heavy_context:
            try:
                heavy_context.__exit__(None, None, None)
            except (RuntimeError, ValueError):
                pass
