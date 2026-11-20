"""GM-004A desktop-facing engine operations; no model or image imports."""

from __future__ import annotations

import math
import hashlib
import time
import uuid
from pathlib import Path

from .common import (GMError, atomic_json, sha_file, strict_load,
                     writable_destination, writable_output_root)
from .gm003_ipc import bounded_call, recv_json, recv_rgb8_bytes, send_json
from .gm003_supervisor import (_compute_spec, _wait_gpu, run_compute, start_session,
                               ROOT)
from .gm004a_operations import OperationStore, artifact
from .gm004a_resident import ResidentAdmission
from .image_limits import LEGACY_BUDGET, MAX_IMAGE_BYTES, admit_photos, photo_budget, operation_seconds
from .lease import validate_lease_handle
from .store import locked
from .winproc import current_identity, identity_alive, terminate_verified

GLOBAL_POOL_LOCK = ROOT / "jobs" / "gm004a-global-resource.lock"
if __import__('os').environ.get('GM063_LAUNCH_CONFIG'):
    from .adaptive_execution.calibration_launch import job_namespace
    GLOBAL_POOL_LOCK=job_namespace()/"gm004a-global-resource.lock"
RAM_POOL = 6 * 1024**3


def _pool_lock():
    from .light.runtime import application, data_root
    return data_root()/"jobs/gm004a-global-resource.lock" if application() else GLOBAL_POOL_LOCK


def _fine_ram_receipt(path, source, reference, operation, stage):
    from .fine_resources import memory_plan
    atomic_json(path / ('ram-admission-' + stage + '.json'),
                memory_plan(source, reference, operation=operation))


def _resident_pool():
    if __import__('os').environ.get('GM063_LAUNCH_CONFIG'):
        from .adaptive_execution.calibration_launch import job_namespace
        return ResidentAdmission(job_namespace()/"gm004a-resident")
    from .light.runtime import application, data_root
    return ResidentAdmission((data_root() if application() else ROOT) / "jobs" / "gm004a-resident")


def _close_resident(job_id: str, attempt_id: str, session: Path, outcome: str):
    pool = _resident_pool()
    row = pool.for_operation(job_id, attempt_id, session)
    return pool.close(row["token"], outcome=outcome)


def _export_artifacts(destination: Path, result_path: Path, session: Path, key: str):
    from .adaptive_execution.portable_contract import export_transaction
    transaction = export_transaction(session, hashlib.sha256(key.encode("utf-8")).hexdigest())
    return [artifact(p) for p in (destination, result_path, transaction / "intent.json",
                                  transaction / "prepared.json", transaction / "commit.json")]


def _binding(lease):
    from .hybrid_replay import VERSION_FIELDS
    keys = {"handle_id", "generation", "owner_instance_id", "project_revision", "recipe_sha256"}
    if VERSION_FIELDS & lease.keys():
        from .hybrid_replay.contract import versions
        versions(lease); keys |= VERSION_FIELDS
    return {key: lease[key] for key in sorted(keys)}


def _inputs(view):
    source = Path(view.get("source_path", view["source"]["canonical_path"]))
    reference = Path(view.get("reference_path", view["reference"]["local_path"]))
    for name, path in (("source", source), ("reference", reference)):
        expected = view[name]
        if path.stat().st_size != expected["bytes"] or sha_file(path) != expected["byte_sha256"]:
            raise GMError("INPUT_IDENTITY_MISMATCH", name, 3)
    return {"source": {"path": str(source), "bytes": source.stat().st_size,
                       "sha256": sha_file(source)},
            "reference": {"path": str(reference), "bytes": reference.stat().st_size,
                          "sha256": sha_file(reference)}}


def _session(path: Path, binding: dict):
    session = path / "session"
    lease = strict_load(session / "lease.json")
    validate_lease_handle(lease, binding=binding)
    owner = strict_load(session / "owner.json")
    host = strict_load(session / "host-process.json")
    if identity_alive(owner) is not True or identity_alive(host) is not True:
        raise GMError("RESULT_NOT_RESIDENT", "Session process exited", 4)
    spec = strict_load(session / "session-spec.json")
    if spec["session_id"] != lease["owner_instance_id"] or spec["project_revision"] != lease["project_revision"]:
        raise GMError("RESULT_LEASE_INVALID", "Session binding mismatch", 7)
    return session, spec, lease, owner, host


def _call(spec, request: dict, *, deadline: float, cancelled=None, pixels=False,
          max_preview_pixels=1024 * 1024):
    def transaction(conn):
        send_json(conn, request)
        reply = recv_json(conn)
        if not isinstance(reply, dict) or reply.get("ok") is not True:
            code = reply.get("error_code", "SESSION_ERROR") if isinstance(reply, dict) else "SESSION_ERROR"
            error = GMError(code, code, 4 if code == "RESULT_NOT_RESIDENT" else
                            8 if code == "CANCELLED" else 9)
            if isinstance(reply, dict) and isinstance(reply.get("resource_boundary"), dict):
                error.resource_boundary = reply["resource_boundary"]
            raise error
        if pixels:
            data, shape = recv_rgb8_bytes(conn, max_pixels=max_preview_pixels)
            if reply["preview"]["shape"] != list(shape):
                raise GMError("PREVIEW_IDENTITY_INVALID", "Preview shape mismatch", 7)
            return reply, data
        return reply
    try:
        return bounded_call(spec["pipe_address"], spec["auth_hex"], transaction,
                            deadline=deadline, cancelled=cancelled)
    except TimeoutError as exc:
        raise GMError("OPERATION_DEADLINE", str(exc), 5) from exc
    except InterruptedError as exc:
        raise GMError("CANCELLED", str(exc), 8) from exc


def _lose_session(session: Path, owner: dict, host: dict):
    lease_path = session / "lease.json"
    lease_error = None
    try:
        if lease_path.is_file():
            lease = strict_load(lease_path)
            lease["state"] = "LOST"
            atomic_json(lease_path, lease)
    except Exception as error:
        lease_error = error
    owner_before = identity_alive(owner)
    if owner_before is True:
        # Prefer an authenticated ABORT so the owner retains the host handle
        # and can record the real empty Job, including when diagnostic I/O fails.
        spec_error = None
        try:
            spec = strict_load(session/"session-spec.json")
            if spec.get("light_exact_exit") and spec.get("pipe_address"):
                _call(spec, {"command":"ABORT"}, deadline=time.monotonic()+3)
                until = time.monotonic()+3
                while identity_alive(owner) is True and time.monotonic()<until:
                    time.sleep(.02)
        except Exception as error:
            spec_error = error
        if spec_error is not None and lease_error is None:
            lease_error = spec_error
    if identity_alive(owner) is True:
        try:
            terminate_verified(owner)
        except (GMError, OSError):
            if identity_alive(owner) is not False:
                raise
    deadline = time.monotonic() + 10
    while True:
        owner_state, host_state = identity_alive(owner), identity_alive(host)
        if (owner_state is False and host_state is False) or time.monotonic() >= deadline:
            break
        time.sleep(.02)
    receipt = {"owner_was_alive": owner_before, "owner_exited": owner_state is False,
               "host_exited": host_state is False, "actual_wait": True,
               "owner_state": owner_state, "host_state": host_state,
               "lease_state": "LOST" if lease_error is None else "UNKNOWN",
               "lease_write_error": str(lease_error) if lease_error else None}
    atomic_json(session / "gm004a-abort-cleanup.json", receipt)
    if not receipt["owner_exited"] or not receipt["host_exited"]:
        raise GMError("OWNED_TREE_WAIT_TIMEOUT", "Session process exit unconfirmed", 9)
    if lease_error is not None:
        raise lease_error
    return receipt


