"""v3 immutable auto intent and CPU projection, reusing original service/store."""
from pathlib import Path
from ..common import atomic_json
from .native_contract import strict_load
from ..cpu_replay.contract import binding,verify,need
from ..store import locked
from .intent import normalize,digest
from .planner_v2 import projection
from .independent_plan import validate,authority
from .independent_contract import authorize,W
FIELDS={'schema','request_key','original_request','intent_id','execution_request','execution_intent_id','resolved_execution','qualification','plan_id'}

def validate_binding(row,request):
 need(type(row)is dict and set(row)==FIELDS and row['schema']=='adaptive-selection-v3','INDEPENDENT_SELECTION_FIELDS');original=normalize(row['original_request']);execution=projection(original)
 need(execution==request==row['execution_request'] and row['intent_id']==digest(original) and row['execution_intent_id']==digest(execution) and original['execution_preference'] in ('auto','cpu'),'INDEPENDENT_SELECTION_PROJECTION')
 resolved=row['resolved_execution'];need(type(resolved)is dict and resolved==dict(candidate='cpu-portable',execution_preference='cpu',quality=original['quality'],operation=original['operation'],scope=resolved.get('scope')) and resolved['scope'] in ('GM059_TEST_ONLY','GM059_FIXED_REPLAY_V5'),'INDEPENDENT_SELECTION_SCOPE');verify(row['qualification']);return row

def submit(value,key,workspace,*,experimental=False,qualification=None):
 v=validate(value);r=v['request'];need(experimental is True and type(key)is str and 0<len(key)<=128,'INDEPENDENT_EXPLICIT_SUBMIT');q=authorize(qualification,projection(r));w=Path(workspace).resolve();need(w.is_relative_to(W/('fixtures' if q['TEST_ONLY'] else 'native')),'QUALIFICATION_WORKSPACE_SCOPE');path=w/'.adaptive/selections'/(digest(key)+'.json')
 with locked(w/'.adaptive/selection.lock'):
  if path.exists():
   row=validate_binding(strict_load(path),projection(r));need(row['original_request']==r and row['qualification']==binding(qualification),'REQUEST_KEY_CONFLICT')
  else:
   proof=authority(r,qualification);need(proof['execution_authorized'] and r['execution_preference'] in ('auto','cpu') and v['qualification']==proof and v['resolved_execution'] is not None,'EXECUTION_NOT_QUALIFIED')
   from .resource_policy_v2 import fresh
   facts=fresh(projection(r),w,qualification,key,'submit');atomic_json(w/'.adaptive/admissions'/(digest(key)+'-v3-submit.json'),facts);need(facts['resource_feasible'] is True,'RESOURCE_NOT_READY',facts['message'])
   row=dict(schema='adaptive-selection-v3',request_key=key,original_request=r,intent_id=digest(r),execution_request=projection(r),execution_intent_id=digest(projection(r)),resolved_execution=v['resolved_execution'],qualification=binding(qualification),plan_id=v['plan_id']);validate_binding(row,row['execution_request']);atomic_json(path,row)
 # The selection lock never covers IPC/worker execution; original submit.lock
 # continues to serialize registration and original OperationStore owns compute.
 from .planner import plan_v1,snapshot
 from .service import _submit_v1
 s=snapshot();s.update(existing=dict(rss=0,commit=0,vram=0,state='FREE'),own_tree_exited=True)
 result=_submit_v1(plan_v1(row['execution_request'],s),key,w,experimental=True,qualification=qualification,_selection=binding(path))
 return dict(result,original_intent=r,intent_id=row['intent_id'],resolved_execution=row['resolved_execution'],selection=binding(path),feedback='选用CPU省显存处理')
