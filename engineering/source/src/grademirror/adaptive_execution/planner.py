"""Read-only resource advice. No package, pixel, Qt, GPU or worker import."""
import time
from .intent import normalize,digest,need
G=1024**3

def snapshot():
    import ctypes
    from ..cpu_replay.contract import Memory
    m=Memory();m.length=ctypes.sizeof(m)
    ok=ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return dict(schema='adaptive-snapshot-v1',captured_at=time.time(),available_ram=int(m.availablePhysical) if ok else None,
        available_commit=int(m.availablePageFile) if ok else None,available_vram=None,existing=None,own_tree_exited=None)

def plan_v1(request,snapshot=None,*,_clock=None,_qualification=None):
    r=normalize(request);s=globals()['snapshot']() if snapshot is None else snapshot
    need(type(s) is dict and set(s)=={'schema','captured_at','available_ram','available_commit','available_vram','existing','own_tree_exited'},'ADAPTIVE_SNAPSHOT_INVALID')
    need(s['schema']=='adaptive-snapshot-v1' and type(s['captured_at']) in (int,float) and __import__('math').isfinite(s['captured_at']),'ADAPTIVE_SNAPSHOT_INVALID')
    need(all(s[k] is None or type(s[k]) is int and 0<=s[k]<2**63 for k in ('available_ram','available_commit','available_vram')),'ADAPTIVE_SNAPSHOT_INVALID')
    existing=s['existing'];need(existing is None or type(existing) is dict and set(existing)=={'rss','commit','vram','state'},'ADAPTIVE_EXISTING_INVALID')
    if existing is not None:
        need(existing['state'] in ('FREE','BUSY','RESIDENT','UNKNOWN') and all(existing[k] is None or type(existing[k]) is int and existing[k]>=0 for k in ('rss','commit','vram')),'ADAPTIVE_EXISTING_INVALID')
    need(s['own_tree_exited'] is None or type(s['own_tree_exited']) is bool,'ADAPTIVE_SNAPSHOT_INVALID')
    meta=r['source']['metadata'];small=max(meta['width'],meta['height'])<=64
    resident=r['operation'] in ('resident_preview','export')
    floor=(1*G,1*G) if resident else (4*G,6*G) if small else (6*G,16*G)
    if _qualification is not None and not resident and not small:
        from .calibration_resources import floor as qualified_commit_floor
        floor=(floor[0],qualified_commit_floor(_qualification,r))
    peak=3*G if small else 6*G
    if resident:peak=max(1024**2,meta['width']*64*3*16)
    reasons=[];unknown=[]
    now=time.time() if _clock is None else _clock
    if not -1<=now-s['captured_at']<=60:unknown.append('SNAPSHOT_STALE')
    for key,threshold in zip(('available_ram','available_commit'),floor):
        if s[key] is None:unknown.append(key.upper()+'_UNKNOWN')
        elif s[key]<threshold:reasons.append(key.upper()+'_BELOW_FLOOR')
    if not resident and existing and existing['state']=='UNKNOWN':unknown.append('POOL_STATE_UNKNOWN')
    elif not resident and existing and existing['state']!='FREE':reasons.append('POOL_'+existing['state'])
    if not resident and s['own_tree_exited'] is not True:unknown.append('OWN_TREE_EXIT_NOT_PROVEN')
    for limit,fact,field in (('ram_bytes','rss','available_ram'),('commit_bytes','commit','available_commit')):
        cap=r['limits'][limit]
        if cap is not None:
            used=existing[fact] if existing is not None else None
            if used is None:unknown.append('EXISTING_'+fact.upper()+'_UNKNOWN')
            elif used+peak>cap:reasons.append('USER_'+limit.upper()+'_TOO_SMALL')
    feasible=False if reasons else None if unknown else True
    stages={'fit':['fast_fit','R0_render']+(['refine_R0_render','H_T','CG_3','CPU256'] if r['quality']=='fine' else []),
        'refine':['R0_render','H_T','CG_3','CPU256'],'replay':['R0_render']+(['CPU256'] if r['quality']=='fine' else []),
        'resident_preview':['resident_mix'],'export':['resident_mix','quantize','encode_readback']}[r['operation']]
    cpu=dict(id='cpu-portable',resource_feasible=feasible,resource_reasons=reasons+unknown,
        qualification='NATIVE_EXECUTION_NOT_QUALIFIED',execution_authorized=False,stages=stages,
        minimum_available_ram=floor[0],minimum_available_commit=floor[1],additional_peak_bytes=peak,
        total_limit_includes_existing=True,available_already_excludes_OS_usage=True,estimated_seconds=None)
    gpu=dict(id='current-gpu-hybrid',resource_feasible=None,resource_reasons=['GPU_SNAPSHOT_UNKNOWN'] if s['available_vram'] is None else ['CURRENT_SCOPE_ONLY'],
        qualification='HISTORICAL_SCOPE_ONLY',execution_authorized=False,estimated_seconds=None)
    candidates=[cpu,gpu];selection='cpu-portable' if r['execution_preference']=='cpu' else None
    from .registry import capabilities
    value=dict(schema='adaptive-plan-v1',request=r,intent_id=digest(r),snapshot=s,candidates=candidates,capabilities=capabilities(r),selected=selection,
        execution_authorized=False,resource_feasible=feasible if selection else None,policy='adaptive-application-v1',
        no_reservation=True,CPU_ENABLED=False,AUTO_ENABLED=False,PRODUCTION_ENABLED=False)
    value['plan_id']=digest(value);return value

