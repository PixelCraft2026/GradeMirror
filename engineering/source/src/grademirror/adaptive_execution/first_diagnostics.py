"""Dry-only diagnostic first/secondary and atexit guard lifetime proof."""
import atexit,sys
from pathlib import Path
from ..common import atomic_json
from ..cpu_replay.contract import ROOT,need
def run(work,c):
 from ..cpu_replay import runtime
 work=Path(work);error=ValueError('GM057_EXPECTED_PRIMARY_STDERR');runtime.error(ROOT/'gm057-forbidden-diagnostic',error);sys.stderr.flush();text=Path(sys.stderr.stream.name).read_text('utf-8');need('GM057_EXPECTED_PRIMARY_STDERR' in text and 'DIAGNOSTIC' in text,'FIRST_DIAGNOSTIC_FIRST_SECONDARY')
 def tail():
  from .first_runtime import _guard
  refused=None
  try:(ROOT/'gm057-forbidden-atexit.bin').open('xb')
  except BaseException as error:refused=getattr(error,'code',str(error))
  need(refused=='NATIVE_WRITE_SCOPE','FIRST_ATEXIT_GUARD_MISSING');need(not sys.stderr.stream.closed,'FIRST_ATEXIT_STDERR_CLOSED');sys.stderr.write('GM057_EXPECTED_ATEXIT_STDERR\n');sys.stderr.flush();atomic_json(work/'atexit-guard-proof.json',dict(guard_active=True,refusal=refused,stderr_still_open=True,write_roots=list(map(str,_guard.roots)),science=0))
 atexit.register(tail);atomic_json(work/'diagnostic-first-secondary.json',dict(primary_preserved=True,secondary_refusal_preserved=True,atexit_proof_deferred=True,science=0))

def numeric_refusals(role,path,c):
 from .first_runtime import _guard
 from ..common import strict_load
 path=Path(path);rows=[]
 def refused(name,fn,prefix):
  try:
   try:fn()
   except BaseException as error:
    code=getattr(error,'code','');text=str(error);need(code.startswith(prefix) or text.startswith(prefix),'FIRST_NEGATIVE_CAUSE:'+name+':'+text);rows.append(dict(name=name,error=text,error_code=code,before_body=True));return
   raise AssertionError('FIRST_NEGATIVE_NOT_ENFORCED:'+name)
  finally:sys.setprofile(_guard.profile);need(sys.getprofile()==_guard.profile,'FIRST_NEGATIVE_GUARD_RESTORE')
 if role in ('entry_fit','entry_r0','entry_cpu'):
  from ..engine.independent_r0.core import Field
  refused('Field_constructor_before_body',lambda:Field({},43),'FIRST_ROLE_OR_DRY_BOUNDARY')
 if role=='entry_host':
  from ..engine import group15_codec
  refused('photo_decode_before_body',lambda:group15_codec.read_source(c['source']['path']),'FIRST_ROLE_OR_DRY_BOUNDARY')
 if role=='entry_cpu':
  from ..engine.cpu_postprocess.service import worker
  refused('CPU_compute_before_body',lambda:worker(path),'FIRST_ROLE_OR_DRY_BOUNDARY')
  data=strict_load(path)
  for tag,bad,prefix in [('unknown',dict(data,unexpected_field=True),'CPU_PAYLOAD_FIELDS'),('resource-null',dict(data,resource_execution=None),'RESOURCE_WORKER_BINDING_REQUIRED')]:
   p=path.parent/('negative-'+tag+'.json');atomic_json(p,bad);refused('CPU_'+tag,lambda:worker(p,entry_only=True),prefix)
 atomic_json(path.parent/'numeric-refusals.json',dict(role=role,rows=rows,science=0,photo_decode=0,profile_restored=True))
