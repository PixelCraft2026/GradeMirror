"""One normal-user CPU preview route, shared by the desktop and CLI."""
import os
import time
import uuid
from pathlib import Path

from ..common import atomic_json, reject_reparse, strict_load, writable_path
from ..cpu_replay import api, runtime
from ..cpu_replay.contract import BACKEND, binding, digest, need
from ..cpu_recipe.generated import complete
from ..gm004a_operations import OperationStore
from ..store import locked
from ..winproc import current_identity, identity_key
from . import contract as k


def recipe(directory, *, fine=False, legacy=False):
    if fine:
        from ..cpu_recipe.generated_fine import complete as load
        m,_=load(directory)
        need(m["provenance"]["TEST_ONLY"] is False and
             m["runtime_requirements"].get("schema")=="gm066-cpu-runtime-v1","LIGHT_CPU_FINE_RECIPE")
        return m
    m, _ = complete(directory)
    need(m["version"] == 2 and m["mode"] == "fast"
         and m["provenance"]["producer"] == ("original_cpu_fit_loop" if legacy else "gm065-preview-original-cpu-fit")
         and m["provenance"]["TEST_ONLY"] is False, "PREVIEW_RECIPE_UNSUPPORTED")
    return m


def run_dir(key):
    need(isinstance(key, str) and str(uuid.UUID(key)) == key, "PREVIEW_REQUEST_KEY")
    return k.user_root() / "runs" / key


