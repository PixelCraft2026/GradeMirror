"""Admission and process caps for the packaged dynamic preview."""
from pathlib import Path
import time

from ..common import atomic_json
from ..cpu_replay.contract import need
from ..image_limits import GIB, MIB
from .dynamic_admission import GPU_GUARD, model, round_up, workspace

SCHEMA = "gm075-preview-dynamic-v2"
RESOURCE_FIELDS = ("ram", "commit", "vram", "reserved")


def candidate_backend(qualification):
    # Old exact-row authorizations cannot select a different admission policy.
    need(qualification is None, "LIGHT_OLD_QUALIFICATION_UNSUPPORTED")
    return None


def capacity(backend, source, reference, quality, operation):
    return model(source, reference, backend, quality, operation)


def envelope(cap):
    probe = next(row for row in cap["stages"] if row["stage"] == "probe")
    return dict(schema=SCHEMA, state="DYNAMIC", capacity_id=cap["capacity_id"],
                startup=cap["startup"], probe={field: probe[field] for field in ("ram", "commit")})


def selected(plan):
    row = plan["selection"]
    cap = capacity(row["backend"], plan["source"], plan["reference"], plan["quality"], plan["operation"])
    need(row["admission"] == envelope(cap) and row["stages"] == cap["stages"]
         and row["objects"] == cap["objects"] and row["geometry"] == cap["geometry"]
         and row["jobs"] == job_limits(cap), "LIGHT_ADMISSION_PLAN_CHANGED")
    return row["admission"]


def same_header(frozen, actual):
    return all(frozen.get(key) == actual.get(key) for key in ("width", "height", "bits")) and (
        str(frozen.get("format", "")).casefold() == str(actual.get("format", "")).casefold()
    ) and all(frozen.get(key) is None or frozen[key] == actual.get(key)
              for key in ("bytes", "sha256"))


def active_plan(backend=None):
    from .runtime import operation
    plan = operation()
    if plan is not None:
        need(backend is None or plan["selection"]["backend"] == backend, "LIGHT_RESOURCE_BACKEND")
        selected(plan)
    return plan


def job_limits(cap):
    """Derive separate finite process limits from role-owned storage.

    Private compute can own its native worker plus a solver child. Shared
    storage belongs to the host for the Job allowance. Driver and library
    commit allowances here bound Jobs and do not enter free-memory admission.
    """
    owners = cap["objects"]
    def owned(role):
        return max(sum(item["commit"] for item in rows if item["owner"] == role)
                   for rows in owners.values())

    io = round_up(owned("io") + 768 * MIB)
    solver = round_up(owned("solver") + 768 * MIB) if owned("solver") else 0
    runtime_commit = (1536 if cap["backend"] == "cpu" else 4096) * MIB
    if "GPU_LLF" in owners:
        # The solver is waited before LLF. Each phase gets its own compute
        # commitment envelope, avoiding overlap with an exited solver Job.
        phase_limits = [sum(obj["commit"] for obj in rows if obj["owner"] == "compute")
                        + runtime_commit + (solver if stage != "GPU_LLF" else 0)
                        for stage,rows in owners.items()]
        compute = round_up(max(phase_limits))
    else:
        compute = round_up(owned("compute") + runtime_commit + solver)
    cpu = next((row for row in cap["stages"] if row["stage"] == "CPU"), None)
    # Tree RSS may count the source and output mappings again in a second
    # process. Only the watchdog envelope carries that duplicate allowance.
    shared = max(sum(obj["ram"] for obj in rows if obj["shared"]) for rows in owners.values())
    host = max(io, round_up(owned("io") + shared + 512 * MIB))
    control = 512 * MIB
    pool = dict(host=host, compute=compute, control_ipc=control, pool=host + compute + control)
    limits = dict(pool=pool, solver=solver, probe=round_up(512 * MIB),
                  cpu_worker=round_up((cpu["object_commit"] if cpu else 0) + 2048 * MIB),
                  tree_rss=round_up(cap["startup"]["ram"] + shared),
                  tree_private=round_up(cap["startup"]["commit"] + shared))
    need(pool["pool"] <= 256 * GIB, "LIGHT_JOB_CAPACITY")
    return limits


def pool_budget(fallback, backend=None):
    plan = active_plan(backend)
    return plan["selection"]["jobs"]["pool"] if plan else fallback


