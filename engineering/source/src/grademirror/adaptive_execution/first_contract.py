"""Exact GM057 capabilities; no change to v1-v3, math or production authority."""
from pathlib import Path
from ..common import strict_load as _read,reject_reparse
from ..cpu_replay.contract import ROOT,FLAGS,RESOURCE_ENVELOPE,binding,verify,need
def read(p):return _read(Path(p))
SCHEMA='cpu-application-execution-v4'
QUAL='adaptive-first-qualification-v1'
RECOVERY='adaptive-first-recovery-qualification-v1'
PARTIAL='adaptive-first-actual-b-recovery-v1'
E=ROOT/'docs/evidence/gm-057/20261101-v1'
W=ROOT/'tests/workspaces/g57/20261101-v1'
I=ROOT/'docs/evidence/gm-057/20261101-plan-v1'
POLICY=dict(schema='application-native-idle-lease-v1',idle_seconds=120,renew_interval_seconds=30,request_seconds=120,TEST_ONLY=False,native_authorized=True,scope='GM057_EXACT_FIRST_FIT_REFINE_ONLY')
LIMITS=dict(fast_compute=7200,fine_compute=8400,fit_worker=3600,fit_launcher=3630,r0_worker=900,r0_launcher=930,solver_worker=1000,solver_launcher=1030,range_worker=600,cpu_worker=3600,cpu_launcher=3630,fast_control=7500,fine_control=8700,fast_root=10000,fine_root=12000,request=120,resident_control=300,export=1200,cleanup=30)
RECOVERY_LIMITS=dict(LIMITS,fast_compute=1800,fast_control=2100,fast_root=3600)
def distribution_metadata(q,inventory):
 rows=inventory.get('distribution_metadata')
 if rows is None:return
 need(type(rows)is dict and set(rows)=={'numpy','scipy'},'FIRST_DISTRIBUTION_METADATA_FIELDS')
 table={x['path']:x for x in inventory['files']};site=Path(q['interpreters']['solver']['path']).resolve().parent.parent/'Lib/site-packages'
 for name,version in (('numpy','2.5.3'),('scipy','1.18.1')):
  row=rows[name];p=(site/(name+'-'+version+'.dist-info')/'METADATA').resolve();need(Path(row['path']).resolve()==p and table.get(row['path'])==row,'FIRST_DISTRIBUTION_METADATA_IDENTITY');verify(row)
  lines=p.read_text('utf-8').splitlines();need(lines.count('Name: '+name)==1 and lines.count('Version: '+version)==1,'FIRST_DISTRIBUTION_METADATA_VERSION')
