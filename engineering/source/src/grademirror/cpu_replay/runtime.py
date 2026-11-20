"""Instance-scoped launch accounting and observations over existing OwnedJob."""
import hashlib
import os
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from ..common import atomic_json, strict_load
from ..store import locked
from ..winproc import OwnedJob, current_identity, identity_alive, identity_key
from .contract import ROOT, authority, need, binding, verify

_entered = False
_writes = set()
_reads = set()
_devices = set()
_denied = []


def execution_authority():
    from .lifecycle import selected_context
    c = selected_context()
    return c if c and c.get('schema') in ('cpu-first-fit-execution-v1','cpu-refine-execution-v1','cpu-application-execution-v1','cpu-application-execution-v2','cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5') else authority()


def enter(role, work, *, entry_only=False, metadata_cleanup=False):
    from .lifecycle import selected_context
    application=selected_context()
    from ..preview.contract import enabled as preview_enabled, runtime_check
    if preview_enabled(application):
        return runtime_check('control' if role == 'owner' else 'io')
    if application and application.get('schema')=='cpu-application-execution-v4':
        from ..adaptive_execution.first_runtime import enter as first_enter
        return first_enter(role,work,application)
    if application and application.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v5'):
        from ..adaptive_execution.native_runtime import enter as native_enter
        return native_enter(role,work,application)
    if application and application.get('schema') in ('cpu-application-execution-v1','cpu-application-execution-v2'):
        from ..adaptive_execution.runtime import enter as application_enter
        return application_enter(role,work,application)
    global _entered
    if _entered:
        return
    _entered = True
    import platform
    platform._syscmd_ver = lambda system='', release='', version='', supported_platforms=None: (
        'Microsoft', 'Windows', '.'.join(map(str, sys.getwindowsversion().platform_version)))
    from .lifecycle import selected_context
    context = selected_context()
    work = Path(work)
    if context:
        need(work.resolve().is_relative_to(Path(context['output_root'])) or
             work.resolve().is_relative_to(Path(context['evidence_root'])), 'INSTANCE_WORK_SCOPE')
    work.mkdir(parents=True, exist_ok=True)
    old = ('/tests/workspaces/g49/', '/tests/workspaces/g50/', '/tests/workspaces/g51/',
           '/docs/evidence/gm-049/', '/docs/evidence/gm-050/', '/docs/evidence/gm-051/')
    roots = [Path((context or authority())[key]).resolve() for key in ('output_root', 'evidence_root')]
    if context:
        # The index validates this separate instance pool; never use old W/E.
        roots.append(Path(context['pool_root']).resolve())
        old += ('/tests/workspaces/g52/', '/docs/evidence/gm-052/')
        old += ('/tests/workspaces/g52r1/', '/docs/evidence/gm-052-r1/')
    inputs = []
    if context and context.get('schema') in ('cpu-first-fit-execution-v1','cpu-refine-execution-v1'):
        inputs = [Path(context[k]['path']).resolve() for k in ('source', 'reference') if context[k]]
    research = (ROOT.parent / 'color-matching-research').resolve()
    environment_roots = [Path(authority()['interpreters'][key]['path']).resolve().parents[1] for key in ('numeric', 'io')]
    if context and context.get('schema')=='cpu-refine-execution-v1':
        environment_roots.append(Path(context['interpreters']['solver']['path']).resolve().parents[1])
        old += ('/tests/workspaces/g53/','/docs/evidence/gm-053/')
    def audit(event, args):
        if metadata_cleanup and event=='import' and args and args[0].split('.')[0] in ('torch','numpy','PIL','cv2','pynvml','pycuda'):
            raise ImportError('CPU_METADATA_CLEANUP_SCIENCE_IMPORT_FORBIDDEN')
        if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
            if os.name == 'nt' and os.fsdecode(args[0]).casefold() == 'nul':
                _devices.add('WINDOWS_NUL_DEVICE')
                return
            p = Path(os.fsdecode(args[0])).absolute()
            text = str(p).replace('\\', '/').lower()
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            write = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if any(piece in text for piece in old):
                _denied.append(text)
                raise PermissionError('OLD_DATA_READ_FORBIDDEN:' + text)
            if not write:
                resolved = p.resolve()
                forbidden_research = resolved.is_relative_to(research) and not any(resolved.is_relative_to(env) for env in environment_roots)
                forbidden_reference = resolved.is_relative_to(ROOT / 'projects') and resolved not in inputs
                forbidden_issuance = '/docs/evidence/gm-052/20261030-plan-v1/' in text
                if forbidden_research or forbidden_reference or forbidden_issuance:
                    _denied.append(text)
                    raise PermissionError('NON_RUNTIME_DATA_READ_FORBIDDEN:'+text)
                if context and context.get('schema')=='cpu-refine-execution-v1' and context['phase']=='render':
                    task_workspace=ROOT/'tests/workspaces/g54/20261030-v1'
                    bundle_root=Path(context['bundle']['directory']).resolve()
                    if resolved.is_relative_to(task_workspace) and not (
                        resolved.is_relative_to(Path(context['output_root'])) or
                        resolved.is_relative_to(Path(context['pool_root'])) or
                        resolved.is_relative_to(bundle_root) or resolved in inputs):
                        _denied.append(text)
                        raise PermissionError('COLD_PRODUCER_DATA_READ_FORBIDDEN:'+text)
                if context and context.get('schema') == 'cpu-first-fit-execution-v1' and context['phase'] == 'render':
                    task_workspace = ROOT / 'tests/workspaces/g53/20261030-v1'
                    bundle_root = Path(context['bundle']['directory']).resolve()
                    if resolved.is_relative_to(task_workspace) and not (
                        resolved.is_relative_to(Path(context['output_root'])) or resolved.is_relative_to(Path(context['pool_root'])) or
                        resolved.is_relative_to(bundle_root) or resolved in inputs):
                        _denied.append(text)
                        raise PermissionError('COLD_PRODUCER_DATA_READ_FORBIDDEN:'+text)
                _reads.add(str(p))
            if write:
                need(any(p.resolve().is_relative_to(root) for root in roots), 'CPU_REPLAY_WRITE_SCOPE', str(p))
                _writes.add(str(p))
        if event == 'import' and args and ('pynvml' in args[0] or args[0].startswith('pycuda')):
            _denied.append('import:' + args[0])
            raise ImportError('CPU_GPU_MODULE_FORBIDDEN')
        if context and context.get('metadata_only', True) and event == 'import' and args and (args[0] == 'torch' or args[0].startswith('torch.')):
            raise ImportError('R1_ZERO_MODEL_RUNTIME')
        if event == 'ctypes.dlopen' and any(n in str(args[0]).lower() for n in ('nvcuda', 'cudart', 'nvml')):
            raise RuntimeError('CPU_GPU_DLL_FORBIDDEN')
        if event in ('os.system', 'os.spawn', 'os.posix_spawn'):
            raise RuntimeError('CPU_UNDECLARED_PROCESS')
        if event == 'subprocess.Popen':
            if Path(str(args[0])).name.lower() not in ('python.exe','pythonw.exe'):
                from ..export_metadata import helper_argv_allowed
                need(helper_argv_allowed(args[0],args[1]), 'CPU_UNDECLARED_EXECUTABLE')
        if event in ('os.rename', 'os.remove', 'os.mkdir', 'os.rmdir'):
            targets = args[:2] if event == 'os.rename' else args[:1]
            for target in targets:
                if isinstance(target, (str, bytes, os.PathLike)):
                    p = Path(os.fsdecode(target)).absolute()
                    need(any(p.resolve().is_relative_to(root) for root in roots), 'CPU_REPLAY_WRITE_SCOPE')
                    _writes.add(str(p))
    sys.addaudithook(audit)
    atomic_json(work / 'actual-entry.json', dict(identity=current_identity(), role=role))
    if entry_only or (context and context.get('metadata_only', True)):
        def profile(frame, event, arg):
            if event == 'call':
                file = frame.f_code.co_filename.replace('\\', '/')
                name = frame.f_code.co_name
                if ((file.endswith('/independent_r0/core.py') and name in ('__init__', 'forward', 'render_tiles')) or
                    (file.endswith('/cpu_postprocess/executor.py') and name == 'execute') or
                    (file.endswith('/group15_codec.py') and name in (('read_source', 'read_reference') if context else ('read_source', 'read_reference', 'write_jpg'))) or
                    (context and file.endswith('/cpu_postprocess/service.py') and name == 'worker') or
                    (file.endswith('/cpu_r0_image/executor.py') and name == '_fit_loop')):
                    raise RuntimeError('ENTRY_ONLY_SCIENCE_OR_IMAGE_CALL')
        sys.setprofile(profile)
        if context and context.get('schema')=='cpu-refine-execution-v1':
            from ..cpu_refine.runtime import zero_science_guard
            zero_science_guard()
    observer = os.environ.get('GM052_OBSERVER_ROOT')
    if observer:
        entry = Path(observer) / 'entries' / uuid.uuid4().hex
        entry.mkdir(parents=True)
        atomic_json(entry / 'entry.json', dict(identity=current_identity(), role=role, work=str(work), entry_only=entry_only))
        deadline = time.monotonic() + 30
        while not (entry / 'ack.json').is_file():
            need(time.monotonic() < deadline, 'ENTRY_ACTUAL_HANDLE_ACK')
            time.sleep(.01)
    if role == 'r0' or role.startswith('entry_') or (context and context.get('schema')=='cpu-refine-execution-v1' and role in ('prepare','solver','small')):
        until = time.monotonic()+30
        while not (work / 'actual-retained.json').is_file():
            need(time.monotonic() < until, 'PARENT_ACTUAL_HANDLE_ACK')
            time.sleep(.01)


