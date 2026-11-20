"""v5 registration in the existing context index; no new store or lease."""
from pathlib import Path
from ..common import atomic_json,reject_reparse
from ..cpu_replay.contract import binding,verify,need,FLAGS
from .native_contract import strict_load
from .native_context import FIELDS as OLD_FIELDS
from .independent_contract import SCHEMA,E,W,authorize,POLICY
from .build import current_closure
FIELDS=OLD_FIELDS|{'runtime_layout','resource_selection'}

def load(path,*,cleanup=False):
 p=Path(path).resolve();reject_reparse(p);need(p.is_relative_to(W) and p.parent.name=='contexts' and p.parent.parent.name=='.adaptive','INDEPENDENT_CONTEXT_POINTER')
 from ..cpu_first_fit.contract import _active
 active=_active.get()
 if active is not None and active.get('schema')==SCHEMA and active['manifest_path']==str(p):
  index=strict_load(p.parent.parent/'context-index.json');need(index.get(active['id'])==binding(p),'INDEPENDENT_ACTIVE_CONTEXT_CHANGED');verify(active['qualification']);verify(active['runtime_layout']);verify(active['resource_selection']);return active
 c=strict_load(p);index=strict_load(p.parent.parent/'context-index.json')
 need(type(c)is dict and set(c)==FIELDS and c['schema']==SCHEMA and c['manifest_path']==str(p) and index.get(c['id'])==binding(p),'INDEPENDENT_CONTEXT_REGISTRATION')
 q=authorize(c['qualification']['path'],c['request'],cleanup=cleanup);ws=p.parent.parent.parent;out=ws/'operations'/c['id'];test=q['TEST_ONLY']
 need(c['qualification']==binding(q['manifest_path']) and c['flags']==FLAGS and c['TEST_ONLY'] is test and c['metadata_only'] is test and c['phase']==('frame' if test else 'render') and c['mode']==c['request']['quality'],'INDEPENDENT_CONTEXT_AUTHORITY')
 need(c['workspace']==str(ws) and ws.is_relative_to(W/('fixtures' if test else 'native')) and c['output_root']==str(out) and c['evidence_root']==str(ws/'.adaptive/logs'/c['id']) and c['pool_root']==str(ws/'pool'),'INDEPENDENT_CONTEXT_PATHS')
 paths=dict(store=str(ws/'store'),operations=str(out),exports=str(ws/'exports'),pool=str(ws/'pool'),logs=c['evidence_root'],temporary=str(out/'tmp'),recipes=str(out))
 need(c['paths']==paths and c['parent_operation'] is None and c['source']==q['source'] and c['reference']==q['reference'] and c['source_identity']==q['source_identity'] and c['reference_identity']==q['reference_identity'],'INDEPENDENT_CONTEXT_INPUTS')
 need(c['lease_policy']==q['lease_policy'] and c['lease_seconds']==120 and c['request_seconds']==120 and c['force_grace_seconds']==30 and c['prepared_clock_shift']==0,'INDEPENDENT_CONTEXT_TIMING')
 if test:
  need(c['fault_point'] in q['fault_points'] and c['pause_at'] in ('','before_ready','before_compute','prepared') and c['fault_action']=='exit' and c['bundle'] is None and c['science_allocation'] is None,'INDEPENDENT_TEST_CONTEXT')
  m=q['frame_context']
 else:
  pkg=q['packages'][c['mode']];m=strict_load(Path(pkg['directory'])/'manifest.json')['mathematical']
  need(c['bundle']=={k:pkg[k] for k in ('directory','bundle_id','math_id','version')} and c['science_allocation']==q['allocations'][c['mode']] and c['fault_point']=='' and c['pause_at']=='' and c['fault_action']=='raise','INDEPENDENT_NATIVE_CONTEXT')
 need(all(c[k]==m[k] for k in ('settings','profile','color_policy')) and c['runtime']==q['runtime'] and c['interpreters']==q['interpreters'] and c['runtime_layout']==q['runtime_layout'],'INDEPENDENT_CONTEXT_RUNTIME_MATH')
 verify(c['resource_selection']);from .independent_selection import validate_binding
 row=validate_binding(strict_load(c['resource_selection']['path']),c['request']);need(row['qualification']==c['qualification'],'INDEPENDENT_SELECTION_QUALIFICATION')
 need(cleanup or c['executor_build']==q['executor_build']==current_closure(native=True),'APPLICATION_EXECUTOR_CHANGED');return c

