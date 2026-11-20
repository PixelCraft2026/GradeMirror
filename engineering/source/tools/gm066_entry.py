"""Packaged launcher. Every science worker is reached only from a bound plan."""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if not getattr(sys, "_grademirror_portable", None):
    sys.path.insert(0,str(ROOT/"src"))

from grademirror.common import GMError, atomic_json
from grademirror.cpu_replay.contract import need
from grademirror.light import runtime as rt
from grademirror.light import portable
from grademirror.light.system import packaged, check_loaded

def preload_solver():
    """Resolve SciPy using Windows version data without a child CMD probe."""
    import platform
    from importlib import import_module
    observed = sys.getwindowsversion()
    version = ".".join(map(str, observed.platform_version or observed[:3]))
    original = platform._syscmd_ver
    def native_version(system="", release="", version_unused="", **_):
        return system or "Microsoft Windows", release, version
    platform._syscmd_ver = native_version
    try:
        import_module("grademirror.engine.independent_r0_p3_lld10.p3_solver")
    finally:
        platform._syscmd_ver = original


def worker(role,pointer,args):
    category, script=rt.SCOPES[role]
    if pointer=="-":
        need(role=="headers","LIGHT_PLAN_REQUIRED")
        value=None
    else:
        value=rt.read_plan(pointer)
        selected=value["selection"]["backend"]
        allowed={"gpu":{"owner","host","gpu","solver","headers"},
                 "cpu":{"cpu_owner","cpu_host","cpu_fit","cpu_r0","cpu_probe","cpu_prepare","cpu_cpu","cpu_solver","headers"},
                 "mixed":{"owner","host","mixed","solver","headers"}}[selected]
        need(role in allowed,"LIGHT_PLAN_WORKER_ROLE")
        if role!="headers":
            work=Path(args[-1]).resolve()
            need(work.is_relative_to(rt.data_root()),"LIGHT_WORKER_OUTSIDE_DATA")
    if category in ("control","io"):
        need(Path(sys.executable).resolve()==rt.bundle_exe(category),"LIGHT_PACKAGED_INTERPRETER")
        packaged(sys.executable)
        check_loaded()
    else:
        profile=value["numeric_profile"] if category=="numeric" else category
        row=value["environment"][profile]
        def r0_stage(stage, module=None):
            if role=="cpu_r0":
                import time
                from grademirror.winproc import current_identity
                atomic_json(work.parent/"r0-role-stage.json",dict(stage=stage,module=module,
                    identity=current_identity(),monotonic=time.monotonic()))
        r0_stage("inventory_before")
        exe=rt.verify_role(row,profile,full=True)
        r0_stage("inventory_verified")
        need(Path(sys.executable).resolve()==exe,"LIGHT_WORKER_EXE_CHANGED")
        rt.attach_external(exe)
        from importlib import import_module
        names=("numpy","scipy") if category=="solver" else ("numpy","torch","cv2","PIL")
        allowed_files={r["path"]:r for r in row["files"]}
        for name in names:
            r0_stage("import_before",name)
            mod=import_module(name)
            r0_stage("import_returned",name)
            need(str(Path(mod.__file__).resolve()) in allowed_files,"LIGHT_EXTERNAL_ORIGIN",name)
        if category == "solver":
            preload_solver()
        check_loaded(allowed_external=allowed_files)
        r0_stage("dll_trust_verified")
    executable=packaged(ROOT/"tools"/script)
    from grademirror.light.gm085_barrier import preflight
    if preflight(role,value,args,executable):
        return 0
    sys.argv=[str(executable),*args]
    from grademirror.light.worker_qualification import worker as qualification_worker
    with rt.scope(value), qualification_worker(role,value,args):
        portable.run_worker(executable)
    return 0

def main():
    rt.enable_application()
    if sys.argv[1]=="probe":
        _,_,profile,path=sys.argv
        directory = Path(path)
        if portable.enabled():
            from grademirror.common import reject_reparse
            need(directory.is_absolute() and directory.parent == rt.data_root() / "probes"
                 and directory.is_dir() and profile == portable.state().role, "PORTABLE_PROBE_SCOPE")
            reject_reparse(directory)
        result=directory/"result.json"
        try:
            if portable.enabled() and profile in ("control", "io"):
                if profile == "io":
                    from grademirror.engine import group15_codec
                row = dict(profile=profile, loaded=check_loaded(), model_load=0, photo_decode=0, cuda_context=0)
                if profile == "io" and (directory/"metadata-probe.json").is_file():
                    from grademirror.light.metadata_probe import run as metadata_probe
                    row["metadata"] = metadata_probe(directory)
            else:
                row=rt.probe(profile)
            atomic_json(result,dict(status="READY",role=row))
            return 0
        except Exception as e:
            atomic_json(result,dict(status="REFUSED",error=str(e),code=getattr(e,"code",type(e).__name__)))
            return 1
    if sys.argv[1]=="worker":
        try:
            return worker(sys.argv[2],sys.argv[3],sys.argv[4:])
        except Exception as error:
            scratch=Path(os.environ.get("TEMP",""))
            if sys.argv[2] in ("cpu_fit","cpu_r0","cpu_prepare","cpu_cpu"):
                payload=Path(sys.argv[-1])
                if payload.is_absolute():
                    scratch=payload.parent
            if scratch.is_absolute() and scratch.is_dir() and scratch.is_relative_to(rt.data_root()):
                from grademirror.common import reject_reparse
                reject_reparse(scratch)
                atomic_json(scratch/"light-entry-error.json",
                            dict(code=getattr(error,"code",type(error).__name__),error=str(error)))
            raise
    if sys.argv[1]=="pick-file":
        from grademirror.light.system import packaged
        packaged(ROOT/"src/grademirror/light/native_picker.py")
        from grademirror.light.native_picker import main as pick
        return pick(Path(sys.argv[2]))
    if sys.argv[1]=="desktop":
        from grademirror.light.desktop import main as run
        return run()
    if sys.argv[1]=="cli":
        from grademirror.light.cli import main as run
        return run(sys.argv[2:])
    raise GMError("LIGHT_ENTRY_UNKNOWN","Unrecognized preview entry")

if __name__=="__main__":
    raise SystemExit(main())

