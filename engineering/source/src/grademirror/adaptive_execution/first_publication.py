"""Metadata-only recovery of trusted saved recipe material; never starts compute."""
from pathlib import Path
from ..common import atomic_json
from ..cpu_replay.contract import binding,verify,need
from ..store import locked
from .first_contract import E,read
def scoped(c):
 return c.get('schema')=='cpu-application-execution-v4' or (c.get('schema')=='cpu-application-execution-v2' and Path(c['qualification']['path']).resolve().parent==E/'fixtures/factories')
def _exited(path):
 p=path/'reclaim-receipt.json'
 if not p.is_file():return False
 r=read(p);return r.get('actual_wait') is True and r.get('tree_empty') is True and bool(r.get('identities')) and all(x.get('exited') is True for x in r['identities'])
def _record(op,directory):
 atomic_json(op/'recipe-publication-recovered.json',dict(recipe=binding(directory/'COMPLETE.json'),reclaim=binding(op/'reclaim-receipt.json'),metadata_only=True,fit=0,CG=0,render=0,LLF=0))
def recover(spec,c):
 if not scoped(c):return False
 from ..gm004a_operations import OperationStore
 op,_=OperationStore(c['paths']['store']).path(spec['job_id'],spec['attempt_id']);directory=Path(spec['semantics']['bundle_directory'])
 if (directory/'COMPLETE.json').exists() or not _exited(op):return False
 from ..cpu_recipe import generated,generated_fine
 from ..cpu_recipe.assets import parse_file
 from ..cpu_recipe.primitives import verify_file,file_record
 mpath=directory/'manifest.json'
 if not mpath.is_file():
  if c['schema']!='cpu-application-execution-v4':return False
  if c['phase']=='refine':
   work=op/'session/P3';required=[work/n for n in ('b.npz','prepare-result.json','owned-wait.json','solver/solver-cleanup.json')]
   if not all(p.is_file() for p in required):return False
   result=read(work/'prepare-result.json');wait=read(work/'owned-wait.json');solver=read(work/'solver/solver-cleanup.json')
   need(result['status']=='PASS' and result['counts']['scalar_CG']==3 and wait['actual_worker_handle_wait'] is True and wait['tree_empty'] is True and wait['exit_code']==0 and solver['actual_worker_handle_wait'] is True and solver['tree_empty'] is True and solver['exit_code']==0,'FIRST_SAVED_P3_UNTRUSTED')
   from ..cpu_refine.host import p3_metadata
   p3=p3_metadata(c,result,work/'b.npz');intent=dict(semantics=spec['semantics'],job_id=spec['job_id'],attempt_id=spec['attempt_id']);generated_fine.publish(directory,c['parent_bundle']['directory'],work/'b.npz',result['solver'],p3,intent,c);_record(op,directory);return True
  if c['phase']!='fit':return False
  work=op/'session/fit';required=[work/n for n in ('parameters.npz','fit-receipt.json','owned-wait.json')]
  if not all(p.is_file() for p in required):return False
  receipt=read(work/'fit-receipt.json');wait=read(work/'owned-wait.json');params=parse_file(work/'parameters.npz','r0')
  need(wait['actual_worker_handle_wait'] is True and wait['tree_empty'] is True and wait['exit_code']==0 and receipt['status']=='COMPLETE' and receipt['producer_build']==c['executor_build']['sha256'] and receipt['parameter_sha256']==params['value_sha256'] and receipt['counts']==dict(fit=1,updates=1000,forward=2001,model_construct=1,optimizer_construct=1),'FIRST_SAVED_FIT_UNTRUSTED')
  intent=dict(schema='cpu-first-fit-intent-v1',semantics=spec['semantics'],job_id=spec['job_id'],attempt_id=spec['attempt_id']);generated.publish(directory,work/'parameters.npz',intent,receipt,c);_record(op,directory);return True
 with locked(directory/'.pack/target.lock',timeout=60):
  if (directory/'COMPLETE.json').is_file():return False
  m=read(mpath);native=c['schema']=='cpu-application-execution-v4';version=m.get('version');need(version in (2,3),'FIRST_RECOVER_VERSION')
  (generated.structure if version==2 else generated_fine.structure)(m)
  need(m['producer_build']==c['executor_build'] and m['provenance']['job_id']==spec['job_id'] and m['provenance']['attempt_id']==spec['attempt_id'] and m['provenance']['TEST_ONLY'] is (not native) and m['mathematical']['inputs']==dict(source_identity=c['source_identity'],reference_identity=c['reference_identity']) and m['mathematical']['settings']==c['settings'],'FIRST_SAVED_RECIPE_AUTHORITY')
  for row in m['assets']:verify_file(directory/row['path'],row)
  values=parse_file(directory/'r0/parameters.npz','r0');need({k:v for k,v in values.items() if k!='expanded_bytes'}==m['mathematical']['r0_parameters'],'FIRST_SAVED_PARAMETER_VALUES')
  if version==3:
   coeff=generated_fine.coefficients((directory/'p3/b.npz').read_bytes(),m['mathematical']['p3']['reference_wh']);need(coeff==m['mathematical']['p3']['b'],'FIRST_SAVED_B_VALUES')
  mark=dict(format='cpu-recipe-complete-v'+str(version),bundle_id=m['bundle_id'],math_id=m['math_id'],manifest=file_record(mpath,name='manifest.json'),operation_id=spec['job_id'],attempt_id=spec['attempt_id'])
  module=generated if version==2 else generated_fine;module.validate_pending(directory,mark)
  atomic_json(directory/'COMPLETE.json',mark);module.complete(directory)
  rp=directory/'.pack/record.json';record=read(rp);atomic_json(rp,dict(record,state='SUCCEEDED',recovered_from_trusted_saved_material=True))
  atomic_json(op/'recipe-publication-recovered.json',dict(recipe=binding(directory/'COMPLETE.json'),reclaim=binding(op/'reclaim-receipt.json'),metadata_only=True,fit=0,CG=0,render=0,LLF=0))
  return True