def _settle_failed_preview(path, session, owner, host, pool, reservation, primary, *, abandon=None):
    """Keep the primary failure even if a cleanup step needs later reconciliation."""
    diagnostics = []
    released = None
    try:
        if session is not None and owner is not None and host is not None:
            _lose_session(session, owner, host)
    except Exception as error:
        diagnostics.append(dict(step="session", code=getattr(error, "code", type(error).__name__),
                                message=str(error)))
    try:
        if reservation is not None:
            released = pool.close(reservation["token"], outcome="STARTUP_FAILED" if owner is None else "LOST")
    except Exception as error:
        diagnostics.append(dict(step="reservation", code=getattr(error, "code", type(error).__name__),
                                message=str(error)))
    try:
        if abandon is not None and (reservation is None or (released and released["state"] == "FREE")):
            abandon()
    except Exception as error:
        diagnostics.append(dict(step="project_claim", code=getattr(error, "code", type(error).__name__),
                                message=str(error)))
    try:
        atomic_json(path / "preview-failure-cleanup.json", dict(
            primary_code=getattr(primary, "code", type(primary).__name__),
            cleanup="PENDING" if (diagnostics or (reservation is not None
                                  and (released is None or released["state"] != "FREE"))) else "SETTLED",
            diagnostics=diagnostics, reservation_token=reservation["token"] if reservation else None,
            reservation_state=released["state"] if released else None))
    except Exception as error:
        primary.add_note(f"Could not write preview cleanup diagnostics: {error}")
    for issue in diagnostics:
        primary.add_note(f'{issue["step"]}: {issue["message"]}')
    return released


def _failure_step(primary, name, action, default=None):
    try:
        return action()
    except Exception as diagnostic:
        primary.add_note(f"{name}: {type(diagnostic).__name__}: {diagnostic}")
        return default


def _failed_operation(ops, job, attempt, error, code):
    return ops.transition(job, attempt, "CANCELLED" if isinstance(error, GMError) and error.exit_code == 8
                          else "FAILED", "terminal", code, error_code=code,
                          exit_code=getattr(error, "exit_code", 9))


def _fit_response(ops, path, spec, *, idempotent=False):
    state = ops.status(spec["job_id"], spec["attempt_id"])
    resident = state.get("resident", False)
    return {**state, "binding": _binding(state["result_lease"]) if resident else None,
            "idempotent_hit": idempotent, "output_root": spec["semantics"]["output_root"],
            "receipt_path": str(path / "fit-identity.json"), "project_revision": "r000002"}


def _require_active_result(path):
    """A surviving old binding permits release, never new pixel operations."""
    from .resource_execution.binding import require_available
    require_available(path)
    from .engine import profiles
    semantic=strict_load(Path(path)/"spec.json")["semantics"]
    identity=semantic.get("profile_id")
    if identity is None:raise profiles.retired_error()
    return profiles.get_profile(identity)


