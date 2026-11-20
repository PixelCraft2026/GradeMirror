"""GM064 plan/selection on the existing application submit and OperationStore."""
import time
from pathlib import Path
from ..common import strict_load as _strict_load,atomic_json
from ..cpu_replay.contract import binding,verify,need,ROOT
from ..store import locked
from .intent import normalize,digest
from .portable_policy import evaluate,POLICY
from . import portable_contract as k

def strict_load(path):return _strict_load(Path(path))

def facts(request,snapshot,model,*,now=None):
    target=model['cold'][request['quality']];p=snapshot['product']
    row=evaluate(snapshot,target['rss_bytes'],target['private_bytes'],occupied_ram=p['rss_bytes'],occupied_commit=p['private_bytes'],cold=True)
    reasons=row['reasons'];pool=snapshot['pool']
    if not -1<=(time.time() if now is None else now)-snapshot['captured_at']<=60:reasons.append('SNAPSHOT_STALE')
    if pool['state']!='FREE' or pool['exited'] is not True:reasons.append('POOL_EXIT_OR_USAGE_UNKNOWN')
    for limit,base in (('ram_bytes',target['rss_bytes']),('commit_bytes',target['private_bytes'])):
        cap=request['limits'][limit]
        if cap is not None and cap<base:reasons.append('USER_LIMIT_TOO_SMALL:'+limit)
    if request['limits']['vram_bytes'] is not None:reasons.append('PORTABLE_GPU_NOT_AUTHORIZED')
    row.update(resource_feasible=not reasons,resource_reasons=list(reasons),minimum_available_ram=row['required_ram_bytes'],
        minimum_available_commit=row['required_commit_bytes'],additional_peak_bytes=row['additional_ram_bytes'],message='资源就绪' if not reasons else '; '.join(reasons))
    return row

def assemble(request,snapshot,proof,model,clock):
    r=normalize(request);f=facts(r,snapshot,model,now=clock);selected=f['resource_feasible'] and proof['execution_authorized']
    row=dict(schema='adaptive-plan-v5',policy=POLICY,request=r,intent_id=digest(r),snapshot=snapshot,qualification=proof,
        prediction=model,evaluation_clock=clock,candidates=[dict(id='gm064-fixed-cpu',**f)],selected='gm064-fixed-cpu' if selected else None,
        execution_authorized=selected,no_reservation=True,CPU_ENABLED=False,AUTO_ENABLED=False,PRODUCTION_ENABLED=False)
    row['plan_id']=digest(row);return row

def plan(request,snapshot=None,*,qualification=None,workspace=None,_clock=None):
    from .memory_snapshot import collect
    q=k.authorize(qualification,request);s=collect(workspace) if snapshot is None else snapshot
    m=strict_load(k.package_member('validation/resource-model.json'));need(q['resource_model']==binding(ROOT/'validation/resource-model.json'),'PORTABLE_RESOURCE_MODEL_CHANGED')
    proof=dict(scope='GM064_FIXED_DOMAIN',evidence=binding(qualification),execution_authorized=q['phase']=='native')
    return assemble(request,s,proof,m,time.time() if _clock is None else _clock)

def validate(v):
    need(type(v)is dict and v.get('schema')=='adaptive-plan-v5' and v.get('plan_id')==digest({key:x for key,x in v.items() if key!='plan_id'}),'PORTABLE_PLAN_CHANGED')
    need(v==assemble(v['request'],v['snapshot'],v['qualification'],v['prediction'],v['evaluation_clock']),'PORTABLE_PLAN_CHANGED');return v

def validate_selection(row,request):
    fields={'schema','request_key','request','intent_id','qualification','plan_id','policy','resource_model','execution_build'}
    need(type(row)is dict and set(row)==fields and row['schema']=='gm064-selection-v1' and row['request']==normalize(request) and
        row['intent_id']==digest(request) and row['policy']==POLICY,'PORTABLE_SELECTION_CHANGED')
    verify(row['qualification']);verify(row['resource_model']);return row

def submit(value,key,workspace,*,experimental=False,qualification=None):
    v=validate(value);r=v['request'];need(experimental is True and type(key)is str and 0<len(key)<=128,'PORTABLE_EXPLICIT_SUBMIT')
    q=k.authorize(qualification,r);root=k.session(qualification);w=Path(workspace).absolute();k.ancestors(w)
    need(w.is_relative_to(k.workspace(root)),'PORTABLE_WORKSPACE_SCOPE')
    from . import c2_fine_replay
    if Path(q['manifest_path']).name in c2_fine_replay.QUALIFICATIONS:
        c2_fine_replay.dispatch_scope(q,root,w,key)
    from .t34r3_c1_route import selected
    route=selected(q,w,key)
    path=route.selection if route is not None else w/'.adaptive/selections'/(digest(key)+'.json')
    with locked(w/'.adaptive/selection.lock'):
        if path.exists():
            row=validate_selection(strict_load(path),r);need(row['qualification']==binding(qualification),'PORTABLE_SAME_KEY_CAPABILITY_CHANGED')
            if route is not None:
                need(row['request_key']==key and row['resource_model']==q['resource_model']
                     and row['execution_build']==q['executor_build']['sha256'],'C1_ROUTE_SELECTION_BINDING')
        else:
            need(v['qualification']==dict(scope='GM064_FIXED_DOMAIN',evidence=binding(qualification),execution_authorized=True),'PORTABLE_PLAN_AUTHORITY_CHANGED')
            from .memory_snapshot import collect
            f=facts(r,collect(w),v['prediction']);need(f['resource_feasible'],'RESOURCE_NOT_READY',f['message'])
            row=dict(schema='gm064-selection-v1',request_key=key,request=r,intent_id=digest(r),qualification=binding(qualification),
                plan_id=v['plan_id'],policy=POLICY,resource_model=q['resource_model'],execution_build=q['executor_build']['sha256'])
            atomic_json(path,row)
    from .planner import plan_v1,snapshot
    from .service import _submit_v1
    return _submit_v1(plan_v1(r,snapshot()),key,w,experimental=True,qualification=qualification,_selection=binding(path))

def legacy_fresh(request,current,q):
    from .memory_snapshot import collect
    f=facts(request,collect(q['paths']['workspace']),strict_load(ROOT/'validation/resource-model.json'))
    return dict(candidates=[f])
