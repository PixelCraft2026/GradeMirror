"""Standard-library execution authority, separate from imported recipe evidence."""
import ast
import ctypes
import hashlib
import json
import os
import functools
from pathlib import Path

from ..common import GMError, canonical_json, strict_load, reject_reparse, writable_path, sha_file
from . import BACKEND

ROOT = Path(__file__).resolve().parents[3]
AUTHORITY = Path(__file__).with_name('authority.json')
FLAGS = dict(CPU_ENABLED=False, AUTO_ENABLED=False, PRODUCTION_ENABLED=False, D01_extended=False)
RESOURCE_ENVELOPE = dict(native_available=6*1024**3, native_commit=16*1024**3,
    job_commit=6*1024**3, tree_RSS=6*1024**3, tree_private=6*1024**3,
    IO_RSS=3*1024**3, IO_private=int(4.5*1024**3), system_physical_margin=1024**3,
    system_commit_margin=1024**3, CPU_workspace=512*1024**2)


def need(condition, code, message=None, exit_code=7):
    if not condition:
        raise GMError(code, message or code, exit_code)


def cpu_error_boundary(fn):
    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BaseException as error:
            error.execution_backend = BACKEND
            error.execution_stage = fn.__name__
            raise
    return run


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def authority():
    return strict_load(AUTHORITY)


def binding(path):
    path = Path(path).resolve()
    return dict(path=str(path), bytes=path.stat().st_size, sha256=sha_file(path))


def verify(item):
    need(type(item) is dict and set(item) == {'path', 'bytes', 'sha256'}, 'FILE_BINDING_FIELDS')
    path = Path(item['path'])
    reject_reparse(path)
    need(path.is_absolute() and path.is_file() and path.stat().st_size == item['bytes']
         and sha_file(path) == item['sha256'], 'FILE_BINDING_CHANGED')


def output(path):
    path = writable_path(Path(path))
    from .lifecycle import selected_context
    test = selected_context()
    from ..preview.contract import enabled as preview_enabled, scoped_output
    if preview_enabled(test):
        return scoped_output(path, test)
    if test and test.get('schema') in ('cpu-application-execution-v1','cpu-application-execution-v2','cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5'):
        need(path.is_absolute() and any(path.resolve().is_relative_to(Path(test['paths'][key])) for key in ('store','operations','exports')),'APPLICATION_OUTPUT_SCOPE')
        return path
    root = Path(test['output_root'] if test else authority()['output_root']).resolve()
    need(path.is_absolute() and path.resolve().is_relative_to(root), 'CPU_REPLAY_OUTPUT_SCOPE')
    return path


def portable(spec):
    return spec.get('semantics', {}).get('backend_id') == BACKEND


def executor_closure():
    if os.environ.get('GM055_CONTEXT') and strict_load(Path(os.environ['GM055_CONTEXT'])).get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5'):
        from ..adaptive_execution.build import current_closure
        return current_closure(native=True)
    if os.environ.get('GM055_CONTEXT'):
        from ..adaptive_execution.build import verified_closure
        return verified_closure(_executor_closure)
    return _executor_closure()