def fit_new_pair(project_path: Path, store_root: Path, output_root: Path,
                 *, request_key: str | None = None, profile: str | None = None,
                 client_identity: dict | None = None, execution_backend=None, allow_experimental=False, resource_request=None):
    """Persist one stable fit intent; a completed fit never starts computation again."""
    from .resource_execution.api import invoke
    from .resource_execution.contract import check_key
    if resource_request is not None:
        return invoke(fit_new_pair,project_path,store_root,output_root,resource_request=resource_request,
            kwargs=dict(request_key=request_key,profile=profile,client_identity=client_identity,
                execution_backend=execution_backend,allow_experimental=allow_experimental))
    check_key(OperationStore(store_root),request_key)
    if execution_backend is not None or allow_experimental:
        from .hybrid_fit.api import fit
        return fit(project_path,store_root,output_root,request_key=request_key,client_identity=client_identity,
            backend=execution_backend,experimental=allow_experimental,profile=profile)
    from .project import show_project, _read_project_manifest
    from .common import writable_path
    from . import fit_publication as fit_tx
    from .engine import profiles
    import os

    project_path = writable_path(Path(project_path))
    if not project_path.is_dir() or project_path.suffix.lower() != ".grademirror":
        raise GMError("PROJECT_FORMAT_INVALID", "A .grademirror project directory is required", 3)
    output_root = writable_output_root(output_root)
    ops = OperationStore(store_root)
    existing = ops.lookup_request(request_key)
    if existing is not None and existing[1]["kind"] == "fit":
        from .retired_algorithms import is_retired
        path,saved_spec=existing;saved=saved_spec["semantics"]
        if is_retired(saved.get("profile_id")) or saved.get("profile_id") is None:
            if profile is not None and profile!=saved.get("profile_id"):
                raise GMError("REQUEST_KEY_CONFLICT","Request key belongs to a retired algorithm intent",2)
            manifest,forward=_read_project_manifest(project_path)
            if (forward or os.path.normcase(saved["project_path"])!=os.path.normcase(str(project_path)) or
                    os.path.normcase(saved["output_root"])!=os.path.normcase(str(output_root)) or
                    saved["source_sha256"]!=manifest["source"]["byte_sha256"] or
                    saved["reference_sha256"]!=manifest["reference"]["byte_sha256"]):
                raise GMError("REQUEST_KEY_CONFLICT","Retired fit request semantics differ",2)
            return {**_fit_response(ops,path,saved_spec,idempotent=True),"algorithm_retired":True,"resident":False,"binding":None}
    if profile is not None:
        profiles.get_profile(profile)
        if existing is not None and existing[1]["semantics"].get("profile_id") != profile:
            raise GMError("REQUEST_KEY_CONFLICT", "Request key belongs to another algorithm profile", 2)
    if existing is not None and existing[1]["kind"] != "fit":
        raise GMError("REQUEST_KEY_CONFLICT", "Request key belongs to a different operation", 2)
    if existing is not None and not fit_tx.is_current(existing[1]):
        path, spec = existing
        saved = spec["semantics"]
        manifest, forward = _read_project_manifest(project_path)
        required = {"project_path", "revision", "source_sha256", "reference_sha256", "output_root"}
        if forward or set(saved) != required:
            raise GMError("HISTORICAL_FIT_REVIEW_REQUIRED", "Historical fit intent is not fully recognizable", 7)
        if (os.path.normcase(saved["project_path"]) != os.path.normcase(str(project_path))
                or os.path.normcase(saved["output_root"]) != os.path.normcase(str(output_root))
                or saved["revision"] != "r000002"
                or saved["source_sha256"] != manifest["source"]["byte_sha256"]
                or saved["reference_sha256"] != manifest["reference"]["byte_sha256"]):
            raise GMError("REQUEST_KEY_CONFLICT", "Historical fit semantics differ", 2)
        try:
            return _fit_response(ops, path, spec, idempotent=True)
        except GMError as exc:
            raise GMError("HISTORICAL_FIT_REVIEW_REQUIRED", "Historical fit commit cannot be verified: " + str(exc), 7) from exc
    semantics = fit_tx.intent(project_path, output_root)
    if profile is not None and semantics["profile_id"] != profile:
        raise GMError("PROJECT_PROFILE_MISMATCH", "Fit profile differs from the frozen project", 2)
    path, spec, accepted = ops.begin("fit", semantics, request_key)
    if accepted["idempotent_hit"]:
        return _fit_response(ops, path, spec, idempotent=True)
    job, attempt = spec["job_id"], spec["attempt_id"]
    pool = _resident_pool()
    reservation = session = owner = host = None
    started = time.monotonic()
    try:
        ops.transition(job, attempt, "RUNNING", "preflight", "FIT_PREFLIGHT", owner=current_identity())
        view = show_project(project_path, available_runtimes=profiles.runtimes())
        if view.get("status") != "METADATA_ONLY" or view.get("read_only"):
            raise GMError("PROJECT_NOT_NEW_PAIR", "A writable metadata-only pair is required", 4)
        fit_tx.claim_project(ops, path, spec)
        inputs = _inputs(view)
        inputs["_profile_id"] = semantics["profile_id"]
        inputs['_profile_contract_sha256']=semantics['profile_contract_sha256']
        if semantics.get('refinement_seed')is not None:
            inputs['_refinement_seed']=dict(binding=semantics['refinement_seed'],directory=str(project_path/'refinement'))
        if semantics.get('saved_p3') is not None:
            from .light.resources import active_plan
            recovery_plan=active_plan("gpu")
            if recovery_plan is not None and recovery_plan.get("saved_gpu_post")!=semantics['saved_p3']:
                raise GMError("GPU_SAVED_P3_PLAN_CHANGED","Saved fine checkpoint differs from the frozen plan",7)
            inputs['_saved_p3']=semantics['saved_p3']
        from .image_probe import inspect_pair
        source_info, reference_info = inspect_pair(Path(inputs["source"]["path"]), Path(inputs["reference"]["path"]))
        budget = photo_budget(source_info, reference_info)
        seconds = operation_seconds(source_info["width"] * source_info["height"])
        fine = semantics["profile_id"] == profiles.FINE
        from .light.resources import gpu_ram_recheck
        quality = "fine" if fine else "fast"
        portable = gpu_ram_recheck(path,source_info,reference_info,quality,"fit","initial")
        if fine:
            from .fine_resources import budget as fine_budget,operation_seconds as fine_seconds
            if not portable: budget=fine_budget(source_info,reference_info,admit=True,operation='fit')
            if not portable:
                _fine_ram_receipt(path,source_info,reference_info,'fit','initial')
            seconds=fine_seconds(source_info["width"]*source_info["height"],reference_info['width']*reference_info['height'])
        from .light.resources import pool_budget
        budget = pool_budget(budget, "gpu")
        inputs.update(_resource_budget=budget, _operation_seconds=seconds)
        from .gpu_resources import resource_geometry
        inputs['_gpu_resource']=resource_geometry(source_info,operation='fit',fine=fine)
        if fine:inputs['_fine_gpu_plan']=inputs['_gpu_resource']['plan']
        from .resource_execution.admission import reservation_lock
        with reservation_lock(path,_pool_lock()):
            from .resource_execution.admission import prepare
            prepare(path,inputs,source_info,reference_info,pool=pool)
            if (path / "cancel.signal").exists():
                raise GMError("CANCELLED", "Cancelled before fit", 8)
            if not gpu_ram_recheck(path,source_info,reference_info,quality,"fit","locked"):
                if fine:
                    fine_budget(source_info,reference_info,admit=True,operation='fit')
                    _fine_ram_receipt(path,source_info,reference_info,'fit','locked')
                else:admit_photos(source_info, reference_info)
            reservation = pool.claim(job, attempt, store_root, path / "session",
                budget_bytes=budget, cancelled=lambda: (path / "cancel.signal").exists())
            gpu = _wait_gpu(path, 120, fine=fine,resource=inputs['_gpu_resource'],operation='fit')
            output_root.mkdir(parents=True, exist_ok=False)
            session = path / "session"
            session_spec, owner, host = start_session(path, session, "development_pair",
                output_root, "r000002", inputs=inputs, client_identity=client_identity)
            pool.bind(reservation["token"], session_spec["session_id"], owner, host)
            _compute_spec(path, output_root / "recipe", gpu, inputs=inputs)
            ops.transition(job, attempt, "RUNNING", "fit_render", "FIT_COMPUTE",
                           owner=current_identity(), launcher=owner, host=host)
            policy = {"ram_limit_bytes": budget["compute"], "aggregate_ram_limit_bytes": budget["pool"],
                      "fit_limit_s": seconds, "render_limit_s": seconds, "wall_limit_s": max(1,seconds-(time.monotonic()-started)) if fine else seconds}
            compute, cleanup = run_compute(path, "development_pair", policy,
                cancel_path=path / "cancel.signal", other_identities=(owner, host))
            from .refinement import expected_fit_count
            fitted=expected_fit_count(semantics)
            if compute["fit_count"] != fitted or compute["render_count"] != 1:
                raise GMError("FIT_COUNTS_INVALID", "New pair fit/render counts differ", 7)
            lease = compute["lease"]
            pool.bind_lease(reservation["token"], lease)
            if portable:
                from .light.consumer_retry import initial_blend
                from .light.eta import emit
                reply = initial_blend(_call,path,session_spec,{"command": "BLEND", **_binding(lease), "alpha": .5},
                    deadline=started + seconds if fine else time.monotonic() + seconds,
                    cancelled=lambda: (path / "cancel.signal").exists(),
                    report=lambda stage:emit(path/"light-publish-progress.json",stage))
            else:
                reply = _call(session_spec, {"command": "BLEND", **_binding(lease), "alpha": .5},
                              deadline=started + seconds if fine else time.monotonic() + seconds,
                              cancelled=lambda: (path / "cancel.signal").exists())
            if time.monotonic() - started > (seconds if fine else max(750, seconds + 420)):
                raise GMError("FIT_DEADLINE", "New pair fit exceeded deadline before publication", 5)
            atomic_json(path / "fit-identity.json", {"fit_count": fitted, "render_count": 1,
                "float32_sha256": compute["float32_sha256"], "half_q16_sha256": reply["q16_sha256"],
                "recipe_sha256": compute["recipe_sha256"], "source_sha256": inputs["source"]["sha256"],
                "reference_sha256": inputs["reference"]["sha256"], "worker_cleanup": cleanup,
                "gpu": compute["gpu"], "elapsed_seconds": time.monotonic() - started})
            publish_started=time.perf_counter()
            marker = fit_tx.prepare(path, spec, output_root / "recipe", compute, reply["q16_sha256"])
            ops.transition(job, attempt, "RUNNING", "committing", "FIT_PREPARED")
            fit_tx.publish(ops, path, spec, marker)
            fit_tx.finish(ops, path, spec)
            from .light.eta import owner_timing
            owner_timing(path,publish_started)
            return _fit_response(ops, path, spec)
    except BaseException as exc:
        import traceback
        error_code = getattr(exc, "code", "FIT_ERROR")
        traceback_text = traceback.format_exc()
        _failure_step(exc, "fit diagnostics", lambda: atomic_json(path / "fit-failure.json", {
            "error_code": error_code, "type": type(exc).__name__, "message": str(exc),
            "traceback": traceback_text,
        }))
        status = _failure_step(exc, "fit status", lambda: ops.status(job, attempt), {})
        if status.get("state") == "SUCCEEDED":
            return _fit_response(ops, path, spec)
        _failure_step(exc, "fit journal", lambda: _failed_operation(ops, job, attempt, exc, error_code))
        released = _settle_failed_preview(path, session, owner, host, pool, reservation, exc,
                                          abandon=lambda: fit_tx.abandon_claim(path, spec))
        from .light.resource_retry import InnerResource,gpu_stopped
        if isinstance(exc,InnerResource) and (path/"fit-claim-closed.json").is_file():
            _failure_step(exc, "fit resource receipt", lambda: gpu_stopped(
                exc,path,spec,session,owner,host,released,ops.progress_snapshot(job,attempt)))
        raise


def _operation_response(ops,path,spec,*,idempotent=False):
    if spec['kind']=='fit':return _fit_response(ops,path,spec,idempotent=idempotent)
    state=ops.status(spec['job_id'],spec['attempt_id'])
    receipt=path/'replay-identity.json'
    return dict(state,idempotent_hit=idempotent,output_root=spec['semantics']['output_root'],
        project_revision=spec['semantics']['revision'],receipt_path=str(receipt) if receipt.is_file() else None)


