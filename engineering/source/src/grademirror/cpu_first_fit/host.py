"""Input preparation and generated recipe stage over the existing resident host."""
import gc
import math
import time
from pathlib import Path
from ..common import atomic_json, strict_load
from ..cpu_replay.contract import need, binding
from ..cpu_replay import runtime, lifecycle
from .contract import E
from ..adaptive_execution.first_host import record_decode as _application_decode


def _worker(host, role, payload):
    work=Path(payload['work']);work.mkdir()
    atomic_json(work/'payload.json',payload)
    atomic_json(work/'scientific-reservation.json',payload['reservation'])
    job,_=runtime.launch(role,work/'payload.json',work,memory=(3 if host.context['TEST_ONLY'] else 6)*1024**3)
    try:
        timeout=600 if host.context['TEST_ONLY'] else 3600 if role=='fit' else 900
        if host.context.get('schema')=='cpu-application-execution-v4':timeout+=30
        if role=='r0' and host.context.get('light_plan'):
            waited=runtime.wait(job,work,timeout=timeout,entry_timeout=180,
                                cancelled=lambda:(host.path/'cancel.signal').is_file())
        else:
            waited=runtime.wait(job,work,timeout=timeout)
        if waited.get('cancellation_observed'):
            need(False, 'CANCELLED', exit_code=8)
        need(waited['exit_code']==0,'FIRST_FIT_WORKER_FAILED:'+role)
    finally:
        if job.poll() is None:
            job.terminate();runtime.wait(job,work,timeout=30)
        job.close()
    return work


def _headers(host):
    from ..engine import group15_codec as codec
    c=host.context
    source=codec.inspect_source(host.sem['source_path'],strict_group15=False,development_pair=True)
    need(source['format']=='JPEG' and source['bits']==8 and max(source['height'],source['width'])>=512,'FIRST_FIT_SOURCE_FORMAT')
    need([source['height'],source['width'],3]==c['source_identity']['shape'],'FIRST_FIT_SOURCE_GEOMETRY')
    reference=None
    if host.sem.get('first_fit'):
        reference=codec.inspect_reference(host.sem['reference_path'],strict_group15=False,development_pair=True)
        need(reference['format'] in ('JPEG','PNG') and reference['bits']==8 and max(reference['height'],reference['width'])>=512,'FIRST_FIT_REFERENCE_FORMAT')
        def grid(h):
            return [max(1,round(h['height']*512/max(h['height'],h['width']))),max(1,round(h['width']*512/max(h['height'],h['width'])))]
        need(grid(source)==grid(reference),'FIRST_FIT_TRAINING_GRID_MISMATCH')
    def metadata(header):
        if header is None:return None
        # ICC raw bytes stay inside the IO host; the declared header identity
        # and color-policy hashes are JSON metadata, never a binary payload.
        return {key:header[key] for key in ('path','bytes','sha256','width','height','bits','format','color','decoder_id')}
    atomic_json(host.session/'headers.json',dict(source=metadata(source),reference=metadata(reference),before_decode=True))


