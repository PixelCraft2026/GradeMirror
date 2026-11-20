"""Formal v4 role contracts before all numerical or image side effects."""
from pathlib import Path
from ..cpu_replay.contract import need,binding
from .first_contract import read,authorize as qualification,W
def session_for(path):
 for p in Path(path).parents:
  if (p/'session-spec.json').is_file():return p
  if p==W:break
 need(False,'FIRST_WORKER_SESSION_REQUIRED')
def authorize(role,path,data,c):
 import sys
 effective=role.removeprefix('entry_');category='solver' if effective=='solver' else 'numeric' if effective in ('fit','r0','prepare','cpu') else 'io' if effective=='host' else 'control'
 need(Path(sys.executable).resolve()==Path(c['interpreters'][category]['path']).resolve(),'FIRST_ROLE_INTERPRETER');q=qualification(c['qualification']['path'],c['request'],cleanup=effective in ('owner','application_control'))
 path=Path(path).resolve();dry=role.startswith('entry_');need(not dry or q['phase']=='dry','FIRST_DRY_TICKET_REQUIRED')
 from .calibration_contract import measurement
 if measurement(c) and not dry and effective not in ('owner','host','application_control','control'):
  allowed={'fit':('fit','r0'),'refine':('r0','prepare','solver','cpu'),'fast_recovery':('r0',),'replay_fine':('r0','cpu')}[c['phase']]
  need(effective in allowed,'GM063_ROLE_PHASE')
 if effective in ('application_control','control'):
  fields={'submit':{'request_key'},'status':{'job_id','attempt_id'},'cancel':{'job_id','attempt_id'},'keep_alive':{'job_id','attempt_id','binding'},'release':{'job_id','attempt_id','binding'},'preview':{'job_id','attempt_id','binding','destination','alpha','request_id'},'export':{'job_id','attempt_id','binding','destination','alpha','request_key'},'qualify':{'job_id','attempt_id','binding'}}
  need(type(data)is dict and set(data)=={'action','arguments'} and data['action'] in fields and type(data['arguments'])is dict and (set(data['arguments'])==fields[data['action']] or data['action']=='cancel' and set(data['arguments'])==fields['cancel']|{'expected_child'} and data['arguments']['expected_child'] is None) and path.is_relative_to(Path(c['workspace'])/'.adaptive/control'),'FIRST_CONTROL_PAYLOAD');need(dry or q['phase']=='native','FIRST_DRY_NO_COMPUTE')
  if not dry and data['action']!='submit':
   from .first_result import authorize as result_authority
   result_authority(c,data['action'],data['arguments'])
  return
 session=session_for(path);ss=read(session/'session-spec.json');spec=read(session.parent/'spec.json')
 from .first_context import operation
 need(ss['execution']==c and all(ss[k]==spec[k] for k in ('job_id','attempt_id','semantics')) and operation(c,spec['semantics'])==c and Path(ss['operation_path'])==session.parent,'FIRST_WORKER_OPERATION')
 if effective=='owner':need(path==session/'session-spec.json' and data==ss,'FIRST_OWNER_PAYLOAD');return
 if effective=='host':need(path==session/'host-payload.json' and data==dict(session=str(session)),'FIRST_HOST_PAYLOAD');return
 if not dry:
  need(effective in (('r0',) if c['phase'] in ('fast_recovery','fast_recovery_tail') else ('r0','prepare','cpu') if c['phase']=='fine_recovery' else ('fit','r0') if c['phase']=='fit' else ('r0','prepare','solver','cpu')),'FIRST_ROLE_PHASE')
  need(q['phase']=='native' and read(session/'compute-go.json')=={'bound':True},'FIRST_COMPUTE_GO')
  from .first_science import worker_claim
  worker_claim(c,effective,session.parent)
 if effective in ('solver','cpu'):
  need(type(data.get('address'))is list and len(data['address'])==2 and data['address'][0]=='127.0.0.1' and type(data['address'][1])is int and 1<=data['address'][1]<=65535 and type(data.get('auth_hex'))is str and len(data['auth_hex'])==64 and all(x in '0123456789abcdef' for x in data['auth_hex']),'FIRST_LOOPBACK_IPC_REQUIRED')
 maps=read(session/'shared-mappings.json');shape=c['source_identity']['shape']
 if 'reference' not in maps and (session/'reference-mapping.json').is_file():maps=dict(maps,reference=read(session/'reference-mapping.json'))
 if effective in ('fit','r0'):
  fields={'work','S','reference' if effective=='fit' else 'O','cancel','operation','reservation'}|({'bundle'} if effective=='r0' else set())
  need(type(data)is dict and set(data)==fields and data['work']==str(path.parent) and data['operation']==str(session.parent) and data['cancel']==str(session.parent/'cancel.signal') and data['reservation']==c['science'],'FIRST_NUMERIC_PAYLOAD')
  for key in ('S','reference' if effective=='fit' else 'O'):
   ident=c['reference_identity'] if key=='reference' else c['source_identity'];desc=dict(name=maps[key]['name'],shape=ident['shape'],dtype='float32' if key=='O' else ident['dtype'])
   if key!='O':desc['readonly']=True
   need(data[key]==desc,'FIRST_SHARED_DESCRIPTOR')
  if effective=='r0':need(data['bundle']==(c['bundle'] if c['phase']=='fit' else c['parent_bundle']['directory']),'FIRST_R0_NEW_PACKAGE_ONLY')
 elif effective=='prepare':
  if c['phase']=='fine_recovery':
   from .first_partial import validate
   v=validate(q);need(type(data)is dict and set(data)=={'work','O','b','reference_wh','cancel'} and data['work']==str(path.parent) and data['cancel']==str(session.parent/'cancel.signal') and data['reference_wh']==[1024,1536] and data['b']==v['b'] and data['O']==dict(name=maps['O']['name'],shape=shape,dtype='float32',readonly=True),'FIRST_PARTIAL_PREPARE_PAYLOAD')
  else:
   need(type(data)is dict and set(data)=={'work','O','reference','reference_wh','cancel'} and data['work']==str(path.parent) and data['cancel']==str(session.parent/'cancel.signal') and data['reference_wh']==[c['reference_identity']['shape'][1],c['reference_identity']['shape'][0]],'FIRST_PREPARE_PAYLOAD')
   for key in ('O','reference'):
    ident=c['source_identity'] if key=='O' else c['reference_identity'];need(data[key]==dict(name=maps[key]['name'],shape=ident['shape'],dtype='float32' if key=='O' else ident['dtype'],readonly=True),'FIRST_PREPARE_SHARED_DESCRIPTOR')
 elif effective=='solver':
  from .portable_contract import enabled as portable_enabled
  native_wh=[c['source_identity']['shape'][1],c['source_identity']['shape'][0]] if portable_enabled(c) else [4000,6000] if measurement(c) else [4672,7008]
  reference_shape=c['reference_identity']['shape'] if portable_enabled(c) else [1536,1024,3]
  reference_wh=reference_shape[1::-1]
  need(path.name=='solver-spec.json' and type(data)is dict and set(data)=={'address','auth_hex','native_wh','reference_wh','reference_shape','identity_control','solver_seconds'} and data['native_wh']==native_wh and data['reference_wh']==reference_wh and data['reference_shape']==reference_shape and data['identity_control'] is False and data['solver_seconds']==1000,'FIRST_SOLVER_PAYLOAD')
 elif effective=='cpu':
  need(isinstance(path,Path) and path.is_absolute() and set(data) in ({'manifest','O_shared_memory','b_path','address','auth_hex','ranges'},{'manifest','O_shared_memory','b_path','address','auth_hex','ranges','resource_execution'}) and data['O_shared_memory']==maps['O']['name'] and data['b_path']==str(Path(c['bundle'])/'p3/b.npz') and data['manifest']['O']['shape']==shape and data['manifest']['core_rows']==256,'FIRST_CPU_PAYLOAD_PATH')
 else:need(False,'FIRST_WORKER_ROLE')

def early_scope(role,path,c):
 path=Path(path).resolve();effective=role.removeprefix('entry_')
 if effective in ('control','application_control'):
  need(path.name=='payload.json' and path.is_relative_to(Path(c['workspace'])/'.adaptive/control'),'FIRST_PREENTRY_CONTROL_SCOPE');return
 session=session_for(path);need(session.is_relative_to(Path(c['workspace'])/'store') or role.startswith('entry_') and session.is_relative_to(Path(c['output_root'])),'FIRST_PREENTRY_SESSION_SCOPE')
 ss=read(session/'session-spec.json');spec=read(session.parent/'spec.json')
 need(ss['execution']==c and spec['semantics']['instance_context']==binding(c['manifest_path']) and spec['semantics']['output_root']==c['output_root'],'FIRST_PREENTRY_SESSION_SCOPE')
 names={'owner':'session-spec.json','host':'host-payload.json','fit':'fit/payload.json','r0':'R0/payload.json','prepare':'P3/payload.json','solver':'P3/solver/solver-spec.json','cpu':'CPU/payload.json'}
 need(effective in names and path==session/names[effective],'FIRST_PREENTRY_ROLE_PATH')
