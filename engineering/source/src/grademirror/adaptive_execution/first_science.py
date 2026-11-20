"""Locked v4 allocation/claim/start/complete accounting. UNKNOWN never refunds."""
import os,time,uuid,json
from pathlib import Path
from ..common import atomic_json,canonical_json
from ..store import locked
from ..cpu_replay.contract import binding,verify,need
from .first_contract import E,I,read
INTERNAL_KEYS={'normalizations','resizes'}
COMPONENTS={
 'source':dict(source_decode=1),'reference':dict(reference_decode=1),
 'normalize_source':dict(normalizations=1,resizes=1),'normalize_reference':dict(normalizations=1,resizes=1),
 'fit':dict(fit=1,updates=1000,fit_forward=2001,forward=2001,Field=1),
 'R0':dict(R0_render=1,render_forward=140,forward=140,Field=1,state_load=1),
 'H':dict(area_H=1),'T':dict(target_T=1),'CG':dict(P3_geometry=1,scalar_CG=3),
 'range':dict(range_scan=1,P3_scalar_evaluations=98224128),
 'CPU':dict(CPU256_service=1,scalar_LLF_footprints=168,compositions=84,P3_scalar_evaluations=387220032,remap_pyramids=43176,D_calls=303408,U_calls=605640)}

ROLE_COMPONENT={'fit':'fit','r0':'R0','prepare':'H','solver':'CG','cpu':'CPU'}
IO={'qualification_quantization','preview','preview_JPG','native_JPG_encode','native_JPG_full_readback'}
def components(phase):
 if phase in ('fast_recovery','fast_recovery_tail'):return ('source','R0')
 if phase=='fine_recovery':return ('source','R0','range','CPU')
 return ('source','reference','normalize_source','normalize_reference','fit','R0') if phase=='fit' else ('source','reference','R0','H','T','CG','range','CPU')
def budget(phase):
 out={}
 for key in components(phase):
  for k,v in COMPONENTS[key].items():
   if k not in INTERNAL_KEYS:out[k]=out.get(k,0)+v
 if phase=='fast_recovery_tail':
  out.update(qualification_quantization=0,preview=0,preview_JPG=0,native_JPG_encode=0,native_JPG_full_readback=0,source_copy=1,mix_traversals=0);return out
 out.update(qualification_quantization=0 if phase in ('fast_recovery','fast_recovery_tail') else 3 if phase=='fit' else 6,preview=1 if phase in ('fast_recovery','fast_recovery_tail') else 4,preview_JPG=1 if phase in ('fast_recovery','fast_recovery_tail') else 4,native_JPG_encode=0 if phase in ('fast_recovery','fast_recovery_tail') else 2,native_JPG_full_readback=0 if phase in ('fast_recovery','fast_recovery_tail') else 2,source_copy=1,mix_traversals=1 if phase in ('fast_recovery','fast_recovery_tail') else 12)
 return out
def events(root=E):
 p=Path(root)/'science/events.jsonl';return [json.loads(x) for x in p.read_text('utf-8').splitlines() if x] if p.exists() else []
def append(row,root=E):
 row=dict(row,wall=time.time());p=Path(root)/'science/events.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('ab') as f:f.write(canonical_json(row)+b'\n');f.flush();os.fsync(f.fileno())
def reserve(path,spec):
 from .context import context as portable_context
 current=portable_context(spec)
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(current):
  from .portable_science import reserve as portable_reserve
  return portable_reserve(path,spec,current)
 from .calibration_contract import measurement
 from .context import context as application_context
 current=application_context(spec)
 if measurement(current):
  from .calibration_science import reserve as calibration_reserve
  return calibration_reserve(path,spec,current)
 from .context import context
 from .first_contract import authorize
 c=context(spec);q=authorize(c['qualification']['path'],c['request']);need(q['phase']=='native','FIRST_DRY_NO_CLAIM');a=read(E/'allocations.json');key=c['science_allocation'];need(key in a['allocations'] and a['allocations'][key]['phase']==c['phase'] and a['allocations'][key]['budget']==budget(c['phase']),'FIRST_ALLOCATION')
 return _claim(E,key,c['phase'],path,binding(c['manifest_path']))

def claim(c):
 r=read(Path(c['output_root'])/'science-claim.json');need(r['context']==binding(c['manifest_path']) and r['allocation']==c['science_allocation'],'FIRST_SCIENCE_CLAIM_BINDING');return r
def _started(c,component,rows):
 r=claim(c);return [x for x in rows if x['kind']=='component_started' and x['reservation']==r['reservation'] and x['component']==component]