def pointer(p):return p is not None and Path(p).resolve().parent==E/'qualifications'
def native(c):return bool(c and c.get('schema')==SCHEMA)
def authorize(path,request,*,cleanup=False):
 from .portable_contract import pointer as portable_pointer
 if portable_pointer(path):
  from .portable_contract import authorize as portable_authorize
  return portable_authorize(path,request,cleanup=cleanup)
 from .calibration_contract import pointer as calibration_pointer
 if calibration_pointer(path):
  from .calibration_contract import authorize as calibration_authorize
  return calibration_authorize(path,request,cleanup=cleanup)
 from .intent import normalize
 from .build import current_closure
 need(pointer(path),'EXECUTION_NOT_QUALIFIED');p=Path(path).resolve();reject_reparse(p);ix=read(E/'qualification-index.json');need(str(p) in ix,'EXECUTION_NOT_QUALIFIED');verify(ix[str(p)]);q=read(p)
 fields={'schema','manifest_path','task','phase','authorization','taskbook','executor_build','runtime','interpreters','source','reference','source_identity','reference_identity','settings','profile','color_policy','paths','resource_envelope','limits','lease_policy','allocations','parent_package','p0'}
 recovery=q.get('schema')==RECOVERY;partial=q.get('schema')==PARTIAL
 need(type(q)is dict and set(q)==fields|({'recovery'} if recovery else {'partial_b'} if partial else set()) and q['schema'] in (QUAL,RECOVERY,PARTIAL) and q['task']=='GM-057' and q['manifest_path']==str(p) and q['phase'] in ('dry','native'),'FIRST_QUALIFICATION_FIELDS')
 need(q['authorization']==binding(I/'authorization.json') and q['authorization']['sha256']=='71623fbc7a0e086b3404f7b74ee7dbb1b29d7e6a87a20783dc579a94936a84c7' and q['taskbook']==binding(ROOT/'docs/tasks/GM-057-unified-first-fit-refine-v1-sol.md') and q['taskbook']['sha256']=='906be6a1d63ef34707b5d7f19b988f61d7ccf0329a8e24c4defa33bb02320dcd','FIRST_TASK_AUTHORITY')
 need(q['paths']==dict(evidence=str(E),workspace=str(W)) and q['limits']==(RECOVERY_LIMITS if recovery else LIMITS) and q['lease_policy']==POLICY and q['resource_envelope']==RESOURCE_ENVELOPE,'FIRST_QUALIFICATION_SCOPE')
 need(binding(I/'fixed-inputs.json')['sha256']=='4004278a8d487644a61406afd2701680b691980335ce6767b088d21416a9ac7c' and binding(I/'interpreters.json')['sha256']=='0c33625c6000870f59b3bd67bc7c264ca43a8f045111d855b2ff929f4e29b52f' and binding(I/'capacity.json')['sha256']=='71d1a98125a329a776085fdd0e1fe7e784dac0d5328273b210bfac4ee7bb843b','FIRST_ISSUANCE_BYTES')
 fixed=read(I/'fixed-inputs.json');need(all(q[k]==fixed[k] for k in ('source','reference')) and all(q[k]==fixed['native'][k] for k in ('source_identity','reference_identity','settings','color_policy')),'FIRST_FIXED_INPUT_MATH')
 from ..engine.independent_r0.profile import PROFILE_ID,PROFILE_CONTRACT_SHA256,CORE_SHA256
 need(q['profile']==dict(id=PROFILE_ID,contract_sha256=PROFILE_CONTRACT_SHA256,core_sha256=CORE_SHA256),'FIRST_PROFILE_IDENTITY')
 need(q['interpreters']==read(I/'interpreters.json')['interpreters'],'FIRST_INTERPRETER_BINDING')
 inv=Path(q['runtime']['inventory']['path']).resolve();need(inv==I/'runtime-frozen.json' or inv.parent==E/'runtime','FIRST_RUNTIME_POINTER');verify(q['runtime']['inventory']);need(q['runtime']['status']=='KNOWN_EXACT_RUNTIME','FIRST_RUNTIME_UNKNOWN')
 if inv!=I/'runtime-frozen.json':
  inventory=read(inv);need(inventory.get('original_inventory')==binding(I/'runtime-frozen.json'),'FIRST_ORIGINAL_RUNTIME_BINDING');old=read(I/'runtime-frozen.json')['files'];table={x['path']:x for x in inventory['files']};need(all(table.get(x['path'])==x for x in old),'FIRST_ORIGINAL_RUNTIME_DRIFT')
 distribution_metadata(q,read(inv))
 for b in q['interpreters'].values():verify(b)
 need(cleanup or q['executor_build']==current_closure(native=True),'QUALIFICATION_BUILD_MISMATCH')
 if q['phase']=='native' and not cleanup:
  verify(q['p0']);proof=read(q['p0']['path']);need(proof['status']=='G01_G06_PASS' and proof['executor_build']==q['executor_build']['sha256'],'FIRST_P0_REQUIRED')
 else:need(q['phase']!='dry' or q['p0'] is None,'FIRST_DRY_PROOF')
 r=normalize(request);need(r['execution_preference']=='cpu' and r['operation'] in (('replay',) if recovery else ('fit','refine')) and r['quality']==('fast' if recovery or r['operation']=='fit' else 'fine'),'EXECUTION_NOT_QUALIFIED')
 need(not recovery or r['reference'] is None,'FIRST_RECOVERY_ZERO_REFERENCE')
 for name,fmt in ((('source','JPEG'),) if recovery else (('source','JPEG'),('reference','PNG'))):
  ident=q[name+'_identity'];expected=dict(file=q[name],metadata=dict(width=ident['shape'][1],height=ident['shape'][0],bits=8,format=fmt));need(r[name]==expected,'FIRST_REQUEST_INPUT')
 if r['operation']=='fit':need(r['recipe'] is None,'FIRST_FIT_OLD_PARAMETERS_FORBIDDEN')
 else:
  pkg=q['parent_package'];need(type(pkg)is dict and r['recipe']=={k:pkg[k] for k in ('directory','bundle_id','math_id','version')} and pkg['version']==2 and Path(pkg['directory']).resolve().is_relative_to(W),'FIRST_REFINE_NEW_PARENT_ONLY')
  for b in pkg['files']:verify(b)
  from ..cpu_recipe.validation import complete
  m,_=complete(pkg['directory']);need(m['provenance']['TEST_ONLY'] is False and (m['bundle_id'],m['math_id'])==(pkg['bundle_id'],pkg['math_id']) and m['mathematical']['inputs']==dict(source_identity=q['source_identity'],reference_identity=q['reference_identity']),'FIRST_PARENT_IDENTITY')
  from .first_parent import validate as actual_parent
  actual_parent(pkg,q,m)
 if recovery:
  verify(q['recovery']);saved=read(q['recovery']['path']);tail=q['allocations']==dict(fast_recovery_tail='N01-fast-recovery-tail-2');need(Path(q['recovery']['path']).resolve()==E/('phases/N01-tail-prefix.json' if tail else 'phases/N01-saved-prefix.json') and saved['status']==('N01_TAIL_PREFIX_AUDIT_PASS' if tail else 'ACK_AND_N01_SAVED_PREFIX_PASS') and saved['parent_package']==q['parent_package'],'FIRST_RECOVERY_VALID_SAVED_PREFIX')
  verify(saved['original_root_wait']);wait=read(saved['original_root_wait']['path']);need(all(wait[k] is True for k in ('actual_root_wait','actual_all_wait','tree_empty','exact')),'FIRST_RECOVERY_PRIOR_COMPLETE_EXIT')
  for key in ('original_error','result','reclaim'):verify(saved[key])
  need(q['allocations'] in (dict(fast_recovery='N01-fast-recovery-1'),dict(fast_recovery_tail='N01-fast-recovery-tail-2')),'FIRST_RECOVERY_ALLOCATION')
 if partial:
  from .first_adjustment import grant as adjustment
  allowed=(dict(fine_recovery='refine-range-recovery-1'),dict(fine_recovery=adjustment()['allocation']))
  need(r['operation']=='refine' and q['allocations'] in allowed,'FIRST_PARTIAL_ALLOCATION')
  from .first_partial import validate
  validate(q)
 if not cleanup:verify(q['source']);verify(q['reference'])
 return q
