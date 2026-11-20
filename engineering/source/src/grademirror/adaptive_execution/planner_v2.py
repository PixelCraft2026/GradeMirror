"""Scoped CPU selection. Advisory plans never authorize native execution."""
import copy
from pathlib import Path
from .intent import normalize,digest,need
from .resource_policy_v2 import evaluate

def projection(request):
    r=normalize(request);p=copy.deepcopy(r);p['execution_preference']='cpu'
    need({k:v for k,v in p.items() if k!='execution_preference'}=={k:v for k,v in r.items() if k!='execution_preference'},'EXECUTION_PROJECTION_CHANGED')
    return p

def authority(request,qualification):
    if qualification is None:return dict(status='NATIVE_REQUALIFICATION_PENDING',execution_authorized=False,evidence=None)
    from .qualification import authorize,GM058_FIXTURE_FACTORY,factory_for
    from ..cpu_replay.contract import binding
    try:
        q=authorize(qualification,projection(request))
        eligible=(factory_for(qualification)==GM058_FIXTURE_FACTORY and q['schema']=='adaptive-engineering-factory-v2' and q['TEST_ONLY'] is True and request['source']['metadata']['format']=='TEST_FRAME' and request['operation']=='fit' and request['quality']=='fast')
        # Qualification authorizes the exact inputs/runtime/build, not a generic CPU.
        return dict(status='TEST_ONLY_EXACT_SCOPE' if eligible else 'NATIVE_REQUALIFICATION_PENDING',execution_authorized=eligible,evidence=binding(qualification))
    except Exception as e:return dict(status='QUALIFICATION_REJECTED',execution_authorized=False,evidence=None,reason=getattr(e,'code',type(e).__name__))

def assemble(request,snapshot,proof,*,now=None):
    r=normalize(request);facts=evaluate(r,snapshot,now=now);stages={'fit':['fast_fit','R0_render']+(['refine_R0_render','H_T','CG_3','CPU256'] if r['quality']=='fine' else []),'refine':['R0_render','H_T','CG_3','CPU256'],'replay':['R0_render']+(['CPU256'] if r['quality']=='fine' else []),'resident_preview':['resident_mix'],'export':['resident_mix','quantize','encode_readback']}[r['operation']]
    selected='cpu-portable' if r['execution_preference'] in ('cpu','auto') and proof['execution_authorized'] and facts['resource_feasible'] is True else None
    authorized=selected is not None
    cpu=dict(id='cpu-portable',**facts,qualification=proof['status'],execution_authorized=authorized,stages=stages,estimated_seconds=None)
    gpu=dict(id='current-gpu-hybrid',resource_feasible=None,resource_reasons=['MANUAL_HISTORICAL_SCOPE_ONLY'],qualification='HISTORICAL_SCOPE_ONLY',execution_authorized=False,estimated_seconds=None)
    from .registry import capabilities
    v=dict(schema='adaptive-plan-v2',request=r,intent_id=digest(r),snapshot=snapshot,candidates=[cpu,gpu],capabilities=capabilities(r),selected=selected,
        resolved_execution=dict(candidate='cpu-portable',execution_preference='cpu',quality=r['quality'],operation=r['operation'],scope=proof['status']) if selected else None,
        qualification=proof,execution_authorized=authorized,resource_feasible=facts['resource_feasible'],policy='adaptive-resource-policy-v2',no_reservation=True,
        status='ENGINEERING_ONLY',native_status='NATIVE_REQUALIFICATION_PENDING',CPU_ENABLED=False,AUTO_ENABLED=False,PRODUCTION_ENABLED=False,
        feedback=facts['message'] if facts['resource_feasible'] is not True else '选用CPU省显存处理' if selected else '当前输入、操作、运行时或构建尚未获得CPU执行资格')
    v['plan_id']=digest(v);return v

def plan(request,snapshot=None,*,qualification=None,workspace=None,_clock=None):
    from .memory_snapshot import collect
    r=normalize(request);return assemble(r,collect(workspace) if snapshot is None else snapshot,authority(r,qualification),now=_clock)

def validate(v):
    need(type(v) is dict and v.get('schema')=='adaptive-plan-v2','ADAPTIVE_PLAN_INVALID')
    need(v.get('plan_id')==digest({k:x for k,x in v.items() if k!='plan_id'}),'ADAPTIVE_PLAN_CHANGED')
    # Replay recorded facts at their recorded clock. New execution reauthenticates.
    stale='SNAPSHOT_STALE' in v['candidates'][0]['resource_reasons']
    need(v==assemble(v['request'],v['snapshot'],v['qualification'],now=v['snapshot']['captured_at']+(61 if stale else 0)),'ADAPTIVE_PLAN_CHANGED')
    return v