def start(c,component):
 current=c
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(current):
  from .portable_science import start as portable_start
  return portable_start(c,component)
 from .calibration_contract import measurement
 if measurement(c):
  from .calibration_science import start as calibration_start
  return calibration_start(c,component)
 need(component in components(c['phase']),'FIRST_COMPONENT_PHASE')
 with locked(E/'science/ledger.lock'):
  r=claim(c);rows=events();need(not _started(c,component,rows),'FIRST_COMPONENT_REUSED');counts=COMPONENTS[component];from .first_adjustment import caps as adjusted_caps
  caps=adjusted_caps();caps.update(normalizations=2*caps['fit'],resizes=2*caps['fit'])
  for k,v in counts.items():need(sum(x['counts'].get(k,0) for x in rows if x['kind']=='component_started')+v<=caps[k],'FIRST_GLOBAL_COMPONENT_CAP:'+k)
  append(dict(kind='component_started',allocation=r['allocation'],reservation=r['reservation'],component=component,counts=counts,before_actual_call=True,started_permanently_consumed=True))
def before_launch(c,role):
 current=c
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(current):
  from .portable_science import before_launch as portable_before_launch
  return portable_before_launch(c,role)
 from .calibration_contract import measurement
 if measurement(c):
  from .calibration_science import before_launch as calibration_launch
  return calibration_launch(c,role)
 need(role in ROLE_COMPONENT and (role in ('r0',) if c['phase'] in ('fast_recovery','fast_recovery_tail') else role in ('r0','prepare','cpu') if c['phase']=='fine_recovery' else role in ('fit','r0') if c['phase']=='fit' else role in ('r0','prepare','solver','cpu')),'FIRST_LAUNCH_PHASE')
 with locked(E/'science/ledger.lock'):
  r=claim(c);rows=events();need(not any(x['kind']=='worker_allocation' and x['reservation']==r['reservation'] and x['role']==role for x in rows),'FIRST_WORKER_ALLOCATION_REUSED')
  append(dict(kind='worker_allocation',allocation=r['allocation'],reservation=r['reservation'],role=role,before_CreateProcess=True,science_started=False))
def worker_claim(c,role,operation):
 current=c
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(current):
  from .portable_science import worker_claim as portable_worker_claim
  return portable_worker_claim(c,role,operation)
 from .calibration_contract import measurement
 if measurement(c):
  from .calibration_science import worker_claim as calibration_claim
  return calibration_claim(c,role,operation)
 with locked(E/'science/ledger.lock'):
  r=claim(c);rows=events();need(Path(r['operation_path'])==Path(operation) and sum(x['kind']=='worker_allocation' and x['reservation']==r['reservation'] and x['role']==role for x in rows)==1 and not any(x['kind']=='worker_claim' and x['reservation']==r['reservation'] and x['role']==role for x in rows),'FIRST_UNIQUE_WORKER_ALLOCATION_CLAIM')
  from ..winproc import current_identity
  append(dict(kind='worker_claim',reservation=r['reservation'],allocation=r['allocation'],role=role,identity=current_identity(),before_preload=True,science_started=False))

def complete(c,component,receipt):
 current=c
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(current):
  from .portable_science import complete as portable_complete
  return portable_complete(c,component,receipt)
 from .calibration_contract import measurement
 if measurement(c):
  from .calibration_science import complete as calibration_complete
  return calibration_complete(c,component,receipt)
 verify(receipt)
 with locked(E/'science/ledger.lock'):
  r=claim(c);rows=events();need(len(_started(c,component,rows))==1 and not any(x['kind']=='completed' and x['reservation']==r['reservation'] and x['component']==component for x in rows),'FIRST_COMPLETION_UNIQUE_START')
  if component=='CPU':
   ack=read(receipt['path']);need(ack['complete'] is True and ack['cores']==28 and ack['blocks']==7008 and ack['four_fields_per_ACK'] is True and len(ack['fields'])==112,'FIRST_CPU_FULL_ACK')
  append(dict(kind='completed',allocation=r['allocation'],reservation=r['reservation'],component=component,counts=COMPONENTS[component],receipt=receipt))
def host_count(host,delta):
 current=host.context
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(current):
  from .portable_science import host_count as portable_host_count
  return portable_host_count(host,delta)
 from .calibration_contract import measurement
 if measurement(host.context):
  from .calibration_science import host_count as calibration_count
  return calibration_count(host,delta)
 c=host.context;limits=budget(c['phase']);need(all(type(v)is int and v>=0 and k in limits for k,v in delta.items()),'FIRST_COUNT_UNKNOWN')
 for key,component in (('source_decode','source'),('reference_decode','reference')):
  if delta.get(key):start(c,component)
 with locked(E/'science/ledger.lock'):
  r=claim(c);rows=events();caps=read(I/'capacity.json')['science_total']
  for k,v in delta.items():
   need(host.counts.get(k,0)+v<=limits[k],'FIRST_STAGE_IO_CAP:'+k)
   if k in IO:need(sum(x['delta'].get(k,0) for x in rows if x['kind']=='host_counter')+v<=caps[k],'FIRST_GLOBAL_IO_CAP:'+k)
  for k,v in delta.items():host.counts[k]=host.counts.get(k,0)+v
  append(dict(kind='host_counter',allocation=r['allocation'],reservation=r['reservation'],delta=delta,counts=dict(host.counts),before_actual_IO=True));atomic_json(host.session/'io-counts.json',host.counts)

