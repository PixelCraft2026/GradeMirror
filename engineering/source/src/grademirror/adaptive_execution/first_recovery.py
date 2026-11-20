"""Restore only S and SharedO from this task's actual committed fast package."""
import gc
from pathlib import Path
from ..common import atomic_json
from ..cpu_replay.contract import binding,need
from .first_contract import read

def prepare(host):
 import numpy as np
 from ..engine import group15_codec as codec
 from ..cpu_replay import runtime
 from ..cpu_replay.host import array_hash
 from .first_host import record_decode
 c=host.context;need(c['phase'] in ('fast_recovery','fast_recovery_tail') and host.sem['application_intent']['operation']=='replay' and not host.sem['first_fit'] and not host.sem['cpu_refine'] and not host.sem['refine_prepare'],'FIRST_RECOVERY_ONLY_R0')
 host.check();runtime.require_runtime(host.session/'host');shape=c['source_identity']['shape'];host.allocate(shape)
 host.progress('read_source');host.count(source_decode=1)
 original,info=codec.read_source(host.sem['source_path'],strict_group15=False,development_pair=True)
 need(list(original.shape)==shape and original.dtype==np.uint8 and info['pixel_sha256']==c['source_identity']['pixel_sha256'],'FIRST_RECOVERY_SOURCE_PIXEL_IDENTITY')
 host.s[:]=original;host.count(source_copy=1);host.icc=info['icc_bytes'];del original;gc.collect();host.s.flags.writeable=False;record_decode(host,'source',info)
 host.progress('R0');work=host.session/'R0';work.mkdir()
 payload=dict(S=dict(name=host.smap.name,shape=shape,dtype='uint8',readonly=True),O=dict(name=host.omap.name,shape=shape,dtype='float32'),bundle=c['bundle'],cancel=str(host.path/'cancel.signal'),operation=str(host.path),work=str(work),reservation=c['science'])
 atomic_json(work/'payload.json',payload);atomic_json(work/'scientific-reservation.json',c['science']);job,_=runtime.launch('r0',work/'payload.json',work)
 try:
  from .calibration_contract import expected_tiles
  total=expected_tiles(c,140)
  waited=runtime.wait(job,work,timeout=930);need(waited['exit_code']==0,'R0_WORKER_FAILED');r0=read(work/'r0-result.json');need(r0['counts']['render']==1 and r0['counts']['forward_batches']==total and r0['counts']['model_construct']==1 and r0['counts']['state_load']==1,'FIRST_RECOVERY_R0_COUNTS');host.count(R0_render=1,forward=total)
 finally:
  if job.poll() is None:job.terminate();runtime.wait(job,work,timeout=30)
  job.close()
 host.o.flags.writeable=False;hashes=dict(O=array_hash(host.o));q=read(c['qualification']['path']);saved=read(q['recovery']['path']);need(hashes==read(saved['result']['path'])['hashes'],'FIRST_RECOVERY_ORIGINAL_O_IDENTITY');host.y=host.o
 return host.finish_prepare(info,r0,hashes,False)
