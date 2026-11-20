"""Public orchestration over the existing OperationStore/portable lifecycle."""
import ctypes
import os
from pathlib import Path
from ..common import strict_load
from ..cpu_replay.contract import (BACKEND, need, digest, binding, cpu_error_boundary,
                                   Memory, resource_admission)
from .contract import load, context, validate_source, destination, E


def admission(c):
    if c.get('schema')=='cpu-application-execution-v6':
        from ..adaptive_execution.low_memory_contract import admit
        return admit(c,'frame_start')
    if c.get('schema') in ('cpu-application-execution-v1','cpu-application-execution-v2','cpu-application-execution-v3','cpu-application-execution-v4'):
        from ..adaptive_execution.resources import admission as application_admission
        return application_admission(c)
    value = Memory(); value.length = ctypes.sizeof(value)
    need(bool(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value))), 'RESOURCE_UNKNOWN')
    return validate_memory(value.availablePhysical,value.availablePageFile,native=not c['TEST_ONLY'])


def validate_memory(physical,commit,*,native,context=None):
    if context is not None and context.get('schema')=='cpu-application-execution-v6':
        from ..adaptive_execution.low_memory_contract import admit
        # No synthesized system-commit/caller facts from two legacy counters.
        return admit(context,'frame_start')
    need(type(native) is bool and type(physical) is int and type(commit) is int and physical>=0 and commit>=0,'RESOURCE_UNKNOWN')
    need(physical >= (6 if native else 4)*1024**3 and commit >= (16 if native else 6)*1024**3,'RESOURCE_NOT_READY',exit_code=5)
    return dict(physical=physical,commit=commit,native=native)


def _sem(c, source, out, manifest):
    validate_source(source, c)
    destination(out, c)
    return dict(backend_id=BACKEND, mode='fast', instance_context=binding(manifest),
                executor_build=c['executor_build']['sha256'], source_path=str(Path(source).resolve()),
                source_sha256=c['source']['sha256'], output_root=str(Path(out).resolve()),
                bundle_directory=str(Path(c['bundle']).resolve()) if isinstance(c['bundle'], str) else '', TEST_ONLY=c['TEST_ONLY'], core_rows=None)


@cpu_error_boundary
def fit_cpu_recipe(source, reference, store_root, output_root, *, bundle_directory, request_key,
                   mode='fast', execution_backend=None, allow_experimental=False, execution_manifest=None):
    need(execution_backend == BACKEND and allow_experimental is True and mode == 'fast', 'EXPLICIT_CPU_FIRST_FIT_REQUIRED', exit_code=2)
    need(execution_manifest is not None, 'FIRST_FIT_MANIFEST_REQUIRED', exit_code=2)
    c = load(execution_manifest)
    need(c['phase'] == 'fit' and not c['TEST_ONLY'], 'PUBLIC_FIRST_FIT_NATIVE_REQUIRED', exit_code=2)
    return _fit(source, reference, store_root, output_root, bundle_directory, request_key, execution_manifest, c)


def _fit(source, reference, store_root, output_root, bundle_directory, request_key, manifest, c):
    from ..cpu_replay import runtime
    from ..cpu_replay.api import _start
    need(c['phase'] in ('fit', 'frame') and c['reference'] is not None and Path(reference).is_absolute(), 'FIRST_FIT_REFERENCE_REQUIRED')
    got = binding(reference)
    need((got['bytes'],got['sha256']) == (c['reference']['bytes'],c['reference']['sha256']), 'FIRST_FIT_REFERENCE_CHANGED')
    need(Path(bundle_directory).resolve() == Path(c['bundle']).resolve(), 'FIRST_FIT_BUNDLE_DESTINATION')
    destination(bundle_directory, c); destination(store_root, c)
    sem = _sem(c, source, output_root, manifest)
    sem.update(first_fit=True, reference_path=str(Path(reference).resolve()), reference_sha256=c['reference']['sha256'])
    # Before fit there are no learned values. The durable intent is immutable;
    # actual generated IDs are separately frozen after COMPLETE and verified.
    sem['math_id'] = digest(dict(inputs=[c['source_identity'],c['reference_identity']], profile=c['profile'], settings=c['settings']))
    sem['bundle_id'] = digest(dict(fit_intent=sem['math_id'], producer=c['executor_build']['sha256']))
    from .contract import active
    with active(manifest):
        work=E/'clients'/c['id']/str(os.getpid())
        runtime.enter('first_fit_client',work)
        try:
            result=_start(store_root,sem,request_key,c)
        except BaseException as error:
            try:runtime.observation(work,'first_fit_client','FAILED')
            except BaseException as secondary:error.add_note('Observation: '+repr(secondary))
            raise
        runtime.observation(work,'first_fit_client','PASS')
        return result


def check_replay(directory, source, manifest, core_rows=None):
    from ..cpu_recipe.generated import complete
    c = load(manifest)
    need(c['phase'] == 'render' and core_rows is None, 'GENERATED_REPLAY_PHASE')
    m, _ = complete(directory)
    validate_source(source, c)
    need(m['bundle_id'] == c['bundle']['bundle_id'] and m['math_id'] == c['bundle']['math_id'] and
         m['mathematical']['r0_parameters']['value_sha256'] == c['bundle']['parameter_sha256'] and
         m['mathematical']['inputs']['source_identity'] == c['source_identity'] and
         m['provenance']['TEST_ONLY'] == c['TEST_ONLY'], 'GENERATED_REPLAY_BINDING')
    return m, c


@cpu_error_boundary
def replay_generated(directory, source, store_root, output_root, *, request_key,
                     execution_backend=None, allow_experimental=False, execution_manifest=None, cpu_core_rows=None):
    from ..cpu_replay import runtime
    from ..cpu_replay.api import _start
    need(execution_backend == BACKEND and allow_experimental is True, 'EXPLICIT_CPU_REPLAY_REQUIRED', exit_code=2)
    m, c = check_replay(directory, source, execution_manifest, cpu_core_rows)
    need(not c['TEST_ONLY'], 'PUBLIC_REPLAY_NATIVE_REQUIRED', exit_code=2)
    return _replay(directory, source, store_root, output_root, request_key, execution_manifest, c, m)


def _replay(directory, source, store_root, output_root, key, manifest, c, m):
    from ..cpu_replay import runtime
    from ..cpu_replay.api import _start
    sem = _sem(c, source, output_root, manifest)
    sem.update(bundle_directory=str(Path(directory).resolve()), bundle_id=m['bundle_id'], math_id=m['math_id'], first_fit=False)
    destination(store_root,c)
    from .contract import active
    with active(manifest):
        work=E/'clients'/c['id']/str(os.getpid())
        runtime.enter('generated_replay_client',work)
        try:
            result=_start(store_root,sem,key,c)
        except BaseException as error:
            try:runtime.observation(work,'generated_replay_client','FAILED')
            except BaseException as secondary:error.add_note('Observation: '+repr(secondary))
            raise
        runtime.observation(work,'generated_replay_client','PASS')
        return result
