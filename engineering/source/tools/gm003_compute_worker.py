"""Sole R0 CUDA worker; one fit or explicit replay, no research imports."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parents[1]
if not getattr(sys, "_grademirror_portable", None):
    sys.path.insert(0, str(ROOT / "src"))
from grademirror.common import GMError, atomic_json, strict_load, sha_file
from grademirror.live_json import read_live_json
from grademirror.image_limits import MAX_PIXELS
from grademirror.gm003_ipc import connect, recv_array, recv_json, send_array, send_json
from grademirror.resource_policy import FAST_WORKING_BYTES,FAST_REQUIRED_FREE_MIB,GPU_MARGIN_BYTES,ALLOCATOR_FRACTION,VERSION,device_memory_snapshot,available_gpu_bytes

_RESOURCE_RUN = None


def _resource_receipt(value):
    if _RESOURCE_RUN is not None:
        atomic_json(_RESOURCE_RUN / 'gpu-preflight.json', value)


def _settled_device_memory(cuda_free, expected_uuid):
    # WDDM accounting can lag context creation. Take a bounded observation
    # interval before admission, with no allocation, model, or kernel retry.
    time.sleep(.5)
    samples = []
    for index in range(3):
        value = device_memory_snapshot(expected_uuid)
        if value['uuid'] != expected_uuid:
            raise ValueError('NVML_UUID_CHANGED')
        effective = available_gpu_bytes(cuda_free, value)
        samples.append(dict(at=time.time(),device_memory=value,available_free_bytes=effective))
        if index < 2:
            time.sleep(.2)
    selected = min(samples,key=lambda row:row['available_free_bytes'])
    return selected['device_memory'],selected['available_free_bytes'],samples


def _sha_array(array):
    return hashlib.sha256(memoryview(array).cast("B")).hexdigest()


def _fetch(session, command, dtype, shape):
    with connect(session["pipe_address"], session["auth_hex"]) as conn:
        send_json(conn, {"command": command})
        reply = recv_json(conn)
        if not reply.get("ok"):
            raise ValueError(reply.get("error_code", "FETCH_FAILED"))
        identity_shape = reply["identity"]["shape"]
        if (not isinstance(identity_shape, list) or len(identity_shape) != 3
                or any(type(v) is not int or v <= 0 for v in identity_shape)
                or identity_shape[2] != 3 or identity_shape[0] * identity_shape[1] > MAX_PIXELS
                or (shape is not None and tuple(identity_shape) != shape)):
            raise ValueError("IPC_INPUT_GEOMETRY_INVALID")
        if dtype is None:
            dtype = {"uint8": "|u1", "uint16": "<u2"}.get(reply["identity"].get("dtype"))
            if dtype is None:
                raise ValueError("IPC_INPUT_DTYPE_INVALID")
        array = recv_array(conn, expected_dtype=dtype, expected_shape=tuple(identity_shape))
    if _sha_array(array) != reply["identity"]["pixel_sha256"]:
        raise ValueError("IPC_PIXEL_HASH_MISMATCH")
    return array, reply["identity"], reply["info"]




def _gpu_preflight(spec):
    import torch
    import cv2
    cv2.setNumThreads(4)
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
        raise ValueError("CUDA_DEVICE_UNAVAILABLE")
    torch.cuda.set_per_process_memory_fraction(ALLOCATOR_FRACTION, 0)
    free, total = torch.cuda.mem_get_info(0)
    # Native source and Y live on host; include tile, convolution workspaces,
    # fit windows, model, transfer buffers, and a separate 512 MiB margin.
    fine = spec.get('profile_id')=='independent-r0-p3-lld10-v1'
    estimate = spec.get('fine_gpu_plan',{}).get('reserved_bytes',2560 * 1024**2) if fine else FAST_WORKING_BYTES
    resource=spec.get('gpu_resource')
    if resource is not None:
        from grademirror.gpu_resources import validate_resource
        operation=('fit' if sys.argv[1]=='development_pair' else 'replay') if _RESOURCE_RUN is not None else None
        plan=validate_resource(resource,fine=fine,operation=operation)
        if fine and spec.get('fine_gpu_plan')!=plan:raise ValueError('FINE_GPU_PLAN_MISMATCH')
        estimate=plan['reserved_bytes'];required=plan['required_free_bytes']
    else:required = max(int(3.5*1024**3),estimate+GPU_MARGIN_BYTES) if fine else FAST_REQUIRED_FREE_MIB*1024**2
    if free < required:
        _resource_receipt(dict(status='REFUSED_CUDA',cuda_free_bytes_before=int(free),required_free_bytes=required))
        raise ValueError("GPU_RESOURCE_UNQUALIFIED")
    props = torch.cuda.get_device_properties(0)
    expected = spec["gpu"]
    if props.name != expected["name"]:
        raise ValueError("GPU_NAME_CHANGED")
    actual_uuid = getattr(props, "uuid", None)
    if actual_uuid is not None and "GPU-" + str(uuid.UUID(bytes=bytes(actual_uuid.bytes))) != expected["uuid"]:
        raise ValueError("GPU_UUID_CHANGED")
    device_memory = None
    observations = []
    effective_free = int(free)
    if not fine or resource is not None:
        # WDDM CUDA memory is a per-context virtual view. Keep its exact raw
        # value and also check the same physical device through NVML. Never
        # replace a failed query with optimistic CUDA-only admission.
        try:
            device_memory,effective_free,observations = _settled_device_memory(int(free),expected['uuid'])
        except (OSError,RuntimeError,ValueError) as exc:
            _resource_receipt(dict(status='QUERY_FAILED',cuda_free_bytes_before=int(free),required_free_bytes=required,error=str(exc)))
            raise ValueError('GPU_RESOURCE_QUERY_FAILED: '+str(exc)) from exc
        _resource_receipt(dict(status='ADMITTED' if effective_free>=required else 'REFUSED_DEVICE',
            cuda_free_bytes_before=int(free),available_free_bytes_before=effective_free,
            required_free_bytes=required,observations=observations,resource_policy_version=VERSION))
        if effective_free < required:
            raise ValueError('GPU_RESOURCE_UNQUALIFIED')
    return {"name": props.name, "uuid": expected["uuid"],
            "driver_version": expected["driver_version"],
            "torch": torch.__version__, "cuda_runtime": torch.version.cuda,
            "opencv_threads": cv2.getNumThreads(),
            "cuda_free_bytes_before": int(free), "cuda_total_bytes": int(total),
            "available_free_bytes_before": effective_free, "device_memory_before": device_memory,
            "device_memory_observations": observations,
            "memory_admission_source": "cuda_raw" if fine and resource is None else "min_cuda_raw_nvml_device",
            "compute_geometry_resource": resource,
            "estimated_required_bytes": estimate,
            "required_extra_margin_bytes": GPU_MARGIN_BYTES,
            "per_process_fraction": ALLOCATOR_FRACTION,
            "required_free_bytes": required,
            "resource_policy_version": VERSION,
            "tf32_matmul": torch.backends.cuda.matmul.allow_tf32,
            "tf32_cudnn": torch.backends.cudnn.allow_tf32,
            "cudnn_deterministic": torch.backends.cudnn.deterministic}


def _progress(run: Path, entry: dict):
    if (run / "cancel.signal").exists():
        raise InterruptedError("CANCELLED_AT_SELECTED_UPDATE")
    if entry.get("stage") == "update_check":
        return
    from grademirror.light.runtime import operation
    if operation() is not None:
        entry=dict(entry,gm066_monotonic=time.monotonic())
    with (run / "fit-progress.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True, allow_nan=False) + "\n")


def _put_y(session, result, recipe_sha256, source_pixel_sha256, fit_count, render_count, *, profile=None, shared_y=None, release_y=None):
    from grademirror.engine.profiles import get_profile
    current = get_profile(profile.get("id") if isinstance(profile, dict) else None,profile.get('contract_sha256') if isinstance(profile,dict) else None)
    if profile.get("contract_sha256") != current["contract_sha256"]:
        raise ValueError("PUT_Y_PROFILE_BINDING_INVALID")
    algorithm = {"profile_id": current["id"], "profile_contract_sha256": current["contract_sha256"]}
    from grademirror.light.runtime import operation
    from grademirror.common import GMError
    with connect(session["pipe_address"], session["auth_hex"]) as conn:
        send_json(conn, {"command": "PUT_Y", **algorithm, "recipe_sha256": recipe_sha256,
                         "source_pixel_sha256": source_pixel_sha256,
                         "fit_count": fit_count, "render_count": render_count,
                         **({"shared_y": shared_y} if shared_y is not None else {})})
        if operation() is not None:
            ready = recv_json(conn)
            if ready != {"ok": True, "handoff_ready": True}:
                code = ready.get("error_code", "PUT_Y_ADMISSION_REQUIRED")
                error = GMError(code, code, 5)
                error.resource_boundary = ready.get("resource_boundary")
                raise error
        if shared_y is None:
            send_array(conn, result)
        else:
            # The host holds a handle but has not touched any result pages.
            result = None
            release_y()
            send_json(conn, {"shared_y_released": True})
        reply = recv_json(conn)
    if not reply.get("ok"):
        raise ValueError(reply.get("error_code", "PUT_Y_FAILED"))
    return reply




def run(run: Path, mode: str):
    from grademirror.light.runtime import operation
    from grademirror.light.eta import emit
    light=operation() is not None
    from grademirror.resource_execution.binding import worker_check
    session_resource=strict_load(run/'session-spec.json').get('resource_execution')
    worker_check(run,'compute',None if session_resource is None else session_resource['binding'])
    # Validate static algorithm identity before importing any numeric runtime.
    from importlib import import_module
    from grademirror.engine.profiles import get_profile
    session = strict_load(run / "session-spec.json")
    spec = strict_load(run / "compute-spec.json")
    profile = get_profile(spec.get("profile_id"),spec.get('profile_contract_sha256'))
    if spec.get("profile_contract_sha256") != profile["contract_sha256"]:
        raise ValueError("COMPUTE_PROFILE_BINDING_INVALID")
    if mode not in {"development_pair","replay"} or session["mode"] != "development_pair":
        raise ValueError("ALGORITHM_RETIRED")
    if profile["id"] == "independent-r0-p3-lld10-v1":
        from grademirror.engine.independent_r0_p3_lld10.service import run_fine
        return run_fine(run,mode,spec,session,fetch=_fetch,put_y=_put_y,gpu_preflight=_gpu_preflight,progress=_progress)
    import numpy as np
    import torch
    adapter = import_module("grademirror.engine." + profile["module"] + ".adapter")
    adapter.check_runtime()
    gpu = _gpu_preflight(spec)
    input_started = time.perf_counter()
    if light:
        atomic_json(run/"phase.json",dict(stage="fetch_source",gm066_monotonic=time.monotonic()))
    source, source_identity, source_info = _fetch(session,"FETCH_SOURCE",None,None)
    fetch_source_seconds=time.perf_counter()-input_started
    fetch_reference_seconds=0.
    if mode != "replay":
        fetch_reference_started=time.perf_counter()
        if light:
            atomic_json(run/"phase.json",dict(stage="fetch_reference",gm066_monotonic=time.monotonic()))
        reference, reference_identity, reference_info = _fetch(session,"FETCH_REFERENCE",None,None)
        fetch_reference_seconds=time.perf_counter()-fetch_reference_started
    else:
        reference = reference_identity = reference_info = None
    torch.cuda.synchronize();input_seconds=time.perf_counter()-input_started
    preparation_seconds=0.;save_seconds=0.;reuse_seconds=0.;bridge={};counts=adapter.EngineCounts()
    atomic_json(run/"bridge.json",bridge)
    if mode=="development_pair":
        preparation_started=time.perf_counter()
        before,source_geometry=adapter.downscale_native(source)
        after,reference_geometry=adapter.downscale_native(reference)
        if after.shape!=before.shape:raise ValueError("TRAINING_GEOMETRY_CHANGED")
        color_policy={"color_policy_id":"untagged-assume-srgb-user-20261017",
            "source":{"interpretation":source_info["color"]["interpretation"],
                "icc_sha256":source_info["color"]["icc_sha256"],"file_sha256":source_identity["file_sha256"]},
            "reference":{"interpretation":reference_info["color"]["interpretation"],
                "marker":"present" if reference_info["color"]["icc_present"] or reference_info["color"]["srgb_present"] else "absent",
                "file_sha256":reference_identity["file_sha256"]}}
        pair=adapter.prepared_pair(before,after,source_sha256=source_identity["file_sha256"],
            reference_sha256=reference_identity["file_sha256"],color_declaration="gamma-encoded-sRGB-float32",
            resize_geometry={"source":source_geometry,"reference":reference_geometry},source_identity=source_identity,
            reference_identity=reference_identity,color_policy=color_policy,io_runtime=spec["io_runtime"])
        torch.cuda.synchronize();preparation_seconds=time.perf_counter()-preparation_started
        fit_started=time.monotonic()
        atomic_json(run/"phase.json",{"stage":"fit","fit_started_wall":time.time()})
        recipe,fit_record=adapter.fit(pair,allow_development_profile=True,device="cuda:0",recipe_kind="development_image",
            device_metadata={"uuid":gpu["uuid"],"driver_version":gpu["driver_version"]},
            progress=lambda entry:_progress(run,entry),counts=counts)
        fit_seconds=time.monotonic()-fit_started
        if light:
            atomic_json(run/"phase.json",dict(stage="save_fast_recipe",gm066_monotonic=time.monotonic()))
        save_started=time.perf_counter()
        atomic_json(run / "training-record.json", fit_record)
        adapter.save_recipe(recipe,Path(spec["recipe_dir"]))
        from grademirror.stability_checkpoint import record
        record(run, Path(spec["recipe_dir"]), {"fit": counts.fit, "updates": 1000})
        save_seconds=time.perf_counter()-save_started
        if recipe.metadata["settings"]["updates"]!=1000 or fit_record["trace"][-1]["update"]!=1000:
            raise ValueError("FORMAL_FIT_INCOMPLETE")
    else:
        if light:
            atomic_json(run/"phase.json",dict(stage="reuse_fast_parameters",gm066_monotonic=time.monotonic()))
        fit_started=time.monotonic();recipe=adapter.load_recipe(Path(spec["recipe_dir"]))
        if recipe.metadata["recipe_sha256"]!=spec["expected_recipe_sha256"]:
            raise ValueError("REPLAY_RECIPE_IDENTITY_CHANGED")
        fit_record=None
        fit_seconds=0.
        reuse_seconds=time.monotonic()-fit_started
    fit_end = time.monotonic()
    torch.cuda.synchronize()
    atomic_json(run / "phase.json", {"stage": "render", "fit_seconds": fit_end - fit_started,
                                      "render_started_wall": time.time(),
                                      "fit_count": counts.fit})
    if (run / "cancel.signal").exists():
        raise InterruptedError("CANCELLED_BEFORE_RENDER")
    render_started = time.monotonic()
    reporting=dict(progress=lambda done,total:emit(run/"render-progress.json","render",done,total),
                   execution_device={k:gpu[k] for k in ("name","uuid","driver_version")}) if light else {}
    with ExitStack() as stack:
        mapping = None
        if light and sys.platform == "win32":
            from multiprocessing.shared_memory import SharedMemory
            from math import prod
            # Render directly into the transfer storage, with no full-image copy.
            mapping = SharedMemory(name="gm-fast-y-" + uuid.uuid4().hex, create=True,
                                   size=prod(source.shape) * np.dtype(np.float32).itemsize)
            stack.callback(mapping.close)
            reporting["out"] = np.ndarray(source.shape, dtype=np.float32, buffer=mapping.buf)
        rendered = adapter.render(source, recipe, device="cuda:0",**reporting,
            source_identity=source_identity, allow_synthetic_test=False,
            tile_side=512,
            cancelled=lambda: (run / "cancel.signal").exists(), counts=counts)
        render_end = time.monotonic()
        torch.cuda.synchronize()
        result_pixels = rendered.result
        # RenderResult also owns the original image. Drop both references
        # before the receive-side Y mapping can increase its working set.
        del rendered, source, reporting
        float_sha = _sha_array(result_pixels)
        atomic_json(run / "pre-handoff.json", {"profile_id": profile["id"],
            "recipe_sha256": recipe.metadata["recipe_sha256"], "float32_sha256": float_sha,
            "fit_count": counts.fit, "render_count": counts.render, "render_tiles": counts.render_tiles,
            "model_forward_batches": (fit_record.get("model_forward_batches", 0) if fit_record else 0) + counts.render_tiles,
            "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            "peak_reserved_bytes": int(torch.cuda.max_memory_reserved())})
        if mode == "replay" and float_sha != spec["expected_float32_sha256"]:
            raise ValueError("REPLAY_FLOAT_HASH_MISMATCH")
        atomic_json(run / "phase.json", {**({"gm066_monotonic":time.monotonic()} if light else {}), "stage": "put_y", "fit_seconds": fit_end - fit_started,
            "render_seconds": render_end - render_started, "fit_count": counts.fit,
            "render_count": counts.render})
        handoff_started = time.monotonic()
        def release_y():
            nonlocal result_pixels
            result_pixels = None
            mapping.close()

        descriptor = ({"name": mapping.name, "sha256": float_sha} if mapping is not None else None)
        reply = _put_y(session, result_pixels, recipe.metadata["recipe_sha256"],
                       source_identity["pixel_sha256"], counts.fit, counts.render, profile=profile,
                       shared_y=descriptor, release_y=release_y)
    torch.cuda.synchronize()
    record = {"mode": mode, "status": "PASS", "profile_id": profile["id"],
              "profile_contract_sha256": profile["contract_sha256"], "recipe_sha256": recipe.metadata["recipe_sha256"],
              "source_identity": source_identity,
              "reference_identity": reference_identity,
              "bridge": bridge, "fit_count": counts.fit, "render_count": counts.render,
              "fit_seconds": fit_end - fit_started, "render_seconds": render_end - render_started,
              "input_fetch_seconds": input_seconds, "input_prepare_seconds": preparation_seconds,
              **({"light_worker_seconds": dict(fetch_source=fetch_source_seconds,
                 fetch_reference=fetch_reference_seconds, fit=fit_seconds,
                 save_fast_recipe=save_seconds, reuse_fast_parameters=reuse_seconds)} if light else {}),
              "host_handoff_seconds": time.monotonic() - handoff_started,
              "render_tiles": getattr(counts, "render_tiles", 0),
              "model_forward_batches": (fit_record.get("model_forward_batches", 0) if fit_record else 0) + getattr(counts,"render_tiles",0),
              "float32_sha256": float_sha, "lease": reply["lease"],
              "gpu": {**gpu, "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
                      "peak_reserved_bytes": int(torch.cuda.max_memory_reserved())},
              "training_record": fit_record}
    atomic_json(run / "compute-result.json", record)
    return record


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in {"development_pair", "replay"}:
        raise SystemExit(2)
    if os.environ.get('GM063_LAUNCH_CONFIG'):
        from grademirror.adaptive_execution.calibration_launch import enter
        enter("G_compute",Path(sys.argv[2]).resolve())
    mode, run_dir = sys.argv[1], Path(sys.argv[2]).resolve()
    if run_dir != Path.cwd().resolve():
        raise SystemExit(2)
    _RESOURCE_RUN = run_dir
    try:
        run(run_dir, mode)
    except Exception as exc:
        try:
            (run_dir / "compute-failure.log").write_text(traceback.format_exc()[:16384], encoding="utf-8")
        except OSError as error:
            exc.add_note(str(error))
        try:
            phase = read_live_json(run_dir / "phase.json")
        except (OSError, ValueError, GMError) as phase_error:
            # Progress is diagnostic here; preserve the original compute failure.
            phase = {"read_error": type(phase_error).__name__}
        stage = phase.get("stage")
        phase_start = phase.get("fit_started_wall" if stage == "fit" else "render_started_wall")
        atomic_json(run_dir / "compute-result.json", {"status": "FAIL", "mode": mode,
            "error": type(exc).__name__, "message": str(exc), "failed_at": time.time(),
            "error_code": getattr(exc, "code", None),
            "resource_boundary": getattr(exc, "resource_boundary", None),
            "phase": phase, "failed_phase_seconds": max(0.,time.time()-phase_start) if phase_start is not None else None})
        raise SystemExit(9)