def request(operation, source, reference, directory, key, *, display_reference=None, parent=None, compatibility=None, submit_lock=False):
    from ..light.runtime import operation as selected_light
    active=selected_light()
    fine=operation in ("refine","fine-replay")
    need(operation in ("fit","replay") or active and fine,"PREVIEW_REQUEST_SCHEMA")
    needs_reference=operation in ("fit","refine")
    need((parent is not None)==(operation=="refine") and (compatibility is None or active and operation=="replay"),
         "LIGHT_CPU_OPERATION_INPUTS")
    from ..adaptive_execution.memory_snapshot import ram_facts
    facts = ram_facts()
    sampled_at,sampled_monotonic=time.time(),time.monotonic()
    if active is not None:
        from ..light.resource_retry import pre_probe
        from ..light.resources import selected
        probe = selected(active)["probe"]
        pre_probe(facts,probe["ram"],probe["commit"],active,run_dir(key),locked=submit_lock,
                  sampled_at=sampled_at,sampled_monotonic=sampled_monotonic)
    else:
        need((facts["physical"]["available_bytes"] or 0)>=4*1024**3
             and (facts["commit"]["headroom_bytes"] or 0)>=4*1024**3,"PREVIEW_PROBE_MEMORY_NOT_READY")
    need((reference is not None)==needs_reference,"PREVIEW_REFERENCE_SCOPE")
    work = run_dir(key)
    work.mkdir(parents=True, exist_ok=True)
    paths = [Path(p).resolve(strict=True) for p in (source, reference) if p is not None]
    for p in paths:
        reject_reparse(p)
        need(p.is_file() and not p.is_relative_to(k.ROOT) and p.suffix.lower() in k.SUFFIXES,
             "PREVIEW_IMAGE_PATH", str(p))
    directory = writable_path(Path(directory))
    need(directory.is_absolute() and not directory.is_relative_to(k.ROOT)
         and directory.suffix.lower() == ".grademirror", "PREVIEW_RECIPE_PATH")
    payload = dict(paths=list(map(str, paths)))
    atomic_json(work / "probe.json", payload)
    probe_dir = work / "probe"
    probe_dir.mkdir(exist_ok=True)
    job, _ = k.start_job("probe", work / "probe.json", probe_dir, 3*1024**3)
    try:
        waited = runtime.wait(job, probe_dir, timeout=60)
        rows = strict_load(probe_dir / "photos.json")
        need(waited["exit_code"] == 0 and len(rows["photos"]) == len(paths),
             "PREVIEW_PHOTO_PROBE", rows.get("error"))
    finally:
        if job.poll() is None:
            job.terminate()
            runtime.wait(job, probe_dir, timeout=30)
        job.close()
    photos = rows["photos"]
    if active is not None:
        from ..light.resources import same_header
        need(same_header(active["source"], photos[0])
             and (not needs_reference or same_header(active["reference"], photos[1])),
             "LIGHT_CPU_INPUT_CHANGED")
    for path, info in zip(paths, photos):
        need(info["path"] == str(path) and k.header(info)["format"] == k.SUFFIXES[path.suffix.lower()],
             "PREVIEW_CONTENT_SUFFIX_MISMATCH")
    source, reference = photos[0], photos[1] if len(photos) == 2 else None
    headers = dict(source=k.header(source), reference=k.header(reference) if reference else None)
    if reference:
        need(headers["source"]["training_grid"] == headers["reference"]["training_grid"],
             "PAIR_ASPECT_RATIO_MISMATCH", "原图与参考图需要相同的训练网格。")
        need(not os.path.lexists(directory), "PREVIEW_RECIPE_ALREADY_EXISTS", str(directory))
    from ..engine.profiles import get_profile, R0, settings_for
    r0 = get_profile(R0)
    profile = {field: r0[field] for field in ("id", "contract_sha256", "core_sha256")}
    settings = settings_for(R0, headers["source"]["training_grid"])
    build = k.package()["build"]
    package_binding = binding(k.manifest_path())
    rt = dict(schema="gm065-preview-runtime-v1", inventory_sha256=k.package()["runtime_sha256"],
              numeric="CPython 3.12.14 / Torch 2.5.1+cu124 / NumPy 1.26.4 / CPU4")
    if active is not None:
        profile_name=active["numeric_profile"]
        row=active["environment"][profile_name]
        rt=dict(schema="gm066-cpu-runtime-v1",inventory_sha256=row["lock"],numeric=row["versions"]["torch"],
                python=row["python"],threads=4)
    render_directory=Path(parent).resolve(strict=True) if parent else directory
    if operation != "fit":
        from ..cpu_recipe.identity import math_closure
        m = recipe(render_directory,fine=operation=="fine-replay",legacy=compatibility is not None)
        math = m["mathematical"]
        compatible=m["runtime_requirements"]==rt
        color_compatible=math["color_policy"] == source["color"]["color_policy_id"]
        if compatibility is not None:
            from ..light.cpu_compatibility import color_matches, read
            read(compatibility,directory,m,rt,build["sha256"])
            compatible=True
            color_compatible=color_matches(math["color_policy"],source,display_reference)
        same_math=math["math_files"] == math_closure("fine" if operation=="fine-replay" else "fast")
        if not same_math and active and operation=="replay" and compatibility is None:
            from ..light.fixed_c_bridge import checked_recipe
            bridge_source = dict(source, training_grid=headers['source']['training_grid'])
            same_math=checked_recipe(render_directory,m,bridge_source,
                display_reference["file"] if display_reference else None,rt,operation=operation)
        need(compatible and math["profile"] == profile and math["settings"] == settings
             and same_math and color_compatible,
             "PREVIEW_RECIPE_RUNTIME_INCOMPATIBLE", "可查看配方。显式重放需要相容的便携运行时。")
        saved = math["inputs"]["source_identity"]
        need(dict(saved, pixel_sha256=None) == k.photo_identity(source), "PREVIEW_RELINK_BYTES_MISMATCH")
        if operation=="refine":
            saved_ref=math["inputs"]["reference_identity"]
            need(dict(saved_ref,pixel_sha256=None)==k.photo_identity(reference),"LIGHT_CPU_PARENT_REFERENCE")
        else:
            saved_ref=None
    else:
        m = saved = saved_ref = None
    if fine:
        from ..fine_contract import validate_limits
        fine_ref=headers["reference"] or (display_reference or {}).get("header")
        need(fine_ref is not None,"LIGHT_CPU_FINE_REFERENCE_REQUIRED")
        validate_limits(headers["source"],fine_ref)
    client = current_identity()
    need(identity_key(client) is not None, "PREVIEW_CLIENT_IDENTITY")
    manifest = work / "request.json"
    c = dict(schema=k.SCHEMA, version=k.VERSION, policy=k.POLICY, package=package_binding,
             executor_build=build, runtime=rt, client=client, key=key, operation=operation,
             phase="fit" if operation == "fit" else "render", mode="fine" if fine else "fast",
             source={n: source[n] for n in ("path", "sha256", "bytes")},
             reference={n: reference[n] for n in ("path", "sha256", "bytes")} if reference else None,
             source_identity=saved or k.photo_identity(source),
             reference_identity=saved_ref or (k.photo_identity(reference) if reference else None),
             headers=headers, profile=profile, settings=settings,
             color_policy=source["color"]["color_policy_id"], bundle=str(directory),
             workspace=str(work), output_root=str(work), pool_root=str(k.user_root() / "pool"),
             manifest_path=str(manifest), lease_seconds=k.POLICY["lease_seconds"],
             force_grace_seconds=30, request_seconds=120, prepared_clock_shift=0,
             fault_point="", pause_at="", TEST_ONLY=False,
             **({"light_plan":active["plan_id"],"light_plan_record":binding(Path(active["plan_path"])),
                 "display_reference":display_reference,
                 "render_bundle":str(render_directory),
                 "compatibility":binding(Path(compatibility)) if compatibility else None} if active else {}),
             science=dict(preview_request=key, operation=operation, profile=profile))
    if manifest.exists():
        need(strict_load(manifest) == c, "PREVIEW_KEY_CONFLICT")
    else:
        atomic_json(manifest, c)
    sem = dict(backend_id=BACKEND, mode=c["mode"], preview_version=k.VERSION, preview_client=client,
               preview_key=key, TEST_ONLY=False, first_fit=operation == "fit",
               instance_context=binding(manifest), executor_build=build["sha256"],
               source_path=str(paths[0]), source_sha256=source["sha256"], output_root=str(work),
               bundle_directory=str(directory), reference_path=str(paths[1]) if reference else None,
               **({"cpu_refine":True,"refine_prepare":operation=="refine"} if fine else {}),
               bundle_id=m["bundle_id"] if m else digest(dict(key=key, stage="fit")),
               math_id=m["math_id"] if m else digest(dict(key=key, profile=profile)), core_rows=None)
    return c, sem