def observation(work, role, status):
    from .lifecycle import selected_context
    c=selected_context()
    from ..preview.contract import enabled as preview_enabled, runtime_check, ROLES
    if preview_enabled(c):
        atomic_json(Path(work) / 'runtime-observation.json',
                    dict(role=role, status=status, **runtime_check(ROLES[role])))
        return
    if c and c.get('schema')=='cpu-application-execution-v4':
        from ..adaptive_execution.first_runtime import observation as first_observation
        return first_observation(work,role,status)
    if c and c.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v5'):
        from ..adaptive_execution.native_runtime import observation as native_observation
        return native_observation(work,role,status)
    if c and c.get('schema') in ('cpu-application-execution-v1','cpu-application-execution-v2'):
        from ..adaptive_execution.runtime import observation as application_observation
        return application_observation(work,role,status)
    sys.setprofile(None)
    modules = []
    for name, module in sorted(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if path and Path(path).is_file() and Path(path).suffix.lower() in ('.py', '.pyd', '.dll'):
            modules.append(dict(module=name, **binding(path)))
    binaries = loaded_binaries()
    atomic_json(Path(work) / 'runtime-observation.json', dict(
        role=role, status=status, identity=current_identity(), modules=modules,
        binaries=binaries,
        writes=sorted(_writes), reads=sorted(_reads), windows_devices=sorted(_devices), denied=_denied, cuda_initialized=False,
        actual_cuda_state='NOT_IMPORTED' if 'torch' not in sys.modules else sys.modules['torch'].cuda.is_initialized()))


def runtime_snapshot_names(identifier):
    """Lossless 128-bit UUID spelling; 30 ASCII units per diagnostic leaf."""
    import base64
    value=identifier if isinstance(identifier,uuid.UUID) else uuid.UUID(str(identifier))
    token=base64.urlsafe_b64encode(value.bytes).rstrip(b'=').decode('ascii')
    need(len(token)==22,'RUNTIME_SNAPSHOT_TOKEN_LENGTH')
    return {kind:prefix+token+'.json' for kind,prefix in
            (('paths','rp-'),('unknown','ru-'),('final','rf-'),('claim','rc-'))}


def loaded_binary_paths():
    """Collect every raw module path before opening any module file."""
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    ps = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    handle = kernel.GetCurrentProcess()
    modules = (w.HMODULE*4096)()
    size = w.DWORD()
    ps.EnumProcessModulesEx.argtypes = [w.HANDLE, ctypes.POINTER(w.HMODULE), w.DWORD, ctypes.POINTER(w.DWORD), w.DWORD]
    ps.GetModuleFileNameExW.argtypes = [w.HANDLE, w.HMODULE, w.LPWSTR, w.DWORD]
    ok=bool(ps.EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules), ctypes.byref(size), 3))
    error=None if ok else ctypes.get_last_error()
    rows=[];errors=[];reported=size.value//ctypes.sizeof(w.HMODULE)
    for module in modules[:min(reported,len(modules))]:
        name = ctypes.create_unicode_buffer(32768)
        length=ps.GetModuleFileNameExW(handle,module,name,len(name))
        if not 0<length<len(name):errors.append(dict(handle=int(module),error=ctypes.get_last_error()))
        else:rows.append(name.value)
    return dict(paths=rows,errors=errors,query_success=ok,native_error=error,reported=reported,listed=len(rows),
                required_bytes=size.value,buffer_bytes=ctypes.sizeof(modules),
                complete=ok and size.value<=ctypes.sizeof(modules) and size.value%ctypes.sizeof(w.HMODULE)==0 and not errors and reported==len(rows))


