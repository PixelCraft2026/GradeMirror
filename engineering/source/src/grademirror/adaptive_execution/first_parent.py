"""Prove the refine input was actually produced by this task's native fit."""
from pathlib import Path
from ..cpu_replay.contract import binding,verify,need
from .first_contract import E,W,SCHEMA,read
FIELDS={'context','qualification','spec','claim','fit_receipt','fit_wait','reclaim'}
def _events_snapshot(root=E):
 from ..store import locked
 from .first_science import events
 root=Path(root).resolve();need(root==E or root.is_relative_to(W/'fixtures'),'FIRST_PARENT_LEDGER_READ_SCOPE')
 with locked(root/'science/ledger.lock'):return events(root)
def validate(pkg,q,m):
 if q.get('task')=='GM-064':
  from .portable_contract import validate_parent
  return validate_parent(pkg,q,m)
 if q.get('task')=='GM-063':
  from .calibration_contract import validate_parent
  return validate_parent(pkg,q,m)
 need(set(pkg)=={'directory','bundle_id','math_id','version','files','producer'} and type(pkg['producer'])is dict and set(pkg['producer'])==FIELDS,'FIRST_PARENT_PRODUCER_FIELDS')
 proof=pkg['producer']
 for key,item in proof.items():
  verify(item);need(Path(item['path']).resolve().parent==E/'qualifications' if key=='qualification' else Path(item['path']).resolve().is_relative_to(W),'FIRST_PARENT_PROOF_SCOPE')
 c=read(proof['context']['path']);spec=read(proof['spec']['path']);claim=read(proof['claim']['path']);receipt=read(proof['fit_receipt']['path']);wait=read(proof['fit_wait']['path']);reclaim=read(proof['reclaim']['path']);op=Path(proof['spec']['path']).parent
 need(c['schema']==SCHEMA and c['phase']=='fit' and c['TEST_ONLY'] is False and c['qualification']==proof['qualification'] and read(c['qualification']['path'])['phase']=='native','FIRST_PARENT_NATIVE_FIT_CONTEXT')
 need(all(c[k]==q[k] for k in ('source_identity','reference_identity','settings','profile','color_policy')) and Path(c['bundle']).resolve()==Path(pkg['directory']).resolve(),'FIRST_PARENT_NATIVE_INPUTS')
 need(proof['context']==spec['semantics']['instance_context'] and spec['semantics']['first_fit'] is True and spec['semantics']['adaptive_application'] is True and spec['semantics']['TEST_ONLY'] is False and spec['semantics']['executor_build']==c['executor_build']['sha256']==m['producer_build']['sha256'] and Path(spec['semantics']['bundle_directory']).resolve()==Path(pkg['directory']).resolve(),'FIRST_PARENT_NATIVE_SPEC')
 need(proof['claim']==binding(op/'scientific-reservation.json') and claim['context']==proof['context'] and Path(claim['operation_path'])==op and claim['phase']=='fit','FIRST_PARENT_NATIVE_CLAIM')
 need(receipt['status']=='COMPLETE' and receipt['counts']==dict(fit=1,updates=1000,forward=2001,model_construct=1,optimizer_construct=1) and receipt['parameter_sha256']==m['mathematical']['r0_parameters']['value_sha256'] and receipt['producer_build']==m['producer_build']['sha256'] and all(receipt[k]==spec[k]==m['provenance'][k] for k in ('job_id','attempt_id')),'FIRST_PARENT_REAL_FIT_RECEIPT')
 need(wait['actual_worker_handle_wait'] is True and wait['tree_empty'] is True and wait['exit_code']==0 and reclaim['actual_wait'] is True and reclaim['tree_empty'] is True and bool(reclaim['identities']) and all(x['exited'] is True for x in reclaim['identities']),'FIRST_PARENT_REAL_EXIT')
 rows=_events_snapshot();need(sum(x['kind']=='claim' and x.get('reservation')==claim['reservation'] and all(x.get(k)==v for k,v in claim.items()) for x in rows)==1 and sum(x['kind']=='completed' and x.get('reservation')==claim['reservation'] and x.get('component')=='fit' and x.get('receipt')==proof['fit_receipt'] for x in rows)==1,'FIRST_PARENT_REAL_COMPLETED_FIT')
 return True
