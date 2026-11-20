"""Portable replay publication adapters for the real OperationStore journal."""
import time
from pathlib import Path
from ..common import atomic_json, strict_load
from ..store import locked
from ..winproc import identity_alive
from .contract import BACKEND, need, portable, verify, digest


def verify_commit(manifest, spec, path):
    if spec['semantics'].get('adaptive_application') is True:
        from ..adaptive_execution.publication import verify_commit as application_verify
        return application_verify(manifest,spec,path)
    if spec['semantics'].get('cpu_refine') is True:
        from ..cpu_refine.publication import verify_result_commit
        return verify_result_commit(manifest,spec,path)
    if spec['semantics'].get('instance_context'):
        from ..cpu_first_fit.publication import verify_result_commit
        return verify_result_commit(manifest, spec, path)
    need(set(manifest) == {'state', 'job_id', 'attempt_id', 'intent_sha256', 'kind', 'artifacts', 'result_lease'}, 'CPU_COMMIT_FIELDS')
    need(manifest['state'] == 'COMPLETE' and manifest['kind'] == 'replay' and
         all(manifest[k] == spec[k] for k in ('job_id', 'attempt_id', 'intent_sha256')), 'CPU_COMMIT_IDENTITY')
    names = set()
    for item in manifest['artifacts']:
        verify(item)
        p = Path(item['path'])
        need(p.parent == path and p.name not in names, 'CPU_COMMIT_ARTIFACT_SCOPE')
        names.add(p.name)
    need(names == {'compute-result.json', 'compute-cleanup.json', 'replay-identity.json'}, 'CPU_COMMIT_PAYLOAD_SET')
    result = strict_load(path / 'compute-result.json')
    identity = strict_load(path / 'replay-identity.json')
    cleanup = strict_load(path / 'compute-cleanup.json')
    sem = spec['semantics']
    need(result['status'] == 'COMPLETE' and result['backend_id'] == BACKEND and result['mode'] == sem['mode']
         and identity == {k: sem[k] for k in ('backend_id', 'bundle_id', 'math_id', 'executor_build', 'source_sha256')}, 'CPU_COMMIT_RESULT')
    need(cleanup['workers_actual_wait'] is True and cleanup['worker_trees_empty'] is True, 'CPU_COMMIT_WAIT')
    lease = manifest['result_lease']
    need(lease['job_id'] == spec['job_id'] and lease['attempt_id'] == spec['attempt_id'] and
         lease['bundle_id'] == sem['bundle_id'] and lease['math_id'] == sem['math_id'] and
         lease['executor_build'] == sem['executor_build'], 'CPU_COMMIT_LEASE')


def commit(ops, path, spec, artifacts, lease):
    from ..gm004a_operations import _append, _journal
    from .runtime import checkpoint
    with locked(path / 'record.lock'):
        current = dict(_journal(path)[-1]['snapshot'])
        need(current['state'] == 'RUNNING' and not (path / 'cancel.signal').exists(), 'CPU_COMMIT_CANCELLED')
        mark = dict(state='COMPLETE', job_id=spec['job_id'], attempt_id=spec['attempt_id'],
                    intent_sha256=spec['intent_sha256'], kind=spec['kind'], artifacts=artifacts, result_lease=lease)
        verify_commit(mark, spec, path)
        checkpoint(path, 'before_commit')
        atomic_json(path / 'commit.json', mark)
        checkpoint(path, 'after_commit')
        from .api import call
        call(path, 'ACTIVATE', lease)
        checkpoint(path, 'before_success')
        current.update(state='SUCCEEDED', stage='terminal', exit_code=0, error_code=None, result_lease=lease)
        _append(path, current, 'CPU_PORTABLE_COMMITTED')
        return current


def recover(ops, path, spec):
    from .lifecycle import reconcile
    return reconcile(ops, path, spec)


def cancel(ops, path, spec):
    from .lifecycle import reconcile
    return reconcile(ops, path, spec, cancel=True)