def reconcile_pool(home):
    """Reconcile the exact CPU operation without holding its reservation lock."""
    from ..cpu_replay.pool import PortableAdmission
    pool = PortableAdmission(home / "pool")
    row = pool.root / "reservation.json"
    if not row.is_file():
        return None
    state = strict_load(row)
    if state["state"] != "FREE":
        prior = Path(state["store_root"]).resolve()
        need(prior.is_relative_to(home / "runs"), "PREVIEW_POOL_IDENTITY")
        # Reuse the full CPU reclaim path, including reopening checks
        # for mappings. An owner receipt alone does not close a CPU lease.
        from ..light.preview32_pool_cleanup import settle
        upgraded = settle(pool, state, prior)
        if upgraded is not None:
            return upgraded
        OperationStore(prior).status(state["job_id"], state["attempt_id"])
        state = strict_load(row)
    return state


def submit(operation, source, reference, directory, key, *, display_reference=None, parent=None, compatibility=None):
    work = run_dir(key)
    home = k.user_root()
    home.mkdir(parents=True, exist_ok=True)
    k.runtime_check("desktop")
    # Shared across package moves and all instances in this user's data root.
    with locked(home / "submit.lock", timeout=1):
        if (work / "request.json").is_file():
            found = OperationStore(work / "store").lookup_request(key, saved_only=True)
            if found:
                _, spec = found
                c = k.context(spec)
                need(operation == c["operation"] and str(Path(source).resolve()) == c["source"]["path"]
                     and (str(Path(reference).resolve()) if reference else None) ==
                     (c["reference"]["path"] if c["reference"] else None)
                     and str(Path(directory).resolve()) == c["bundle"], "PREVIEW_KEY_CONFLICT")
                return OperationStore(work / "store").status(spec["job_id"], spec["attempt_id"])
        state = reconcile_pool(home)
        need(state is None or state["state"] == "FREE", "PREVIEW_POOL_BUSY",
             "请先释放已有结果或关闭占用该结果的窗口。")
        c, sem = request(operation, source, reference, directory, key,display_reference=display_reference,
                         parent=parent,compatibility=compatibility,submit_lock=True)
        if (work / "cancel.signal").exists():
            need(False, "CANCELLED", exit_code=8)
        token = k._active.set(c)
        try:
            return api._start(work / "store", sem, key, c)
        except Exception as error:
            from ..light.resource_retry import InnerResource
            if isinstance(error,InnerResource) and error.inner_resource["cleanup"]=="COMPLETE":
                error.inner_resource["cleanup"]="UNKNOWN"
                try:
                    waited=strict_load(work/"probe/owned-wait.json")
                except (OSError,ValueError):
                    waited={}
                if waited.get("actual_wait") is True and waited.get("tree_empty") is True and waited.get("exit_code")==0:
                    error.inner_resource["lifecycle"]["probe_wait"]=waited
                    error.inner_resource["cleanup"]="COMPLETE"
            raise
        finally:
            k._active.reset(token)


