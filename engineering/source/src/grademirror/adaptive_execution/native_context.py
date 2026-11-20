"""Private v3 registration over the existing workspace registry and operation store."""
from pathlib import Path
from ..common import atomic_json,reject_reparse
from .native_contract import strict_load
from ..cpu_replay.contract import FLAGS,binding,verify,need
from .native_contract import SCHEMA,E,W,POLICY,authorize
from .build import current_closure
FIELDS={'schema','id','phase','mode','TEST_ONLY','metadata_only','workspace','manifest_path','request','source','reference','source_identity','reference_identity','executor_build','output_root','evidence_root','pool_root','qualification','flags','fault_point','fault_action','pause_at','prepared_clock_shift','lease_seconds','request_seconds','force_grace_seconds','bundle','paths','parent_operation','lease_policy','runtime','interpreters','settings','profile','color_policy','science_allocation'}
def load(path,*,cleanup=False):
    from .independent_contract import W as independent_W
    if Path(path).resolve().is_relative_to(independent_W):
        from .independent_context import load as independent_load
        return independent_load(path,cleanup=cleanup)
    p=Path(path).resolve();reject_reparse(p)
    need(p.is_relative_to(W) and p.parent.name=='contexts' and p.parent.parent.name=='.adaptive','NATIVE_CONTEXT_POINTER')
    c=strict_load(p);index=strict_load(p.parent.parent/'context-index.json')
    need(type(c)is dict and set(c)==FIELDS and c['schema']==SCHEMA and c['manifest_path']==str(p) and index.get(c['id'])==binding(p),'NATIVE_CONTEXT_REGISTRATION')
    q=authorize(c['qualification']['path'],c['request'],cleanup=cleanup);pkg=q['packages'][c['mode']]
    ws=p.parent.parent.parent;out=ws/'operations'/c['id']
    need(c['qualification']==binding(q['manifest_path']) and c['flags']==FLAGS and c['TEST_ONLY'] is False and c['metadata_only'] is False and c['phase']=='render' and c['mode']==c['request']['quality'],'NATIVE_CONTEXT_AUTHORITY')
    need(c['workspace']==str(ws) and ws.is_relative_to(W) and c['output_root']==str(out) and c['evidence_root']==str(ws/'.adaptive/logs'/c['id']) and c['pool_root']==str(ws/'pool'),'NATIVE_CONTEXT_PATHS')
    paths=dict(store=str(ws/'store'),operations=str(out),exports=str(ws/'exports'),pool=str(ws/'pool'),logs=c['evidence_root'],temporary=str(out/'tmp'),recipes=str(out))
    need(c['paths']==paths and c['parent_operation'] is None and c['source']==q['source'] and c['reference'] is None and c['source_identity']==q['source_identity'] and c['reference_identity']==q['reference_identity'] and c['bundle']=={k:pkg[k] for k in ('directory','bundle_id','math_id','version')},'NATIVE_CONTEXT_INPUTS')
    need(c['lease_policy']==POLICY and c['lease_seconds']==120 and c['request_seconds']==120 and c['force_grace_seconds']==30 and c['prepared_clock_shift']==0 and c['fault_point']=='' and c['fault_action']=='raise' and c['pause_at']=='','NATIVE_CONTEXT_TIMING')
    math=strict_load(Path(pkg['directory'])/'manifest.json')['mathematical']
    need(c['settings']==math['settings'] and c['profile']==math['profile'] and c['color_policy']==math['color_policy'],'NATIVE_CONTEXT_MATH_DECLARATION')
    need(c['runtime']==q['runtime'] and c['interpreters']==q['interpreters'] and c['science_allocation']==q['allocations'][c['mode']],'NATIVE_CONTEXT_RUNTIME_ALLOCATION')
    need(cleanup or c['executor_build']==q['executor_build']==current_closure(native=True),'APPLICATION_EXECUTOR_CHANGED')
    return c

def register(workspace,request,qualification,identifier,mode,**kw):
    need(set(kw)<= {'fault','pause','parent'} and not any(kw.values()),'NATIVE_TEST_INSTRUCTION_FORBIDDEN')
    q=authorize(qualification,request);ws=Path(workspace).resolve();reject_reparse(ws);need(ws.is_relative_to(W),'QUALIFICATION_WORKSPACE_SCOPE')
    out=ws/'operations'/identifier;out.mkdir(parents=True)
    pkg=q['packages'][mode];m=strict_load(Path(pkg['directory'])/'manifest.json')['mathematical']
    p=ws/'.adaptive/contexts'/(identifier+'.json')
    c=dict(schema=SCHEMA,id=identifier,phase='render',mode=mode,TEST_ONLY=False,metadata_only=False,workspace=str(ws),manifest_path=str(p),request=request,source=q['source'],reference=None,source_identity=q['source_identity'],reference_identity=q['reference_identity'],executor_build=current_closure(native=True),output_root=str(out),evidence_root=str(ws/'.adaptive/logs'/identifier),pool_root=str(ws/'pool'),qualification=binding(qualification),flags=FLAGS,fault_point='',fault_action='raise',pause_at='',prepared_clock_shift=0,lease_seconds=120,request_seconds=120,force_grace_seconds=30,bundle={k:pkg[k] for k in ('directory','bundle_id','math_id','version')},paths=dict(store=str(ws/'store'),operations=str(out),exports=str(ws/'exports'),pool=str(ws/'pool'),logs=str(ws/'.adaptive/logs'/identifier),temporary=str(out/'tmp'),recipes=str(out)),parent_operation=None,lease_policy=POLICY,runtime=q['runtime'],interpreters=q['interpreters'],settings=m['settings'],profile=m['profile'],color_policy=m['color_policy'],science_allocation=q['allocations'][mode])
    atomic_json(p,c)
    from ..store import locked
    with locked(ws/'.adaptive/registry.lock'):
        ip=ws/'.adaptive/context-index.json';index=strict_load(ip) if ip.exists() else {};need(identifier not in index,'APPLICATION_CONTEXT_REUSE');index[identifier]=binding(p);atomic_json(ip,index)
    return c

def operation(c,sem):
    need(sem.get('adaptive_application') is True and sem.get('TEST_ONLY') is False and sem['executor_build']==c['executor_build']['sha256'] and sem['source_sha256']==c['source']['sha256'] and sem['mode']==c['mode'] and sem['output_root']==c['output_root'] and sem.get('adaptive_parent_operation') is None and sem['bundle_directory']==c['bundle']['directory'] and sem['bundle_id']==c['bundle']['bundle_id'] and sem['math_id']==c['bundle']['math_id'],'NATIVE_OPERATION_CONTEXT')
    return c