def _executor_closure(*, indexed=False,native_scope=False):
    """Resolve the explicit entry graph, including literal delayed product imports.

    The traversal hashes relative logical names. It does not use a whole-tree
    hash, installed-module discovery, a cwd, or an imported mathematical ID.
    """
    seeds = ['grademirror.cli', 'grademirror.cpu_replay.api',
             'grademirror.cpu_replay.owner', 'grademirror.cpu_replay.host',
             'grademirror.cpu_replay.numeric', 'grademirror.engine.cpu_postprocess.transport',
             'grademirror.engine.cpu_postprocess.receiver', 'grademirror.engine.group15_codec',
             'grademirror.gm004a_preview', 'grademirror.gm004a_export',
             'grademirror.cpu_recipe.validation']
    seeds.extend('grademirror.cpu_refine.'+p.stem for p in
                 (ROOT/'src/grademirror/cpu_refine').glob('*.py') if p.stem!='__init__')
    seeds.extend('grademirror.adaptive_execution.'+p.stem for p in
                 (ROOT/'src/grademirror/adaptive_execution').glob('*.py'))
    seeds.extend(('grademirror.desktop.adaptive_controller','grademirror.desktop.adaptive_panel','grademirror.desktop.backend','grademirror.desktop.window'))
    source_index = None
    if indexed:
        from ..adaptive_execution.build import source_names
        source_index = source_names(ROOT)
    def present(path):
        return path.is_file() if source_index is None else os.path.normcase(str(path.relative_to(ROOT/'src'))) in source_index
    pending = list(seeds)
    found = {}
    while pending:
        name = pending.pop()
        if name in found or not name.startswith('grademirror'):
            continue
        parts = name.split('.')
        pending.extend('.'.join(parts[:i]) for i in range(1, len(parts)))
        path = ROOT / 'src' / (name.replace('.', '/') + '.py')
        package = False
        if not present(path):
            path = ROOT / 'src' / name.replace('.', '/') / '__init__.py'
            package = True
        if not present(path):
            continue
        found[name] = path
        base = name.split('.') if package else name.split('.')[:-1]
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = (base[:len(base)-node.level+1] if node.level else [])
                module = '.'.join(prefix + ([node.module] if node.module else []))
                pending.append(module)
                pending.extend(module + '.' + a.name for a in node.names)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '__import__':
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    pending.append(node.args[0].value)
    paths = set(found.values())
    paths.update((ROOT / 'tools' / name) for name in
                 ('gm052_worker.py', 'gm052_r1_worker.py', 'gm003_session_owner.py', 'gm003_session_host.py'))
    paths.update((ROOT / 'tools').glob('gm053_worker.py'))
    paths.update((ROOT / 'src/grademirror/cpu_first_fit').glob('*.py'))
    paths.update((ROOT / 'schemas').glob('cpu-first-fit-*.json'))
    paths.update((ROOT / 'schemas').glob('cpu-recipe-bundle-v2.json'))
    paths.update((ROOT/'src/grademirror/cpu_refine').glob('*.py'))
    paths.add(ROOT/'src/grademirror/cpu_recipe/generated_fine.py')
    paths.update((ROOT/'tools').glob('gm054_worker.py'))
    paths.update((ROOT/'tools').glob('gm055_worker.py'))
    paths.update((ROOT/'tools').glob('gm056_worker.py'))
    paths.update((ROOT/'tools').glob('gm057_worker.py'))
    paths.update((ROOT/'tools').glob('gm059_worker.py'))
    paths.update((ROOT/'tools').glob('grademirror_cpu_*.py'))
    paths.add(ROOT/'tools/grademirror_desktop.py')
    paths.update((ROOT/'schemas').glob('adaptive-*.json'))
    paths.update((ROOT/'schemas').glob('cpu-application-*.json'))
    paths.add(ROOT/'tools/gm008_solver_worker.py')
    paths.update((ROOT/'schemas').glob('cpu-refine-*.json'))
    paths.update((ROOT/'schemas').glob('cpu-recipe-bundle-v3.json'))
    if not native_scope:paths.add(AUTHORITY)
    paths.update((ROOT / 'schemas').glob('cpu-replay-*.json'))
    rows = [dict(path=p.relative_to(ROOT).as_posix(), sha256=sha_file(p))
            for p in sorted(paths) if p.is_file()]
    if indexed:
        need(source_names(ROOT)==source_index,'APPLICATION_SOURCE_INDEX_CHANGED_DURING_READ')
    return dict(files=rows, sha256=digest(rows))


def manifest(mode, runtime):
    a = authority()
    bundle = next(b for b in a['bundles'] if b['mode'] == mode)
    return dict(schema='cpu-replay-execution-v1', backend=BACKEND, mode=mode,
                qualification='QUALIFICATION_IN_PROGRESS', bundle_id=bundle['bundle_id'],
                math_id=bundle['math_id'], bundle_declared_build=bundle['bundle_declared_build'],
                executor_build=executor_closure(), runtime=runtime, source=a['source'],
                source_shape=a['source_shape'], parameter_sha256=a['parameter_sha256'],
                b=a['b'] if mode == 'fine' else None, core_rows=256 if mode == 'fine' else None,
                CPU_threads=4, interop=1, precision='FP32/channels_last',
                scientific_caps=a['scientific_caps'], output_root=a['output_root'],
                evidence_root=a['evidence_root'], color_policy=bundle['color_policy'],
                color_declaration=bundle['color_declaration'], resource_envelope=RESOURCE_ENVELOPE, flags=FLAGS)


def validate_manifest(path, mode):
    a = authority()
    expected_path = Path(a['evidence_root']) / 'manifests' / (mode + '.json')
    need(Path(path).resolve() == expected_path.resolve(), 'EXECUTION_MANIFEST_POINTER')
    m = strict_load(expected_path)
    runtime = strict_load(Path(a['evidence_root']) / 'runtime.json')
    need(runtime.get('status') == 'KNOWN_EXACT_RUNTIME', 'RUNTIME_UNKNOWN')
    need(m == manifest(mode, runtime), 'EXECUTION_MANIFEST_INVALID')
    for value in a['interpreters'].values():
        verify(value)
    for group in runtime['environments'].values():
        for item in group['files']:
            verify(item)
    return m


