"""Version 3 plan over the same resource policy and exact v5 capability."""
from .intent import normalize,digest,need
from .planner_v2 import projection,assemble
from . import independent_contract as contract

def authority(request,qualification):
 from ..cpu_replay.contract import binding
 try:
  q=contract.authorize(qualification,projection(request));return dict(status='GM059_TEST_ONLY' if q['TEST_ONLY'] else 'GM059_FIXED_REPLAY_V5',execution_authorized=q['phase'] in ('frame','native'),evidence=binding(qualification))
 except Exception as e:return dict(status='QUALIFICATION_REJECTED',execution_authorized=False,evidence=None,reason=getattr(e,'code',type(e).__name__))

def project_snapshot(snapshot):
 # Only the new advisory vocabulary changes. Never infer exit or usage.
 import copy
 if type(snapshot)is dict and type(snapshot.get('pool'))is dict and snapshot['pool'].get('state') in ('BOUND','STARTING'):
  snapshot=copy.deepcopy(snapshot);raw=snapshot['pool']['state'];snapshot['pool']['state']={'BOUND':'RESIDENT','STARTING':'BUSY'}[raw]
  snapshot['diagnostics'].append(dict(code='GM059_POOL_STATE_PROJECTION',raw_state=raw,advisory_state=snapshot['pool']['state'],exit_or_usage_inferred=False))
 return snapshot

def assemble_v3(request,snapshot,proof,*,now=None):
 snapshot=project_snapshot(snapshot)
 v=assemble(request,snapshot,proof,now=now);v.pop('plan_id');v.update(schema='adaptive-plan-v3',policy='adaptive-independent-runtime-v3',status='INDEPENDENT_COMPATIBILITY_QUALIFICATION_ONLY',native_status='GM059_EXACT_REPLAY_QUALIFICATION_ONLY' if proof['execution_authorized'] else 'NATIVE_REQUALIFICATION_PENDING');v['plan_id']=digest(v);return v

def plan(request,snapshot=None,*,qualification=None,workspace=None,_clock=None):
 from .memory_snapshot import collect
 r=normalize(request);return assemble_v3(r,collect(workspace) if snapshot is None else snapshot,authority(r,qualification),now=_clock)

def validate(v):
 need(type(v)is dict and v.get('schema')=='adaptive-plan-v3' and v.get('plan_id')==digest({k:x for k,x in v.items() if k!='plan_id'}),'INDEPENDENT_PLAN_CHANGED')
 stale='SNAPSHOT_STALE' in v['candidates'][0]['resource_reasons'];need(v==assemble_v3(v['request'],v['snapshot'],v['qualification'],now=v['snapshot']['captured_at']+(61 if stale else 0)),'INDEPENDENT_PLAN_CHANGED');return v
