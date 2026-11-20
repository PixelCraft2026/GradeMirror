"""Packaged preview authority and isolated process entry points."""
import contextvars
import functools
import os
import sys
import time
from pathlib import Path

from ..common import atomic_json, reject_reparse, sha_file, strict_load, writable_path
from ..cpu_replay.contract import FLAGS, ROOT, binding, digest, need, verify
from ..image_limits import GIB, photo_budget

VERSION = "0.1.0-preview.1"
SCHEMA = "gm065-preview-request-v1"
MANIFEST = ROOT / "preview-manifest.json"
RUNTIME = ".runtime/gm059-v1"
ROLES = {"probe": "io", "owner": "control", "host": "io", "fit": "numeric", "r0": "numeric",
         "prepare":"numeric","cpu":"numeric","solver":"solver"}
FORMATS = {"jpeg": (8,), "png": (8, 16), "tiff": (8, 16), "webp": (8,), "bmp": (8,)}
SUFFIXES = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".tif": "tiff",
            ".tiff": "tiff", ".webp": "webp", ".bmp": "bmp"}
POLICY = dict(max_pixels=24_000_000, max_axis=8192, threads=4, job_commit=6*GIB,
              lease_seconds=7200, platform="windows-amd64", minimum_build=17763)
_active = contextvars.ContextVar("gm065_request", default=None)


def enabled(value):
    return bool(value and value.get("schema") == SCHEMA)


def selected():
    return _active.get()


def manifest_path():
    from ..light.runtime import application
    return ROOT/"light-manifest.json" if application() else MANIFEST


def user_root():
    from ..light.runtime import application, data_root
    if application():
        return data_root()/"cpu"
    value = Path(os.environ.get("LOCALAPPDATA", ""))
    need(value.is_absolute(), "PREVIEW_LOCALAPPDATA_REQUIRED")
    root = writable_path(value / "GradeMirror" / "Preview")
    need(not root.is_relative_to(ROOT), "PREVIEW_PACKAGE_READ_ONLY")
    return root


def architecture():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    api = kernel.IsWow64Process2
    api.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_ushort)]
    api.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    process, native = ctypes.c_ushort(), ctypes.c_ushort()
    need(api(kernel.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)),
         "PREVIEW_WINDOWS_ARCH_UNKNOWN")
    return "AMD64" if (process.value, native.value) == (0, 0x8664) else "UNSUPPORTED"


@functools.lru_cache(maxsize=1)
def package():
    need(sys.platform == "win32" and architecture() == "AMD64"
         and sys.getwindowsversion().build >= POLICY["minimum_build"], "PREVIEW_WINDOWS_X64_REQUIRED")
    m = strict_load(manifest_path())
    from ..light.runtime import application
    if application():
        need(m["schema"]=="gm066-light-package-v1" and m["production_flags"]==FLAGS
             and m["build"]["sha256"]==digest(m["build"]["files"]), "LIGHT_CPU_PACKAGE_CONTRACT")
        return dict(m,operations=["fit","replay","refine","fine-replay"])
    need(m["schema"] == "gm065-preview-package-v1" and m["version"] == VERSION
         and m["operations"] == ["fit", "replay"] and m["quality"] == "fast"
         and m["policy"] == POLICY and m["production_flags"] == FLAGS
         and m["formats"] == {key: list(value) for key, value in FORMATS.items()}, "PREVIEW_PACKAGE_CONTRACT")
    need(m["build"]["sha256"] == digest(m["build"]["files"]), "PREVIEW_BUILD_ID")
    return m


def member(path):
    p = Path(path).absolute()
    reject_reparse(p)
    need(p.resolve() == p and p.is_relative_to(ROOT), "PREVIEW_PACKAGE_MEMBER", str(p))
    row = package()["files"].get(p.relative_to(ROOT).as_posix())
    need(row is not None and p.is_file() and p.stat().st_size == row["bytes"]
         and sha_file(p) == row["sha256"], "PREVIEW_PACKAGE_MEMBER_CHANGED", str(p))
    return p


def interpreter(category):
    return member(ROOT / RUNTIME / category / "python.exe")


def environment(category, scratch=None):
    windir = Path(os.environ["SystemRoot"]).resolve()
    local = user_root()
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "LOCALAPPDATA", "USERPROFILE",
                                            "APPDATA", "OS", "PROCESSOR_ARCHITECTURE") if key in os.environ}
    env.update(PATH=os.pathsep.join(str(p) for p in (ROOT / RUNTIME / category,
               ROOT / RUNTIME / "base" / "DLLs", windir / "System32", windir)),
               CUDA_VISIBLE_DEVICES="-1", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4",
               OPENBLAS_NUM_THREADS="4", NUMEXPR_NUM_THREADS="4",
               TEMP=str(scratch or local), TMP=str(scratch or local))
    return env