def _replay_intent(project_path, output_root):
    from .portable_project import open_portable_project
    from .engine import profiles

    # Use this one checked absolute identity for intent, mkdir, host spec and
    # the public return. In particular, do not pass the raw CLI spelling on.
    output_root = writable_output_root(output_root)
    from .light import recovery
    pending = recovery.check(project_path)
    if pending and pending[0]["state"] == "PENDING":
        from .project import show_project
        view = show_project(project_path, available_runtimes=profiles.runtimes())
        if view.get("metadata_only"):
            marker,recipe=pending
            view=dict(view,revision_id="r000002",recipe_dir=str(Path(project_path)/"recovery-input"),
                      recipe_sha256=marker["recipe_sha256"],recovery=marker)
        else:
            view=open_portable_project(project_path,available_runtimes=profiles.runtimes())
    else:
        view = open_portable_project(project_path,available_runtimes=profiles.runtimes())
    if view.get("algorithm_retired"):raise profiles.retired_error()
    if view.get("read_only"):
        raise GMError("PROJECT_READ_ONLY", "Project runtime unavailable", 4)
    inputs = _inputs(view)
    recipe = strict_load(Path(view["recipe_dir"]) / "recipe.json")
    inputs["_profile_id"] = profiles.recipe_profile(recipe)["id"]
    inputs['_profile_contract_sha256']=recipe['profile_contract_sha256']
    inputs['_recipe_dir']=view['recipe_dir']
    if recipe["recipe_sha256"] != view["recipe_sha256"]:
        raise GMError("REPLAY_RECIPE_IDENTITY_CHANGED", "Recipe identity mismatch", 7)
    artifact_index = (strict_load(Path(view["artifacts_index_path"]))
                      if "artifacts_index_path" in view else {})
    checkpoint=view.get("recovery")
    expected_float = checkpoint["float32_sha256"] if checkpoint else artifact_index.get("result_float32_sha256")
    expected_half = artifact_index.get("half_q16_sha256")
    expected = (expected_float,) if checkpoint else (expected_float,expected_half)
    if (not all(isinstance(value, str) and len(value) == 64 and
                all(c in "0123456789abcdef" for c in value)
                for value in expected)):
        raise GMError("REPLAY_EXPECTED_HASH_INVALID", "Saved result identity is invalid", 7)
    if "source_identity" in recipe:
        source_shape = recipe["source_identity"]["shape"]
        tile_side = recipe["settings"]["tile_side"]
        tiles = ((source_shape[0] + tile_side - 1) // tile_side) * (
                 (source_shape[1] + tile_side - 1) // tile_side)
    else:
        raise GMError("REPLAY_SOURCE_IDENTITY_MISSING", "R0 requires saved source geometry", 7)
    semantics = {"project_path": str(Path(project_path).resolve()),
                 "revision": view["revision_id"], "recipe_sha256": view["recipe_sha256"],
                 "source_sha256": inputs["source"]["sha256"],
                 "reference_sha256": inputs["reference"]["sha256"],
                 "output_root": str(output_root), "tiles": tiles,
                 "profile_id":inputs["_profile_id"],
                 "profile_contract_sha256":recipe["profile_contract_sha256"],
                 "core_sha256":recipe["core_sha256"]}
    if checkpoint:
        semantics.update(saved_recovery=checkpoint)
    if view["profile_id"] == profiles.FINE:
        semantics.update(fine_hashes=recipe["postprocess"]["hashes"],p3_values_sha256=recipe["postprocess"]["coefficient_sha256"])
    return view,inputs,recipe,semantics,expected_float,expected_half


def replay(project_path: Path, store_root: Path, output_root: Path,
           *, request_key: str | None = None, client_identity: dict | None = None,
           execution_backend: str | None = None, allow_experimental: bool = False,
           execution_manifest: Path | None = None, source_path: Path | None = None,
           reference_path: Path | None = None, resource_request=None):
    from .resource_execution.api import invoke
    from .resource_execution.contract import check_key
    if resource_request is not None:
        return invoke(replay,project_path,store_root,output_root,resource_request=resource_request,
            kwargs=dict(request_key=request_key,client_identity=client_identity,execution_backend=execution_backend,
                allow_experimental=allow_experimental,execution_manifest=execution_manifest,source_path=source_path,reference_path=reference_path))
    check_key(OperationStore(store_root),request_key)
    if execution_backend is not None or execution_manifest is not None or allow_experimental:
        from .hybrid_replay.api import replay as hybrid_replay
        return hybrid_replay(project_path,store_root,output_root,request_key=request_key,
            client_identity=client_identity,backend=execution_backend,experimental=allow_experimental,
            manifest=execution_manifest,source=source_path,reference=reference_path)
    if project_path is None or source_path is not None or reference_path is not None:
        raise GMError('REPLAY_ARGS_INVALID','Explicit project or experimental manifest required',2)
    view,inputs,recipe,semantics,expected_float,expected_half = _replay_intent(project_path,output_root)
    from .engine import profiles
    from .image_probe import inspect_pair
    source_info, reference_info = inspect_pair(Path(inputs["source"]["path"]), Path(inputs["reference"]["path"]))
    budget = photo_budget(source_info, reference_info)
    seconds = operation_seconds(source_info["width"] * source_info["height"])
    fine = view["profile_id"] == profiles.FINE
    if fine:
        from .fine_resources import budget as fine_budget,operation_seconds as fine_seconds
        from .light.resources import active_plan
        if active_plan("gpu") is None:
            budget=fine_budget(source_info,reference_info,operation='replay')
        seconds=fine_seconds(source_info["width"]*source_info["height"],reference_info['width']*reference_info['height'])
    from .light.resources import pool_budget
    budget = pool_budget(budget, "gpu")
    inputs.update(_resource_budget=budget, _operation_seconds=seconds)
    from .gpu_resources import resource_geometry
    inputs['_gpu_resource']=resource_geometry(source_info,operation='replay',fine=fine)
    if fine:inputs['_fine_gpu_plan']=inputs['_gpu_resource']['plan']
    tiles=semantics['tiles']
    ops = OperationStore(store_root)
    path, spec, accepted = ops.begin("replay", semantics, request_key)
    if accepted["idempotent_hit"]:
        return _operation_response(ops,path,spec,idempotent=True)
    job, attempt = spec["job_id"], spec["attempt_id"]
    session = None
    owner = host = None
    pool = _resident_pool()
    reservation = None
    started = time.monotonic()
    try:
        ops.transition(job, attempt, "RUNNING", "preflight", "REPLAY_PREFLIGHT",
                       owner=current_identity())
        from .light.resources import gpu_ram_recheck
        quality = "fine" if fine else "fast"
        portable = gpu_ram_recheck(path,source_info,reference_info,quality,"replay","initial")
        if fine and not portable:
            fine_budget(source_info,reference_info,admit=True,operation='replay')
            _fine_ram_receipt(path,source_info,reference_info,'replay','initial')
        from .resource_execution.admission import reservation_lock
        with reservation_lock(path,_pool_lock()):
            from .resource_execution.admission import prepare
            prepare(path,inputs,source_info,reference_info,pool=pool)
            if (path / "cancel.signal").exists():
                raise GMError("CANCELLED", "Cancelled before replay", 8)
            if "_resource_budget" in inputs and not gpu_ram_recheck(
                    path,source_info,reference_info,quality,"replay","locked"):
                if fine:
                    fine_budget(source_info,reference_info,admit=True,operation='replay')
                    _fine_ram_receipt(path,source_info,reference_info,'replay','locked')
                else:admit_photos(source_info, reference_info)
            reservation = pool.claim(job, attempt, store_root, path / "session",
                budget_bytes=budget, cancelled=lambda: (path / "cancel.signal").exists())
            gpu = _wait_gpu(path, 120, fine=fine,resource=inputs['_gpu_resource'],operation='replay')
            device = recipe["device"]
            if not portable and (gpu["name"], gpu["uuid"], gpu["driver_version"]) != (
                    device["name"], device["uuid"], device["driver_version"]):
                raise GMError("REPLAY_DEVICE_CHANGED", "GPU identity changed", 7)
            output_root.mkdir(parents=True, exist_ok=False)
            session = path / "session"
            session_spec, owner, host = start_session(path, session,
                "development_pair",
                output_root, view["revision_id"], inputs=inputs, client_identity=client_identity)
            pool.bind(reservation["token"], session_spec["session_id"], owner, host)
            _compute_spec(path, Path(view["recipe_dir"]), gpu,
                          expected={"expected_recipe_sha256": view["recipe_sha256"],
                                    "expected_float32_sha256": expected_float}, inputs=inputs)
            ops.transition(job, attempt, "RUNNING", "render", "REPLAY_RENDER",
                           owner=current_identity(), launcher=owner, host=host)
            policy = {"ram_limit_bytes": budget["compute"],
                      "aggregate_ram_limit_bytes": budget["pool"],
                      "render_limit_s": seconds, "wall_limit_s": max(1,seconds-(time.monotonic()-started)) if fine else seconds}
            compute, cleanup = run_compute(path, "replay", policy,
                cancel_path=path / "cancel.signal", other_identities=(owner, host))
            if (compute["fit_count"], compute["render_count"], compute["float32_sha256"]) != (0, 1, expected_float):
                raise GMError("REPLAY_IDENTITY_INVALID", "Replay counts or Y differ", 7)
            lease = compute["lease"]
            pool.bind_lease(reservation["token"], lease)
            if portable:
                from .light.consumer_retry import initial_blend
                from .light.eta import emit
                reply = initial_blend(_call,path,session_spec,{"command": "BLEND", **_binding(lease), "alpha": .5},
                    deadline=started + seconds if fine else time.monotonic() + seconds,
                    cancelled=lambda: (path / "cancel.signal").exists(),
                    report=lambda stage:emit(path/"light-publish-progress.json",stage))
            else:
                reply = _call(session_spec, {"command": "BLEND", **_binding(lease), "alpha": .5},
                              deadline=started + seconds if fine else time.monotonic() + seconds,
                              cancelled=lambda: (path / "cancel.signal").exists())
            if expected_half is not None and reply["q16_sha256"] != expected_half:
                raise GMError("REPLAY_HALF_MISMATCH", "Half blend hash differs", 7)
            if view.get("recovery"):
                expected_half=reply["q16_sha256"]
            if time.monotonic() - started > (seconds if fine else seconds + 180):
                raise GMError("REPLAY_DEADLINE", "Replay exceeded image-size deadline", 5)
            publish_started=time.perf_counter()
            atomic_json(path / "replay-identity.json", {"fit_count": 0, "render_count": 1,
                "tiles": tiles, "float32_sha256": expected_float,
                "half_q16_sha256": expected_half,
                "recipe_sha256": view["recipe_sha256"], "source_sha256": inputs["source"]["sha256"],
                "reference_sha256": inputs["reference"]["sha256"],
                "gpu": compute["gpu"], "worker_cleanup": cleanup,
                "elapsed_seconds": time.monotonic() - started})
            committed = ops.commit(job, attempt,
                [artifact(path / "compute-result.json"), artifact(path / "cleanup.json"),
                 artifact(path / "replay-identity.json")], lease)
            if view.get("recovery"):
                from .light import recovery
                recovery.finish_publication(project_path,path,request_key)
            from .light.eta import owner_timing
            owner_timing(path,publish_started)
            return {**committed, "binding": _binding(lease), "output_root": str(output_root),
                    "receipt_path": str(path / "replay-identity.json")}
    except BaseException as exc:
        if getattr(exc,"code",None)=="MIXED_RESULT_PENDING_PUBLICATION":
            raise
        code = getattr(exc, "code", "REPLAY_ERROR")
        _failure_step(exc, "replay journal", lambda: _failed_operation(ops, job, attempt, exc, code))
        released = _settle_failed_preview(path, session, owner, host, pool, reservation, exc)
        from .light.resource_retry import InnerResource,gpu_stopped
        if isinstance(exc,InnerResource):
            _failure_step(exc, "replay resource receipt", lambda: gpu_stopped(
                exc,path,spec,session,owner,host,released,ops.progress_snapshot(job,attempt)))
        raise


def get_preview(store_root: Path, job_id: str, attempt_id: str, binding: dict,
                *, view="result", alpha=1.0, max_side=1024, roi=None,
                request_id: str | None = None):
    from .preview_limits import MAX_FIT_SIDE
    if type(max_side) is not int or not 1 <= max_side <= MAX_FIT_SIDE:
        raise GMError("PREVIEW_MAX_SIDE_INVALID", "Preview side exceeds display limit", 2)
    ops = OperationStore(store_root)
    if ops.status(job_id, attempt_id)["state"] != "SUCCEEDED":
        raise GMError("RESULT_UNAVAILABLE", "Replay operation not committed", 4)
    path, spec = ops.path(job_id, attempt_id)
    if spec["kind"] not in {"fit", "replay"}:
        raise GMError("RESULT_UNAVAILABLE", "Not a result operation", 4)
    return _read_preview(path, binding, job_id, attempt_id, view=view, alpha=alpha,
        max_side=max_side, roi=roi, request_id=request_id or str(uuid.uuid4()),
        deadline=time.monotonic() + (120 if binding.get('backend_id') is not None else 30))


def _read_preview(path, binding, job_id, attempt_id, *, view, alpha, max_side,
                  roi, request_id, deadline):
    _require_active_result(path)
    session, session_spec, lease, owner, host = _session(path, binding)
    with locked(session / "preview-admission.lock"):
        atomic_json(session / "preview-latest.json", {"request_id": request_id,
            "admitted_at": time.time()})
    request = {"command": "PREVIEW_PIXEL", **binding, "view": view,
               "alpha": alpha, "max_side": max_side,
               "roi": list(roi) if roi is not None else None, "request_id": request_id}
    try:
        reply, pixels = _call(session_spec, request, deadline=deadline,
                              pixels=True, max_preview_pixels=max_side ** 2 if roi is None else 512 ** 2)
    except GMError as exc:
        if exc.code in {"OPERATION_DEADLINE", "CANCELLED"}:
            _lose_session(session, owner, host)
            _close_resident(job_id, attempt_id, session, "LOST")
        raise
    if strict_load(session / "preview-latest.json")["request_id"] != request_id:
        raise GMError("PREVIEW_STALE", "A newer preview request superseded this one", 4)
    if reply["preview"]["request_id"] != request_id or reply["preview"]["binding"] != binding:
        raise GMError("PREVIEW_IDENTITY_INVALID", "Preview binding or request differs", 7)
    return {"pixels": pixels, "preview": reply["preview"],
            "fit_count": reply["fit_count"], "render_count": reply["render_count"],
            "result_lease": lease}


def get_previews(store_root, job_id, attempt_id, binding, requests, *, superseded=None):
    """Bounded native batch; full validation at both ends, each wire call unchanged.

    No partial batch is returned. Supersession is checked between calls, never
    passed to IPC cancellation (which would destroy the resident session).
    """
    from .preview_limits import MAX_PREVIEW_BATCH_TILES, PreviewSuperseded
    if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_PREVIEW_BATCH_TILES:
        raise GMError("PREVIEW_BATCH_INVALID", "Invalid preview batch size", 2)
    from .light.runtime import application
    views={"source","result","reference"} if application() else {"source","result"}
    for request in requests:
        if not isinstance(request, dict) or set(request) != {"view", "alpha", "roi", "request_id"}:
            raise GMError("PREVIEW_BATCH_INVALID", "Invalid preview request", 2)
        roi, alpha, rid = request["roi"], request["alpha"], request["request_id"]
        if (request["view"] not in views
                or type(alpha) not in (int, float) or not math.isfinite(alpha) or not 0 <= alpha <= 1
                or not isinstance(rid, str) or not 1 <= len(rid) <= 128
                or not isinstance(roi, (tuple, list)) or len(roi) != 4
                or any(type(n) is not int for n in roi)
                or not (0 <= roi[0] < roi[2] <= roi[0]+512 and 0 <= roi[1] < roi[3] <= roi[1]+512)):
            raise GMError("PREVIEW_BATCH_INVALID", "Invalid native tile", 2)
    if len({r["request_id"] for r in requests}) != len(requests):
        raise GMError("PREVIEW_BATCH_INVALID", "Duplicate tile request identity", 2)
    requests = [{**r,"roi":tuple(r["roi"])} for r in requests]
    binding = dict(binding)
    deadline = time.monotonic() + (120 if binding.get('backend_id') is not None else 30)
    ops = OperationStore(store_root)
    if ops.status(job_id, attempt_id)["state"] != "SUCCEEDED":
        raise GMError("RESULT_UNAVAILABLE", "Result operation not committed", 4)
    path, spec = ops.path(job_id, attempt_id)
    if spec["kind"] not in {"fit", "replay"}:
        raise GMError("RESULT_UNAVAILABLE", "Not a result operation", 4)
    rows = []
    for request in requests:
        if superseded is not None and superseded():
            raise PreviewSuperseded()
        reply = _read_preview(path, binding, job_id, attempt_id, max_side=512,
                              deadline=deadline, **request)
        meta, raw = reply["preview"], reply["pixels"]
        roi = request["roi"]
        shape = [roi[3]-roi[1], roi[2]-roi[0], 3]
        if (meta.get("roi") != list(roi) or meta.get("native_pixel_roi") is not True
                or meta.get("shape") != shape or meta.get("alpha") != request["alpha"]
                or len(raw) != math.prod(shape)
                or hashlib.sha256(raw).hexdigest() != meta.get("pixel_sha256")):
            raise GMError("PREVIEW_IDENTITY_INVALID", "Batch tile differs", 7)
        rows.append(reply)
    # Recheck durable publication AND current lease after the last wire reply.
    if ops.status(job_id, attempt_id)["state"] != "SUCCEEDED" or ops.path(job_id, attempt_id)[1] != spec:
        raise GMError("PREVIEW_IDENTITY_INVALID", "Batch operation changed", 7)
    _session(path, binding)
    if superseded is not None and superseded():
        raise PreviewSuperseded()
    return rows


def abort_operation(store_root: Path, request_key: str, *, job_id=None, attempt_id=None):
    """Bounded desktop close fallback. Stop only this command's exact session owner."""
    ops = OperationStore(store_root)
    found = ops.lookup_request(request_key)
    if found is None:
        return {"cancel_disposition": "NOT_ACCEPTED_YET"}
    path, spec = found
    if (job_id is not None or attempt_id is not None) and (job_id, attempt_id) != (spec["job_id"], spec["attempt_id"]):
        raise GMError("ADDRESS_MISMATCH", "Close address differs from the active request", 2)
    if spec['kind'] in ('fit','replay'):
        from .hybrid_fit import is_fit_type
        if is_fit_type(spec):
            from .hybrid_fit.api import abort_pending
            return abort_pending(ops,spec)
    cancelled = ops.cancel(spec["job_id"], spec["attempt_id"])
    result_job, result_attempt = spec["job_id"], spec["attempt_id"]
    if spec["kind"] == "export":
        result_job, result_attempt = (spec["semantics"]["replay_job_id"], spec["semantics"]["replay_attempt_id"])
        result_path, _ = ops.path(result_job, result_attempt)
    else:
        result_path = path
    session = result_path / "session"
    if not all((session / name).is_file() for name in ("owner.json", "host-process.json")):
        return {**cancelled, "cleanup_state": "STARTUP_PENDING"}
    reservation = _resident_pool().for_operation(result_job, result_attempt, session)
    owner, host = strict_load(session / "owner.json"), strict_load(session / "host-process.json")
    if reservation.get("owner") not in (None, owner) or reservation.get("host") not in (None, host):
        raise GMError("RESIDENT_BINDING_INVALID", "Close session identity differs", 7)
    cleanup = _lose_session(session, owner, host)
    if spec["kind"] == "export":
        _cleanup_owned_export_temp(path, spec, session)
    released = _resident_pool().close(reservation["token"], outcome="LOST")
    atomic_json(path / "close-cleanup.json", {"request_key": request_key, "job_id": spec["job_id"],
        "attempt_id": spec["attempt_id"], "cleanup": cleanup, "reservation": released})
    return {**cancelled, "cleanup_state": "COMPLETE", "resident_state": "LOST", "cleanup": cleanup}


def _cleanup_owned_export_temp(path, spec, session):
    """After exact host exit only, remove its private unpublished staging file."""
    with locked(path / "export-cleanup.lock"):
        return _cleanup_owned_export_temp_locked(path, spec, session)


def _cleanup_owned_export_temp_locked(path, spec, session):
    import os
    import re
    key = spec["request_key"]
    from .adaptive_execution.portable_contract import export_transaction
    transaction = export_transaction(session, hashlib.sha256(key.encode("utf-8")).hexdigest())
    intent_path = transaction / "intent.json"
    if not intent_path.is_file() or (transaction / "commit.json").exists():
        return
    saved = strict_load(intent_path)
    request = saved.get("request", {})
    destination = Path(spec["semantics"]["destination"])
    temporary = Path(saved["temporary_path"])
    session_spec = strict_load(session / "session-spec.json")
    if (request.get("operation_job_id") != spec["job_id"] or request.get("operation_attempt_id") != spec["attempt_id"]
            or saved.get("request_key") != key or saved.get("destination") != str(destination)
            or destination.exists() or temporary.parent != destination.parent
            or re.fullmatch(r"\.gm004a-" + hashlib.sha256(key.encode()).hexdigest()[:12] + r"-[0-9a-f]{32}\.partial", temporary.name) is None):
        raise GMError("EXPORT_CLEANUP_CONFLICT", "Unpublished staging binding differs", 7)
    temporary = writable_destination(temporary, Path(spec["semantics"].get("publication_root", session_spec["output_root"])),
        forbidden=(session_spec["source_path"], session_spec["reference_path"]), allow_existing=True)
    if not temporary.exists():
        return
    before = temporary.stat()
    if before.st_nlink != 1 or before.st_size > MAX_IMAGE_BYTES:
        raise GMError("EXPORT_CLEANUP_CONFLICT", "Unpublished file is shared or over budget", 7)
    digest = sha_file(temporary)
    after = temporary.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise GMError("EXPORT_CLEANUP_CONFLICT", "Unpublished file changed during cleanup", 7)
    temporary.unlink()
    atomic_json(path / "export-unpublished-cleanup.json", {"temporary_path": str(temporary),
        "sha256": digest, "bytes": before.st_size, "removed": True, "published": False,
        "job_id": spec["job_id"], "attempt_id": spec["attempt_id"], "request_key": key,
        "reason": "EXACT_OWNED_HOST_EXIT"})


def keep_alive(store_root: Path, job_id: str, attempt_id: str, binding: dict):
    ops = OperationStore(store_root)
    path, spec = ops.path(job_id, attempt_id)
    from .resource_execution.binding import require_available
    require_available(path,spec)
    if spec["kind"] not in {"fit", "replay"} or ops.status(job_id, attempt_id)["state"] != "SUCCEEDED":
        raise GMError("RESULT_UNAVAILABLE", "Committed result required", 4)
    session, session_spec, lease, owner, host = _session(path, binding)
    reply = _call(session_spec, {"command": "STATUS", **binding},
                  deadline=time.monotonic() + 10)
    updated = reply.get("lease")
    validate_lease_handle(updated, binding=binding)
    pool = _resident_pool()
    reservation = pool.for_operation(job_id, attempt_id, session)
    pool.bind_lease(reservation["token"], updated)
    return {"result_lease": updated}


def save_preview(store_root: Path, job_id: str, attempt_id: str, binding: dict,
                 destination: Path, *, view="result", alpha=1.0,
                 max_side=1024, roi=None, request_id: str | None = None):
    if binding.get('backend_id') == 'r0-cpu-portable-v1':
        from .cpu_replay.api import preview
        return preview(store_root, job_id, attempt_id, binding, destination, view=view,
                       alpha=alpha, max_side=max_side, roi=roi, request_id=request_id)
    ops = OperationStore(store_root)
    if ops.status(job_id, attempt_id)["state"] != "SUCCEEDED":
        raise GMError("RESULT_UNAVAILABLE", "Replay operation not committed", 4)
    path, spec = ops.path(job_id, attempt_id)
    _require_active_result(path)
    if spec["kind"] not in {"fit", "replay"}:
        raise GMError("RESULT_UNAVAILABLE", "Not a result operation", 4)
    session, session_spec, lease, owner, host = _session(path, binding)
    destination = writable_destination(destination, Path(session_spec["output_root"]),
        forbidden=(session_spec["source_path"], session_spec["reference_path"]))
    request_id = request_id or str(uuid.uuid4())
    try:
        reply = _call(session_spec, {"command": "PREVIEW_JPG", **binding,
            "view": view, "alpha": alpha, "max_side": max_side,
            "roi": list(roi) if roi is not None else None,
            "request_id": request_id, "destination": str(destination)},
            deadline=time.monotonic() + (120 if binding.get('backend_id') is not None else 30))
    except GMError as exc:
        if exc.code in {"OPERATION_DEADLINE", "CANCELLED"}:
            _lose_session(session, owner, host)
            _close_resident(job_id, attempt_id, session, "LOST")
        raise
    if reply["preview"]["request_id"] != request_id or reply["preview"]["binding"] != binding:
        raise GMError("PREVIEW_IDENTITY_INVALID", "Preview binding or request differs", 7)
    return {"preview": reply["preview"], "file": reply["file"],
            "fit_count": reply["fit_count"], "render_count": reply["render_count"],
            "result_lease": lease}


def result_status(store_root: Path, job_id: str, attempt_id: str):
    ops = OperationStore(store_root)
    operation = ops.status(job_id, attempt_id)
    path, spec = ops.path(job_id, attempt_id)
    if spec.get('semantics', {}).get('backend_id') == 'r0-cpu-portable-v1':
        from .cpu_replay.api import result_status as portable_status
        return portable_status(store_root, job_id, attempt_id)
    if operation.get('resource_plan_error') is not None:
        return dict(operation=operation,result_lease=operation.get('result_lease'),resident=False,
            binding=None,result_lease_usable=False,resource_plan_error=operation['resource_plan_error'],recovery=operation.get('recovery'))
    from .hybrid_replay.publication import is_hybrid
    if is_hybrid(spec):
        return {"operation": operation, "result_lease": operation.get("result_lease"),
                "resident": operation["resident"], "binding": operation["binding"],
                "recovery": operation.get("recovery")}
    if operation.get("algorithm_retired"):
        return {"operation":operation,"result_lease":operation.get("result_lease"),
            "resident":False,"binding":None,"algorithm_retired":True}
    if spec["kind"] not in {"fit", "replay"}:
        raise GMError("RESULT_UNAVAILABLE", "Not a result operation", 4)
    session = path / "session"
    lease_path = session / "lease.json"
    lease = strict_load(lease_path) if lease_path.is_file() else None
    if lease is not None and lease["state"] == "RESIDENT":
        for filename in ("owner.json", "host-process.json"):
            identity = strict_load(session / filename)
            if identity_alive(identity) is not True:
                lease = {**lease, "state": "LOST"}
                break
        if lease["expires_at"] <= time.time():
            lease = {**lease, "state": "LOST"}
    return {"operation": operation, "result_lease": lease,
            "resident": lease is not None and lease["state"] == "RESIDENT"}


def release(store_root: Path, job_id: str, attempt_id: str, binding: dict):
    if binding.get('backend_id') == 'r0-cpu-portable-v1':
        from .cpu_replay.api import release as portable_release
        return portable_release(store_root, job_id, attempt_id, binding)
    if binding.get('backend_id') is not None:
        from .hybrid_replay.api import release as hybrid_release
        return hybrid_release(_release_legacy,store_root,job_id,attempt_id,binding)
    return _release_legacy(store_root,job_id,attempt_id,binding)


def _release_legacy(store_root: Path, job_id: str, attempt_id: str, binding: dict):
    ops = OperationStore(store_root)
    path, spec = ops.path(job_id, attempt_id)
    from .hybrid_replay.publication import is_hybrid
    if is_hybrid(spec):
        from .hybrid_replay.recovery import persistent
        state = persistent(ops, path, spec)
    else:
        state = ops.status(job_id, attempt_id)
    if state["state"] != "SUCCEEDED":
        raise GMError("RESULT_UNAVAILABLE", "Replay operation not committed", 4)
    path, spec = ops.path(job_id, attempt_id)
    if spec["kind"] not in {"fit", "replay"}:
        raise GMError("RESULT_UNAVAILABLE", "Not a result operation", 4)
    session, session_spec, _, owner, host = _session(path, binding)
    try:
        reply = _call(session_spec, {"command": "RELEASE", **binding},
                      deadline=time.monotonic() + (10 if is_hybrid(spec) else 15))
    except GMError as exc:
        if exc.code in {"OPERATION_DEADLINE", "CANCELLED"}:
            if is_hybrid(spec):
                raise  # The R1 coordinator owns exact waits and cleanup.
            _lose_session(session, owner, host)
            _close_resident(job_id, attempt_id, session, "LOST")
        raise
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        receipt_path = session / "owner-cleanup.json"
        if (receipt_path.is_file() and identity_alive(owner) is False
                and identity_alive(host) is False):
            receipt = strict_load(receipt_path)
            if receipt.get("actual_wait") is True and receipt.get("owned_tree_empty") is True:
                admission = _close_resident(job_id, attempt_id, session, "RELEASED")
                return {"released": reply.get("released") is True,
                        "result_lease": strict_load(session / "lease.json"),
                        "owner_cleanup": receipt, "resident_admission": admission,
                        "exit_code": 0}
        time.sleep(.05)
    raise GMError("SESSION_CLOSE_WAIT_TIMEOUT", "Actual host/owner exit unconfirmed", 9)


def export(store_root: Path, job_id: str, attempt_id: str, binding: dict,
           destination: Path, *, alpha: float, format: str | None,
           bit_depth: int | None, icc_policy: str | None,
           request_key: str | None = None, export_root: Path | None = None):
    if binding.get('backend_id') == 'r0-cpu-portable-v1':
        from .cpu_replay.api import export as portable_export
        return portable_export(store_root, job_id, attempt_id, binding, destination,
                               alpha=alpha, format=format, bit_depth=bit_depth,
                               icc_policy=icc_policy, request_key=request_key, export_root=export_root)
    if (type(alpha) not in (float, int) or not math.isfinite(alpha) or not 0 <= alpha <= 1
            or (format, bit_depth) not in {("tiff", 8), ("tiff", 16),
                ("png", 8), ("png", 16), ("jpeg", 8), ("webp", 8), ("bmp", 8)}
            or icc_policy != "preserve_source"
            ):
        raise GMError("EXPORT_REQUEST_INVALID", "Explicit supported alpha, format, depth and ICC policy required", 2)
    replay_ops = OperationStore(store_root)
    replay_status = replay_ops.status(job_id, attempt_id)
    replay_path, replay_spec = replay_ops.path(job_id, attempt_id)
    _require_active_result(replay_path)
    if replay_spec["kind"] not in {"fit", "replay"} or replay_status["state"] != "SUCCEEDED":
        raise GMError("RESULT_UNAVAILABLE", "Committed result required", 4)
    session = replay_path / "session"
    session_spec_for_path = strict_load(session / "session-spec.json")
    publication_root = writable_output_root(export_root or Path(session_spec_for_path["output_root"]))
    destination = writable_destination(destination, publication_root,
        forbidden=(session_spec_for_path["source_path"],
                   session_spec_for_path["reference_path"]), allow_existing=True)
    source_decode = strict_load(session / "source-decode.json")
    if session_spec_for_path.get("mode") in {"development_pair", "group15"}:
        source_format = {"TIFF": "tiff", "PNG": "png", "JPEG": "jpeg",
                         "WEBP": "webp", "BMP": "bmp"}.get(source_decode["info"]["format"])
        if (format, bit_depth) != (source_format, source_decode["info"]["bits"]):
            raise GMError("EXPORT_SOURCE_FORMAT_MISMATCH",
                "Output format and bit depth must match the source image", 2)
        suffixes = {"tiff": {".tif", ".tiff"}, "png": {".png"},
                    "jpeg": {".jpg", ".jpeg"}, "webp": {".webp"}, "bmp": {".bmp"}}
        if destination.suffix.lower() not in suffixes[source_format]:
            raise GMError("EXPORT_DESTINATION_EXTENSION_MISMATCH",
                "Output filename extension must match the source format", 2)
    icc_hash = source_decode["info"]["color"]["icc_sha256"]
    effective_icc_policy = "preserve_source" if icc_hash is not None else "none"
    result_recipe_sha = replay_status["result_lease"]["recipe_sha256"]
    semantic = {"replay_job_id": job_id, "replay_attempt_id": attempt_id,
        "binding": binding, "alpha": float(alpha), "format": format,
        "bit_depth": bit_depth, "icc_policy": effective_icc_policy,
        "icc_sha256": icc_hash, "destination": str(destination),
        "publication_root": str(publication_root),
        "source_sha256": replay_spec["semantics"]["source_sha256"],
        "recipe_sha256": result_recipe_sha}
    from .export_metadata import policy_for_export_key
    ops = OperationStore(store_root)
    key = request_key or str(uuid.uuid4())
    prior = ops.lookup_request(key,saved_only=True)
    prior_semantics = prior[1]['semantics'] if prior else None
    metadata_policy=policy_for_export_key(session_spec_for_path['source_path'],semantic['source_sha256'],prior=prior_semantics)
    semantic.update(metadata_policy)
    path, spec, accepted = ops.begin("export", semantic, key)
    if not accepted["idempotent_hit"] and destination.exists():
        ops.transition(spec["job_id"], spec["attempt_id"], "FAILED", "terminal",
                       "OUTPUT_EXISTS", error_code="OUTPUT_EXISTS", exit_code=3)
        raise GMError("OUTPUT_EXISTS", str(destination), 3)
    if accepted["idempotent_hit"]:
        if accepted["state"] == "SUCCEEDED":
            return {**strict_load(path / "export-result.json"), "already_exported": True,
                    "job_id": spec["job_id"], "attempt_id": spec["attempt_id"]}
        # A host may have published the file just before this operation's
        # status write. Recover bytes from its independent transaction, never
        # invoke the encoder again for this key.
        from .gm004a_export import recover_export
        recovered = recover_export(session, key)
        if recovered["status"] not in {"SUCCEEDED", "SUCCEEDED_RECOVERED"}:
            raise GMError("EXPORT_INTERRUPTED", "Export has no complete publication", 7)
        replay_compute = strict_load(replay_path / "compute-result.json")
        recovered = {**recovered, "fit_count": replay_compute["fit_count"],
                     "render_count": replay_compute["render_count"]}
        if binding.get('backend_id') is not None:
            from .hybrid_replay.contract import versions
            recovered.update(versions(binding))
        atomic_json(path / "export-result.json", recovered)
        committed = ops.commit(spec["job_id"], spec["attempt_id"],
            _export_artifacts(destination, path / "export-result.json", session, key))
        return {**recovered, **{k: committed[k] for k in ("job_id", "attempt_id")},
                "already_exported": True}
    export_job, export_attempt = spec["job_id"], spec["attempt_id"]
    owner = host = None
    started = time.monotonic()
    export_seconds = 300 if binding.get('backend_id') is not None else session_spec_for_path.get("operation_seconds", 120)
    try:
        ops.transition(export_job, export_attempt, "RUNNING", "preflight",
                       "EXPORT_PREFLIGHT", owner=current_identity())
        with locked(_pool_lock(), timeout=120):
            session, session_spec, lease, owner, host = _session(replay_path, binding)
            if (path / "cancel.signal").exists():
                raise GMError("CANCELLED", "Export cancelled before encoding", 8)
            export_req = {"request_key": key, "alpha": float(alpha),
                "format": format, "bit_depth": bit_depth,
                "icc_policy": effective_icc_policy, "icc_sha256": icc_hash,
                "source_sha256": replay_spec["semantics"]["source_sha256"],
                "recipe_sha256": lease["recipe_sha256"],
                "lease_id": lease["handle_id"], "lease_generation": lease["generation"],
                "width": source_decode["identity"]["shape"][1],
                "height": source_decode["identity"]["shape"][0],
                "operation_job_id": export_job, "operation_attempt_id": export_attempt}
            export_req.update(metadata_policy)
            if binding.get('backend_id') is not None:
                from .hybrid_replay.contract import versions
                export_req.update(versions(binding))
            ops.transition(export_job, export_attempt, "RUNNING", "encoding",
                           "EXPORT_ENCODING", owner=current_identity(), host=host)
            reply = _call(session_spec, {"command": "EXPORT_V2", **binding,
                "destination": str(destination), "publication_root": str(publication_root),
                "export": export_req},
                deadline=started + export_seconds,
                cancelled=None)  # Host publication guard orders cancellation; retain a healthy connection.
            result = reply["export"]
            if (result["status"] not in {"SUCCEEDED", "SUCCEEDED_RECOVERED"}
                    or result["destination"] != str(destination)
                    or result["metadata"]["alpha"] != float(alpha)
                    or result["metadata"]["recipe_sha256"] != lease["recipe_sha256"]
                    or result["metadata"]["source_sha256"] != replay_spec["semantics"]["source_sha256"]):
                raise GMError("EXPORT_SEMANTIC_MISMATCH", "Export receipt differs", 7)
            if result["size_bytes"] > MAX_IMAGE_BYTES or time.monotonic() - started > export_seconds:
                raise GMError("EXPORT_BUDGET", "Export size or deadline exceeded", 5)
            # A second client may cancel after the host's durable publication.
            # status() then promotes that exact receipt without touching the
            # host again; this call returns the same committed file identity.
            if ops.status(export_job, export_attempt)["state"] == "SUCCEEDED":
                return {**strict_load(path / "export-result.json"),
                        "job_id": export_job, "attempt_id": export_attempt,
                        "already_exported": False, "operation_state": "SUCCEEDED"}
            ops.transition(export_job, export_attempt, "RUNNING", "committing",
                           "EXPORT_PUBLISHED", owner=current_identity())
            atomic_json(path / "export-result.json", {**result,
                "fit_count": reply["fit_count"], "render_count": reply["render_count"],
                "elapsed_seconds": time.monotonic() - started})
            committed = ops.commit(export_job, export_attempt,
                _export_artifacts(destination, path / "export-result.json", session, key))
            return {**result, "job_id": export_job, "attempt_id": export_attempt,
                    "already_exported": False, "operation_state": committed["state"]}
    except BaseException as exc:
        from .adaptive_execution.portable_contract import export_transaction
        publication = (export_transaction(session, hashlib.sha256(key.encode("utf-8")).hexdigest())
                       / "commit.json") if owner is not None else None
        if (publication is not None and publication.is_file()
                and ops.status(export_job, export_attempt)["state"] == "SUCCEEDED"):
            return {**strict_load(path / "export-result.json"),
                    "job_id": export_job, "attempt_id": export_attempt,
                    "already_exported": False, "operation_state": "SUCCEEDED"}
        code = exc.code if isinstance(exc, GMError) else "EXPORT_ERROR"
        ops.transition(export_job, export_attempt,
                       "CANCELLED" if isinstance(exc, GMError) and exc.exit_code == 8 else "FAILED",
                       "terminal", code, error_code=code,
                       exit_code=exc.exit_code if isinstance(exc, GMError) else 9)
        if (owner is not None and host is not None and
                ((isinstance(exc, GMError) and exc.code == "OPERATION_DEADLINE") or identity_alive(host) is not True)):
            _lose_session(session, owner, host)
            _close_resident(job_id, attempt_id, session, "LOST")
            _cleanup_owned_export_temp(path, spec, session)
            exc.resident_lost = True
        raise
