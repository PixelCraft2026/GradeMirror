"""GM059 declarative allocations over native_science's existing ledger."""
from pathlib import Path
from ..cpu_replay.contract import need,verify,binding
from .native_contract import strict_load
from .independent_contract import E,W,SCHEMA,authorize
TOTAL=dict(source_decode=4,R0_render=4,forward=560,Field=4,state_load=4,CPU256_service=2,scalar_LLF_footprints=336,compositions=168)
IO_TOTAL=dict(qualification_quantization=36,preview=16,preview_JPG=16,native_JPG_encode=8,native_JPG_full_readback=8)

def budget(mode):
 from .native_science import BASE
 need(mode in ('fast','fine'),'SCIENCE_MODE')
 return dict(BASE,CPU256_service=1 if mode=='fine' else 0,scalar_LLF_footprints=168 if mode=='fine' else 0,compositions=84 if mode=='fine' else 0,qualification_quantization=6 if mode=='fine' else 3,preview=2,preview_JPG=2,native_JPG_encode=1,native_JPG_full_readback=1)

def allow_root(root,ctx):
 if Path(root)!=E:return False
 verify(ctx);c=strict_load(ctx['path']);need(c['schema']==SCHEMA and Path(c['workspace']).is_relative_to(W) and c['TEST_ONLY'] is False,'INDEPENDENT_SCIENCE_ROOT');return True

def condition_input(c):
 if c.get('TEST_ONLY') is True:return None
 a=strict_load(E/'allocations.json');row=a['allocations'][c['science_allocation']]
 if row['kind']!='conditional':return None
 item=row['engineering_condition'];need(Path(item['path']).resolve().is_relative_to(E),'INDEPENDENT_CONDITION_INPUT_SCOPE');verify(item);return item

def validate_condition(proof,c):
 need(proof['status']=='ENGINEERING_REPAIR_ZERO_SCIENCE_PASS' and proof['mode']==c['mode'] and proof['numerical_resource_identity_hard_failure'] is False and proof['prior_tree_complete_actual_wait'] is True and proof['prior_result_unusable'] is True and proof['executor_build']==c['executor_build']['sha256'],'INDEPENDENT_CONDITIONAL_NOT_AUTHORIZED')

def entry_condition_checks(c,work):
 from ..common import atomic_json
 from . import native_runtime
 need(native_runtime._guard.dry and c['schema']==SCHEMA,'INDEPENDENT_CONDITION_ENTRY_ONLY')
 item=condition_input(c);need(item is not None,'INDEPENDENT_CONDITION_ENTRY_INPUT');saved=strict_load(item['path']);need(saved['status']=='PENDING_NOT_AUTHORIZED','ENTRY_MUST_NOT_ENABLE_NATIVE')
 rejected=[]
 try:allocation(c)
 except Exception as e:need(getattr(e,'code',None)=='INDEPENDENT_CONDITIONAL_NOT_AUTHORIZED','PENDING_CONDITION_WRONG_REJECTION');rejected.append(dict(kind='pending',code=e.code))
 else:raise AssertionError('pending condition authorized')
 positive=dict(saved,status='ENGINEERING_REPAIR_ZERO_SCIENCE_PASS');validate_condition(positive,c)
 for key,value in (('mode','fine'),('prior_result_unusable',None),('numerical_resource_identity_hard_failure',True),('executor_build','0'*64)):
  bad=dict(positive);bad[key]=value
  try:validate_condition(bad,c)
  except Exception as e:need(getattr(e,'code',None)=='INDEPENDENT_CONDITIONAL_NOT_AUTHORIZED','CONDITION_NEGATIVE_WRONG_REJECTION');rejected.append(dict(kind=key,code=e.code))
  else:raise AssertionError('bad condition authorized')
 try:(E/'UNDECLARED-CONDITION-READ-MUST-FAIL.json').open('rb')
 except PermissionError as e:need(str(e).startswith('NATIVE_READ_SCOPE:'),'UNDECLARED_READ_WRONG_CAUSE')
 else:raise AssertionError('undeclared read allowed')
 atomic_json(Path(work)/'entry-guard-checks.json',dict(status='PASS',bound_condition=item,actual_read=True,pending_native_authority_rejected=True,pure_in_memory_policy_positive=True,negative_cases=rejected,unrelated_read_rejected=True,science=0,image_IO=0))

def allocation(c):
 a=strict_load(E/'allocations.json');rows=a['allocations'];need(a['schema']=='gm059-scientific-allocations-v1' and set(rows)<= {'initial-fast','initial-fine','conditional-fast','conditional-fine'} and {'initial-fast','initial-fine'}<=set(rows),'INDEPENDENT_ALLOCATION_SET')
 key=c['science_allocation'];need(key in rows,'INDEPENDENT_ALLOCATION_SCOPE');row=rows[key];need(row['mode']==c['mode'] and row['budget']==budget(c['mode']) and row['kind']==('initial' if key.startswith('initial-') else 'conditional'),'INDEPENDENT_ALLOCATION_BUDGET')
 if row['kind']=='conditional':
  verify(row['engineering_condition']);proof=strict_load(row['engineering_condition']['path']);validate_condition(proof,c)
 return key,a

def reserve_frame(path,spec,c):
 from ..common import atomic_json
 need(c['schema']==SCHEMA and c['TEST_ONLY'] is True and max(c['source_identity']['shape'][:2])<=64,'INDEPENDENT_FRAME_ZERO_SCOPE')
 value=dict(TEST_ONLY=True,science=0,photo_decode=0,before_any_frame=True,application_context=binding(c['manifest_path']));atomic_json(Path(path)/'scientific-reservation.json',value);return value

def host_limits(c):
 if c['TEST_ONLY']:return dict(source_copy=0,mix_traversals=0,**{k:0 for k in budget('fine')}),IO_TOTAL
 return dict(budget(c['mode']),**IO_TOTAL,source_copy=1,mix_traversals=24),IO_TOTAL