def loaded_binaries(snapshot=None):
    snapshot=loaded_binary_paths() if snapshot is None else snapshot
    need(snapshot['complete'],'ACTUAL_DLL_ENUMERATION')
    return [binding(path) for path in snapshot['paths']]


def require_runtime(work):
    """Actual imports/DLLs must match the frozen runtime or product closure."""
    from .lifecycle import selected_context
    c = selected_context()
    from ..preview.contract import enabled as preview_enabled, runtime_check
    if preview_enabled(c):
        atomic_json(Path(work) / 'before-math-runtime.json', runtime_check('numeric'))
        return
    if c and c.get('schema')=='cpu-application-execution-v4':
        from ..adaptive_execution.first_runtime import capture as first_capture
        return first_capture(work,c)
    if c and c.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v5'):
        from ..adaptive_execution.native_runtime import capture
        return capture(work,c)
    if c and c.get('schema') in ('cpu-application-execution-v1','cpu-application-execution-v2'):
        from ..adaptive_execution.runtime import require_runtime as application_runtime
        return application_runtime(work,c)
    if c and c.get('schema')=='cpu-refine-execution-v1':
        from ..cpu_refine.runtime import require_runtime as require_refine_runtime
        return require_refine_runtime(work,c)
    if not c or c.get('schema') != 'cpu-first-fit-execution-v1':
        return
    verify(c['runtime']['inventory'])
    inventory = strict_load(Path(c['runtime']['inventory']['path']))
    known = {os.path.normcase(str(Path(row['path']).resolve())):row['sha256']
             for env in inventory['environments'].values() for row in env['files']}
    product = {row['path']:row['sha256'] for row in c['executor_build']['files']}
    rows = loaded_binaries()
    for name,module in sorted(sys.modules.items()):
        path = getattr(module,'__file__',None)
        if path and Path(path).is_file() and Path(path).suffix.lower() in ('.py','.pyd','.dll'):
            rows.append(dict(module=name,**binding(path)))
    differences=[]
    for row in rows:
        path=Path(row['path']).resolve()
        expected=(product.get(path.relative_to(ROOT).as_posix()) if path.is_relative_to(ROOT/'src') or path.is_relative_to(ROOT/'tools')
                  else known.get(os.path.normcase(str(path))))
        if expected != row['sha256']:
            differences.append(dict(actual=row,expected=expected))
    atomic_json(Path(work)/'before-math-runtime.json',dict(actual=rows,differences=differences))
    need(not differences,'FIRST_FIT_RUNTIME_IMPORT_OR_DLL_CHANGED')


