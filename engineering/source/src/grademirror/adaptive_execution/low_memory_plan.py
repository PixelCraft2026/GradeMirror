"""Version4 plan: shared allocation model and explicit negative qualification."""
from .intent import normalize,digest,need
from .resource_policy_v3 import allocation_model,evaluate


def assemble(request,snapshot,*,now=None):
    from .low_memory_contract import scope
    r=normalize(request);model=allocation_model(r)
    facts=evaluate(r,snapshot,now=now,model=model)
    value=dict(schema='adaptive-plan-v4',policy='resource-policy-v3',request=r,
        intent_id=digest(r),snapshot=snapshot,allocation_model=model,
        qualification=scope(r),candidates=[dict(id='cpu-low-memory-v3',**facts)],
        selected=None,execution_authorized=False,resource_feasible=facts['resource_feasible'],
        no_reservation=True,status='MODELED_TARGET_NOT_FIT' if not model['target_fits'] else 'MODEL_UNCERTIFIED',
        CPU_ENABLED=False,AUTO_ENABLED=False,PRODUCTION_ENABLED=False,D01_extended=False,
        evaluation_clock=snapshot['captured_at'] if now is None else now)
    value['plan_id']=digest(value);return value


def plan(request,snapshot=None,*,qualification=None,workspace=None,_clock=None):
    from .memory_snapshot import collect
    if qualification is not None:
        from .low_memory_contract import E
        from pathlib import Path
        need(Path(qualification).resolve()==E/'candidate-plan.json','RESOURCE_V3_AUTHORITY_POINTER')
    facts=collect(workspace) if snapshot is None else snapshot
    import time
    return assemble(request,facts,now=time.time() if _clock is None else _clock)


def validate(value):
    need(type(value)is dict and value.get('schema')=='adaptive-plan-v4' and
         value.get('plan_id')==digest({k:v for k,v in value.items() if k!='plan_id'}),
         'RESOURCE_V3_PLAN_CHANGED')
    need(value==assemble(value['request'],value['snapshot'],now=value['evaluation_clock']),
         'RESOURCE_V3_PLAN_CHANGED')
    return value


def submit(value,request_key,workspace,*,experimental=False,qualification=None):
    from .low_memory_contract import context,admit,E
    from pathlib import Path
    need(experimental is True,'EXECUTION_NOT_QUALIFIED')
    need(qualification is not None and Path(qualification).resolve()==E/'candidate-plan.json',
         'RESOURCE_V3_AUTHORITY_POINTER')
    need(type(request_key)is str and 0<len(request_key)<=128,'APPLICATION_REQUEST_KEY')
    return admit(context(value,workspace),'submit')
