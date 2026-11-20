"""v4 workspace contexts: pending intent is separate from committed recipe IDs."""
from pathlib import Path
from ..common import atomic_json,reject_reparse
from ..cpu_replay.contract import FLAGS,binding,verify,need
from .first_contract import SCHEMA,E,W,POLICY,authorize,read
from .build import current_closure
FIELDS={'schema','id','phase','mode','TEST_ONLY','metadata_only','workspace','manifest_path','request','source','reference','source_identity','reference_identity','executor_build','output_root','evidence_root','pool_root','qualification','flags','fault_point','fault_action','pause_at','prepared_clock_shift','lease_seconds','request_seconds','force_grace_seconds','bundle','paths','parent_operation','lease_policy','runtime','interpreters','settings','profile','color_policy','science_allocation','science','cancel_after_updates','parent_bundle'}
def paths(ws,out):return dict(store=str(ws/'store'),operations=str(out),exports=str(ws/'exports'),pool=str(ws/'pool'),logs=str(ws/'.adaptive/logs'/out.name),temporary=str(out/'tmp'),recipes=str(out))
def load(path,*,cleanup=False):
 p=Path(path).resolve();reject_reparse(p);need(p.is_relative_to(W) and p.parent.name=='contexts' and p.parent.parent.name=='.adaptive','FIRST_CONTEXT_POINTER');c=read(p);need(type(c)is dict and set(c)==FIELDS and c['schema']==SCHEMA and c['manifest_path']==str(p) and read(p.parent.parent/'context-index.json').get(c['id'])==binding(p),'FIRST_CONTEXT_REGISTRATION')
 q=authorize(c['qualification']['path'],c['request'],cleanup=cleanup);ws=p.parent.parent.parent;out=ws/'operations'/c['id'];fit=c['request']['operation']=='fit';recovery=q['schema']=='adaptive-first-recovery-qualification-v1';partial=q['schema']=='adaptive-first-actual-b-recovery-v1';phase=next(iter(q['allocations'])) if recovery or partial else 'fit' if fit else 'refine'
 need(c['phase']==phase and c['mode']==('fast' if fit or recovery else 'fine') and c['TEST_ONLY'] is False and c['metadata_only'] is False and c['flags']==FLAGS,'FIRST_CONTEXT_PHASE')
 need(c['qualification']==binding(q['manifest_path']) and c['workspace']==str(ws) and c['output_root']==str(out) and c['pool_root']==str(ws/'pool') and c['evidence_root']==str(ws/'.adaptive/logs'/c['id']) and c['paths']==paths(ws,out),'FIRST_CONTEXT_PATHS')
 need(all(c[k]==q[k] for k in ('source','reference','source_identity','reference_identity','settings','profile','color_policy','runtime','interpreters')) and c['bundle']==(q['parent_package']['directory'] if recovery else str(out/('fast-recipe' if fit else 'fine-recipe'))) and c['parent_bundle']==(None if fit else q['parent_package']),'FIRST_CONTEXT_INPUTS')
 need(c['science_allocation']==q['allocations'][phase] and c['science']==read(E/'allocations.json')['allocations'][c['science_allocation']]['budget'],'FIRST_CONTEXT_ALLOCATION')
 need(c['parent_operation'] is None and c['lease_policy']==POLICY and c['lease_seconds']==120 and c['request_seconds']==120 and c['force_grace_seconds']==30 and c['prepared_clock_shift']==0 and c['fault_point']=='' and c['fault_action']=='raise' and c['pause_at']=='' and c['cancel_after_updates']==0,'FIRST_CONTEXT_LIMITS')
 need(cleanup or c['executor_build']==q['executor_build']==current_closure(native=True),'APPLICATION_EXECUTOR_CHANGED');return c
def register(workspace,request,qualification,identifier,mode,**kw):
 need(set(kw)<={'fault','pause','parent'} and not any(kw.values()),'FIRST_NATIVE_TEST_INSTRUCTION');q=authorize(qualification,request);ws=Path(workspace).resolve();reject_reparse(ws);need(ws.is_relative_to(W),'FIRST_CONTEXT_SCOPE');out=ws/'operations'/identifier;out.mkdir(parents=True);p=ws/'.adaptive/contexts'/(identifier+'.json');fit=request['operation']=='fit';recovery=q['schema']=='adaptive-first-recovery-qualification-v1';partial=q['schema']=='adaptive-first-actual-b-recovery-v1';phase=next(iter(q['allocations'])) if recovery or partial else 'fit' if fit else 'refine';allocation=q['allocations'][phase]
 c=dict(schema=SCHEMA,id=identifier,phase=phase,mode=mode,TEST_ONLY=False,metadata_only=False,workspace=str(ws),manifest_path=str(p),request=request,executor_build=current_closure(native=True),output_root=str(out),evidence_root=str(ws/'.adaptive/logs'/identifier),pool_root=str(ws/'pool'),qualification=binding(qualification),flags=FLAGS,fault_point='',fault_action='raise',pause_at='',prepared_clock_shift=0,lease_seconds=120,request_seconds=120,force_grace_seconds=30,bundle=q['parent_package']['directory'] if recovery else str(out/('fast-recipe' if fit else 'fine-recipe')),paths=paths(ws,out),parent_operation=None,lease_policy=POLICY,science_allocation=allocation,science=read(E/'allocations.json')['allocations'][allocation]['budget'],cancel_after_updates=0,parent_bundle=None if fit else q['parent_package'])
 c.update({k:q[k] for k in ('source','reference','source_identity','reference_identity','settings','profile','color_policy','runtime','interpreters')});atomic_json(p,c)
 from ..store import locked
 with locked(ws/'.adaptive/registry.lock'):
  ixp=ws/'.adaptive/context-index.json';ix=read(ixp) if ixp.exists() else {};need(identifier not in ix,'APPLICATION_CONTEXT_REUSE');ix[identifier]=binding(p);atomic_json(ixp,ix)
 return c
def operation(c,sem):
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(c):
  from .portable_context import operation as portable_operation
  return portable_operation(c,sem)
 from .calibration_contract import measurement
 if measurement(c):
  from .calibration_context import operation as calibration_operation
  return calibration_operation(c,sem)
 fit=c['phase']=='fit';refine=c['phase']=='refine';cpu_refine=c['phase'] in ('refine','fine_recovery');need(sem.get('adaptive_application') is True and sem.get('TEST_ONLY') is False and sem['executor_build']==c['executor_build']['sha256'] and sem['source_sha256']==c['source']['sha256'] and sem['output_root']==c['output_root'] and sem['mode']==c['mode'] and sem['bundle_directory']==c['bundle'] and sem.get('first_fit') is fit and sem.get('refine_prepare') is refine and sem.get('cpu_refine') is cpu_refine and sem.get('adaptive_parent_operation') is None,'FIRST_OPERATION_CONTEXT');return c
