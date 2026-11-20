"""Immutable original intent plus explicit CPU execution projection."""
from pathlib import Path
from ..common import atomic_json,strict_load
from ..cpu_replay.contract import binding,verify
from ..store import locked
from .intent import normalize,digest,need
from .planner_v2 import projection,authority,validate

def validate_binding(row,request):
    if isinstance(row,dict) and row.get('schema')=='gm064-selection-v1':
        from .portable_plan import validate_selection
        return validate_selection(row,request)
    if isinstance(row,dict) and row.get('schema')=='adaptive-selection-v3':
        from .independent_selection import validate_binding as independent_validate
        return independent_validate(row,request)
    fields={'schema','request_key','original_request','intent_id','execution_request','execution_intent_id','resolved_execution','qualification','plan_id'}
    need(type(row) is dict and set(row)==fields and row['schema']=='adaptive-selection-v2','RESOURCE_SELECTION_FIELDS')
    original=normalize(row['original_request']);execution=projection(original)
    need(execution==request==row['execution_request'] and row['intent_id']==digest(original) and row['execution_intent_id']==digest(execution),'RESOURCE_SELECTION_PROJECTION')
    need(original['execution_preference'] in ('auto','cpu') and row['resolved_execution']==dict(candidate='cpu-portable',execution_preference='cpu',quality=original['quality'],operation=original['operation'],scope='TEST_ONLY_EXACT_SCOPE'),'RESOURCE_SELECTION_SCOPE')
    verify(row['qualification']);return row

def submit(value,key,workspace,*,experimental=False,qualification=None):
    v=validate(value);r=v['request'];need(experimental is True,'EXECUTION_NOT_QUALIFIED')
    need(type(key) is str and 0<len(key)<=128,'APPLICATION_REQUEST_KEY')
    from .qualification import authorize
    q=authorize(qualification,projection(r));w=Path(workspace).resolve()
    need(w.is_relative_to(Path(q['workspace_root'])),'QUALIFICATION_WORKSPACE_SCOPE')
    path=w/'.adaptive/selections'/(digest(key)+'.json')
    with locked(w/'.adaptive/selection.lock'):
        if path.exists():
            row=strict_load(path);validate_binding(row,projection(r))
            need(row['original_request']==r and row['intent_id']==digest(r),'REQUEST_KEY_CONFLICT')
            need(row['qualification']==binding(qualification),'RESOURCE_SELECTION_QUALIFICATION_CHANGED')
        else:
            proof=authority(r,qualification)
            need(proof['execution_authorized'] and r['execution_preference'] in ('auto','cpu'),'EXECUTION_NOT_QUALIFIED')
            from .resource_policy_v2 import fresh
            facts=fresh(projection(r),w,qualification,key,'submit')
            atomic_json(w/'.adaptive/admissions'/(digest(key)+'-v2-submit.json'),facts)
            need(facts['resource_feasible'] is True,'RESOURCE_NOT_READY',facts['message'])
            # Incoming plan must bind the same independently reauthenticated scope.
            need(v['qualification']==proof and v['resolved_execution'] is not None,'RESOURCE_PLAN_NOT_SELECTED')
            row=dict(schema='adaptive-selection-v2',request_key=key,original_request=r,intent_id=digest(r),execution_request=projection(r),execution_intent_id=digest(projection(r)),resolved_execution=v['resolved_execution'],qualification=binding(qualification),plan_id=v['plan_id'])
            validate_binding(row,row['execution_request']);atomic_json(path,row)
        from .planner import plan_v1
        from .service import _submit_v1
        from .memory_snapshot import collect
        s=collect(w)
        legacy=dict(schema='adaptive-snapshot-v1',captured_at=s['captured_at'],available_ram=s['physical']['available_bytes'],available_commit=s['legacy_process_commit_available'],available_vram=None,existing=dict(rss=0,commit=0,vram=0,state='FREE'),own_tree_exited=True)
        result=_submit_v1(plan_v1(row['execution_request'],legacy),key,w,experimental=True,qualification=qualification,_selection=binding(path))
        return dict(result,original_intent=r,intent_id=row['intent_id'],resolved_execution=row['resolved_execution'],selection=binding(path),feedback='选用CPU省显存处理')