def status(key):
    work = run_dir(key)
    if not (work / "store").is_dir():
        return {}
    ops = OperationStore(work / "store")
    found = ops.lookup_request(key, saved_only=True)
    if not found:
        return {}
    path, spec = found
    from ..live_json import read_live_json
    progress=read_live_json(path / "progress.json")
    if not spec["semantics"].get("preview_version"):
        return progress
    from ..light.runtime import application
    if not application():
        return progress
    current=ops.progress_snapshot(spec["job_id"],spec["attempt_id"])
    return dict(current,**{k:v for k,v in progress.items() if k!="state"})


def saved_address(key):
    store = run_dir(key) / "store"
    if not store.is_dir():
        return None
    found = OperationStore(store).lookup_request(key, saved_only=True)
    if found is None:
        return None
    spec = found[1]
    return spec["job_id"], spec["attempt_id"]


def cancel(key):
    work = run_dir(key)
    work.mkdir(parents=True, exist_ok=True)
    atomic_json(work / "cancel.signal", dict(key=key))
    if (work / "store").is_dir():
        ops = OperationStore(work / "store")
        found = ops.lookup_request(key, saved_only=True)
        if found:
            _, spec = found
            return ops.cancel(spec["job_id"], spec["attempt_id"])


def cancel_export(key, result, request_key):
    from .host import export_cancel_path
    from ..gm004a_export import _request_dir
    path,spec=OperationStore(run_dir(key)/"store").path(result["job_id"],result["attempt_id"])
    c=k.context(spec)
    need(c["client"]==current_identity(),"PREVIEW_CONSUMER_IDENTITY")
    marker=export_cancel_path(path/"session",request_key)
    marker.parent.mkdir(parents=True,exist_ok=True)
    with locked(marker.with_suffix(".lock"),timeout=10):
        tx,_=_request_dir(path/"session",request_key)
        if (tx/"commit.json").exists():
            return dict(cancel_disposition="ALREADY_COMMITTED")
        atomic_json(marker,dict(request_key=request_key))
        return dict(cancel_disposition="REQUESTED")


def consume(key, result, action, **args):
    store = run_dir(key) / "store"
    pair = (result["job_id"], result["attempt_id"])
    path, spec = OperationStore(store).path(*pair)
    c = k.context(spec, cleanup=action == "release")
    need(c["client"] == current_identity(), "PREVIEW_CONSUMER_IDENTITY")
    token = k._active.set(c)
    try:
        lease = result["binding"]
        if action == "preview":
            return api.preview(store, *pair, lease, **args)
        if action == "keep_alive":
            api.check_binding(path,spec,lease)
            return api.call(path,"KEEP_ALIVE",lease)
        if action == "release":
            return api.release(store, *pair, lease)
        if action == "qualify":
            from ..light.runtime import operation
            active=operation()
            need(active is not None and active.get("validation_evidence") is True
                 and active["plan_id"]==c.get("light_plan"),"LIGHT_VALIDATION_EVIDENCE_REQUIRED")
            api.check_binding(path,spec,lease)
            return api.call(path,"QUALIFY",lease)
        if action == "export":
            api.check_binding(path, spec, lease)
            result = api.call(path, "EXPORT", lease, **args)
            need(result["status"] != "INTERRUPTED", "PREVIEW_EXPORT_INTERRUPTED")
            return result
        raise ValueError("PREVIEW_CONSUMER_ACTION")
    finally:
        k._active.reset(token)
