"""Explicit role selection and immutable external interpreter receipts."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import base64
import stat
import os
import sys
import time
import uuid

from ..common import atomic_json, reject_reparse, sha_file, strict_load, writable_path
from ..cpu_replay.contract import binding, digest, need, verify
from . import portable

ROOT = Path(__file__).resolve().parents[3]
VERSION = "0.1.0-preview.50"
# Preserve the schema version of saved execution plans across distribution releases.
PLAN_VERSION = "0.1.0-preview.29"
BUNDLED = "runtime" if portable.enabled() else ".runtime/gm059-v1"
ENTRY = ROOT / "tools/gm066_entry.py"
SCHEMA = "gm066-environment-v1"
EXTERNAL = ("cpu", "cu124", "solver")
SCOPES = {
    "owner": ("control", "gm003_session_owner.py"),
    "host": ("io", "gm003_session_host.py"),
    "headers": ("io", "gm_photo_probe.py"),
    "gpu": ("cu124", "gm003_compute_worker.py"),
    "solver": ("solver", "gm008_solver_worker.py"),
    "mixed": ("cu124", "gm023_compute_worker.py"),
    "cpu_owner": ("control", "gm065_entry.py"),
    "cpu_host": ("io", "gm065_entry.py"),
    "cpu_fit": ("numeric", "gm065_entry.py"),
    "cpu_r0": ("numeric", "gm065_entry.py"),
    "cpu_prepare": ("numeric", "gm065_entry.py"),
    "cpu_cpu": ("numeric", "gm065_entry.py"),
    "cpu_solver": ("solver", "gm065_entry.py"),
    "cpu_probe": ("io", "gm065_entry.py"),
}
_active = ContextVar("gm066-preview", default=None)
_science_boundary = ContextVar("preview-science-boundary", default=None)
_application = False


def enable_application():
    global _application
    _application = True


def application():
    return _application


def data_root():
    path = Path(os.environ.get("LOCALAPPDATA", ""))
    need(path.is_absolute(), "LIGHT_LOCALAPPDATA_REQUIRED")
    path = writable_path(path / "GradeMirror" / "LightPreview")
    need(not path.is_relative_to(ROOT), "LIGHT_PACKAGE_READ_ONLY")
    return path


def lock(profile):
    need(profile in EXTERNAL, "LIGHT_PROFILE_UNKNOWN")
    location = ROOT / "tools/gm066/locks" / (profile + ".json")
    row = strict_load(location)
    need(row["profile"] == profile and sha_file(location.with_suffix(".txt")) == row["lock_sha256"], "LIGHT_LOCK_CHANGED")
    return row


def bundle_exe(role):
    if portable.enabled():
        return portable.executable(role)
    return ROOT / BUNDLED / role / "python.exe"


def windows_program_files():
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion",0,
            winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
        value,kind=winreg.QueryValueEx(key,"ProgramFilesDir")
    folder=Path(value)
    need(kind==winreg.REG_SZ and folder.is_absolute() and folder.is_dir(),"LIGHT_PROGRAM_FILES_UNKNOWN")
    reject_reparse(folder)
    return str(folder.resolve(strict=True))


def launch_env(exe, scratch, *, gpu=False, device=None):
    windows = Path(os.environ["SystemRoot"]).resolve()
    env = {k: os.environ[k] for k in ("SystemRoot","WINDIR","LOCALAPPDATA","APPDATA","USERPROFILE",
                                     "ProgramData","OS","PROCESSOR_ARCHITECTURE") if k in os.environ}
    if sys.platform=="win32":
        env["ProgramFiles"]=windows_program_files()
    env.update(PATH=os.pathsep.join(map(str,(exe.parent, windows/"System32", windows))),
               CUDA_VISIBLE_DEVICES=(device or {}).get("uuid", "0") if gpu else "-1", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4",
               OPENBLAS_NUM_THREADS="4", NUMEXPR_NUM_THREADS="4",
               TEMP=str(scratch), TMP=str(scratch))
    return env


def _registry():
    path = data_root() / "environment.json"
    if not path.exists():
        return {}
    row = strict_load(path)
    need(row["schema"] == SCHEMA and set(row["roles"]) <= set(EXTERNAL), "LIGHT_REGISTRY_SCHEMA")
    return row["roles"]


def snapshot():
    from .system import manifest, packaged
    package = manifest()
    descriptors = package.get("bundled_roles")
    if descriptors is None:
        return _registry()
    expected = {"cpu", "solver"} | ({"cu124"} if package["edition"] == "gpu" else set())
    need(package["edition"] in ("cpu", "gpu") and set(descriptors) == expected,
         "LIGHT_OFFLINE_ROLE_SET")
    rows = {}
    for profile, desc in descriptors.items():
        need(desc["profile"] == profile and desc["lock"] == lock(profile)["lock_sha256"],
             "LIGHT_OFFLINE_ROLE_LOCK")
        prefix = f"{BUNDLED}/{profile}/"
        expected_exe = bundle_exe(profile).relative_to(ROOT).as_posix()
        need(desc["exe"] == expected_exe and all(
             (name.startswith(prefix) or name == expected_exe) and name in package["files"] and
             not any(part in ("", ".", "..") for part in name.split("/")) and
             not any(separator in name for separator in (chr(92), ":"))
             for name in desc["files"]), "LIGHT_OFFLINE_ROLE_SCOPE")
        records = {name: package["files"][name] for name in desc["files"]}
        need(len(records) == len(desc["files"]) and digest(records) == desc["inventory_sha256"]
             and set(desc["identity"]) <= records.keys() and desc["exe"] in desc["identity"],
             "LIGHT_OFFLINE_ROLE_INVENTORY")
        def absolute_record(name):
            return dict(records[name], path=str(ROOT / name))
        if not portable.enabled():
            packaged(ROOT / (prefix + "python312._pth"))
        rows[profile] = dict(desc, exe=str(ROOT / desc["exe"]),
                             identity=[absolute_record(name) for name in desc["identity"]],
                             files=[absolute_record(name) for name in desc["files"]],
                             gpu=dict(available=True, cuda="12.4",
                                      compiled_architectures=desc["compiled_architectures"],
                                      status="BUNDLED_DRIVER_NOT_CHECKED") if profile == "cu124" else None)
    return rows


def operation():
    return _active.get()


def science_boundary():
    return _science_boundary.get()


@contextmanager
def scope(value, *, before_science=None):
    token = _active.set(value)
    guard = _science_boundary.set(before_science)
    try:
        yield
    finally:
        _science_boundary.reset(guard)
        _active.reset(token)


def verify_role(row, profile, *, full=False):
    need(row["profile"] == profile and row["lock"] == lock(profile)["lock_sha256"], "LIGHT_PROFILE_RESELECT")
    for record in row["identity"]:
        verify(record)
    if full:
        checked_dirs=set()
        for record in row["files"]:
            path=Path(record["path"])
            if path.parent not in checked_dirs:
                reject_reparse(path.parent)
                checked_dirs.add(path.parent)
            info=path.stat(follow_symlinks=False)
            need(not info.st_file_attributes & 0x400 and stat.S_ISREG(info.st_mode)
                 and info.st_size==record["bytes"] and sha_file(path)==record["sha256"],
                 "LIGHT_EXTERNAL_FILE_CHANGED",path.name)
    return Path(row["exe"])


def cpu_native_scratch(scratch):
    # Torch DLL initialization needs native temporary-file operations on Win32.
    # Keep these separate from the durable, deeply nested operation receipts.
    parent = writable_path(data_root() / "_rt")
    leaf = digest(str(Path(scratch).resolve()))[:12]
    folder = writable_path(parent / leaf)
    need(len(str(folder)) <= 180, "LIGHT_CPU_NATIVE_TEMP_PATH_TOO_LONG",
         "CPU 运行时需要较短的私有临时目录。", 5)
    folder.mkdir(parents=True, exist_ok=True)
    reject_reparse(folder)
    return folder


def worker(role, args, scratch, *, value=None):
    capability = value if value is not None else operation()
    need(capability is not None, "LIGHT_PREVIEW_BINDING_REQUIRED")
    category, script = SCOPES[role]
    external = (capability.get("numeric_profile") if category == "numeric" else category)
    if category in ("control", "io"):
        exe = bundle_exe(category)
        pointer = "-"
    else:
        need(external in capability["environment"], "LIGHT_ENVIRONMENT_REQUIRED", str(external))
        row = capability["environment"][external]
        exe = verify_role(row, external)
        pointer = capability["plan_path"]
    if category in ("control", "io") and capability.get("plan_path"):
        pointer = capability["plan_path"]
    argv = portable.argv(exe, ENTRY, "worker", role, pointer, *args, external=category not in ("control", "io"))
    gpu = role == "gpu" or role == "mixed" and args[0] == "R0"
    if application() and (role == "cpu_host" or category == "numeric" and external == "cpu"):
        scratch = cpu_native_scratch(scratch)
    return argv,launch_env(exe, scratch, gpu=gpu, device=capability.get("device"))


def headers_scope():
    return operation() or (dict(environment={}, plan_path=None) if application() else None)


def prepare_probe(exe, profile):
    from ..winproc import OwnedJob
    from ..cpu_replay.runtime import wait
    from ..adaptive_execution.memory_snapshot import ram_facts
    exe = Path(exe).absolute()
    reject_reparse(exe)
    exe = exe.resolve(strict=True)
    need(profile in EXTERNAL and exe.is_file() and (
         exe == bundle_exe(profile) if portable.enabled() else exe.name.casefold() == "python.exe"),
         "LIGHT_SELECT_PYTHON")
    facts = ram_facts()
    from .system import manifest
    bundled = manifest().get("bundled_roles") is not None
    ram_floor = 1280 * 1024**2 if bundled else 7 * 1024**3
    commit_floor = 2560 * 1024**2 if bundled else 7 * 1024**3
    need((facts["physical"]["available_bytes"] or 0) >= ram_floor
         and (facts["commit"]["headroom_bytes"] or 0) >= commit_floor,
         "LIGHT_PROBE_MEMORY_NOT_READY", exit_code=5)
    work = data_root() / "probes" / uuid.uuid4().hex
    work.mkdir(parents=True)
    job = OwnedJob(6*1024**3)
    try:
        job.spawn(portable.argv(exe, ENTRY, "probe", profile, work, external=True),
                  str(ROOT),launch_env(exe,work,gpu=profile=="cu124"))
        receipt = wait(job,work,timeout=120)
        result = strict_load(work / "result.json")
        need(receipt["exit_code"] == 0 and result["status"] == "READY", "LIGHT_ENVIRONMENT_REFUSED",
             str(result.get("error")))
        need(verify_role(result["role"],profile) == exe, "LIGHT_PROBE_EXECUTABLE_CHANGED")
        return result["role"]
    finally:
        if job.child and job.poll() is None:
            job.terminate()
            wait(job,work,timeout=30)
        job.close()


def register(exe, profile):
    from ..store import locked
    from .system import manifest
    need(manifest().get("bundled_roles") is None, "LIGHT_OFFLINE_ROLES_FIXED")
    row = prepare_probe(exe,profile)
    root = data_root()
    with locked(root/"execution-admit.lock",timeout=2), locked(root/"environment.lock",timeout=2):
        for pool in (root/"jobs/gm004a-resident/reservation.json", root/"cpu/pool/reservation.json"):
            need(not pool.is_file() or strict_load(pool)["state"] in ("FREE","RELEASED"),
                 "LIGHT_RELEASE_BEFORE_SWITCHING_ENVIRONMENT")
        roles = _registry()
        roles[profile] = row
        atomic_json(root/"environment.json",dict(schema=SCHEMA,roles=roles))
    return {k:row[k] for k in ("profile","python","versions","gpu","lock")}


def _site(exe):
    return exe.parent.parent/"Lib/site-packages" if exe.parent.name.casefold()=="scripts" else exe.parent/"Lib/site-packages"


def attach_external(exe):
    if portable.enabled():
        role = portable.state().role
        need(Path(exe) == bundle_exe(role), "LIGHT_WORKER_EXE_CHANGED")
        site = ROOT / BUNDLED / role / "Lib/site-packages"
        need(str(site) in sys.path and site.is_dir(), "LIGHT_ISOLATION_REQUIRED")
        return site
    site = _site(Path(exe))
    reject_reparse(site)
    need(site.is_dir() and sys.flags.isolated and sys.flags.no_site, "LIGHT_ISOLATION_REQUIRED")
    need(not os.environ.get("PYTHONPATH") and not os.environ.get("PYTHONHOME"), "LIGHT_PYTHON_ENVIRONMENT")
    sys.path.append(str(site))
    return site


def probe(profile):
    from importlib import import_module, metadata
    from .system import check_loaded
    exe=Path(sys.executable).resolve()
    from ..preview.contract import architecture
    need(sys.version_info[:3] in ((3,12,10),(3,12,14)) and sys.platform=="win32"
         and sys.getwindowsversion().build>=17763 and architecture()=="AMD64",
         "LIGHT_PYTHON_BUILD_REQUIRED")
    site=attach_external(exe)
    model=lock(profile)
    bundled = (snapshot().get(profile) if exe == bundle_exe(profile) else None)
    if bundled is not None:
        return probe_bundled(bundled, profile)
    files,versions={},{}
    checked_dirs=set()
    identity={exe,Path(sys._base_executable).resolve()}
    base=Path(sys.base_prefix).resolve()
    for p in (base/"python312.dll",exe.parent.parent/"pyvenv.cfg"):
        if p.is_file():
            identity.add(p)
    for item in model["rows"]:
        dist=metadata.distribution(item["name"])
        need(dist.version==item["version"] and dist.files is not None,"LIGHT_WHEEL_VERSION",item["name"])
        versions[item["name"]]=dist.version
        for entry in dist.files:
            p=Path(dist.locate_file(entry)).absolute()
            if p.suffix in (".pyc",".pyo"):
                continue
            p=Path(os.path.normpath(p))
            need(p.is_relative_to(site.parent.parent),"LIGHT_WHEEL_FILE_SCOPE",p.name)
            if p.parent not in checked_dirs:
                reject_reparse(p.parent)
                checked_dirs.add(p.parent)
            info=p.stat(follow_symlinks=False)
            need(not info.st_file_attributes & 0x400 and stat.S_ISREG(info.st_mode),
                 "LIGHT_WHEEL_FILE_TYPE",p.name)
            record=dict(path=str(p),bytes=info.st_size,sha256=sha_file(p))
            if entry.hash is not None and entry.hash.mode=="sha256":
                got=base64.urlsafe_b64encode(bytes.fromhex(record["sha256"])).decode().rstrip("=")
                need(got==entry.hash.value,"LIGHT_WHEEL_RECORD_CHANGED",p.name)
            files[str(p)]=record
            if p.name in ("METADATA","RECORD","WHEEL"):
                identity.add(p)
    gpu=None
    module_names=("numpy","scipy") if profile=="solver" else ("numpy","torch","cv2","PIL")
    for name in module_names:
        loaded=import_module(name)
        need(str(Path(loaded.__file__).resolve()) in files,"LIGHT_IMPORT_ORIGIN",name)
    if profile=="cu124":
        import torch
        available=torch.cuda.is_available()
        count=torch.cuda.device_count() if available else 0
        capability=tuple(torch.cuda.get_device_capability(0)) if count==1 else None
        compiled=sorted(torch.cuda.get_arch_list()) if count==1 else []
        # A lower/equal native SM minor in the same generation can run
        # directly. Avoid inferring support from PTX or across generations.
        supported=bool(capability and any(
            tag.startswith("sm_") and tag[3:].isdigit() and
            divmod(int(tag[3:]),10)[0]==capability[0] and int(tag[-1])<=capability[1]
            for tag in compiled))
        gpu=dict(available=available and count==1 and supported, cuda=torch.version.cuda,
                 count=count, compute_capability=capability, compiled_architectures=compiled,
                 reason=None if supported else "NVIDIA_DEVICE_OR_COMPILED_ARCH_UNQUALIFIED")
        need(gpu["cuda"]=="12.4", "LIGHT_CUDA_BUILD")
    native = {str(p.resolve()):binding(p) for p in identity}
    for dll in (*base.glob("*.dll"),*(base/"DLLs").glob("*.pyd"),*(base/"DLLs").glob("*.dll")):
        native[str(dll.resolve())]=binding(dll)
    files.update(native)
    check_loaded(allowed_external=files)
    return dict(profile=profile, exe=str(exe), python=".".join(map(str,sys.version_info[:3])),
                lock=model["lock_sha256"], versions=versions, gpu=gpu,
                identity=[binding(Path(p)) for p in sorted(identity,key=str)], files=list(files.values()))


def probe_bundled(row, profile):
    from importlib import import_module, metadata
    from .system import check_loaded

    verify_role(row, profile, full=True)
    versions = {item["name"]: metadata.version(item["name"]) for item in lock(profile)["rows"]}
    need(versions == row["versions"], "LIGHT_OFFLINE_WHEEL_VERSION")
    files = {r["path"]: r for r in row["files"]}
    names = ("numpy", "scipy") if profile == "solver" else ("numpy", "torch", "cv2", "PIL")
    for name in names:
        mod = import_module(name)
        need(str(Path(mod.__file__).resolve()) in files, "LIGHT_IMPORT_ORIGIN", name)
    if profile != "solver":
        import torch
        cuda = torch.version.cuda
        need(cuda == ("12.4" if profile == "cu124" else None), "LIGHT_CUDA_BUILD")
    check_loaded()
    return row


def _read_frozen_plan(path):
    path = Path(path)
    reject_reparse(path)
    need(path.is_absolute() and path.is_relative_to(data_root()/"plans") and path.parent==data_root()/"plans",
         "LIGHT_PLAN_PATH")
    value=strict_load(path)
    need(value["schema"]=="gm066-preview-plan-v1" and value["version"]==PLAN_VERSION
         and value["plan_path"]==str(path) and value["plan_id"]==digest({k:v for k,v in value.items() if k!="plan_id"}),
         "LIGHT_PLAN_ID")
    from .system import manifest
    need(value["build"]==manifest()["build"]["sha256"],"LIGHT_PLAN_BUILD_CHANGED")
    from .resources import selected
    selected(value)
    return value


def read_plan(path):
    from ..winproc import identity_alive
    value = _read_frozen_plan(path)
    need(identity_alive(value["client"]) is True, "LIGHT_PLAN_CLIENT_LOST")
    return value


def saved_plan_reference(record, *, plan_id, client):
    """Validate saved reference facts for reclamation without activating stale authority."""
    from ..cpu_replay.contract import verify
    verify(record)
    value = _read_frozen_plan(record["path"])
    need(value["plan_id"] == plan_id and value["client"] == client and
         value["selection"]["backend"] == "cpu", "LIGHT_CLEANUP_PLAN_ID")
    ref = value["reference"]
    return dict(header={k: ref[k] for k in ("width", "height", "bits")}, sha256=ref["sha256"])