def workspace_limit(height, width, core=None):
    from .runtime import operation
    plan = operation()
    if plan is None:
        return 512 * MIB
    source = plan["source"]
    row = plan["selection"]
    if core is None:
        core = 512 if row["backend"] == "mixed" else 256
    need((height, width) == (source["height"], source["width"])
         and row["geometry"]["cpu_core_rows"] == core, "LIGHT_WORKSPACE_GEOMETRY")
    # The stripe implementation calls this per core. Recompute only the
    # integer footprint, with no array allocation and no caller-supplied cap.
    limit = workspace(height, width, core)
    need(row["geometry"]["cpu_workspace"] == limit, "LIGHT_WORKSPACE_LIMIT_CHANGED")
    return limit


def demand(plan, stage):
    selected(plan)
    if stage == "startup":
        return plan["selection"]["admission"]["startup"]
    if stage == "cpu-child":
        remaining = [row for row in plan["selection"]["stages"]
                     if row["stage"] not in ("training", "training_prepare")]
        return {name: max(row[name] for row in remaining) for name in ("ram", "commit")}
    for row in plan["selection"]["stages"]:
        if row["stage"] == stage:
            return row
    need(False, "LIGHT_ADMISSION_STAGE", stage)


def _credit(plan, stage, buffers):
    """Credit only live storage with a mapped descriptor and expected owner.

    The caller holds the attached Windows SharedMemory handle through the
    check. The descriptor must agree with that handle and the frozen object.
    Lack of an inventory means a conservative full stage requirement.
    """
    objects = {obj["name"]: obj for obj in plan["selection"]["objects"].get(stage, ())}
    result = {}
    names = set()
    intervals = []
    for name, descriptor, mapping in buffers:
        obj = objects.get(name)
        if obj is None:
            continue
        import ctypes
        size = descriptor["bytes"]
        # The fast GPU Y view is transferred serially between processes.
        # Its resident pages may be credited here without giving the Job/RSS
        # planner an allowance for two simultaneous working-set copies.
        fast_y = (name == "Y" and plan["selection"]["backend"] == "gpu"
                  and plan["quality"] == "fast"
                  and isinstance(descriptor.get("name"), str)
                  and descriptor["name"].startswith("gm-fast-y-"))
        if obj["shared"] or fast_y:
            need(name not in result and mapping.name not in names
                 and descriptor["name"] == mapping.name and size == obj["commit"]
                 and size <= mapping.size, "LIGHT_RESIDENT_OBJECT_ID", name)
            start = ctypes.addressof(ctypes.c_char.from_buffer(mapping.buf))
            identifier = mapping.name
            names.add(mapping.name)
        else:
            if not (mapping.flags.owndata and mapping.flags.c_contiguous
                    and size == mapping.nbytes == obj["commit"]):
                continue
            start = int(mapping.__array_interface__["data"][0])
            identifier = "owned-array"
        end = start + size
        need(all(end <= a or start >= b for a,b in intervals), "LIGHT_RESIDENT_ALIAS", name)
        intervals.append((start,end))
        result[name] = dict(mapping=identifier, ram=_resident_bytes(start, size), commit=obj["commit"])
    return result


