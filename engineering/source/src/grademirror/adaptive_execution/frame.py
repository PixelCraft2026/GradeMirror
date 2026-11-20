"""Frozen TEST_ONLY computation boundary; no fit, R0, P3, CG or LLF."""
import math
from ..common import atomic_json
from .intent import need

def prepare(host):
    import numpy as np
    from ..cpu_replay.host import array_hash
    from ..cpu_replay import runtime
    runtime.require_runtime(host.session/'host')
    host.check();shape=host.context['source_identity']['shape']
    need(host.context['TEST_ONLY'] is True and max(shape[:2])<=64 and shape[2]==3,'APPLICATION_FRAME_SCOPE')
    from .recipes import prepare as prepare_recipe
    from ..cpu_replay import lifecycle
    from ..cpu_replay.contract import binding
    directory,m=prepare_recipe(host.context,host.spec,checkpoint=lambda name:lifecycle.checkpoint(host.path,name))
    host.sem=dict(host.sem,bundle_id=m['bundle_id'],math_id=m['math_id'])
    atomic_json(host.path/'bundle-authorization.json',dict(directory=str(directory),bundle_id=m['bundle_id'],math_id=m['math_id'],
        context=host.spec['semantics']['instance_context'],complete=binding(directory/'COMPLETE.json'),before_frame_output=True))
    host.allocate(shape);host.s[:]=(np.arange(math.prod(shape)).reshape(shape)%251).astype(np.uint8)
    host.o[:]=host.s.astype(np.float32)/np.float32(255)+np.float32(.03125)
    host.s.flags.writeable=False;host.o.flags.writeable=False
    hashes=dict(O=array_hash(host.o))
    if host.sem['mode']=='fine':
        hashes.update(host.postprocess(True));host.o=None;host.omap.close();host.omap.unlink();host.omap=None
        with host.receiver.prepared_view() as view:host.y=view.view()
        del view
    else:host.y=host.o
    atomic_json(host.session/'frame-actual.json',dict(TEST_ONLY=True,shape=shape,frame_generation=1,
        fit=0,model_load=0,forward=0,R0=0,P3=0,CG=0,LLF=0,photo_decode=0))
    return host.finish_prepare(dict(pixel_sha256=array_hash(host.s),icc_bytes=None),None,hashes,True)