def launch(role, payload, work, *, memory=6*1024**3, script=None, retain_job=None):
    """Predeclare the complete child argv before the suspended CreateProcess."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    from .lifecycle import selected_context
    context = selected_context()
    from ..preview.contract import enabled as preview_enabled, start_job
    if preview_enabled(context):
        need(script is None and (role in ('host','fit','r0') or
             context.get('light_plan') and role in ('prepare','solver','cpu')), 'PREVIEW_WORKER_ROLE')
        return start_job(role, payload, work, memory, retain_job=retain_job)
    a = execution_authority()
    effective_role = role.removeprefix('entry_')
    exe = a['interpreters']['numeric' if effective_role in ('fit', 'r0', 'cpu', 'frame_sender') else 'io' if effective_role == 'host' else 'control']['path']
    if context and context.get('schema')=='cpu-refine-execution-v1':
        need(context['phase']!='entry' or role.startswith('entry_'),'REFINE_ENTRY_ROLE_ONLY')
        need(effective_role in ('r0','cpu','range','prepare','solver','small','host','frame_sender','control','owner','reader'),
             'REFINE_WORKER_ROLE')
        category='solver' if effective_role=='solver' else 'numeric' if effective_role in ('r0','cpu','range','prepare','small') else 'io' if effective_role in ('host','frame_sender') else 'control'
        exe=a['interpreters'][category]['path']
        if role!='host':script=script or ROOT/'tools/gm054_worker.py'
    if context and context.get('schema') == 'cpu-first-fit-execution-v1' and role not in ('host',):
        script = script or ROOT / 'tools/gm053_worker.py'
    if context and role == 'frame_sender':
        exe = a['interpreters']['io']['path']
        script = ROOT / 'tools/gm052_r1_worker.py'
        if context.get('schema')=='cpu-refine-execution-v1':script=ROOT/'tools/gm054_worker.py'
    native=context and context.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v5')
    application=context and context.get('schema') in ('cpu-application-execution-v1','cpu-application-execution-v2','cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5')
    if context and context.get('schema')=='cpu-application-execution-v4':
        need(effective_role in ('fit','r0','prepare','solver','cpu','host','owner','control','application_control'),'FIRST_WORKER_ROLE')
        category='solver' if effective_role=='solver' else 'numeric' if effective_role in ('fit','r0','prepare','cpu') else 'io' if effective_role=='host' else 'control'
        exe=a['interpreters'][category]['path'];script=ROOT/'tools/gm057_worker.py'
        if not role.startswith('entry_') and effective_role in ('fit','r0','prepare','solver','cpu'):
            from ..adaptive_execution.first_science import before_launch
            before_launch(context,effective_role)
    elif native:
        need(effective_role in ('r0','cpu','host','owner','control','application_control'),'NATIVE_WORKER_ROLE')
        exe=a['interpreters']['numeric' if effective_role in ('r0','cpu') else 'io' if effective_role=='host' else 'control']['path']
        script=ROOT/('tools/gm059_worker.py' if context.get('schema')=='cpu-application-execution-v5' else 'tools/gm056_worker.py')
    elif application:
        need(role in ('host','frame_sender'),'APPLICATION_WORKER_ROLE_FORBIDDEN')
        exe=a['interpreters']['io']['path'];script=ROOT/'tools/gm055_worker.py'
    script = script or ROOT / ('tools/gm003_session_host.py' if role == 'host' else 'tools/gm052_worker.py')
    argv = [exe, '-B'] + (['-S'] if exe == a['interpreters']['control']['path'] else [])
    argv += ['-X', 'utf8', str(script)]
    argv += [str(work.parent)] if role == 'host' and not application else [role, str(Path(payload).resolve())]
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1',
               PYTHONUTF8='1', CUDA_VISIBLE_DEVICES='-1', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4',
               OPENBLAS_NUM_THREADS='4', NUMEXPR_NUM_THREADS='4', TEMP=str(work), TMP=str(work))
    if context and context.get('schema') == 'cpu-first-fit-execution-v1':
        env['GM053_CONTEXT'] = str(Path(context['evidence_root']) / 'contexts' / (context['id']+'.json'))
    if context and context.get('schema')=='cpu-refine-execution-v1':
        env['GM054_CONTEXT']=str(Path(context['evidence_root'])/'contexts'/(context['id']+'.json'))
    if application:env['GM055_CONTEXT']=context['manifest_path']
    if context and context.get('schema')=='cpu-application-execution-v5':
        from ..adaptive_execution.independent_layout import environment
        category='numeric' if effective_role in ('r0','cpu') else 'io' if effective_role in ('host','frame_sender') else 'control'
        env=environment(category,work,context=context)
    from ..adaptive_execution.portable_contract import enabled as portable_enabled
    if portable_enabled(context):
        from ..adaptive_execution.portable_runtime import environment
        argv=[exe,'-I','-B','-X','utf8',str(script),role,str(Path(payload).resolve())]
        env=environment(category,work,context=context)
        from ..adaptive_execution.portable_paths import launch_probe
        launch_probe(context,work,payload,env,effective_role)
        if effective_role in ('host','owner','control','application_control'):
            from ..adaptive_execution.portable_resources import admit
            admit(context,'before_launch_'+effective_role,role=effective_role)
        if effective_role=='solver':memory=3*1024**3
    if role == 'test_actor':
        env['GM052_OUT'] = str(work)
    observer = os.environ.get('GM052_OBSERVER_ROOT')
    need(observer is not None, 'QUALIFICATION_LAUNCH_OBSERVER_REQUIRED')
    plan = Path(observer) / 'plans' / (uuid.uuid4().hex + '.json')
    atomic_json(plan, dict(argv=argv, role=role, cwd=str(ROOT), parent=current_identity(),
                          before_CreateProcess=True, allocation='within prepaid root graph'))
    if exe != a['interpreters']['control']['path'] and not (context and context.get('schema')=='cpu-application-execution-v5') and not portable_enabled(context):
        base_plan = plan.with_name(plan.stem + '-base.json')
        atomic_json(base_plan, dict(
            argv=[a['interpreters']['control']['path'], *argv[1:]], role=role+'-base', cwd=str(ROOT),
            expected_parent_executable=exe, before_CreateProcess=True,
            allocation='same prepaid venv/base/conhost graph', base_executable_binding=a['interpreters']['control']))
        approve_plan(base_plan)
    approve_plan(plan)
    job = OwnedJob(memory)
    try:
        who = job.spawn(argv, str(ROOT), env)
        atomic_json(work / 'launcher.json', who)
        return job, who
    except BaseException:
        job.close()
        raise


def wait(job, work, *, timeout=1200, entry_timeout=None, cancelled=None):
    from .. import winproc as win
    work = Path(work)
    deadline = time.monotonic() + timeout
    entry_deadline = None if entry_timeout is None else time.monotonic() + entry_timeout
    handle = None
    actual = None
    cancel_deadline = None
    forced = False
    try:
        while job.poll() is None:
            if handle is None and (work / 'actual-entry.json').is_file():
                actual = strict_load(work / 'actual-entry.json')['identity']
                handle = win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION | win.SYNCHRONIZE, False, actual['pid'])
                need(handle and win.identity_key(win.process_identity(actual['pid'])) == win.identity_key(actual), 'WORKER_ACTUAL_IDENTITY')
                if entry_deadline is not None:
                    need(job.member(actual["pid"]) and
                         win.identity_key(win._identity_from_handle(handle,actual["pid"]))==win.identity_key(actual),
                         "WORKER_ACTUAL_JOB_MEMBERSHIP")
                atomic_json(work / 'actual-retained.json', dict(identity=actual, retained=True))
            if entry_deadline is not None and handle is None:
                need(time.monotonic()<entry_deadline,"OWNED_CPU_ENTRY_TIMEOUT")
            if cancelled is not None and cancelled() and cancel_deadline is None:
                cancel_deadline = time.monotonic() + 2
            if cancel_deadline is not None and time.monotonic() >= cancel_deadline and not forced:
                job.terminate()
                forced = True
            job.memory_peak()
            need(time.monotonic() < deadline, 'OWNED_CPU_TIMEOUT')
            time.sleep(.02)
        code = job.wait(30000)
        empty = job.wait_empty(30000)
        actual_wait = None if handle is None else win.k.WaitForSingleObject(handle, 30000) == win.WAIT_OBJECT_0
        row = dict(actual_wait=True, tree_empty=empty, exit_code=code, forced=forced,
                   cancellation_observed=cancel_deadline is not None,
                   actual_worker_identity=actual, actual_worker_handle_wait=actual_wait,
                   peak_job_memory_bytes=job.memory_peak(), peak_tree_rss_bytes=job.peak_tree_rss_bytes,
                   rss_sample_complete=job.rss_sample_complete)
        atomic_json(work / 'owned-wait.json', row)
        need(empty and actual_wait is not False, 'OWNED_CPU_TREE_OR_ACTUAL_WAIT')
        return row
    finally:
        if handle:
            win.close(handle)


def start_owner(payload, work):
    from .lifecycle import selected_context
    from ..preview.contract import argv as preview_argv, enabled as preview_enabled, environment
    preview = selected_context()
    if preview_enabled(preview):
        argv = preview_argv('owner', payload)
        exe = argv[0]
        env = environment('control', work)
        atomic_json(Path(work) / 'preview-owner-plan.json', dict(argv=argv, before_CreateProcess=True))
    else:
        argv, exe, env = _legacy_owner_launch(payload, work)
    spec = strict_load(payload)
    process = _spawn_owner(argv, exe, env, spec, work)
    from ..light.runtime import application, operation
    if preview_enabled(preview) and application():
        import threading
        from ..preview.host_broker import serve
        owner = strict_load(Path(work) / "owner-launch.json")["identity"]
        plan = operation()
        try:
            need(plan is not None, "HOST_BROKER_PLAN_REQUIRED")
            threading.Thread(target=serve, args=(process, owner, spec, work, plan),
                             name="cpu-host-broker", daemon=True).start()
        except BaseException:
            process.terminate()
            process.wait(timeout=30)
            raise
    return process


def _legacy_owner_launch(payload, work):
    a = execution_authority()
    exe = a['interpreters']['control']['path']
    argv = [exe, '-B', '-S', '-X', 'utf8', str(ROOT / 'tools/gm003_session_owner.py'), str(work)]
    if a.get('schema') in ('cpu-application-execution-v1','cpu-application-execution-v2','cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5'):
        worker='gm059_worker.py' if a['schema']=='cpu-application-execution-v5' else 'gm057_worker.py' if a['schema']=='cpu-application-execution-v4' else 'gm056_worker.py' if a['schema']=='cpu-application-execution-v3' else 'gm055_worker.py'
        argv=[exe,'-B','-S','-X','utf8',str(ROOT/'tools'/worker),'owner',str(Path(payload).resolve())]
    from ..adaptive_execution.portable_contract import enabled as portable_enabled
    if portable_enabled(a):
        from ..adaptive_execution.portable_resources import admit
        admit(a,'before_launch_owner',role='owner');argv=[exe,'-I',*argv[1:]]
    observer = os.environ.get('GM052_OBSERVER_ROOT')
    need(observer, 'QUALIFICATION_LAUNCH_OBSERVER_REQUIRED')
    plan = Path(observer) / 'plans' / (uuid.uuid4().hex + '.json')
    atomic_json(plan,
                dict(argv=argv, role='owner', cwd=str(ROOT), parent=current_identity(),
                     before_CreateProcess=True, allocation='within prepaid root graph'))
    approve_plan(plan)
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1',
               PYTHONUTF8='1', CUDA_VISIBLE_DEVICES='-1', TEMP=str(work), TMP=str(work))
    spec = strict_load(payload)
    if spec['semantics'].get('instance_context'):
        env['GM054_CONTEXT' if spec['semantics'].get('cpu_refine') else 'GM053_CONTEXT'] = spec['semantics']['instance_context']['path']
    if a.get('schema') in ('cpu-application-execution-v1','cpu-application-execution-v2','cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5'):
        env.pop('GM053_CONTEXT',None);env.pop('GM054_CONTEXT',None);env['GM055_CONTEXT']=a['manifest_path']
    if a.get('schema')=='cpu-application-execution-v5':
        from ..adaptive_execution.independent_layout import environment
        env=environment('control',work,context=a)
    if portable_enabled(a):
        from ..adaptive_execution.portable_runtime import environment
        env=environment('control',work,context=a)
    return argv, exe, env


def _spawn_owner(argv, exe, env, spec, work):
    process = None
    who = None
    try:
        with (Path(work) / 'owner-stdout.txt').open('xb') as out, (Path(work) / 'owner-stderr.txt').open('xb') as err:
            process = subprocess.Popen(argv, executable=exe, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
        from ..winproc import process_identity
        who = process_identity(process.pid)
        need(identity_key(who) is not None, 'CREATED_OWNER_IDENTITY_UNKNOWN')
        atomic_json(Path(work) / 'owner-launch.json', dict(identity=who, session_id=spec['session_id'], pool_token=spec['pool_token']))
        from .lifecycle import checkpoint
        checkpoint(Path(work).parent, 'owner_created')
        atomic_json(Path(work) / 'owner-launch-ack.json', dict(identity=who, retained_before_ack=True))
        return process
    except BaseException:
        if process is not None:
            atomic_json(Path(work) / 'stop.signal', dict(reason='OWNER_LAUNCH_FAILURE'))
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=30)
            atomic_json(Path(work) / 'owner-launch-failed.json', dict(identity=who, actual_handle_wait=True, exit_code=process.returncode, host_launch_ack_published=False))
        raise


def approve_plan(plan,*,context=None):
    """Parent observes the immutable plan before this process can create a child."""
    plan = Path(plan)
    from .lifecycle import selected_context
    from ..adaptive_execution.portable_contract import enabled as portable_enabled,e as portable_e
    c=context if context is not None else selected_context()
    if portable_enabled(c):
        from ..adaptive_execution.first_ack import approve
        until=time.monotonic()+30
        while not Path(str(plan)+'.ack').is_file():
            need(time.monotonic()<until,'PRE_CREATE_PLAN_ACK');time.sleep(.01)
        return approve(plan,until,scope=portable_e(c),context=c)
    until = time.monotonic()+30
    while not Path(str(plan)+'.ack').is_file():
        need(time.monotonic() < until, 'PRE_CREATE_PLAN_ACK')
        time.sleep(.01)
    if plan.resolve().is_relative_to(ROOT/'docs/evidence/gm-063/20261104-v1/runs'):
        from ..adaptive_execution.first_ack import approve
        return approve(plan,until,scope=ROOT/'docs/evidence/gm-063/20261104-v1')
    if plan.resolve().is_relative_to(ROOT/'docs/evidence/gm-057/20261101-v1/runs'):
        from ..adaptive_execution.first_ack import approve
        return approve(plan,until)
    saved = strict_load(Path(str(plan)+'.ack'))
    verify(saved['plan'])
    need(saved.get('before_CreateProcess') is True, 'PRE_CREATE_PLAN_ACK_FIELDS')


def error(work, exc):
    row = dict(type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc(),
               notes=getattr(exc, '__notes__', []))
    for name in ('code', 'errno', 'winerror'):
        value = getattr(exc, name, None)
        if value is not None:
            row[name] = value
    filename = getattr(exc, 'filename', None)
    if isinstance(filename, (str, bytes)):
        row['path_utf16_length'] = len(os.fsdecode(filename).encode('utf-16-le', errors='surrogatepass')) // 2
    try:
        atomic_json(Path(work) / 'error.json', row)
    except BaseException as secondary:
        text = 'PRIMARY ' + repr(row) + '\nDIAGNOSTIC ' + repr(secondary) + '\n'
        stream = sys.stderr
        if stream is not None:
            stream.write(text)
            stream.flush()
        else:
            with (Path(work) / 'stderr-fallback.txt').open('xb') as f:
                f.write(text.encode('utf-8')[:128*1024])


def checkpoint(work, name):
    """Only the isolated TEST_ONLY factory supplies this fault instruction."""
    spec_path = Path(work) / 'spec.json'
    if spec_path.is_file() and (strict_load(spec_path)['semantics'].get('test_context') or
                              strict_load(spec_path)['semantics'].get('cpu_refine') is True or
                              strict_load(spec_path)['semantics'].get('adaptive_application') is True):
        from .lifecycle import checkpoint as instance_checkpoint
        return instance_checkpoint(work, name)
    path = Path(work) / 'test-fault.json'
    if path.is_file():
        fault = strict_load(path)
        need(fault.get('TEST_ONLY') is True and os.environ.get('GM052_TEST_ONLY') == '1', 'TEST_FAULT_FORBIDDEN')
        if fault['point'] == name:
            atomic_json(Path(work) / ('checkpoint-' + name + '.json'), dict(identity=current_identity(), point=name))
            if fault.get('action') == 'raise_close':
                raise OSError('TEST_ONLY_POST_COMMIT_MONITOR_CLOSE_FAILURE')
            os._exit(87)


def stderr_error(exc):
    """CLI diagnostics do not replace or suppress the original JSON error."""
    text = '{} [{}]: {}\n'.format(getattr(exc, 'code', type(exc).__name__), getattr(exc, 'execution_stage', 'cpu_replay'), str(exc))
    try:
        if sys.stderr is None:
            raise OSError('STDERR_UNAVAILABLE')
        sys.stderr.write(text)
        sys.stderr.flush()
    except BaseException as secondary:
        try:
            from .lifecycle import test_context
            ticket = os.environ.get('GM052_R1_CONTEXT')
            diagnostic_context = test_context(binding(ticket), allow_stale=True) if ticket else None
            if os.environ.get('GM053_CONTEXT'):
                from ..cpu_first_fit.contract import selected
                diagnostic_context = selected(cleanup=True)
            if os.environ.get('GM055_CONTEXT'):
                from ..adaptive_execution.context import selected
                diagnostic_context=selected(cleanup=True)
            root = Path((diagnostic_context or authority())['evidence_root']) / 'clients' / 'diagnostics' / uuid.uuid4().hex
            root.mkdir(parents=True)
            with (root / 'stderr-fallback.txt').open('xb') as f:
                f.write(('PRIMARY '+text+'DIAGNOSTIC '+repr(secondary)+'\n').encode('utf-8')[:128*1024])
        except BaseException as fallback:
            exc.add_note('stderr/fallback failed: '+repr(secondary)+'; '+repr(fallback))