def _resident_bytes(start, size):
    """Count only complete pages currently valid in the local working set."""
    import ctypes as c
    from mmap import PAGESIZE
    class Entry(c.Structure):
        _fields_ = [("address", c.c_void_p), ("attributes", c.c_size_t)]
    query = c.windll.psapi.QueryWorkingSetEx
    query.argtypes = (c.c_void_p, c.c_void_p, c.c_ulong)
    query.restype = c.c_int
    first = round_up(start, PAGESIZE)
    final = (start + size) // PAGESIZE * PAGESIZE
    resident = 0
    chunk = 2048 * PAGESIZE
    for base in range(first, final, chunk):
        count = min(2048, (final - base) // PAGESIZE)
        entries = (Entry * count)()
        for index in range(count):
            entries[index].address = base + index * PAGESIZE
        # Current-process pseudo handle, no foreign process enumeration.
        if not query(c.c_void_p(-1), entries, c.sizeof(entries)):
            return 0
        resident += sum(item.attributes & 1 for item in entries)
    return resident * PAGESIZE


def _shadow_receipt(receipt, plan, stage, point, credited, sample):
    if "resource_shadow" in plan:
        try:
            from .resource_shadow import boundary
            receipt["resource_shadow"] = boundary(plan, stage, point, credited, sample)
        except Exception:
            receipt["resource_shadow"] = dict(schema="gm084-resource-shadow-v2", state="UNAVAILABLE")
    return receipt


def check_host(path, plan, stage, *, point=None, facts=None, buffers=(), retryable=False):
    from ..adaptive_execution.memory_snapshot import ram_facts
    from ..common import GMError
    from .resource_retry import InnerResource
    limits = demand(plan, stage)
    credited = _credit(plan, stage, buffers)
    required = {field: round_up(max(0, limits.get("object_" + field, 0)
                                    + limits.get("runtime_" + field, 0)
                                    + limits.get("uncertainty_" + field, 0)
                                    + limits.get("external_" + field + "_guard", 0)
                                    - sum(item[field] for item in credited.values())))
                if credited else limits[field] for field in ("ram", "commit")}
    sample = facts if facts is not None else ram_facts()
    actual = dict(ram=sample["physical"]["available_bytes"], commit=sample["commit"]["headroom_bytes"])
    unknown = any(type(value) is not int for value in actual.values())
    allow = not unknown and all(actual[field] >= required[field] for field in required)
    point = point or stage
    path = Path(path) / f"dynamic-{point}.json"
    receipt = dict(plan_id=plan["plan_id"], capacity_id=plan["selection"]["admission"]["capacity_id"],
                          stage=stage, point=point, sampled_monotonic=time.monotonic(),
                          sample=actual, required=required, credited=credited, allowed=allow)
    atomic_json(path, _shadow_receipt(receipt, plan, stage, point, credited, actual))
    if not allow:
        code = "PREVIEW_MEMORY_UNKNOWN" if unknown else "PREVIEW_MEMORY_NOT_READY"
        if retryable:
            raise InnerResource(code, "关闭占用内存的程序，并检查系统提交余量。", point,
                                [(field, actual[field], required[field]) for field in required],
                                reason="RESOURCE_UNKNOWN" if unknown else "CAPACITY_CHANGED")
        error = GMError(code, point, 5)
        error.resource_boundary = dict(stage=stage, point=point, available=actual, required=required)
        raise error
    return actual


def check_cpu_child(path, plan, *, facts=None):
    return check_host(path, plan, "cpu-child", point="cpu_inner_admission", facts=facts, retryable=True)


def check_gpu_stage(path, plan, torch, stage, *, point=None, buffers=(), release_unused_cache=False):
    """Resample the same device and host resources before a CUDA stage."""
    point = point or stage
    from ..common import GMError
    from ..resource_policy import available_gpu_bytes, device_memory_snapshot
    limits = demand(plan, stage)
    need(limits["reserved"] > 0, "LIGHT_GPU_ADMISSION_STAGE")
    proof = dict(plan_id=plan["plan_id"], device_uuid=plan["device"]["uuid"], stage=stage, point=point,
                 allocated=None, reserved=None, cuda_free=None, nvml_free=None,
                 effective_free=None, required=limits["reserved"] + GPU_GUARD, allowed=False)
    receipt = Path(path) / f"dynamic-{point}-vram.json"
    try:
        if release_unused_cache:
            before_allocated = int(torch.cuda.memory_allocated())
            before_reserved = int(torch.cuda.memory_reserved())
            proof["cache_boundary"] = dict(allocated_before=before_allocated,
                                            reserved_before=before_reserved, empty_cache_called=False)
            if 0 <= before_allocated < before_reserved:
                proof["cache_boundary"]["empty_cache_called"] = True
                torch.cuda.empty_cache()
        allocated = proof["allocated"] = int(torch.cuda.memory_allocated())
        reserved = proof["reserved"] = int(torch.cuda.memory_reserved())
        if "cache_boundary" in proof:
            proof["cache_boundary"].update(allocated_after=allocated, reserved_after=reserved)
        cuda_free, cuda_total = map(int, torch.cuda.mem_get_info())
        need(0 <= cuda_free <= cuda_total, "LIGHT_GPU_MEMORY_SAMPLE")
        proof["cuda_free"] = cuda_free
        nvml = device_memory_snapshot(proof["device_uuid"])
        need(nvml["uuid"] == proof["device_uuid"], "LIGHT_DEVICE_CHANGED")
        proof["nvml_free"] = nvml["free_bytes"]
        free = proof["effective_free"] = available_gpu_bytes(cuda_free, nvml)
        total = proof["effective_total"] = min(cuda_total, nvml["total_bytes"])
        required = proof["required"] = max(0, limits["reserved"] - allocated) + GPU_GUARD
        allowed = proof["allowed"] = (0 <= allocated <= reserved <= limits["reserved"] and
                                      limits["reserved"] * 5 <= total * 4 and free >= required)
    except (GMError, OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        proof.update(reason="RESOURCE_UNKNOWN", error=str(error), sampled_monotonic=time.monotonic())
        atomic_json(receipt, _shadow_receipt(proof, plan, stage, point, {}, {}))
        raise GMError("GPU_RESOURCE_UNQUALIFIED", f"{point}：无法确认当前显存，已停止处理。", 5) from error
    proof["sampled_monotonic"] = time.monotonic()
    atomic_json(receipt, _shadow_receipt(proof, plan, stage, point, {}, {"vram": proof["effective_free"]}))
    if not allowed:
        error = GMError("GPU_RESOURCE_UNQUALIFIED",
                        f"{point}：当前可用显存 {free / GIB:.2f} GiB，阶段需要 {required / GIB:.2f} GiB，"
                        f"显存保留预算 {limits['reserved'] / GIB:.2f} GiB（分配器上限 {total * .8 / GIB:.2f} GiB）。", 5)
        error.resource_boundary = dict(stage=stage, point=point, available={"vram": free},
                                       required={"vram": required}, reserved=reserved,
                                       reserved_budget=limits["reserved"], total=total)
        raise error
    # CUDA reservation is not proof of process-owned Windows commit. Sample
    # after cache release and credit only verified host objects.
    check_host(path, plan, stage, point=point + "-host",
               buffers=tuple(owned_object(name, array) for name, array in buffers))


def owned_object(name, array):
    return name, {"bytes": array.nbytes}, array


def host_boundary(path, backend, stage, *, point=None, buffers=()):
    plan = active_plan(backend)
    if plan is not None:
        check_host(path, plan, stage, point=point, buffers=buffers)


def shared_object(name, mapping, array):
    return name, dict(name=mapping.name, bytes=array.nbytes), mapping


def host_identity_ack(session, filename, who):
    from ..common import strict_load
    from ..winproc import identity_key
    path = Path(session)/filename
    deadline = time.monotonic() + 30
    while not path.is_file():
        need(time.monotonic() < deadline, "LIGHT_HOST_RETAIN_TIMEOUT")
        time.sleep(.02)
    need(identity_key(strict_load(path)) == identity_key(who), "LIGHT_HOST_RETAIN_ID")


def stop_before_decode(session, role):
    """Engineering may stop only after the real owner retained the host."""
    from .runtime import operation
    plan = operation()
    if not plan or plan.get("validation_evidence") is not True or plan.get("engineering_worker_stop") != role:
        return
    from ..common import GMError
    from ..winproc import current_identity
    check_host(session, plan, "decode", point="engineering-before-host-decode")
    atomic_json(Path(session)/"engineering-worker-stop.json",
                dict(role=role, identity=current_identity(), plan_id=plan["plan_id"],
                     science=0, boundary="after host identity acknowledgement, before decode"))
    raise GMError("LIGHT_ENGINEERING_WORKER_STOP", "Controlled pre-decode stop", 8)


def scoped_gpu_plan(source, legacy, *, fine, operation):
    plan = active_plan()
    if plan is None:
        return legacy
    backend = plan["selection"]["backend"]
    original = plan["source"]
    need(backend in ("gpu", "mixed") and
         (source["width"], source["height"]) == (original["width"], original["height"])
         and (plan["operation"] == operation or (plan["operation"], operation) == ("refine", "fit"))
         and fine == (backend == "gpu" and plan["quality"] == "fine"), "LIGHT_GPU_ADMISSION_SCOPE")
    rows = {row["stage"]: row for row in plan["selection"]["stages"]}
    reserved = plan["selection"]["admission"]["startup"]["reserved"]
    updated = dict(legacy, reserved_bytes=reserved, required_free_bytes=reserved + GPU_GUARD)
    if not fine:
        updated["render_working_bytes"] = rows["R0"]["reserved"]
        updated["training_working_bytes"] = rows.get("training", {}).get("reserved", 0)
    return updated


def gpu_ram_recheck(path, source, reference, quality, operation, checkpoint):
    plan = active_plan("gpu")
    if plan is None:
        return False
    need(plan["quality"] == quality and (plan["operation"] == operation or
         (operation, plan["operation"]) == ("fit", "refine")) and
         same_header(plan["source"], source) and same_header(plan["reference"], reference),
         "LIGHT_GPU_ADMISSION_SCOPE")
    check_host(path, plan, "startup", point="gpu-ram-" + checkpoint, retryable=True)
    return True


def mixed_stage(plan, stage, physical, commit):
    active = active_plan("mixed")
    if active is None:
        return physical, commit
    source = active["source"]
    identity = plan["source_identity"]
    need(identity["shape"] == [source["height"], source["width"], 3]
         and identity["bit_depth"] == source["bits"]
         and identity["file_sha256"] == source["sha256"], "LIGHT_MIXED_ADMISSION_SCOPE")
    row = demand(active, {"output": "export"}.get(stage, stage))
    return row["ram"], row["commit"]