def prepare(host):
    import numpy as np
    from multiprocessing.shared_memory import SharedMemory
    from ..engine import group15_codec as codec
    from ..cpu_recipe.generated import publish, complete
    from ..cpu_replay.host import array_hash
    c=host.context
    host.check()
    host.sem=dict(host.sem)  # Durable operation intent is never rewritten.
    shape=c['source_identity']['shape']; dtype=np.dtype(c['source_identity']['dtype'])
    if not c['TEST_ONLY']: _headers(host)
    host.smap=SharedMemory(create=True,size=math.prod(shape)*dtype.itemsize)
    host.omap=SharedMemory(create=True,size=math.prod(shape)*4)
    host.s=np.ndarray(shape,dtype,buffer=host.smap.buf)
    host.o=np.ndarray(shape,np.float32,buffer=host.omap.buf)
    atomic_json(host.session/'shared-mappings.json',dict(S=dict(name=host.smap.name,bytes=host.s.nbytes),
                O=dict(name=host.omap.name,bytes=host.o.nbytes),full_O_allocations=1,shape=shape,source_dtype=str(dtype)))
    refmap=reference=None
    try:
        if c['phase']=='frame':
            host.s[:]=(np.arange(math.prod(shape)).reshape(shape)%251).astype(np.uint8)
            host.o[:]=host.s.astype(np.float32)/np.float32(255)+np.float32(.03125)
            source_info=dict(pixel_sha256=array_hash(host.s),icc_bytes=None)
        elif c['TEST_ONLY']:
            with open(host.sem['source_path'],'rb') as f: original=np.load(f,allow_pickle=False)
            need(original.dtype==np.float32 and list(original.shape)==shape,'FIRST_FIT_F2_SOURCE')
            host.s[:]=original;del original
            source_info=dict(pixel_sha256=array_hash(host.s),icc_bytes=None)
        else:
            host.count(source_decode=1)
            original,source_info=codec.read_source(host.sem['source_path'],strict_group15=False,development_pair=True)
            need(original.dtype==np.uint8 and list(original.shape)==shape and source_info['pixel_sha256']==c['source_identity']['pixel_sha256'],'FIRST_FIT_SOURCE_PIXELS')
            _application_decode(host,'source',source_info)
            host.s[:]=original;host.count(source_copy=1);host.icc=source_info['icc_bytes'];del original;gc.collect()
        host.s.flags.writeable=False
        if host.sem.get('first_fit'):
            if c['phase']=='frame':
                from ..cpu_recipe.assets import parse_file
                parsed=parse_file(c['reference']['path'],'r0')
                work=host.session/'fit';work.mkdir()
                receipt=dict(status='COMPLETE',job_id=host.spec['job_id'],attempt_id=host.spec['attempt_id'],
                    producer_build=c['executor_build']['sha256'],parameter_sha256=parsed['value_sha256'],TEST_ONLY=True,
                    metadata_fixture=True,counts=dict(fit=0,updates=0,forward=0,model_construct=0))
                parameter_file=c['reference']['path']
                atomic_json(work/'fit-receipt.json',receipt)
            else:
                if c['TEST_ONLY']:
                    ref=np.load(host.sem['reference_path'],allow_pickle=False)
                    reference_info=dict(pixel_sha256=array_hash(ref))
                else:
                    host.count(reference_decode=1)
                    ref,reference_info=codec.read_reference(host.sem['reference_path'],strict_group15=False,development_pair=True)
                need(list(ref.shape)==c['reference_identity']['shape'] and reference_info['pixel_sha256']==c['reference_identity']['pixel_sha256'],'FIRST_FIT_REFERENCE_PIXELS')
                _application_decode(host,'reference',reference_info)
                refmap=SharedMemory(create=True,size=ref.nbytes);reference=np.ndarray(ref.shape,ref.dtype,buffer=refmap.buf)
                reference[:]=ref;reference.flags.writeable=False;del ref
                atomic_json(host.session/'reference-mapping.json',dict(name=refmap.name,bytes=reference.nbytes,lifecycle='fit preparation only'))
                atomic_json(host.session/'shared-mappings.json',dict(S=dict(name=host.smap.name,bytes=host.s.nbytes),O=dict(name=host.omap.name,bytes=host.o.nbytes),
                    reference=dict(name=refmap.name,bytes=reference.nbytes),full_O_allocations=1,shape=shape))
                work=_worker(host,'fit',dict(work=str(host.session/'fit'),S=dict(name=host.smap.name,shape=shape,dtype=str(dtype),readonly=True),
                    reference=dict(name=refmap.name,shape=list(reference.shape),dtype=str(reference.dtype),readonly=True),
                    cancel=str(host.path/'cancel.signal'),operation=str(host.path),reservation=c['science']))
                receipt=strict_load(work/'fit-receipt.json');parameter_file=work/'parameters.npz'
                del reference;reference=None;refmap.close();refmap.unlink();refmap=None;gc.collect()
            intent=dict(schema='cpu-first-fit-intent-v1',semantics=host.spec['semantics'],job_id=host.spec['job_id'],attempt_id=host.spec['attempt_id'])
            if c.get('schema')=='cpu-application-execution-v4':host.progress('save_fast_recipe')
            info=publish(host.sem['bundle_directory'],parameter_file,intent,receipt,c,
                         checkpoint=lambda point:lifecycle.checkpoint(host.path,point))
            atomic_json(host.path/'recipe-committed.json',dict(bundle_id=info['bundle_id'],math_id=info['math_id'],
                        complete=binding(Path(host.sem['bundle_directory'])/'COMPLETE.json'),fit_receipt=binding(work/'fit-receipt.json')))
        m,_=complete(host.sem['bundle_directory'])
        if not host.sem.get('first_fit'):
            need(m['bundle_id']==host.sem['bundle_id'] and m['math_id']==host.sem['math_id'] and
                 m['bundle_id']==c['bundle']['bundle_id'] and m['math_id']==c['bundle']['math_id'], 'GENERATED_HOST_BUNDLE_CHANGED')
        host.sem.update(bundle_id=m['bundle_id'],math_id=m['math_id'])
        # Freeze the actual learned identity before the first render. This is
        # a receipt bound to the authoritative input-stage context, not a new
        # self-reported qualification grant.
        atomic_json(host.session/'render-authorization.json',dict(schema='cpu-first-fit-render-authorization-v1',
                    parent_context=host.spec['semantics']['instance_context'],bundle_id=m['bundle_id'],math_id=m['math_id'],
                    parameter_sha256=m['mathematical']['r0_parameters']['value_sha256'],
                    producer_build=m['producer_build']['sha256'],executor_build=c['executor_build']['sha256'],
                    bundle_complete=binding(Path(host.sem['bundle_directory'])/'COMPLETE.json')))
        r0=None
        if c['phase']!='frame':
            work=_worker(host,'r0',dict(work=str(host.session/'R0'),S=dict(name=host.smap.name,shape=shape,dtype=str(dtype),readonly=True),
                O=dict(name=host.omap.name,shape=shape,dtype='float32'),bundle=host.sem['bundle_directory'],
                cancel=str(host.path/'cancel.signal'),operation=str(host.path),reservation=c['science']))
            r0=strict_load(work/'r0-result.json');host.count(R0_render=1,forward=r0['counts']['forward_batches'])
        host.o.flags.writeable=False;host.y=host.o
        if c['TEST_ONLY'] and c['phase']!='frame':
            # Small validation bytes only; never write native float caches.
            np.save(host.session/'small-O.npy',host.o,allow_pickle=False)
            from ..engine.group15_codec import quantize8
            q=quantize8(host.o)
            atomic_json(host.session/'small-Q8.json',dict(sha256=array_hash(q),shape=list(q.shape),traversals=1))
            del q
        host.finish_prepare(source_info,r0,dict(O=array_hash(host.o)),c['TEST_ONLY'])
    finally:
        reference=None
        if refmap:
            refmap.close();refmap.unlink()
