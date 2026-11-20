"""One immutable plan on the original operation, with pre-allocation checks."""
import sys,time
from pathlib import Path
from ..common import strict_load,sha_file
from ..fit_publication import operation_lock
from ..hybrid_replay.resource_journal import create_record
from .contract import require,digest,guarded,policy_binding

NAME='resource-execution-plan.json'

def validate(path, spec, *, expected=None, stage=None, inputs=False):
    from ..hybrid_replay.contract import execution_contract
    path=Path(path);plan=strict_load(path/NAME);bound=strict_load(path/'resource-plan-bound.json')
    require(plan['plan_sha256']==digest({k:v for k,v in plan.items() if k!='plan_sha256'}),'RESOURCE_PLAN_HASH')
    require(bound==dict(plan_sha256=plan['plan_sha256'],job_id=spec['job_id'],attempt_id=spec['attempt_id'],intent_sha256=spec['intent_sha256']), 'RESOURCE_PLAN_BINDING')
    from ..gm004a_operations import _journal
    require(_journal(path)[-1]['snapshot'].get('resource_plan_sha256')==plan['plan_sha256'],'RESOURCE_PLAN_JOURNAL_BINDING')
    if expected is not None:require(expected==bound,'RESOURCE_PLAN_BINDING')
    require(all(plan[k]==spec[k] for k in ('job_id','attempt_id','intent_sha256')),'RESOURCE_PLAN_IDENTITY')
    require(plan['resource_intent']==spec['semantics']['resource_intent'] and plan['resource_intent_sha256']==digest(plan['resource_intent']),'RESOURCE_PLAN_INTENT')
    require(plan['execution_contract_sha256']==execution_contract()[1] and plan['policy_sha256']==digest(policy_binding()),'RESOURCE_IMPLEMENTATION_CHANGED')
    from ..hybrid_fit.tiers import for_spec,consistent
    consistent(for_spec(spec),plan['core_rows'])
    require(plan['deadline_at']==spec['operation_deadline_at'] and plan['fallback'] is False and
            plan['tile_side']==512 and plan['threads']==4,'RESOURCE_PLAN_PARAMETERS')
    if stage is not None:
        require(stage in {'host','compute',*(r['stage'] for r in plan['stages'])},'RESOURCE_PLAN_STAGE')
        require(time.time()<plan['deadline_at'],'RESOURCE_DEADLINE')
    for file,row in plan['runtimes'].items():
        p=Path(file);require(p.stat().st_size==row['bytes'] and sha_file(p)==row['sha256'],'RESOURCE_RUNTIME_CHANGED')
    if inputs:
        for name,loc in plan['input_locators'].items():
            p=Path(loc['path']);require(p.is_file() and p.stat().st_size==loc['bytes'] and sha_file(p)==loc['sha256'] and
                loc['sha256']==spec['semantics'][name+'_sha256'],'RESOURCE_INPUT_CHANGED',name)
    return plan

def bind(path,spec,plan):
    from .. import winproc as win
    path=Path(path)
    with operation_lock(path,spec):
        require(win.identity_key(win.current_identity())==win.identity_key(spec['startup_owner']) and
                time.time()<spec['operation_deadline_at'],'RESOURCE_STARTUP_OWNER')
        require(not(path/NAME).exists(),'RESOURCE_PLAN_ALREADY_BOUND')
        create_record(path/NAME,plan)
        bound=dict(plan_sha256=plan['plan_sha256'],job_id=spec['job_id'],attempt_id=spec['attempt_id'],intent_sha256=spec['intent_sha256'])
        create_record(path/'resource-plan-bound.json',bound)
        from ..gm004a_operations import _journal,_append
        current=dict(_journal(path)[-1]['snapshot']);require(current['state'] not in ('FAILED','CANCELLED','SUCCEEDED','CANCEL_REQUESTED'),'CANCELLED')
        _append(path,dict(current,resource_plan_sha256=plan['plan_sha256']),'RESOURCE_PLAN_BOUND')
    return bound

def worker_check(operation, stage, expected=None):
    path=Path(operation)
    if not(path/'spec.json').exists():
        require(expected is None,'RESOURCE_PLAN_OPERATION_MISSING');return None
    spec=strict_load(path/'spec.json')
    if not guarded(spec):
        require(expected is None,'RESOURCE_PLAN_UNEXPECTED');return None
    require(expected is not None,'RESOURCE_WORKER_BINDING_REQUIRED')
    value=validate(path,spec,expected=expected,stage=stage,inputs=True)
    exe=Path(sys.executable).resolve()
    require(any(exe==Path(p).resolve() for p in value['runtimes']),'RESOURCE_RUNTIME_CHANGED')
    return value

def payload_check(payload,stage):
    """CPU transport locates its operation through manifest.output_root."""
    if stage=='CPU' and 'manifest' in payload:
        output=Path(payload['manifest']['output_root'])
        require(output.name=='CPU','RESOURCE_PAYLOAD_PATH')
        operation=output.parent
        if 'operation_path' in payload:require(Path(payload['operation_path']).resolve()==operation.resolve(),'RESOURCE_PAYLOAD_PATH')
    else:
        require(isinstance(payload.get('operation_path'),str),'RESOURCE_PAYLOAD_PATH')
        operation=Path(payload['operation_path'])
    plan=worker_check(operation,stage,payload.get('resource_execution'))
    if stage=='CPU'and plan is not None:
        from ..hybrid_fit.tiers import consistent
        consistent(plan['core_rows'],payload['manifest']['core_rows'])
    return plan

def pointer(path):
    path=Path(path)
    if not(path/'spec.json').exists() or not guarded(strict_load(path/'spec.json')):return None
    spec=strict_load(path/'spec.json');validate(path,spec)
    return strict_load(path/'resource-plan-bound.json')

def diagnostic(path,spec):
    """A plan error forbids activation; it never substitutes for recovery."""
    if not guarded(spec):return None
    try:validate(path,spec);return None
    except Exception as error:
        return getattr(error,'code',type(error).__name__)


def project(state,error):
    return state if error is None else dict(state,resident=False,binding=None,result_lease_usable=False,resource_plan_error=error)


def unpublished(path,spec,state,error):
    """Retain cancellation and unknown owner facts before any publication."""
    from ..gm004a_operations import _journal,_append,TERMINAL
    from .. import winproc as win
    if error is None or state['state'] in TERMINAL or (Path(path)/'commit.json').exists():return state
    with operation_lock(path,spec):
        current=dict(_journal(path)[-1]['snapshot'])
        if current['state'] in TERMINAL:return current
        owner=win.identity_alive(current.get('owner') or spec['startup_owner'])
        expired=time.time()>=spec['operation_deadline_at'] or current['state']=='QUEUED' and time.time()>=spec['start_deadline_at']
        if owner is False or owner is True and expired:
            cancelled=current['state']=='CANCEL_REQUESTED'
            current.update(state='CANCELLED' if cancelled else 'FAILED',stage='recovered',
                exit_code=8 if cancelled else 9,error_code='CANCELLED' if cancelled else 'RESOURCE_PLAN_INCOMPLETE')
            _append(path,current,'RESOURCE_PLAN_INTERRUPTED')
        return current


def require_available(path,spec=None):
    spec=strict_load(Path(path)/'spec.json') if spec is None else spec
    error=diagnostic(path,spec)
    require(error is None,'RESOURCE_PLAN_UNAVAILABLE',str(error))
