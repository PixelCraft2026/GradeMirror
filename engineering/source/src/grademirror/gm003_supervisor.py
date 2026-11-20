"""Fixed group15 execution. One managed CUDA worker per fit or explicit replay."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from .common import GMError, atomic_json, sha_file, strict_load, writable_output_root
from .gm003_ipc import connect, recv_json, send_json
from .image_limits import LEGACY_BUDGET, validate_budget
from .live_json import read_live_json
from .store import locked
from .supervisor import _terminal
from .winproc import OwnedJob, current_identity, identity_alive, process_rss
from .resource_policy import FAST_REQUIRED_FREE_MIB,VERSION

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PY = Path("__historical_runtime_not_shipped__")
NEURAL_PY = Path("__historical_runtime_not_shipped__")
IO_PY = Path("__historical_runtime_not_shipped__")
WORKER = ROOT / "tools/gm003_compute_worker.py"
OWNER = ROOT / "tools/gm003_session_owner.py"


def _gpu_snapshot():
    try:
        from .adaptive_execution.calibration_launch import nvidia_smi,declare
        from .light.runtime import operation
        from .light.planner import nvidia_tool
        smi=nvidia_tool() if operation() is not None else nvidia_smi()
        selected=operation()
        if selected and selected.get("environment", {}).get("cu124", {}).get("inventory_sha256"):
            from .light.devices import snapshot
            return snapshot(smi, selected["environment"]["cu124"], selected.get("device"))
        if os.environ.get('GM063_LAUNCH_CONFIG'):declare([smi,"--query-gpu=name,uuid,driver_version,memory.free,memory.total","--format=csv,noheader,nounits"],ROOT,role="existing_gpu_query")
        raw = subprocess.check_output([smi, "--query-gpu=name,uuid,driver_version,memory.free,memory.total",
            "--format=csv,noheader,nounits"], timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        rows = raw.decode("utf-8", "replace").strip().splitlines()
        if len(rows) != 1:
            raise ValueError("GPU_COUNT_UNQUALIFIED")
        name, gpu_uuid, driver, free, total = [part.strip() for part in rows[0].split(",")]
        selected=operation()
        expected=selected.get("device") if selected else None
        if expected and (gpu_uuid,driver)!=(expected["uuid"],expected["driver_version"]):
            raise GMError("LIGHT_DEVICE_CHANGED","NVIDIA device or driver differs from the submitted plan.",5)
        return {"name":name,"uuid":gpu_uuid,"driver_version":driver,
                "free_mib":int(free),"total_mib":int(total)}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise GMError("GPU_RESOURCE_UNQUALIFIED", f"无法读取 NVIDIA 显卡状态：{exc}", 5) from exc


def _wait_gpu(run: Path, limit_s: float, *, fine=False,required_bytes=None,resource=None,operation=None):
    from .resource_execution.admission import wait_budget
    with wait_budget(run,limit_s) as limit:
        return _wait_gpu_original(run,limit,fine=fine,required_bytes=required_bytes,resource=resource,operation=operation)


def _wait_gpu_original(run: Path, limit_s: float, *, fine=False,required_bytes=None,resource=None,operation=None):
    from .light.runtime import operation as light_operation
    from .light.resource_retry import InnerResource
    active=light_operation()

    def refusal(code, message, free=None, *, reason="CAPACITY_CHANGED"):
        if active and active["selection"]["backend"]=="gpu":
            return InnerResource(code,message,"gpu_inner_wait",
                                 [("vram",free,required_mib*1024**2)],reason=reason)
        return GMError(code,message,5)

    start = time.monotonic()
    if resource is not None:
        from .gpu_resources import validate_resource
        plan=validate_resource(resource,fine=fine,operation=operation)
        required_mib=math.ceil(plan['required_free_bytes']/1024**2)
    else:required_mib=max(3584,math.ceil((required_bytes or 0)/1024**2)) if fine else FAST_REQUIRED_FREE_MIB
    samples = []
    while True:
        if (run / "cancel.signal").exists():
            raise GMError("CANCELLED_WAITING", "Cancellation during GPU wait", 8)
        try:
            sample = _gpu_snapshot()
        except GMError as error:
            if error.code in ("GPU_RESOURCE_UNQUALIFIED","LIGHT_DEVICE_CHANGED","LIGHT_NVIDIA_TOOL_UNKNOWN",
                              "LIGHT_NVIDIA_NO_SUPPORTED_DEVICE"):
                raise refusal(error.code,str(error),reason="GPU_DETECTION_FAILED") from error
            raise
        sample['required_free_mib'] = required_mib
        sample['resource_policy_version'] = VERSION
        if resource is not None:sample['resource_plan']=resource
        ready = sample['free_mib'] >= required_mib
        reason = 'READY' if ready else 'VRAM_BELOW_MINIMUM'
        sample["admission_rule"] = "size-derived free VRAM and 80 percent allocator ceiling"
        samples.append({"time": time.time(), **sample})
        atomic_json(run / "gpu-wait.json", {"wait_seconds":time.monotonic()-start,
            "wait_limit_s":limit_s, "snapshot":sample, "waiting":not ready, "reason":reason})
        if (required_mib-512)*5>sample['total_mib']*4 or required_mib>sample['total_mib']:
            atomic_json(run/'gpu-admission.json',dict(samples=samples,admitted=False,reason='DEVICE_CAPACITY',wait_seconds=time.monotonic()-start))
            raise refusal('GPU_RAM_REQUIRED',f"此路线预计需要 {required_mib/1024:.2f} GiB 空闲显存，当前可用 {sample['free_mib']/1024:.2f} GiB；显卡总显存 {sample['total_mib']/1024:.2f} GiB，分配器上限为总量的 80%。",
                          sample['free_mib']*1024**2,reason="DEVICE_CAPACITY")
        if ready:
            atomic_json(run / "gpu-admission.json", {"samples": samples, "wait_seconds": time.monotonic()-start,
                         "admitted": True, "snapshot": sample})
            return sample
        if time.monotonic() - start >= limit_s:
            atomic_json(run / "gpu-admission.json", {"samples": samples, "wait_seconds": time.monotonic()-start,
                         "admitted": False, "reason":reason, "required_free_mib":required_mib})
            message=f"此路线预计需要 {required_mib/1024:.2f} GiB 空闲显存，当前可用 {sample['free_mib']/1024:.2f} GiB；已等待 {limit_s:g} 秒，尚未启动计算。"
            raise refusal("GPU_RESOURCE_UNQUALIFIED",message,sample['free_mib']*1024**2,reason=reason)
        time.sleep(min(.5, max(.05, limit_s - (time.monotonic()-start))))


def _session_spec(run: Path, session_dir: Path, mode: str, output: Path, project_revision: str,
                  *, inputs: dict | None = None, client_identity: dict | None = None):
    if not Path(output).is_absolute():
        raise GMError("OUTPUT_ROOT_RELATIVE", "Session output root must be absolute", 3)
    output = writable_output_root(output)
    if client_identity is not None:
        # Opt in only from the owning process itself. One-shot CLI sessions
        # deliberately omit this identity and continue to use the lease TTL.
        if client_identity != current_identity() or client_identity.get("unknown"):
            raise GMError("SESSION_CLIENT_IDENTITY_INVALID", "Client must be the current process", 9)
    key = uuid.uuid4().hex
    if inputs is None:
        raise GMError("SESSION_INPUT_BINDINGS_REQUIRED", "Explicit source and reference bindings required", 3)
    bound = inputs
    spec = {"mode": mode, "session_id": str(uuid.uuid4()), "created_at": time.time(),
            "pipe_address": None, "auth_hex": os.urandom(32).hex(),
            "source_path": str(bound["source"]["path"]),
            "reference_path": str(bound["reference"]["path"]),
            "source_sha256": bound["source"]["sha256"],
            "reference_sha256": bound["reference"]["sha256"],
            "source_bytes": bound["source"]["bytes"],
            "reference_bytes": bound["reference"]["bytes"],
            "output_root": str(output), "project_revision": project_revision,
            "resource_budget": validate_budget(bound.get("_resource_budget", LEGACY_BUDGET)),
            "operation_seconds": bound.get("_operation_seconds", 120)}
    if bound.get("_profile_id") is not None:
        from .engine.profiles import get_profile
        profile = get_profile(bound["_profile_id"],bound.get('_profile_contract_sha256'))
        spec.update(profile_id=profile["id"], profile_contract_sha256=profile["contract_sha256"])
    from .light.runtime import operation
    if operation() is not None:
        spec["light_exact_exit"] = True
    if client_identity is not None:
        spec["client_identity"] = dict(client_identity)
    if bound.get('_hybrid_execution') is not None:
        from .hybrid_replay.contract import require
        from .hybrid_replay import BACKEND_ID
        hybrid = bound['_hybrid_execution']
        require(hybrid['backend_id']==BACKEND_ID,'HYBRID_SESSION_BACKEND')
        spec.update(backend_id=BACKEND_ID,hybrid_execution=hybrid)
        if hybrid.get('fit_contract')=='gm023-hybrid-fit-v1':
            from .light.runtime import operation, bundle_exe
            spec['io_python']=str(bundle_exe("io") if operation() is not None else IO_PY)
    from .resource_execution.binding import pointer
    resource=pointer(run)
    if resource is not None:spec['resource_execution']=dict(operation_path=str(run),binding=resource)
    atomic_json(session_dir / "session-spec.json", spec)
    atomic_json(run / "session-spec.json", spec)
    return spec


def start_session(run: Path, session_dir: Path, mode: str, output: Path, project_revision: str,
                  *, inputs: dict | None = None, client_identity: dict | None = None):
    from .resource_execution.binding import pointer,worker_check
    bound_resource=pointer(run)
    if bound_resource is not None:worker_check(run,'host',bound_resource)
    if not Path(output).is_absolute():
        raise GMError("OUTPUT_ROOT_RELATIVE", "Session output root must be absolute", 3)
    output = writable_output_root(output)
    session_dir.mkdir(parents=True, exist_ok=False)
    spec = _session_spec(run, session_dir, mode, output, project_revision, inputs=inputs,
                         client_identity=client_identity)
    env = os.environ.copy()
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(session_dir), "TMP": str(session_dir)})
    env.pop("PYTHONPATH", None)
    log = (session_dir / "owner.log").open("wb")
    try:
        if spec.get('backend_id') is not None:
            from .hybrid_replay.audit import start
            start('session-owner')
            if spec['hybrid_execution'].get('fit_contract')=='gm023-hybrid-fit-v1':
                from .hybrid_fit.audit import record_launch
                record_launch('session-owner',[CONTROL_PY,'-B',OWNER,session_dir],session_dir)
        if os.environ.get('GM063_LAUNCH_CONFIG'):
            from .adaptive_execution.calibration_launch import declare
            declare([str(CONTROL_PY),"-B",str(OWNER),str(session_dir)],session_dir,role="existing_session_owner")
        argv=[str(CONTROL_PY), "-B", str(OWNER), str(session_dir)]
        from .light.runtime import operation, worker
        if operation() is not None:
            argv,env=worker("owner",(session_dir,),session_dir)
        proc = subprocess.Popen(argv,
            cwd=ROOT if operation() is not None else session_dir,
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=log, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as exc:
        # CreateProcess failed: no process handle or child was created. Record
        # this separately from an owned child that required an actual wait.
        atomic_json(session_dir / "startup-cleanup.json", {"actual_wait": False,
            "no_child_created": True, "host_exited": True,
            "creation_error": str(exc), "winerror": getattr(exc, "winerror", None)})
        raise
    finally:
        log.close()
    ready_ok = False
    try:
        deadline = time.monotonic() + (900 if spec.get('backend_id') is not None else max(30, spec["operation_seconds"]))
        while time.monotonic() < deadline:
            if (run / "cancel.signal").exists():
                raise GMError("CANCELLED", "Session startup cancelled", 8)
            if (session_dir / "host-ready.json").is_file():
                ready = strict_load(session_dir / "host-ready.json")
                spec["pipe_address"] = ready["ipc_address"]
                atomic_json(session_dir / "session-spec.json", spec)
                atomic_json(run / "session-spec.json", spec)
                owner = strict_load(session_dir / "owner.json")
                host = strict_load(session_dir / "host-process.json")
                if identity_alive(owner) is not True or identity_alive(host) is not True:
                    raise GMError("SESSION_IDENTITY_INVALID", "Owner/host exited before ready", 9)
                ready_ok = True
                return spec, owner, host
            if proc.poll() is not None:
                break
            time.sleep(.05)
        raise GMError("SESSION_START_FAILED", "ResultSession host did not become ready", 9)
    finally:
        if not ready_ok:
            try:
                atomic_json(session_dir / "startup-failure.json", {"owner_pid": proc.pid,
                    "observed_exit_code": proc.poll(), "recorded_at": time.time()})
            except OSError:
                pass  # The retained owner handle is still available.
            if proc.poll() is None:
                proc.terminate()  # retained Popen process handle, not a reused PID
            try:
                exit_code = proc.wait(timeout=10)
                waited = True
            except subprocess.TimeoutExpired:
                proc.kill()
                exit_code = proc.wait(timeout=10)
                waited = True
            host_path = session_dir / "host-process.json"
            host_identity = strict_load(host_path) if host_path.is_file() else None
            owner_receipt = session_dir / "owner-cleanup.json"
            host_exited = (strict_load(owner_receipt).get("owned_tree_empty") is True
                           if host_identity is None and owner_receipt.is_file() else None)
            stop = time.monotonic() + 10
            while host_identity is not None and time.monotonic() < stop:
                host_exited = identity_alive(host_identity) is False
                if host_exited:
                    break
                time.sleep(.02)
            atomic_json(session_dir / "startup-cleanup.json", {"owner_exit_code": exit_code,
                "actual_wait": waited, "host_exited": host_exited,
                "host_identity_recorded": host_identity is not None})


def host_call(spec, command: str, **fields):
    with connect(spec["pipe_address"], spec["auth_hex"]) as conn:
        send_json(conn, {"command": command, **fields})
        reply = recv_json(conn)
    if not reply.get("ok"):
        raise GMError(str(reply.get("error_code", "SESSION_ERROR")), command, 9)
    return reply


def _worker_env(run: Path):
    env = os.environ.copy()
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(run), "TMP": str(run),
                "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4",
                "NUMEXPR_NUM_THREADS": "4", "CUDA_VISIBLE_DEVICES": "0"})
    env.pop("PYTHONPATH", None)
    return env


def _compute_failure_error(run: Path, *, typed_cuda_oom=False) -> GMError:
    fallback = GMError("COMPUTE_FAILED", "Compute worker failed; see compute-result.json", 9)
    try:
        path = run / "compute-result.json"
        if path.stat().st_size > 16384:
            return fallback
        result = strict_load(path)
        if isinstance(result, dict) and result.get("status") == "FAIL" and isinstance(result.get("message"), str):
            detail = " ".join(result["message"].split())[:400]
            if detail:
                if typed_cuda_oom and result.get("error_code") in ("PREVIEW_MEMORY_NOT_READY", "PREVIEW_MEMORY_UNKNOWN"):
                    error = GMError(result["error_code"], detail, 5)
                    error.resource_boundary = result.get("resource_boundary")
                    return error
                if (typed_cuda_oom and result.get("error") == "OutOfMemoryError"
                        and detail.startswith("CUDA out of memory.")):
                    phase = result.get("phase")
                    stage = phase.get("stage") if isinstance(phase, dict) else None
                    prefix = f"{stage}: " if isinstance(stage, str) else ""
                    return GMError("GPU_CUDA_OUT_OF_MEMORY", prefix + detail + "; see compute-result.json", 9)
                return GMError("COMPUTE_FAILED", f"Compute worker failed: {detail}; see compute-result.json", 9)
    except (OSError, ValueError, GMError):
        pass  # Missing or malformed diagnostics must not hide the exit failure.
    return fallback


def run_compute(run: Path, mode: str, policy: dict, *, cancel_path: Path | None = None,
                other_identities: tuple[dict, ...] = ()):
    from .light.resources import active_plan
    dynamic = active_plan()
    if dynamic is not None:
        jobs = dynamic["selection"]["jobs"]
        if int(policy["ram_limit_bytes"]) != jobs["pool"]["compute"]:
            raise GMError("LIGHT_GPU_JOB_BUDGET_CHANGED", "Compute Job differs from the frozen plan", 5)
        limit = jobs["pool"]["compute"]
        policy = dict(policy, aggregate_ram_limit_bytes=jobs["tree_rss"])
    else:
        limit = int(policy["ram_limit_bytes"]) - 256 * 1024**2
    job = OwnedJob(limit)
    worker = None
    started = time.monotonic()
    forced = False
    code = None
    peak_aggregate = None if dynamic is not None else 0
    peak_aggregate_lower_bound = None
    aggregate_samples = 0
    aggregate_complete = True
    incomplete_since = None
    incomplete_sample = None
    teardown_gap = None
    from .fine_observation import ProcessSamples, sample_processes
    observations=ProcessSamples(run/'process-memory.jsonl')
    try:
        argv,env=[str(NEURAL_PY), "-B", str(WORKER), mode, str(run)],_worker_env(run)
        from .light.runtime import operation, worker as light_worker
        if operation() is not None:
            argv,env=light_worker("gpu",(mode,run),run)
        worker = job.spawn(argv,str(run),env)
        roles = [('control', current_identity()), ('worker', worker)]
        roles.extend((('session_owner', 'host')[i] if i < 2 else 'session_related', who)
                     for i, who in enumerate(other_identities))
        required_roles = [role for role, _ in roles]
        while True:
            code = job.poll()
            job.memory_peak()
            # A failing worker keeps its typed cause. A successful worker
            # still owes final deadline, pool coverage and actual-wait checks.
            if dynamic is not None and code not in (None, 0):
                break
            if job.peak_tree_rss_bytes > policy["ram_limit_bytes"]:
                raise GMError("RAM_LIMIT", "Owned compute tree exceeded RSS limit", 5)
            phase = read_live_json(run / "phase.json")
            stage = phase.get('stage', 'numeric_startup')
            sample_due = time.monotonic() - observations.last >= .2
            if dynamic is not None:
                # One current sampling round across compute and session identities.
                # Lifetime Job/OS peaks are telemetry and cannot be added to it.
                sample_started = time.time()
                # poll() proved this exact launcher exited. The Job snapshot
                # still includes any live numeric descendants.
                live_roles = roles if code is None else [row for row in roles if row[0] != 'worker']
                required = required_roles if code is None else [role for role, _ in live_roles]
                measurement = sample_processes(live_roles, snapshots=[job.member_snapshot()],
                                               required_roles=required, include_lifetime_peaks=sample_due)
                sample_finished = time.time()
                aggregate_samples += 1
                aggregate_complete = aggregate_complete and measurement["rss_complete"]
                now = time.monotonic()
                prior_gap = incomplete_sample
                prior_gap_start = incomplete_since
                if measurement["rss_complete"]:
                    incomplete_since = None
                    incomplete_sample = None
                else:
                    if incomplete_since is None:
                        incomplete_since = now
                    incomplete_sample = measurement
                lower_bound = measurement["known_partial_rss_bytes"]
                if lower_bound is not None:
                    peak_aggregate_lower_bound = max(peak_aggregate_lower_bound or 0, lower_bound)
                    if measurement["rss_complete"]:
                        peak_aggregate = max(peak_aggregate or 0, lower_bound)
                if sample_due:
                    observations.take(stage, roles, job_highwater=job.peak_job_memory_bytes,
                                      measurement=measurement)
                aggregate_limit = policy["aggregate_ram_limit_bytes"]
                over_limit = aggregate_limit and lower_bound is not None and lower_bound > aggregate_limit
                missing = incomplete_since is not None and (now - incomplete_since >= 1 or code == 0)
                missed_before_exit = code == 0 and prior_gap is not None
                if missed_before_exit and measurement["rss_complete"] and peak_aggregate is not None:
                    # A last-sample race can only be settled for members of
                    # this compute Job. Session roles must have remained known.
                    from .winproc import identity_key
                    others={identity_key(who) for role,who in live_roles}
                    old_known={identity_key(item["identity"]) for item in prior_gap["processes"]
                               if item["rss_bytes"] is not None}
                    if (None not in others and others <= old_known and now-prior_gap_start <= .25
                            and all(row.get("origin")=="job" or row.get("role")=="worker"
                                    for row in prior_gap["unresolved"])):
                        teardown_gap=dict(prior=prior_gap,settled_by="exact_root_exit_and_empty_compute_job")
                        missed_before_exit=False
                if over_limit or missing or missed_before_exit:
                    error = GMError(
                        "RAM_POOL_LIMIT" if over_limit else "RAM_POOL_OBSERVATION_UNKNOWN",
                        "Aggregate compute/session RSS exceeded" if over_limit
                        else "Compute/session RSS coverage could not be confirmed", 5)
                    try:
                        atomic_json(run / "rss-watchdog-stop.json", {
                            "stage": stage, "limit_bytes": aggregate_limit,
                            "worker_exit_code": code, "sample_started_wall": sample_started,
                            "sample_finished_wall": sample_finished, "measurement": measurement,
                            "prior_live_sample_unresolved": missed_before_exit, "code": error.code,
                        })
                    except OSError as diagnostic:
                        error.add_note(f"RSS stop receipt unavailable: {diagnostic}")
                    raise error
            else:
                other_rss = 0
                for identity in other_identities:
                    value = process_rss(identity["pid"]) if identity_alive(identity) is True else None
                    if value is None:
                        aggregate_complete = False
                    else:
                        other_rss += value
                aggregate = job.peak_tree_rss_bytes + other_rss
                peak_aggregate = max(peak_aggregate, aggregate)
                if policy.get("aggregate_ram_limit_bytes") and aggregate > policy["aggregate_ram_limit_bytes"]:
                    raise GMError("RAM_POOL_LIMIT", "Aggregate compute/session RSS exceeded", 5)
                if sample_due:
                    observations.take(stage, roles, job_highwater=job.peak_job_memory_bytes,
                                      snapshots=[job.member_snapshot()], required_roles=required_roles)
            if code is not None and dynamic is None:
                break
            if cancel_path is not None and cancel_path.exists():
                # Worker checks this at selected updates/tile boundaries.
                grace = time.monotonic() + 5
                while time.monotonic() < grace and job.poll() is None:
                    time.sleep(.025)
                if job.poll() is None:
                    forced = True
                    job.terminate()
                raise GMError("CANCELLED", "Compute cancelled", 8)
            if ((phase.get("stage") == "fit" and time.time() - phase["fit_started_wall"] > policy.get("fit_limit_s", 120))
                    or (phase.get("stage") == "render" and time.time() - phase["render_started_wall"] > policy.get("render_limit_s", 120))
                    or phase.get("fit_seconds", 0) > policy.get("fit_limit_s", 120) or phase.get("render_seconds", 0) > policy.get("render_limit_s", 120)):
                raise GMError("STAGE_TIMEOUT", "Fit or render stage exceeded", 5)
            if time.monotonic() - started > (120 if mode == "synthetic" else policy["wall_limit_s"]):
                raise GMError("WALL_TIMEOUT", "Compute worker exceeded deadline", 5)
            if code is not None:
                break
            time.sleep(.05)
        code = job.wait()
        if not job.wait_empty(10000):
            forced = True
            job.terminate()
            if not job.wait_empty(10000):
                raise GMError("OWNED_TREE_WAIT_TIMEOUT", "Compute descendant remained", 9)
        if teardown_gap and forced:
            raise GMError("RAM_POOL_OBSERVATION_UNKNOWN","Incomplete RSS at forced descendant teardown",5)
        receipt = {"mode": mode, "owner": current_identity(), "launcher": worker, "worker": worker,
                   "rss_terminal_gap": teardown_gap,
                   "worker_exit_code": code, "owned_processes_exited": True,
                   "forced_termination": forced, "actual_wait": True,
                   "job_commit_limit_bytes": limit, "peak_job_memory_bytes": job.peak_job_memory_bytes,
                   "peak_tree_rss_bytes": job.peak_tree_rss_bytes,
                   "peak_aggregate_rss_bytes": peak_aggregate,
                   "aggregate_sample_complete": aggregate_complete and (dynamic is None or aggregate_samples > 0),
                   "aggregate_rss_method": "current_exact_identities" if dynamic is not None else "legacy",
                   "peak_aggregate_rss_lower_bound_bytes": peak_aggregate_lower_bound,
                   "aggregate_rss_samples": aggregate_samples if dynamic is not None else None,
                   "rss_sample_complete": job.rss_sample_complete,
                   "elapsed_seconds": time.monotonic() - started}
        atomic_json(run / "cleanup.json", receipt)
        if code != 0:
            raise _compute_failure_error(run, typed_cuda_oom=dynamic is not None)
        return strict_load(run / "compute-result.json"), receipt
    except BaseException:
        if worker and job.poll() is None:
            forced = True
            job.terminate()
        if worker:
            code = job.wait()
            empty = job.wait_empty(10000)
            atomic_json(run / "cleanup.json", {"mode": mode, "owner": current_identity(),
                "launcher": worker, "worker": worker, "worker_exit_code": code,
                "owned_processes_exited": empty, "forced_termination": forced,
                "actual_wait": True, "job_commit_limit_bytes": limit,
                "peak_job_memory_bytes": job.peak_job_memory_bytes,
                "peak_tree_rss_bytes": job.peak_tree_rss_bytes,
                "peak_aggregate_rss_bytes": peak_aggregate,
                "aggregate_sample_complete": aggregate_complete and (dynamic is None or aggregate_samples > 0),
                "aggregate_rss_method": "current_exact_identities" if dynamic is not None else "legacy",
                "peak_aggregate_rss_lower_bound_bytes": peak_aggregate_lower_bound,
                "aggregate_rss_samples": aggregate_samples if dynamic is not None else None,
                "rss_sample_complete": job.rss_sample_complete,
                "elapsed_seconds": time.monotonic() - started})
        raise
    finally:
        job.close()


def _compute_spec(run: Path, recipe_dir: Path, gpu: dict, *, expected=None,
                  inputs: dict | None = None):
    if inputs is None:
        raise GMError("SESSION_INPUT_BINDINGS_REQUIRED", "Explicit source and reference bindings required", 3)
    bound = inputs
    from .light.runtime import operation, bundle_exe
    io= bundle_exe("io") if operation() is not None else IO_PY
    spec = {"source_path": str(bound["source"]["path"]),
            "reference_path": str(bound["reference"]["path"]),
            "recipe_dir": str(recipe_dir), "gpu": gpu,
            "io_runtime": {"python": str(io), "tifffile": "2026.9.15",
                           "imagecodecs": "2026.8.16", "numpy": "2.5.3", "pillow": "12.3.0"}}
    from .engine.profiles import get_profile
    profile = get_profile(bound.get("_profile_id"),bound.get('_profile_contract_sha256'))
    spec["profile_id"] = profile["id"]
    spec["profile_contract_sha256"] = profile["contract_sha256"]
    if '_fine_gpu_plan' in bound:spec['fine_gpu_plan']=bound['_fine_gpu_plan']
    if '_gpu_resource' in bound:spec['gpu_resource']=bound['_gpu_resource']
    if '_refinement_seed' in bound:spec['refinement_seed']=bound['_refinement_seed']
    if '_saved_p3' in bound:spec['saved_p3']=bound['_saved_p3']
    if expected:
        spec.update(expected)
    atomic_json(run / "compute-spec.json", spec)


def _artifact(root: Path, path: Path, kind: str):
    return {"path": str(path.relative_to(root)).replace("\\", "/"), "bytes": path.stat().st_size,
            "sha256": sha_file(path), "artifact_kind": kind}


def run_gm003(store, path: Path, spec: dict, entry: Path, job_id: str, attempt_id: str):
    raise GMError("ALGORITHM_RETIRED","The GM-003 B0 execution entry has retired",4)


def run_replay(store, path: Path, spec: dict, entry: Path, job_id: str, attempt_id: str):
    raise GMError("ALGORITHM_RETIRED","The GM-003 B0 execution entry has retired",4)