def validate_v1(value):
    need(type(value) is dict and value.get('schema')=='adaptive-plan-v1','ADAPTIVE_PLAN_INVALID')
    need(set(value)=={'schema','request','intent_id','snapshot','candidates','capabilities','selected','execution_authorized','resource_feasible','policy',
        'no_reservation','CPU_ENABLED','AUTO_ENABLED','PRODUCTION_ENABLED','plan_id'} and value['execution_authorized'] is False and
        value['no_reservation'] is True and value['CPU_ENABLED'] is False and value['AUTO_ENABLED'] is False and
        value['PRODUCTION_ENABLED'] is False,'ADAPTIVE_PLAN_FIELDS')
    payload={k:v for k,v in value.items() if k!='plan_id'}
    need(type(value['candidates']) is list and len(value['candidates'])==2 and all(type(x) is dict for x in value['candidates']) and
        type(value['candidates'][0].get('resource_reasons')) is list and type(value['snapshot']) is dict and
        type(value['snapshot'].get('captured_at')) in (int,float),'ADAPTIVE_PLAN_FIELDS')
    need(value.get('plan_id')==digest(payload) and value['intent_id']==digest(normalize(value['request'])),'ADAPTIVE_PLAN_CHANGED')
    # Validate the recorded advisory state without expiring durable same-key
    # identities. A *new* launch independently enforces the real current clock
    # and fresh OS admission in service.submit before creating any worker.
    stale='SNAPSHOT_STALE' in value['candidates'][0]['resource_reasons']
    asof=value['snapshot']['captured_at']+(61 if stale else 0)
    need(value==plan_v1(value['request'],value['snapshot'],_clock=asof),'ADAPTIVE_PLAN_CHANGED')
    return value


def plan(request,snapshot=None,*,qualification=None,workspace=None,_clock=None,version=2):
    from .portable_contract import pointer as portable_pointer
    if portable_pointer(qualification):
        from .portable_plan import plan as portable_plan
        return portable_plan(request,snapshot,qualification=qualification,workspace=workspace,_clock=_clock)
    from .low_memory_contract import applies
    if version==3 or version==2 and qualification is None and applies(request) and not (isinstance(snapshot,dict) and snapshot.get('schema')=='adaptive-snapshot-v1'):
        from .low_memory_plan import plan as low_memory_plan
        return low_memory_plan(request,snapshot,qualification=qualification,workspace=workspace,_clock=_clock)
    from .independent_contract import pointer as independent_pointer
    if independent_pointer(qualification):
        from .independent_plan import plan as independent_plan
        return independent_plan(request,snapshot,qualification=qualification,workspace=workspace,_clock=_clock)
    if version==1 or isinstance(snapshot,dict) and snapshot.get('schema')=='adaptive-snapshot-v1':
        return plan_v1(request,snapshot,_clock=_clock)
    from .planner_v2 import plan as latest
    return latest(request,snapshot,qualification=qualification,workspace=workspace,_clock=_clock)

def validate(value):
    if isinstance(value,dict) and value.get('schema')=='adaptive-plan-v5':
        from .portable_plan import validate as portable_validate
        return portable_validate(value)
    if isinstance(value,dict) and value.get('schema')=='adaptive-plan-v4':
        from .low_memory_plan import validate as low_memory_validate
        return low_memory_validate(value)
    if isinstance(value,dict) and value.get('schema')=='adaptive-plan-v3':
        from .independent_plan import validate as independent_validate
        return independent_validate(value)
    if isinstance(value,dict) and value.get('schema')=='adaptive-plan-v2':
        from .planner_v2 import validate as latest
        return latest(value)
    return validate_v1(value)
