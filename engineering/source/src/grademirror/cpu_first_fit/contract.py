"""Standard-library, frozen instance authority for first fit and generated replay."""
import os
import contextvars
from contextlib import contextmanager
from pathlib import Path
from ..common import strict_load, writable_path
from ..cpu_replay.contract import ROOT, BACKEND, FLAGS, need, digest, binding, verify, executor_closure

E = ROOT / 'docs/evidence/gm-053/20261030-v1'
W = ROOT / 'tests/workspaces/g53/20261030-v1'
FIELDS = {'schema', 'id', 'phase', 'mode', 'TEST_ONLY', 'metadata_only', 'executor_build',
          'runtime', 'interpreters', 'source', 'reference', 'source_identity', 'reference_identity',
          'settings', 'profile', 'color_policy', 'output_root', 'evidence_root', 'pool_root',
          'bundle', 'science', 'flags', 'lease_seconds', 'request_seconds', 'prepared_clock_shift',
          'fault_point', 'fault_action', 'pause_at', 'force_grace_seconds', 'cancel_after_updates'}
TEST_FAULTS = {'', 'before_startup', 'after_claim', 'after_mkdir', 'after_spec', 'before_owner',
               'owner_created', 'prepared', 'before_commit', 'after_commit', 'before_success',
               'release_pending', 'proof_written', 'pool_freed', 'bundle_before_complete',
               'bundle_after_complete', 'cleanup_once'}
_active = contextvars.ContextVar('cpu_first_fit_instance', default=None)


@contextmanager
def active(manifest, *, cleanup=False):
    value = load(manifest,cleanup=cleanup)
    token = _active.set(value)
    try:
        if value.get('schema')=='cpu-application-execution-v4':
            from ..adaptive_execution.first_residency import scope
            with scope(value):yield value
        else:yield value
    finally:
        _active.reset(token)


def load(path, *, cleanup=False):
    path = Path(path).resolve()
    if path.parent.name=='contexts' and path.parent.parent.name=='.adaptive':
        from ..adaptive_execution.context import load as application_load
        return application_load(path,cleanup=cleanup)
    if path.parent == ROOT/'docs/evidence/gm-054/20261030-v1/contexts':
        from ..cpu_refine.contract import load as refine_load
        return refine_load(path,cleanup=cleanup)
    need(path.parent == E / 'contexts', 'FIRST_FIT_MANIFEST_POINTER')
    item = binding(path)
    index = strict_load(E / 'contexts/index.json')
    c = strict_load(path)
    need(type(c) is dict and set(c) == FIELDS and c['schema'] == 'cpu-first-fit-execution-v1', 'FIRST_FIT_MANIFEST_FIELDS')
    need(index.get(c['id']) == item and c['mode'] == 'fast' and c['phase'] in ('fit', 'render', 'frame'), 'FIRST_FIT_MANIFEST_AUTHORITY')
    need(c['flags'] == FLAGS and type(c['TEST_ONLY']) is bool and type(c['metadata_only']) is bool, 'FIRST_FIT_FLAGS')
    need(Path(c['output_root']).resolve() == W / c['id'] and Path(c['evidence_root']).resolve() == E and
         Path(c['pool_root']).resolve().parent == W / 'pools', 'FIRST_FIT_INSTANCE_SCOPE')
    need(cleanup or c['executor_build'] == executor_closure(), 'FIRST_FIT_BUILD_CHANGED')
    need(c['fault_point'] in TEST_FAULTS and c['fault_action'] in ('raise', 'exit') and
         c['pause_at'] in ('', 'before_ready', 'before_compute', 'prepared') and
         c['force_grace_seconds'] in (1, 30), 'FIRST_FIT_TEST_INSTRUCTION')
    if c['TEST_ONLY']:
        need(c['source_identity']['shape'] == [33, 47, 3] and c['source_identity']['dtype'] in ('uint8', 'float32') and
             0 < c['lease_seconds'] <= 120 and 0 < c['request_seconds'] <= 120 and
             c['prepared_clock_shift'] in (0, 7000) and c['settings']['updates'] in (1, 3, 1000), 'FIRST_FIT_SMALL_SCOPE')
    else:
        need(not c['metadata_only'] and c['phase'] != 'frame' and c['lease_seconds'] == 7200 and
             c['request_seconds'] == 120 and c['prepared_clock_shift'] == 0 and c['fault_point'] == '' and
             c['pause_at'] == '' and c['force_grace_seconds'] == 30 and c['cancel_after_updates'] == 0 and
             c['source_identity']['dtype'] == 'uint8' and c['settings']['updates'] == 1000, 'FIRST_FIT_NATIVE_SCOPE')
    if not cleanup:
        for entry in c['interpreters'].values():
            verify(entry)
        need(c['runtime']['status'] == 'KNOWN_EXACT_RUNTIME', 'FIRST_FIT_RUNTIME_UNKNOWN')
        verify(c['runtime']['inventory'])
    return c


def selected(*, cleanup=False):
    current = _active.get()
    if current is not None:
        return current
    value = os.environ.get('GM055_CONTEXT') or os.environ.get('GM054_CONTEXT') or os.environ.get('GM053_CONTEXT')
    return load(value, cleanup=cleanup) if value else None


def context(spec, *, cleanup=False):
    sem = spec.get('semantics', spec)
    if 'preview_version' in sem:
        from ..preview.contract import context as preview_context
        return preview_context(spec, cleanup=cleanup)
    if sem.get('adaptive_application') is True:
        from ..adaptive_execution.context import context as application_context
        return application_context(spec,cleanup=cleanup)
    if sem.get('cpu_refine') is True:
        from ..cpu_refine.contract import context as refine_context
        return refine_context(spec,cleanup=cleanup)
    item = sem['instance_context']
    verify(item)
    c = load(item['path'], cleanup=cleanup)
    need(sem['executor_build'] == c['executor_build']['sha256'] and sem['mode'] == 'fast' and
         sem['output_root'] == c['output_root'] and sem['source_sha256'] == c['source']['sha256'] and
         sem.get('TEST_ONLY', False) == c['TEST_ONLY'], 'FIRST_FIT_SESSION_CONTEXT')
    return c


def validate_source(source, c):
    need(Path(source).is_absolute(), 'SOURCE_ABSOLUTE', exit_code=2)
    got = binding(source)
    need((got['bytes'], got['sha256']) == (c['source']['bytes'], c['source']['sha256']), 'FIRST_FIT_SOURCE_CHANGED')
    return got


def destination(value, c):
    p = writable_path(Path(value))
    need(p.is_absolute() and p.resolve().is_relative_to(Path(c['output_root'])), 'FIRST_FIT_OUTPUT_SCOPE')
    return p


def check_session(session):
    s = strict_load(Path(session) / 'session-spec.json')
    operation = strict_load(Path(session).parent / 'spec.json')
    need(s['backend_id'] == BACKEND and s['operation_path'] == str(Path(session).parent) and
         all(s[k] == operation[k] for k in ('job_id', 'attempt_id', 'semantics')), 'FIRST_FIT_WORKER_OPERATION')
    c = context(s)
    need(s['execution'] == c, 'FIRST_FIT_WORKER_TICKET')
    validate_source(s['semantics']['source_path'], c)
    return s