def _claim(root,allocation,phase,path,ctx):
 from .first_contract import W
 root=Path(root);need(root==E or (root.is_relative_to(W/'fixtures') and ctx.get('TEST_ONLY') is True and ctx.get('metadata_only') is True),'FIRST_ACCOUNTING_FIXTURE_SCOPE')
 with locked(root/'science/ledger.lock'):
  rows=events(root);prior=[x for x in rows if x['kind']=='claim' and x['allocation']==allocation];superseded={x['reservation'] for x in rows if x['kind']=='superseded-not-started'}
  need(not any(x['reservation'] not in superseded for x in prior),'FIRST_ALLOCATION_ALREADY_CLAIMED')
  if not any(x['kind']=='allocation' and x['allocation']==allocation for x in rows):append(dict(kind='allocation',allocation=allocation,phase=phase,budget=budget(phase),before_claim=True),root)
  row=dict(kind='claim',allocation=allocation,reservation=str(uuid.uuid4()),phase=phase,operation_path=str(path),context=ctx,budget=budget(phase),before_any_science=True);append(row,root)
  if root==E:atomic_json(Path(path)/'scientific-reservation.json',row);atomic_json(Path(read(ctx['path'])['output_root'])/'science-claim.json',row)
  return row
def _supersede_not_started(root,reservation,proof):
 from .first_contract import W
 root=Path(root)
 with locked(root/'science/ledger.lock'):
  rows=events(root);claims=[x for x in rows if x['kind']=='claim' and x['reservation']==reservation];need(len(claims)==1 and not any(x.get('reservation')==reservation and x['kind'] in ('component_started','superseded-not-started') for x in rows),'FIRST_NOT_STARTED_NOT_PROVEN')
  if root==E:
   verify(proof['native_wait']);wait=read(proof['native_wait']['path']);need(all(wait[k] is True for k in ('actual_all_wait','tree_empty','exact')) and proof['zero_side_effects_proven'] is True,'FIRST_ZERO_EXIT_PROOF')
  else:need(root.is_relative_to(W/'fixtures') and proof==dict(TEST_ONLY=True,metadata_only=True,no_compute_authority=True),'FIRST_FIXTURE_PROOF')
  append(dict(kind='superseded-not-started',allocation=claims[0]['allocation'],reservation=reservation,proof=proof,gross_claim_retained=True,refund=False),root)

def stage_complete(c):
 current=c
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(current):
  from .portable_science import stage_complete as portable_stage_complete
  return portable_stage_complete(c)
 from .calibration_contract import measurement
 if measurement(c):
  from .calibration_science import stage_complete as calibration_complete
  return calibration_complete(c)
 with locked(E/'science/ledger.lock'):
  r=claim(c);rows=events()
  for component in components(c['phase']):need(sum(x['kind']=='completed' and x.get('reservation')==r['reservation'] and x.get('component')==component for x in rows)==1,'FIRST_STAGE_INCOMPLETE:'+component)
  return dict(reservation=r['reservation'],all_components_completed=True,components=list(components(c['phase'])))

def check_accepted(spec,c):
 from .first_context import operation
 need(read(c['manifest_path'])==c and spec['semantics']['instance_context']==binding(c['manifest_path']),'FIRST_PREVALIDATED_CONTEXT_BYTES');operation(c,spec['semantics']);q=read(c['qualification']['path']);need(c['qualification']==binding(q['manifest_path']) and q['executor_build']==c['executor_build'],'FIRST_PREVALIDATED_QUALIFICATION_BYTES');return q

def reserve_validated(path,spec,c):
 current=c
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(current):
  from .portable_science import reserve as portable_reserve
  return portable_reserve(path,spec,c)
 from .calibration_contract import measurement
 if measurement(c):
  from .calibration_science import reserve as calibration_reserve
  return calibration_reserve(path,spec,c)
 q=check_accepted(spec,c);need(q['phase']=='native' and c['fault_point']=='' and c['pause_at']=='','FIRST_PREVALIDATED_NATIVE_ONLY');a=read(E/'allocations.json');key=c['science_allocation'];need(q['allocations'][c['phase']]==key and a['allocations'][key]['phase']==c['phase'] and a['allocations'][key]['budget']==budget(c['phase']),'FIRST_PREVALIDATED_ALLOCATION');return _claim(E,key,c['phase'],path,binding(c['manifest_path']))

def reuse_actual_b(c,cap):
 need(c['phase']=='fine_recovery','FIRST_PARTIAL_LOAD_PHASE')
 with locked(E/'science/ledger.lock'):
  r=claim(c);rows=events();need(sum(x['kind']=='worker_claim' and x.get('reservation')==r['reservation'] and x.get('role')=='prepare' for x in rows)==1 and not any(x['kind']=='actual_b_reuse' and x.get('reservation')==r['reservation'] for x in rows),'FIRST_PARTIAL_LOAD_ONCE')
  append(dict(kind='actual_b_reuse',allocation=r['allocation'],reservation=r['reservation'],capability=binding(E/'resumes/actual-b-capability.json'),b=cap['b'],before_numpy_load=True,range_saved_b_load=1,new_CG=0))
