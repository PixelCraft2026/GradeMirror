"""Independent ResultSession owner; its Job survives the one-shot compute supervisor."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if not getattr(sys, "_grademirror_portable", None):
    sys.path.insert(0, str(ROOT / "src"))
from grademirror.common import atomic_json, strict_load
from grademirror.winproc import OwnedJob, current_identity, identity_alive
from grademirror import winproc as win
from grademirror.image_limits import LEGACY_BUDGET, validate_budget

IO_PYTHON = Path("__historical_runtime_not_shipped__")
HOST = ROOT / "tools/gm003_session_host.py"


def main(session_dir: Path):
    if strict_load(Path(session_dir) / 'session-spec.json').get('backend_id') == 'r0-cpu-portable-v1':
        from grademirror.cpu_replay.owner import main as portable_owner
        return portable_owner(session_dir)
    if os.environ.get('GM063_LAUNCH_CONFIG'):
        from grademirror.adaptive_execution.calibration_launch import enter
        enter("session_owner",session_dir)
    session_dir = session_dir.resolve()
    spec = strict_load(session_dir / "session-spec.json")
    owner = current_identity()
    if not owner or owner.get("unknown"):
        raise RuntimeError("SESSION_OWNER_IDENTITY_UNKNOWN")
    atomic_json(session_dir / "owner.json", owner)
    env = os.environ.copy()
    env.update({"CUDA_VISIBLE_DEVICES": "-1", "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4",
                "OPENBLAS_NUM_THREADS": "4", "NUMEXPR_NUM_THREADS": "4",
                "PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(session_dir), "TMP": str(session_dir)})
    env.pop("PYTHONPATH", None)
    host_budget = validate_budget(spec.get("resource_budget", LEGACY_BUDGET))["host"]
    job = OwnedJob(host_budget)
    host = None
    expired = False
    client_exited = False
    actual_handle=None;actual_identity=None
    try:
        if spec.get('backend_id') is not None:
            from grademirror.hybrid_replay.audit import start
            start('session-host',2)
            if spec['hybrid_execution'].get('fit_contract')=='gm023-hybrid-fit-v1':
                from grademirror.hybrid_fit.audit import record_launch
                record_launch('session-host',[IO_PYTHON,'-B',HOST,session_dir],session_dir)
        argv=[str(IO_PYTHON), "-B", str(HOST), str(session_dir)]
        from grademirror.light.runtime import operation,worker
        light=operation() is not None
        if light:
            argv,env=worker("host",(session_dir,),session_dir)
        host = job.spawn(argv, str(session_dir), env)
        atomic_json(session_dir / "host-process.json", host)
        while job.poll() is None:
            if (spec.get('backend_id') is not None or light) and actual_handle is None and (session_dir/'host-entry.json').is_file():
                actual_identity=strict_load(session_dir/'host-entry.json')
                actual_handle=win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION|win.SYNCHRONIZE,False,actual_identity['pid'])
                if (not actual_handle or not job.member(actual_identity['pid'])
                        or win.identity_key(win._identity_from_handle(actual_handle,actual_identity['pid']))
                        !=win.identity_key(actual_identity)):
                    raise RuntimeError('HYBRID_HOST_IDENTITY_UNKNOWN')
                atomic_json(session_dir/'host-actual.json',actual_identity)
            job.memory_peak()
            client = spec.get("client_identity")
            # A GUI owns this session for its lifetime, including an abrupt
            # exit. Unknown liveness never authorizes killing even this Job.
            client_exited = client is not None and identity_alive(client) is False
            if client_exited:
                job.terminate()
                break
            if job.peak_tree_rss_bytes > host_budget:
                job.terminate()
                break
            lease_path = session_dir / "lease.json"
            if lease_path.is_file():
                try:
                    lease = strict_load(lease_path)
                    expired = lease.get("state") == "RESIDENT" and time.time() > lease.get("expires_at", 0)
                except (OSError, ValueError, TypeError):
                    expired = False
            elif time.time() - spec["created_at"] > max(300, spec.get("operation_seconds", 120) + 240):
                expired = True
            active_path = session_dir / "active-operation.json"
            if expired and active_path.is_file():
                try:
                    active = strict_load(active_path)
                    if active.get("deadline_wall", 0) > time.time():
                        expired = False
                except (OSError, ValueError, TypeError):
                    pass
            if expired:
                job.terminate()
                break
            time.sleep(.05)
        exit_code = job.wait(10000)
        actual_wait=(actual_handle is not None and win.k.WaitForSingleObject(actual_handle,10000)==win.WAIT_OBJECT_0)
        if (spec.get('backend_id') is not None or spec.get('light_exact_exit')) and not actual_wait:
            raise RuntimeError('SESSION_HOST_ACTUAL_WAIT')
        if not job.wait_empty(10000):
            job.terminate()
            if not job.wait_empty(10000):
                raise RuntimeError("SESSION_HOST_TREE_WAIT_TIMEOUT")
        atomic_json(session_dir / "owner-cleanup.json", {
            "owner": owner, "host": host, "host_exit_code": exit_code,
            "actual_wait": True, "owned_tree_empty": True,
            "peak_tree_rss_bytes": job.peak_tree_rss_bytes,
            "peak_job_memory_bytes": job.peak_job_memory_bytes,
            "rss_sample_complete": job.rss_sample_complete,
            "session_id": spec["session_id"], "idle_expired": expired,
            "client_exited": client_exited,
            "actual_host_identity": actual_identity, "actual_host_handle_wait": actual_wait})
        return 0 if exit_code == 0 else 9
    except BaseException:
        # A failed startup or abnormal owner must still produce a truthful
        # receipt after waiting for every process that entered this Job.
        waited = False
        empty = False
        exit_code = None
        if host is not None:
            try:
                if job.poll() is None:
                    job.terminate()
                exit_code = job.wait(10000)
                waited = True
                empty = job.wait_empty(10000)
            except BaseException:
                pass
        else:
            try:
                empty = job.wait_empty(10000)
                waited = empty
            except BaseException:
                pass
        atomic_json(session_dir / "owner-cleanup.json", {
            "owner": owner, "host": host, "host_exit_code": exit_code,
            "actual_wait": waited, "owned_tree_empty": empty,
            "peak_tree_rss_bytes": job.peak_tree_rss_bytes,
            "peak_job_memory_bytes": job.peak_job_memory_bytes,
            "rss_sample_complete": job.rss_sample_complete,
            "session_id": spec["session_id"], "idle_expired": expired,
            "abnormal_exit": True, "client_exited": client_exited})
        raise
    finally:
        if actual_handle:win.close(actual_handle)
        job.close()


if __name__ == "__main__":
    if sys.argv[1:] != ['--gm021-entry-only'] and (len(sys.argv) != 2 or sys.argv[1].startswith('-') or not (Path(sys.argv[1]) / 'session-spec.json').is_file()):
        print(json.dumps(dict(error='SESSION_ARGUMENT_INVALID', science=0)))
        raise SystemExit(1)
    try:
        if sys.argv[1:]==['--gm021-entry-only']:
            print(json.dumps(dict(entry=__file__,science=0,executable=sys.executable)));raise SystemExit(0)
        if len(sys.argv) != 2:
            raise ValueError("SESSION_ARG_INVALID")
        raise SystemExit(main(Path(sys.argv[1])))
    except Exception:
        if len(sys.argv) == 2:
            (Path(sys.argv[1]) / "owner-failure.log").write_text(traceback.format_exc()[:16384], encoding="utf-8")
        raise