def check_bundle(directory, source, execution_manifest, backend, experimental, core_rows):
    need(backend == BACKEND and experimental is True, 'EXPLICIT_CPU_REPLAY_REQUIRED', exit_code=2)
    from ..cpu_recipe import verify_cpu_recipe
    info = verify_cpu_recipe(directory)  # Metadata guard ends before any runtime entry.
    if info['mode']=='fast' and info['version'] in (2,3):
        from ..cpu_first_fit.api import check_replay
        return check_replay(directory, source, execution_manifest, core_rows)
    mode = info['mode']
    need(core_rows in (None, 256) if mode == 'fine' else core_rows is None, 'CPU_CORE_STRATEGY', exit_code=2)
    a = authority()
    row = next((b for b in a['bundles'] if b['mode'] == mode), None)
    need(row and row['bundle_id'] == info['bundle_id'] and row['math_id'] == info['math_id'], 'UNAUTHORIZED_CPU_BUNDLE')
    directory = Path(directory).resolve()
    for item in row['files']:
        verify(dict(item, path=str(directory / item['path'])))
    need(Path(source).is_absolute(), 'SOURCE_ABSOLUTE', exit_code=2)
    actual = binding(source)
    need(actual['bytes'] == a['source']['bytes'] and actual['sha256'] == a['source']['sha256'], 'CPU_REPLAY_SOURCE_CHANGED')
    m = validate_manifest(execution_manifest, mode)
    return strict_load(directory / 'manifest.json'), m


class Memory(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                ('totalPhysical', ctypes.c_ulonglong), ('availablePhysical', ctypes.c_ulonglong),
                ('totalPageFile', ctypes.c_ulonglong), ('availablePageFile', ctypes.c_ulonglong),
                ('totalVirtual', ctypes.c_ulonglong), ('availableVirtual', ctypes.c_ulonglong),
                ('availableExtended', ctypes.c_ulonglong)]


def resource_admission(native, *, context=None):
    from ..adaptive_execution.portable_contract import enabled as portable_enabled
    if portable_enabled(context):
        from ..adaptive_execution.portable_resources import admit
        return admit(context,'before_launch_owner',role='owner')
    if context is not None and context.get('schema')=='cpu-application-execution-v6':
        from ..adaptive_execution.low_memory_contract import admit
        return admit(context,'native_start')
    value = Memory()
    value.length = ctypes.sizeof(value)
    need(bool(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value))), 'RESOURCE_UNKNOWN')
    physical, commit = value.availablePhysical, value.availablePageFile
    need(physical >= (6 if native else 2)*1024**3 and commit >= (16 if native else 4)*1024**3,
         'RESOURCE_NOT_READY', exit_code=5)
    return dict(physical=physical, commit=commit, native=native)


def worker_authority(session):
    """Every actual executor rechecks the same current, non-self-authored ticket."""
    session = Path(session)
    spec = strict_load(session / 'session-spec.json')
    need(spec['backend_id'] == BACKEND and Path(spec['operation_path']).resolve() == session.parent.resolve(), 'WORKER_SESSION_POINTER')
    sem = spec['semantics']
    if sem.get('instance_context'):
        from ..cpu_first_fit.contract import check_session
        return check_session(session)
    operation = strict_load(session.parent / 'spec.json')
    need(operation['semantics'] == sem and operation['job_id'] == spec['job_id'] and
         operation['attempt_id'] == spec['attempt_id'], 'WORKER_OPERATION_IDENTITY')
    if sem.get('TEST_ONLY'):
        from .lifecycle import context
        context(spec)
        need(sem['source_sha256'] != authority()['source']['sha256'], 'TEST_ONLY_NATIVE_FORBIDDEN')
        return spec
    bundle, manifest_data = check_bundle(sem['bundle_directory'], sem['source_path'],
                                       sem['execution_manifest']['path'], BACKEND, True, sem['core_rows'])
    need(manifest_data == spec['execution'] and bundle['bundle_id'] == sem['bundle_id'] and
         manifest_data['executor_build']['sha256'] == sem['executor_build'], 'WORKER_EXECUTOR_IDENTITY')
    return spec


def require_current_executor(value):
    need(type(value) is str and value == executor_closure()['sha256'], 'CPU_EXECUTION_CREDENTIAL_STALE')


def io_memory_sample(identity):
    from ..winproc import process_memory
    sample = process_memory(identity)
    need(type(sample['rss_bytes']) is int and type(sample['private_commit_bytes']) is int, 'IO_RESOURCE_UNKNOWN')
    need(0 <= sample['rss_bytes'] <= RESOURCE_ENVELOPE['IO_RSS'] and
         0 <= sample['private_commit_bytes'] <= RESOURCE_ENVELOPE['IO_private'], 'IO_RESOURCE_LIMIT')
    return sample