def runtime_check(category, *, check_system=True):
    from ..light.runtime import application, operation, bundle_exe
    if application():
        from ..light.system import check_loaded
        active=operation()
        if category in ("numeric","solver"):
            role=active["environment"][active["numeric_profile"] if category=="numeric" else "solver"]
            need(Path(sys.executable).resolve()==Path(role["exe"]),"LIGHT_CPU_WORKER_EXE")
            result=check_loaded(allowed_external={r["path"]:r for r in role["files"]})
        else:
            from ..light import portable
            if category == "desktop" and portable.enabled():
                portable.application_image()
            else:
                need(Path(sys.executable).resolve()==bundle_exe(category),"LIGHT_CPU_CONTROL_EXE")
            result=check_loaded()
        return dict(loaded=result)
    from ..cpu_replay.runtime import loaded_binary_paths
    need(sys.flags.isolated == 1 and not os.environ.get("PYTHONHOME") and not os.environ.get("PYTHONPATH"),
         "PREVIEW_PYTHON_ISOLATION")
    need(Path(sys.executable).resolve() == interpreter(category), "PREVIEW_INTERPRETER_IDENTITY")
    need(all(Path(p).resolve().is_relative_to(ROOT) for p in sys.path), "PREVIEW_SYS_PATH")
    checked = {}
    for module in tuple(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if name and Path(name).is_file():
            p = member(Path(name).resolve())
            checked[p.relative_to(ROOT).as_posix()] = "package"
    system = Path(os.environ["SystemRoot"]).resolve()
    snapshot = loaded_binary_paths()
    need(snapshot["complete"], "PREVIEW_BINARY_ENUMERATION")
    unknown = []
    for name in snapshot["paths"]:
        p = Path(name).resolve()
        if p.is_relative_to(ROOT):
            p = member(p)
            checked[p.relative_to(ROOT).as_posix()] = "package"
        else:
            key = p.relative_to(system).as_posix().lower() if p.is_relative_to(system) else None
            defender = Path(system.anchor) / "ProgramData" / "Microsoft" / "Windows Defender" / "Platform"
            if p.name.casefold() == "mpoav.dll" and p.is_relative_to(defender):
                key = "defender/" + p.relative_to(defender).as_posix().lower()
            expected = package()["system_files"].get(key)
            if expected is None or sha_file(p) not in expected:
                unknown.append(p.name)
            else:
                checked[key] = "exact-system"
    if check_system:
        need(not unknown, "PREVIEW_SYSTEM_BINARY_UNVERIFIED", ", ".join(sorted(set(unknown))))
    return dict(checked=checked, unknown_system=sorted(set(unknown)), architecture=architecture(),
                windows_build=sys.getwindowsversion().build)


def header(value, *, fixture_sha=None, light_plan=None):
    fmt = value["format"].lower()
    width, height, bits = (value[k] for k in ("width", "height", "bits"))
    from ..light.runtime import operation
    selected = operation()
    max_axis, max_pixels = POLICY["max_axis"], POLICY["max_pixels"]
    if selected or light_plan:
        from ..image_limits import MAX_PIXELS
        max_axis = max_pixels = MAX_PIXELS
    from .startup_fixture import allowed as startup_fixture
    fixture = max(width, height) < 512 and startup_fixture(dict(value, sha256=fixture_sha or value.get("sha256")))
    need(fmt in FORMATS and type(bits) is int and bits in FORMATS[fmt]
         and all(type(v) is int and 0 < v <= max_axis for v in (width, height))
         and (512 <= max(width, height) or fixture) and width * height <= max_pixels,
         "PREVIEW_FORMAT_OR_GEOMETRY", "检查 RGB 格式、位深、最长边 512–8192 和每张 2400 万像素上限。")
    return dict(width=width, height=height, bits=bits, format=fmt,
                training_grid=[max(1, round(v * 512 / max(width, height))) for v in (height, width)])


def photo_identity(info):
    return dict(shape=[info["height"], info["width"], 3], dtype=f"uint{info['bits']}",
                bit_depth=info["bits"], file_sha256=info["sha256"], file_size_bytes=info["bytes"],
                pixel_sha256=info.get("pixel_sha256"))


def admission(c, *, facts=None):
    from ..adaptive_execution.memory_snapshot import ram_facts
    import shutil
    source, reference = c["headers"]["source"], c["headers"]["reference"]
    budget = photo_budget(source, reference or (c.get('display_reference') or {}).get('header') or source)
    from ..light.runtime import operation
    plan = operation()
    if plan:
        from ..light.resources import check_host, pool_budget
        need(plan["plan_id"] == c["light_plan"] and plan["selection"]["backend"] == "cpu",
             "LIGHT_CPU_ADMISSION_PLAN_MISSING")
        # The fine child starts after parent release. Training is outside its
        # remaining interval. It still checks the largest remaining stage.
        stage = "startup"
        if c["operation"] == "refine" and plan["operation"] == "fit":
            from ..light.resources import check_cpu_child
            actual = check_cpu_child(c["workspace"], plan, facts=facts)
        else:
            actual = check_host(c["workspace"], plan, stage, point="cpu_inner_admission", facts=facts, retryable=True)
        need(shutil.disk_usage(c["workspace"]).free >= 512*1024**2, "PREVIEW_DISK_NOT_READY", exit_code=5)
        return dict(**actual, envelope=pool_budget(budget, "cpu"), policy=POLICY)
    need(c.get("light_plan") is None and c["mode"] != "fine", "LIGHT_CPU_PLAN_MISSING")
    startup = dict(ram=max(6*GIB,budget["pool"]+GIB),commit=max(16*GIB,budget["pool"]+GIB))
    if facts is None:
        facts=ram_facts()
    sampled_monotonic=time.monotonic()
    ram = facts["physical"]["available_bytes"]
    commit = facts["commit"]["headroom_bytes"]
    minimum_ram, minimum_commit = startup["ram"], startup["commit"]
    if type(ram) is not int or type(commit) is not int or ram<minimum_ram or commit<minimum_commit:
        from ..light.resource_retry import InnerResource,intent
        unknown=type(ram) is not int or type(commit) is not int
        error=InnerResource("PREVIEW_MEMORY_UNKNOWN" if unknown else "PREVIEW_MEMORY_NOT_READY",
                            "关闭占用内存的程序，并检查系统提交余量。", "cpu_inner_admission",
                            [("ram",ram,minimum_ram),("commit",commit,minimum_commit)],
                            reason="RESOURCE_UNKNOWN" if unknown else "CAPACITY_CHANGED")
        if plan:
            proof=error.inner_resource
            proof.update(sampled_monotonic=sampled_monotonic,decision_monotonic=time.monotonic(),intent=intent(plan),plan_id=plan["plan_id"],
                         request_key=plan["request_key"],project=plan["project"])
            atomic_json(Path(c["workspace"])/"inner-admission-rejected.json",proof)
        raise error
    need(shutil.disk_usage(c["workspace"]).free >= 512*1024**2, "PREVIEW_DISK_NOT_READY", exit_code=5)
    return dict(ram=ram, commit=commit, envelope=budget, policy=POLICY)


def context(spec, *, cleanup=False):
    sem = spec.get("semantics", spec)
    verify(sem["instance_context"])
    p = Path(sem["instance_context"]["path"])
    c = strict_load(p)
    m = package()
    current_package = binding(manifest_path())
    package_matches = (c["package"] == current_package or
                       cleanup and all(c["package"][key] == current_package[key] for key in ("bytes", "sha256")))
    need(enabled(c) and c["manifest_path"] == str(p) and c["version"] == VERSION
         and package_matches and c["executor_build"] == m["build"]
         and c["policy"] == POLICY and c["operation"] in m["operations"]
         and c["client"] == sem["preview_client"] and sem["mode"]==c["mode"] and
         (c["mode"]=="fast" if c["operation"] in ("fit","replay") else c["mode"]=="fine" and c.get("light_plan")
          and sem.get("cpu_refine") is True and sem.get("refine_prepare")== (c["operation"]=="refine"))
         and sem["preview_version"] == VERSION and sem.get("TEST_ONLY") is False
         and sem["first_fit"] == (c["operation"] == "fit") and c["source"]["sha256"] == sem["source_sha256"]
         and c["source"]["path"] == sem["source_path"] and c["output_root"] == sem["output_root"]
         and c["bundle"] == sem["bundle_directory"] and c["key"] == sem["preview_key"]
         and c["executor_build"]["sha256"] == sem["executor_build"], "PREVIEW_CONTEXT_MISMATCH")
    w = Path(c["workspace"])
    need(p.parent == w and w.parent == user_root() / "runs"
         and c["output_root"] == str(w) and c["pool_root"] == str(user_root() / "pool")
         and c["lease_seconds"] == POLICY["lease_seconds"] and c["request_seconds"] == 120
         and c["force_grace_seconds"] == 30 and c["prepared_clock_shift"] == 0
         and c["fault_point"] == c["pause_at"] == "", "PREVIEW_CONTEXT_SCOPE")
    from ..engine.profiles import R0, get_profile, settings_for
    profile = {name: get_profile(R0)[name] for name in ("id", "contract_sha256", "core_sha256")}
    need(c["profile"] == profile and c["settings"] == settings_for(R0, c["headers"]["source"]["training_grid"])
         and c["phase"] == ("fit" if c["operation"] == "fit" else "render")
         and (c["reference"] is None) == (c["operation"] in ("replay","fine-replay"))
         and (c["reference_identity"] is None) == (c["operation"] in ("replay","fine-replay"))
         and Path(c["bundle"]).is_absolute() and not Path(c["bundle"]).is_relative_to(ROOT),
         "PREVIEW_PROFILE_OR_ROUTE_CHANGED")
    if c.get("light_plan"):
        render_bundle=Path(c["render_bundle"])
        need(render_bundle.is_absolute() and (render_bundle==Path(c["bundle"]) or c["operation"]=="refine"),
             "LIGHT_CPU_RENDER_BUNDLE")
        if c.get("compatibility"):
            need(c["operation"]=="replay","LIGHT_LEGACY_REPLAY_ONLY")
            verify(c["compatibility"])
    display=c.get("display_reference")
    if display is not None:
        need(type(display) is dict and set(display)=={"file","header"} and c.get("light_plan"),
             "LIGHT_REFERENCE_SCOPE")
        h=display["header"]
        from ..light.runtime import operation, saved_plan_reference
        active = operation()
        if cleanup and (active is None or active["plan_id"] != c["light_plan"]) and c.get("light_plan_record"):
            saved = saved_plan_reference(c["light_plan_record"], plan_id=c["light_plan"], client=c["client"])
            need(h == saved["header"] and display["file"]["sha256"] == saved["sha256"],
                 "LIGHT_REFERENCE_GEOMETRY")
        else:
            need(active and active["plan_id"] == c["light_plan"] and
                 h == {k:active["reference"][k] for k in ("width","height","bits")} and
                 display["file"]["sha256"] == active["reference"]["sha256"], "LIGHT_REFERENCE_GEOMETRY")
    for name in ("source", "reference"):
        if c[name] is not None:
            h = c["headers"][name]
            identity = c[name+"_identity"]
            photo = c[name]
            need(header(h, fixture_sha=identity["file_sha256"], light_plan=c.get("light_plan")) == h
                 and identity["shape"] == [h["height"], h["width"], 3]
                 and identity["dtype"] == f"uint{h['bits']}" and identity["bit_depth"] == h["bits"]
                 and identity["file_sha256"] == photo["sha256"] and identity["file_size_bytes"] == photo["bytes"]
                 and Path(photo["path"]).is_absolute(), "PREVIEW_HEADER_BINDING")
    if c["operation"] == "fit":
        need(c["headers"]["source"]["training_grid"] == c["headers"]["reference"]["training_grid"]
             and c["reference"]["path"] == sem["reference_path"]
             and c["source_identity"]["pixel_sha256"] is None
             and c["reference_identity"]["pixel_sha256"] is None, "PREVIEW_FIT_REQUEST_BINDING")
    if not cleanup:
        from ..winproc import identity_alive
        need(identity_alive(c["client"]) is True, "PREVIEW_CLIENT_EXITED", exit_code=8)
    return c


def scoped_output(path, c):
    p = writable_path(Path(path))
    need(p.is_absolute() and p.is_relative_to(Path(c["workspace"])), "PREVIEW_OUTPUT_SCOPE")
    return p


def argv(role, payload):
    from ..light.runtime import application,worker
    if application():
        work=Path(payload).parent
        return worker("cpu_"+role,(role,payload),work)[0]
    return [str(interpreter(ROLES[role])), "-I", "-B", "-X", "utf8",
            str(member(ROOT / "tools/gm065_entry.py")), role, str(payload)]


def start_job(role, payload, work, memory, *, retain_job=None):
    from ..winproc import OwnedJob, current_identity
    work = Path(work)
    from ..light.runtime import application,worker
    if application():
        from ..light.resources import active_plan
        active = active_plan("cpu")
        need(active is not None, "LIGHT_CPU_PLAN_MISSING")
        jobs = active["selection"]["jobs"]
        if role in ("host", "owner"):
            memory = jobs["pool"]["pool"]
        else:
            memory = {"probe": jobs["probe"], "solver": jobs["solver"],
                      "cpu": jobs["cpu_worker"]}.get(role, jobs["pool"]["compute"])
        args,env=worker("cpu_"+role,(role,payload),work)
    else:
        args,env=argv(role,payload),environment(ROLES[role],work)
    atomic_json(work / "launch.json", dict(argv=args, role=role, owner=current_identity()))
    job = OwnedJob(memory)
    if retain_job is not None:
        retain_job(job)
    try:
        if role == "host" and application():
            from .host_broker import request
            who = request(job, work.parent, active)
            atomic_json(work / "launcher.json", who)
        elif role == "r0" and application():
            events=[]
            def launch_trace(stage, fields):
                events.append(dict(stage=stage, monotonic=time.monotonic(), **fields))
                atomic_json(work/f"{role}-native-launch.json", dict(role=role, events=events))
            from .r0_startup import start
            who=start(job,args,str(ROOT),env,work,launch_trace,
                      cancel=lambda:(work.parent.parent/"cancel.signal").is_file(),
                      role=role, launch_profile="default")
            launch_trace("launcher_write_before", {})
            atomic_json(work / "launcher.json", who)
            launch_trace("launcher_write_returned", {})
        else:
            who = job.spawn(args, str(ROOT), env)
            atomic_json(work / "launcher.json", who)
        return job, who
    except BaseException as error:
        if retain_job is not None or getattr(error,"startup_owns_job",False):
            raise
        if job.child:
            job.terminate()
            job.wait(30000)
            need(job.wait_empty(30000), "PREVIEW_FAILED_JOB_NOT_EMPTY")
        job.close()
        raise


def activate_worker(role, payload):
    from ..common import strict_load
    from ..cpu_replay.contract import worker_authority
    from ..winproc import current_identity, identity_key
    payload = Path(payload).resolve()
    session = payload.parent if role in ("host","owner") else payload.parents[2 if role=="solver" else 1]
    expected = {"owner": session / "session-spec.json", "host": session / "host-payload.json",
                 "fit": session / "fit/payload.json", "r0": session / "R0/payload.json",
                "prepare":session/"P3/payload.json","solver":session/"P3/solver/solver-spec.json",
                "cpu":session/"CPU/payload.json"}[role]
    need(payload == expected, "PREVIEW_WORKER_PAYLOAD_PATH")
    spec = worker_authority(session)
    c = context(spec)
    from ..light.runtime import operation as light_operation, application
    if application():
        active=light_operation()
        need(active and c.get("light_plan")==active["plan_id"] and active["selection"]["backend"]=="cpu",
             "LIGHT_CPU_PLAN_MISMATCH")
    need(enabled(c) and session.parent.is_relative_to(Path(c["workspace"]) / "store"), "PREVIEW_SESSION_SCOPE")
    claim = strict_load(session.parent / "scientific-reservation.json")
    need(claim == dict(schema=SCHEMA, request=spec["semantics"]["instance_context"],
                       job_id=spec["job_id"], attempt_id=spec["attempt_id"], key=c["key"]),
         "PREVIEW_OPERATION_CLAIM")
    need(role != "fit" or c["operation"] == "fit", "PREVIEW_FIT_NOT_REQUESTED")
    if role in ("prepare","solver","cpu"):
        need(c.get("light_plan") and c["mode"]=="fine" and (role=="cpu" or c["operation"]=="refine"),
             "LIGHT_CPU_FINE_WORKER_ROUTE")
    _active.set(c)
    work = session / "host" if role == "host" else payload.parent
    if role in ("fit", "r0","prepare","solver"):
        who = current_identity()
        atomic_json(work / "actual-entry.json", dict(identity=who, role=role))
        until = time.monotonic()+30
        while not (work / "actual-retained.json").is_file():
            need(time.monotonic() < until, "PREVIEW_ACTUAL_HANDLE_ACK")
            time.sleep(.02)
        need(identity_key(strict_load(work / "actual-retained.json")["identity"]) == identity_key(who),
             "PREVIEW_ACTUAL_HANDLE_ID")
    return session
