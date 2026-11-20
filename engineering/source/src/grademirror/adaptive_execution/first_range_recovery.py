"""Resume only original range/publication/CPU from this task's successful CG."""
import gc,time
from pathlib import Path
from ..common import atomic_json
from ..cpu_replay.contract import binding,verify,need
from .first_contract import read
from . import first_science as science

def range_worker(payload,c):
 import numpy as np
 from ..cpu_replay.numeric import attach
 from ..cpu_replay.host import array_hash
 from ..engine.cpu_postprocess.executor import ArrayProvider
 from ..engine.cpu_postprocess.core import Gate
 from ..engine.independent_r0_p3_lld10.ledger import Ledger
 from ..engine.cpu_fine_image.execution import range_scan
 from .first_partial import validate
 cap=validate(read(c['qualification']['path']));need(c['phase']=='fine_recovery' and payload['b']==cap['b'],'FIRST_PARTIAL_RANGE_ONLY');work=Path(payload['work']);om=o=None
 cancelled=lambda:Path(payload['cancel']).exists()
 try:
  verify(cap['b']);raw=Path(cap['b']['path']).read_bytes()
  with (work/'b.npz').open('xb') as f:f.write(raw)
  need(binding(work/'b.npz')['sha256']==cap['b']['sha256'],'FIRST_PARTIAL_COPY_BYTES')
  science.reuse_actual_b(c,cap)
  with np.load(work/'b.npz',allow_pickle=False) as archive:b=np.ascontiguousarray(archive['b'])
  report=read(cap['solver']['path']);inputs=read(cap['inputs']['path']);need(b.dtype==np.float64 and list(b.shape)==report['shape'] and b.flags.c_contiguous and np.isfinite(b).all() and array_hash(b)==report['coefficient_sha256'],'FIRST_PARTIAL_LOADED_B_IDENTITY')
  om,o=attach(payload['O']);need(not cancelled(),'CANCELLED',exit_code=8)
  ledger=Ledger(work/'range-scan-actual.json',dict(P3_scalar_evaluations=3*o.shape[0]*o.shape[1]),cancel_path=payload['cancel'],deadline=time.monotonic()+600)
  science.start(c,'range');ranges=range_scan(ArrayProvider(o,b),dict(reference_wh=payload['reference_wh']),Gate(ledger.take,cancelled,lambda **kw:None,time.monotonic()+600));ledger.save('COMPLETE');science.complete(c,'range',binding(work/'range-scan-actual.json'))
  result=dict(status='PASS',b=binding(work/'b.npz'),b_shape=list(b.shape),b_sha256=array_hash(b),ranges=ranges,solver=report,H_sha256=inputs['H_sha256'],T_sha256=inputs['T_sha256'],counts=dict(area_H=0,target_T=0,P3_geometry=0,scalar_CG=0),reused_actual_CG=cap)
  atomic_json(work/'prepare-result.json',result);return result
 finally:
  o=None
  if om:om.close()

def prepare(host):
 import numpy as np
 from ..engine import group15_codec as codec
 from ..cpu_replay import runtime,lifecycle
 from ..cpu_replay.host import array_hash
 from ..cpu_refine.host import worker,p3_metadata
 from ..cpu_recipe.generated_fine import publish
 from ..cpu_recipe.validation import complete
 from .first_host import record_decode
 from .first_partial import validate
 c=host.context;host.counts['reference_decode']=0;cap=validate(read(c['qualification']['path']));need(c['phase']=='fine_recovery' and host.sem['cpu_refine'] is True and host.sem['refine_prepare'] is False,'FIRST_PARTIAL_HOST_PHASE');host.check();host.sem=dict(host.sem);runtime.require_runtime(host.session/'host');shape=c['source_identity']['shape'];host.allocate(shape)
 host.count(source_decode=1);source,info=codec.read_source(host.sem['source_path'],strict_group15=False,development_pair=True);need(list(source.shape)==shape and source.dtype==np.uint8 and info['pixel_sha256']==c['source_identity']['pixel_sha256'],'FIRST_PARTIAL_SOURCE_IDENTITY');record_decode(host,'source',info);host.s[:]=source;host.count(source_copy=1);host.icc=info['icc_bytes'];del source;gc.collect();host.s.flags.writeable=False
 host.progress('R0');work=worker(host,'r0',dict(work=str(host.session/'R0'),S=dict(name=host.smap.name,shape=shape,dtype='uint8',readonly=True),O=dict(name=host.omap.name,shape=shape,dtype='float32'),bundle=c['parent_bundle']['directory'],cancel=str(host.path/'cancel.signal'),operation=str(host.path),reservation=c['science']),timeout=930)
 r0=read(work/'r0-result.json');host.count(R0_render=1,forward=r0['counts']['forward_batches']);host.o.flags.writeable=False;need(array_hash(host.o)==read(cap['R0']['path'])['O_sha256'],'FIRST_PARTIAL_SAME_R0')
 host.progress('range_scan');work=worker(host,'prepare',dict(work=str(host.session/'P3'),O=dict(name=host.omap.name,shape=shape,dtype='float32',readonly=True),b=cap['b'],reference_wh=[1024,1536],cancel=str(host.path/'cancel.signal')),timeout=630);result=read(work/'prepare-result.json')
 need(result['counts']==dict(area_H=0,target_T=0,P3_geometry=0,scalar_CG=0) and result['reused_actual_CG']==cap,'FIRST_PARTIAL_NO_NEW_CG')
 atomic_json(host.session/'actual-CG-lineage.json',dict(source=cap,new_range=binding(work/'prepare-result.json'),new_range_wait=binding(work/'owned-wait.json'),no_new_HT_CG_reference=True))
 p3=p3_metadata(c,result,work/'b.npz');intent=dict(semantics=host.spec['semantics'],job_id=host.spec['job_id'],attempt_id=host.spec['attempt_id']);host.progress('save_fine_recipe');publish(host.sem['bundle_directory'],c['parent_bundle']['directory'],work/'b.npz',result['solver'],p3,intent,c,checkpoint=lambda point:lifecycle.checkpoint(host.path,point))
 m,_=complete(host.sem['bundle_directory']);host.sem.update(bundle_id=m['bundle_id'],math_id=m['math_id']);atomic_json(host.session/'render-authorization.json',dict(schema='cpu-refine-render-authorization-v1',parent_context=host.spec['semantics']['instance_context'],bundle_id=m['bundle_id'],math_id=m['math_id'],bundle_complete=binding(Path(host.sem['bundle_directory'])/'COMPLETE.json'),executor_build=c['executor_build']['sha256'],before_LLF=True));lifecycle.checkpoint(host.path,'before_LLF');host.check();hashes=dict(O=array_hash(host.o));hashes.update(host.postprocess(False));host.o=None;host.omap.close();host.omap.unlink();host.omap=None
 with host.receiver.prepared_view() as view:host.y=view.view()
 del view
 return host.finish_prepare(info,r0,hashes,False)
