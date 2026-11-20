"""Only native orchestration; original codecs, renderer, receiver and filters remain."""
import gc
from pathlib import Path
from ..common import atomic_json
from .native_contract import strict_load
from ..cpu_replay.contract import need,binding
from . import native_science as science

def prepare(host):
    import numpy as np
    from ..engine import group15_codec as codec
    from ..cpu_replay import runtime
    from ..cpu_replay.host import array_hash
    c=host.context;host.check();runtime.require_runtime(host.session/'host')
    shape=c['source_identity']['shape'];host.allocate(shape)
    host.progress('read_source');science.start(c,'source',dict(source_decode=1));host.count(source_decode=1)
    original,info=codec.read_source(host.sem['source_path'],strict_group15=False,development_pair=True)
    need(list(original.shape)==shape and original.dtype==np.uint8 and info['pixel_sha256']==c['source_identity']['pixel_sha256'],'NATIVE_SOURCE_PIXEL_IDENTITY')
    host.s[:]=original;host.count(source_copy=1);host.icc=info['icc_bytes'];del original;gc.collect();host.s.flags.writeable=False
    atomic_json(host.path/'native-source-receipt.json',dict(source=c['source'],pixel_sha256=info['pixel_sha256'],shape=shape,decode_completed=True))
    science.complete(c,'source',dict(source_decode=1),binding(host.path/'native-source-receipt.json'))
    host.progress('R0');work=host.session/'R0';work.mkdir()
    payload=dict(S=dict(name=host.smap.name,shape=shape,dtype='uint8',readonly=True),O=dict(name=host.omap.name,shape=shape,dtype='float32'),bundle=host.sem['bundle_directory'],cancel=str(host.path/'cancel.signal'),operation=str(host.path),work=str(work),reservation=dict(R0_render=1,forward=140,model=1,state_load=1))
    atomic_json(work/'payload.json',payload);atomic_json(work/'scientific-reservation.json',payload['reservation'])
    science.start(c,'R0',dict(R0_render=1,forward=140,Field=1,state_load=1))
    job,_=runtime.launch('r0',work/'payload.json',work)
    try:
        waited=runtime.wait(job,work,timeout=1200);need(waited['exit_code']==0,'R0_WORKER_FAILED');r0=strict_load(work/'r0-result.json')
        need(r0['counts']['render']==1 and r0['counts']['forward_batches']==140 and r0['counts']['model_construct']==1 and r0['counts']['state_load']==1,'NATIVE_R0_COUNTS')
        host.count(R0_render=1,forward=140);science.complete(c,'R0',dict(R0_render=1,forward=140,Field=1,state_load=1),binding(work/'r0-result.json'))
    finally:
        if job.poll() is None:job.terminate();runtime.wait(job,work,timeout=30)
        job.close()
    host.o.flags.writeable=False;hashes=dict(O=array_hash(host.o))
    if c['mode']=='fine':
        host.progress('CPU')
        science.start(c,'CPU',dict(CPU256_service=1,scalar_LLF_footprints=168,compositions=84));hashes.update(host.postprocess(False))
        science.complete(c,'CPU',dict(CPU256_service=1,scalar_LLF_footprints=168,compositions=84),binding(host.session/'CPU/fields-ACK.json'))
        host.o=None;host.omap.close();host.omap.unlink();host.omap=None
        with host.receiver.prepared_view() as view:host.y=view.view()
        del view
    else:host.y=host.o
    return host.finish_prepare(info,r0,hashes,False)

def postprocess_metadata(host):
    from ..cpu_recipe.generated_fine import complete
    m,_=complete(host.sem['bundle_directory']);p3=m['mathematical']['p3']
    return dict(shape=p3['b']['members']['b']['shape'],dtype='float64',sha256=p3['b']['value_sha256']),p3['ranges'],p3['reference_wh']


def retain_cpu_before_ready(job,work,*,expected_role='cpu'):
    """Retain the original streaming worker before it can preload or emit ready."""
    import time
    from .. import winproc as win
    need(expected_role in ('cpu','entry_cpu'),'CPU_EARLY_ROLE')
    work=Path(work);until=time.monotonic()+30;handle=None
    try:
        while not (work/'actual-entry.json').is_file():
            need(job.poll() is None,'CPU_EARLY_EXIT_BEFORE_IDENTITY')
            need(time.monotonic()<until,'CPU_EARLY_ENTRY_TIMEOUT');time.sleep(.01)
        entry=strict_load(work/'actual-entry.json');who=entry['identity']
        need(entry['role']==expected_role and entry['dry']==(expected_role=='entry_cpu') and job.member(who['pid']),'CPU_EARLY_ROLE_OR_MEMBERSHIP')
        handle=win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION|win.SYNCHRONIZE,False,who['pid'])
        need(handle and win.identity_key(who) is not None
             and win.identity_key(win._identity_from_handle(handle,who['pid']))==win.identity_key(who),'CPU_EARLY_ACTUAL_IDENTITY')
        proof=dict(identity=who,retained=True,before_preload_and_ready=True,source='HOST_RETAINED_ACTUAL_HANDLE')
        atomic_json(work/'parent-retained-before-ready.json',proof)
        atomic_json(work/'actual-retained.json',proof)
        return handle,who
    except BaseException:
        if handle:win.close(handle)
        raise
