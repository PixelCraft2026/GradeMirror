"""Third v4 capability: exact committed recipe/result, no compute permission."""
from pathlib import Path
from ..common import atomic_json
from ..cpu_replay.contract import binding,verify,need
from .first_contract import read
SCHEMA='adaptive-first-result-authority-v1'
ACTIONS=('status','cancel','preview','export','release','keep_alive','qualify')
def recipe_scope(c,spec,directory,m):
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(c):
  from .portable_contract import recipe_scope as portable_scope
  return portable_scope(c,spec,directory,m)
 from .calibration_contract import measurement
 if measurement(c):
  from .calibration_contract import recipe_scope as calibration_scope
  return calibration_scope(c,spec,directory,m)
 need(c['schema']=='cpu-application-execution-v4' and c['TEST_ONLY'] is False and Path(directory).resolve()==Path(c['bundle']).resolve()==Path(spec['semantics']['bundle_directory']).resolve(),'FIRST_RESULT_RECIPE_DIRECTORY')
 if c['phase'] in ('fast_recovery','fast_recovery_tail'):
  q=read(c['qualification']['path'])
  from .first_parent import validate
  validate(c['parent_bundle'],q,m);need(m['version']==2,'FIRST_RECOVERY_FAST_ONLY')
 else:
  need(m['producer_build']==c['executor_build'] and m['provenance']['TEST_ONLY'] is False and all(m['provenance'][k]==spec[k] for k in ('job_id','attempt_id')) and m['mathematical']['inputs']=={k+'_identity':c[k+'_identity'] for k in ('source','reference')} and m['mathematical']['settings']==c['settings'] and m['version']==(2 if c['phase']=='fit' else 3),'FIRST_RESULT_REAL_PRODUCER')
 return True

def publish(host):
 c=host.context
 from ..cpu_recipe.validation import complete
 directory=Path(host.sem['bundle_directory']);m,mark=complete(directory);recipe_scope(c,host.spec,directory,m);files=[binding(directory/n) for n in ('manifest.json','COMPLETE.json')]+[binding(directory/x['path']) for x in m['assets']]
 value=dict(schema=SCHEMA,context=binding(c['manifest_path']),qualification=c['qualification'],job_id=host.spec['job_id'],attempt_id=host.spec['attempt_id'],executor_build=c['executor_build']['sha256'],recipe=dict(directory=str(directory),bundle_id=m['bundle_id'],math_id=m['math_id'],version=m['version']),files=files,binding=host.binding,actions=list(ACTIONS),compute_authorized=False,CPU=False,AUTO=False,PRODUCTION=False)
 atomic_json(host.path/'result-authority.json',value);return value
def authorize(c,action,args):
 from ..gm004a_operations import OperationStore
 op,spec=OperationStore(Path(c['workspace'])/'store').path(args['job_id'],args['attempt_id']);need(spec['semantics']['instance_context']==binding(c['manifest_path']),'FIRST_RESULT_OPERATION')
 p=op/'result-authority.json'
 if not p.is_file():need(action in ('status','cancel'),'FIRST_RESULT_NOT_READY');return
 value=read(p);fields={'schema','context','qualification','job_id','attempt_id','executor_build','recipe','files','binding','actions','compute_authorized','CPU','AUTO','PRODUCTION'}
 need(type(value)is dict and set(value)==fields and value['schema']==SCHEMA and value['context']==binding(c['manifest_path']) and value['qualification']==c['qualification'] and all(value[k]==args[k] for k in ('job_id','attempt_id')) and value['executor_build']==c['executor_build']['sha256'] and value['actions']==list(ACTIONS) and action in ACTIONS and all(value[k] is False for k in ('compute_authorized','CPU','AUTO','PRODUCTION')),'FIRST_RESULT_CAPABILITY')
 for row in value['files']:verify(row)
 from ..cpu_recipe.validation import complete
 m,_=complete(value['recipe']['directory']);recipe_scope(c,spec,value['recipe']['directory'],m);expected_files=[binding(Path(value['recipe']['directory'])/n) for n in ('manifest.json','COMPLETE.json')]+[binding(Path(value['recipe']['directory'])/x['path']) for x in m['assets']];need(value['files']==expected_files,'FIRST_RESULT_EXACT_ASSET_SET');need((m['bundle_id'],m['math_id'],m['version'])==tuple(value['recipe'][k] for k in ('bundle_id','math_id','version')),'FIRST_RESULT_RECIPE_CHANGED')
 if 'binding' in args:need(args['binding']==value['binding'],'CPU_RESULT_BINDING_STALE')
