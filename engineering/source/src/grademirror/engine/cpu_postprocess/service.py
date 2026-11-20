"""Thin experimental OperationStore adapter and managed CPU worker transport."""
import os,time
from pathlib import Path
from ...common import GMError,strict_load,sha_file,atomic_json
from ...gm004a_operations import OperationStore
from ...winproc import current_identity,identity_alive
from . import EXECUTOR_ID


class ExperimentalStore(OperationStore):
    """Reuse journal/cancel/recovery; qualify a distinct execution manifest.

    The underlying operation kind is replay, but its semantics and receipts
    explicitly identify the experimental executor. No legacy recipe is minted.
    """
    @staticmethod
    def _fit_residency(path,spec,state):
        return {**state,'experimental_executor':EXECUTOR_ID,'production_activation':False}

    @staticmethod
    def _verify_manifest(manifest,spec,path):
        fail=lambda message:GMError('COMMIT_INVALID',message,7)
        if (spec['semantics'].get('executor_id')!=EXECUTOR_ID or manifest.get('state')!='COMPLETE' or
            any(manifest.get(k)!=spec.get(k) for k in ('job_id','attempt_id','intent_sha256','kind')) or
            manifest.get('result_lease')is not None):raise fail('Experimental manifest identity')
        items=manifest.get('artifacts',[])
        if len(items)!=2:raise fail('Experimental result and cleanup required')
        by_name={}
        for item in items:
            p=Path(item['path'])
            if (p.parent!=path or p.name not in ('executor-result.json','executor-cleanup.json') or
                not p.is_file() or item['bytes']!=p.stat().st_size or item['sha256']!=sha_file(p)):
                raise fail('Experimental artifact differs')
            by_name[p.name]=strict_load(p)
        if len(by_name)!=2:raise fail('Duplicate experimental artifact')
        result=by_name['executor-result.json'];cleanup=by_name['executor-cleanup.json']
        if (result.get('executor_id')!=EXECUTOR_ID or result.get('status')!='COMPLETE' or
            result.get('coverage_complete')is not True or result.get('manifest_sha256')!=spec['semantics']['manifest_sha256'] or
            cleanup.get('actual_wait')is not True or cleanup.get('tree_empty')is not True or
            cleanup.get('exit_code')!=0):raise fail('Incomplete experimental execution')
        if spec['semantics'].get('transport_contract')=='gm017-transport-pattern-v1':
            sem=spec['semantics']
            if (result.get('job_id')!=spec['job_id']or result.get('attempt_id')!=spec['attempt_id']or
                result.get('hashes')!=sem['expected_hashes']or result.get('shape')!=sem['shape']or
                result.get('core_rows')!=sem['core_rows']or result.get('provider_kind')!='transport_pattern'or
                result.get('science_calls')!=0 or result.get('block_count')!=(sem['shape'][0]+sem['core_rows']-1)//sem['core_rows']or
                cleanup.get('worker_identity')!=result.get('worker_identity')or cleanup.get('worker_identity_exited')is not True):
                raise fail('Transport coverage, hash or worker receipt differs')


def worker(payload_path,*,entry_only=False):
    """Only called beneath OwnedJob, with an explicit CPU-only environment."""
    if os.environ.get('CUDA_VISIBLE_DEVICES')!='-1':raise RuntimeError('CPU_ENVIRONMENT')
    payload=strict_load(Path(payload_path))
    fields={'manifest','O_shared_memory','b_path','address','auth_hex','ranges'}
    if set(payload) not in (fields,fields|{'resource_execution'}):raise ValueError('CPU_PAYLOAD_FIELDS')
    from ...resource_execution.binding import payload_check,worker_check
    if 'resource_execution' in payload:
        if type(payload['resource_execution'])is not dict:raise GMError('RESOURCE_WORKER_BINDING_REQUIRED','CPU resource pointer must be an object',5)
        payload_check(payload,'CPU')
    else:
        worker_check(Path(payload['manifest']['output_root']).parent,'CPU')
    payload={key:payload[key] for key in fields}
    m=payload['manifest']
    if identity_alive(m['owner'])is not True:raise RuntimeError('OWNER_NOT_LIVE')
    from .contract import validate
    validate(m)
    if entry_only:
        import sys
        atomic_json(Path(m['output_root'])/'entry-only.json',dict(status='PASS',executor_id=EXECUTOR_ID,
                    numpy_imported='numpy'in sys.modules,torch_imported='torch'in sys.modules))
        return
    from .executor import execute
    from .transport import _run
    return _run(payload,execute)