def register(workspace,request,qualification,identifier,mode,*,fault='',pause='',parent=None,resource_selection=None):
 need(parent is None and resource_selection is not None,'INDEPENDENT_SELECTION_REQUIRED');q=authorize(qualification,request);test=q['TEST_ONLY'];ws=Path(workspace).resolve();reject_reparse(ws)
 need(ws.is_relative_to(W/('fixtures' if test else 'native')),'QUALIFICATION_WORKSPACE_SCOPE');verify(resource_selection)
 from .independent_selection import validate_binding
 row=validate_binding(strict_load(resource_selection['path']),request);need(row['qualification']==binding(qualification),'INDEPENDENT_SELECTION_QUALIFICATION')
 if test:m=q['frame_context'];bundle=None;allocation=None
 else:
  need(not fault and not pause,'INDEPENDENT_NATIVE_FAULT_FORBIDDEN');pkg=q['packages'][mode];m=strict_load(Path(pkg['directory'])/'manifest.json')['mathematical'];bundle={k:pkg[k] for k in ('directory','bundle_id','math_id','version')};allocation=q['allocations'][mode]
 out=ws/'operations'/identifier;out.mkdir(parents=True);p=ws/'.adaptive/contexts'/(identifier+'.json')
 c=dict(schema=SCHEMA,id=identifier,phase='frame' if test else 'render',mode=mode,TEST_ONLY=test,metadata_only=test,workspace=str(ws),manifest_path=str(p),request=request,source=q['source'],reference=q['reference'],source_identity=q['source_identity'],reference_identity=q['reference_identity'],executor_build=q['executor_build'],output_root=str(out),evidence_root=str(ws/'.adaptive/logs'/identifier),pool_root=str(ws/'pool'),qualification=binding(qualification),flags=FLAGS,fault_point=fault,fault_action='exit' if test else 'raise',pause_at=pause,prepared_clock_shift=0,lease_seconds=120,request_seconds=120,force_grace_seconds=30,bundle=bundle,paths=dict(store=str(ws/'store'),operations=str(out),exports=str(ws/'exports'),pool=str(ws/'pool'),logs=str(ws/'.adaptive/logs'/identifier),temporary=str(out/'tmp'),recipes=str(out)),parent_operation=None,lease_policy=q['lease_policy'],runtime=q['runtime'],runtime_layout=q['runtime_layout'],interpreters=q['interpreters'],settings=m['settings'],profile=m['profile'],color_policy=m['color_policy'],science_allocation=allocation,resource_selection=resource_selection)
 atomic_json(p,c)
 from ..store import locked
 with locked(ws/'.adaptive/registry.lock'):
  ip=ws/'.adaptive/context-index.json';index=strict_load(ip) if ip.exists() else {};need(identifier not in index,'APPLICATION_CONTEXT_REUSE');index[identifier]=binding(p);atomic_json(ip,index)
 return c

def operation(c,sem):
 need(sem.get('adaptive_application') is True and sem.get('TEST_ONLY') is c['TEST_ONLY'] and sem['executor_build']==c['executor_build']['sha256'] and sem['source_sha256']==c['source']['sha256'] and sem['mode']==c['mode'] and sem['output_root']==c['output_root'] and sem.get('adaptive_parent_operation') is None,'INDEPENDENT_OPERATION_CONTEXT')
 if not c['TEST_ONLY']:need(all(sem[k]==c['bundle'][k] for k in ('bundle_id','math_id')) and sem['bundle_directory']==c['bundle']['directory'],'INDEPENDENT_OPERATION_PACKAGE')
 return c
