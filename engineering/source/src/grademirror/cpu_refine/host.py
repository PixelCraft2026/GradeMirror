"""Resident host adapter: release reference and solver before CPU256 LLF."""
import gc
from pathlib import Path
from ..common import atomic_json,strict_load
from ..cpu_replay import runtime,lifecycle
from ..cpu_replay.contract import need,binding
from ..adaptive_execution.first_host import record_decode as _application_decode


def worker(host,role,payload,*,limit=6*1024**3,timeout=1200):
    work=Path(payload['work']);work.mkdir(parents=True)
    atomic_json(work/'payload.json',payload);atomic_json(work/'scientific-reservation.json',host.context['science'])
    if host.context.get('schema')=='cpu-application-execution-v4' and role=='prepare':timeout=max(timeout,1900)
    job,_=runtime.launch(role,work/'payload.json',work,memory=limit)
    try:
        waited=runtime.wait(job,work,timeout=timeout);need(waited['exit_code']==0,'REFINE_WORKER_FAILED:'+role)
        return work
    finally:
        if job.poll() is None:job.terminate();runtime.wait(job,work,timeout=30)
        job.close()


def prepare(host):
    import numpy as np
    from multiprocessing.shared_memory import SharedMemory
    from ..engine import group15_codec as codec
    from ..cpu_replay.host import array_hash
    from ..cpu_recipe.validation import complete
    from ..cpu_recipe.generated_fine import publish,coefficients
    c=host.context;host.check();host.sem=dict(host.sem)
    runtime.require_runtime(host.session/'host')
    shape=c['source_identity']['shape'];host.allocate(shape);refmap=None;reference=None
    try:
        if c['phase']=='frame':
            host.s[:]=(np.arange(np.prod(shape)).reshape(shape)%251).astype(np.uint8)
            host.o[:]=host.s.astype(np.float32)/np.float32(255)+np.float32(.03125)
            source_info=dict(pixel_sha256=array_hash(host.s),icc_bytes=None)
        else:
            host.count(source_decode=1)
            source,source_info=codec.read_source(host.sem['source_path'],strict_group15=False,development_pair=True)
            need(list(source.shape)==shape and source.dtype==np.uint8 and
                 source_info['pixel_sha256']==c['source_identity']['pixel_sha256'],'REFINE_SOURCE_PIXELS')
            _application_decode(host,'source',source_info)
            host.s[:]=source;host.count(source_copy=1);host.icc=source_info['icc_bytes'];del source;gc.collect()
        host.s.flags.writeable=False;r0=None
        render_bundle=host.sem['parent_bundle'] if host.sem['refine_prepare'] else host.sem['bundle_directory']
        if c['phase']!='frame':
            work=worker(host,'r0',dict(work=str(host.session/'R0'),S=dict(name=host.smap.name,shape=shape,dtype='uint8',readonly=True),
                O=dict(name=host.omap.name,shape=shape,dtype='float32'),bundle=render_bundle,
                cancel=str(host.path/'cancel.signal'),operation=str(host.path),reservation=c['science']))
            r0=strict_load(work/'r0-result.json');host.count(R0_render=1,forward=r0['counts']['forward_batches'])
        host.o.flags.writeable=False
        if host.sem['refine_prepare']:
            host.count(reference_decode=1)
            ref,info=codec.read_reference(host.sem['reference_path'],strict_group15=False,development_pair=True)
            need(list(ref.shape)==c['reference_identity']['shape'] and
                 info['pixel_sha256']==c['reference_identity']['pixel_sha256'],'REFINE_REFERENCE_PIXELS')
            _application_decode(host,'reference',info)
            refmap=SharedMemory(create=True,size=ref.nbytes);reference=np.ndarray(ref.shape,ref.dtype,buffer=refmap.buf)
            reference[:]=ref;reference.flags.writeable=False;del ref
            atomic_json(host.session/'reference-mapping.json',dict(name=refmap.name,bytes=reference.nbytes))
            work=worker(host,'prepare',dict(work=str(host.session/'P3'),O=dict(name=host.omap.name,shape=shape,dtype='float32',readonly=True),
                reference=dict(name=refmap.name,shape=list(reference.shape),dtype='uint8',readonly=True),
                reference_wh=[reference.shape[1],reference.shape[0]],cancel=str(host.path/'cancel.signal')))
            result=strict_load(work/'prepare-result.json')
            reference=None;refmap.close();refmap.unlink();refmap=None;gc.collect()
            atomic_json(host.session/'reference-released.json',dict(released=True,before_LLF=True,solver_wait=binding(work/'solver/solver-cleanup.json')))
            p3=p3_metadata(c,result,work/'b.npz')
            intent=dict(semantics=host.spec['semantics'],job_id=host.spec['job_id'],attempt_id=host.spec['attempt_id'])
            if c.get('schema')=='cpu-application-execution-v4':host.progress('save_fine_recipe')
            publish(host.sem['bundle_directory'],render_bundle,work/'b.npz',result['solver'],p3,intent,c,
                    checkpoint=lambda point:lifecycle.checkpoint(host.path,point))
        m,_=complete(host.sem['bundle_directory'])
        host.sem.update(bundle_id=m['bundle_id'],math_id=m['math_id'])
        atomic_json(host.session/'render-authorization.json',dict(schema='cpu-refine-render-authorization-v1',
            parent_context=host.spec['semantics']['instance_context'],bundle_id=m['bundle_id'],math_id=m['math_id'],
            bundle_complete=binding(Path(host.sem['bundle_directory'])/'COMPLETE.json'),executor_build=c['executor_build']['sha256'],before_LLF=True))
        lifecycle.checkpoint(host.path,'before_LLF');host.check()
        hashes=dict(O=array_hash(host.o));hashes.update(host.postprocess(c['phase']=='frame'))
        host.o=None;host.omap.close();host.omap.unlink();host.omap=None
        with host.receiver.prepared_view() as view:host.y=view.view()
        del view
        host.finish_prepare(source_info,r0,hashes,c['TEST_ONLY'])
    finally:
        reference=None
        if refmap:refmap.close();refmap.unlink()


def p3_metadata(c,result,b_path):
    """Original package metadata, shared with pure saved-publication recovery."""
    from ..cpu_recipe.generated_fine import coefficients
    return dict(b=coefficients(Path(b_path).read_bytes(),[c['reference_identity']['shape'][1],c['reference_identity']['shape'][0]]),
        reference_wh=[c['reference_identity']['shape'][1],c['reference_identity']['shape'][0]],
        H_sha256=result['H_sha256'],T_sha256=result['T_sha256'],ranges=result['ranges'],
        constants=dict(J=8,P3_h=8,sigma=.1,delta=1/64,composition='FP32(FP64(O)+(FP64(LP)-FP64(LO)))',range_margin=2/255),
        solver_policy=dict(rtol=1e-8,atol=1e-12,maxiter=2000))
