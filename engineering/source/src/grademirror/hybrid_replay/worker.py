"""Unchanged R0 and CPU executor calls beneath separate bounded Jobs."""
import ctypes, os, time, traceback, uuid, weakref
from pathlib import Path
from ..common import atomic_json, strict_load
from ..winproc import current_identity
from .contract import require
from .audit import science


def sha_array(value):
    import hashlib
    return hashlib.sha256(memoryview(value).cast('B')).hexdigest()


def r0(payload_path):
    import numpy as np, torch
    from multiprocessing import shared_memory
    from ..engine.independent_r0_p3_lld10 import adapter
    from ..engine.independent_r0 import adapter as base
    from ..resource_policy import device_memory_snapshot, available_gpu_bytes
    from .jobs import Nvml
    p=strict_load(payload_path);require(set(p)=={'operation_path','recipe_dir','plan','shared'},'HYBRID_R0_PAYLOAD')
    path=Path(p['operation_path']);out=path/'R0';plan=p['plan'];expected=plan['device'];vram=plan['R0_gpu']
    shared=p['shared'];od=shared['O'];shape=plan['source_identity']['shape']
    require(od['shape']==shape==shared['S']['shape']and len(shape)==3 and shape[2]==3 and all(type(n)is int and n>0 for n in shape),'HYBRID_R0_OUTPUT_SHAPE')
    require(np.dtype(od['dtype'])==np.dtype(np.float32)and np.dtype(od['dtype']).isnative,'HYBRID_R0_OUTPUT_DTYPE')
    require(isinstance(od['name'],str)and bool(od['name'])and all(od['name'].lstrip('/').casefold()!=shared[k]['name'].lstrip('/').casefold()for k in('S','R')if k in shared),'HYBRID_R0_OUTPUT_ALIAS')
    require(np.__version__=='1.26.4' and torch.__version__=='2.5.1+cu124','HYBRID_RUNTIME')
    torch.set_num_threads(4);torch.set_num_interop_threads(1);torch.cuda.set_per_process_memory_fraction(.8,0);torch.cuda.init()
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    torch.backends.cudnn.benchmark=False;torch.backends.cudnn.deterministic=True
    nv=Nvml(expected['uuid'])
    try:metadata=nv.sample()
    finally:nv.close()
    require((metadata['name'],metadata['uuid'],metadata['driver_version'])==
            (expected['name'],expected['uuid'],expected['driver_version']),'REPLAY_DEVICE_CHANGED')
    dll=ctypes.WinDLL('nvcuda.dll');fn=dll.cuDeviceGetUuid_v2;buf=(ctypes.c_ubyte*16)()
    fn.argtypes=[ctypes.c_void_p,ctypes.c_int];fn.restype=ctypes.c_int
    require(fn(ctypes.byref(buf),0)==0,'HYBRID_RAW_UUID_UNKNOWN')
    raw='GPU-'+str(uuid.UUID(bytes=bytes(buf)));snap=device_memory_snapshot(expected['uuid']);free,total=torch.cuda.mem_get_info()
    require(raw==expected['uuid'] and available_gpu_bytes(free,snap)>=vram['required_free_bytes'], 'HYBRID_R0_GPU_ADMISSION')
    atomic_json(out/'gpu-admission.json',dict(plan=vram,raw_uuid=raw,raw_free=free,raw_total=total,nvml=snap,metadata=metadata,fraction=.8))
    ss=oo=source=target=value=saved=None;refs=[];original=None;primary_error=None
    try:
        ss=shared_memory.SharedMemory(name=p['shared']['S']['name'],create=False)
        oo=shared_memory.SharedMemory(name=p['shared']['O']['name'],create=False)
        require(oo.size>=shape[0]*shape[1]*shape[2]*4,'HYBRID_R0_OUTPUT_CAPACITY')
        source=np.ndarray(plan['source_identity']['shape'],dtype=np.dtype(p['shared']['S']['dtype']),buffer=ss.buf)
        source.flags.writeable=False;target=np.ndarray(source.shape,np.float32,buffer=oo.buf)
        refs=[weakref.ref(source),weakref.ref(target)]
        science(path,'R0-load',trained_R0_loads=1);saved=adapter.load_recipe(Path(p['recipe_dir']))
        require(saved.metadata['recipe_sha256']==plan['parent_recipe_sha256'] and
            sha_array(saved.coefficients)==saved.metadata['postprocess']['coefficient_sha256'],'HYBRID_RECIPE_OR_B_BRIDGE')
        tiles=plan['tiles'];science(path,'R0-render',R0_renders=1,model_forward_batches=tiles)
        counts=base.EngineCounts();forwards=0;last=-1.;started=time.monotonic();original=base.core.Field.forward
        def forward(self,*args,**kwargs):
            nonlocal forwards,last
            if(path/'cancel.signal').exists():
                from ..common import GMError
                raise GMError('CANCELLED','Cancelled before R0 tile',8)
            if time.monotonic()-started>=900:raise TimeoutError('HYBRID_R0_DEADLINE')
            forwards+=1;now=time.monotonic()
            if now-last>=1:
                atomic_json(out/'progress.json',dict(stage='R0',tiles=forwards,total=tiles,elapsed=now-started));last=now
            return original(self,*args,**kwargs)
        base.core.Field.forward=forward
        from ..light.runtime import operation
        render_device=expected if operation() is not None else None
        try:value=base.render(source,saved.r0_recipe,source_identity=plan['source_identity'],tile_side=512,device='cuda:0',counts=counts,out=target,execution_device=render_device,cancelled=lambda:(path/'cancel.signal').exists())
        finally:base.core.Field.forward=original;original=None
        if(path/'cancel.signal').exists():
            from ..common import GMError
            raise GMError('CANCELLED','Cancelled after R0 tile write',8)
        require(counts.fit==0 and counts.render==1 and counts.render_tiles==forwards==tiles,'HYBRID_R0_COUNTS')
        require(sha_array(value.result)==saved.metadata['postprocess']['hashes']['O'],'HYBRID_R0_HASH')
        require(value.result is target,'HYBRID_R0_OUTPUT_OWNERSHIP');torch.cuda.synchronize()
        final=dict(max_allocated=torch.cuda.max_memory_allocated(),max_reserved=torch.cuda.max_memory_reserved(),nvml=device_memory_snapshot(expected['uuid']))
        atomic_json(out/'gpu-final.json',final)
        require(final['max_reserved']<=vram['reserved_bytes'] and final['nvml']['free_bytes']>=512*1024**2,'HYBRID_R0_GPU_LIMIT')
        atomic_json(out/'result.json',dict(status='PASS',fit=counts.fit,render=counts.render,tiles=tiles,
            model_forward_batches=forwards,O_sha256=sha_array(target),b_sha256=sha_array(saved.coefficients),
            recipe_sha256=plan['parent_recipe_sha256'],elapsed=time.monotonic()-started))
    except BaseException as exc:
        primary_error=dict(traceback=traceback.format_exc(),error_type=type(exc).__name__,message=str(exc))
        try:atomic_json(out/'failure.json',primary_error)
        except BaseException:pass  # Do not replace the primary error with diagnostic IO.
        traceback.clear_frames(exc.__traceback__);raise
    finally:
        if original is not None:base.core.Field.forward=original
        source=target=value=saved=None
        cleanup_errors=[]
        for mapping in(ss,oo):
            if mapping is not None:
                try:mapping.close()
                except BaseException as error:cleanup_errors.append(dict(type=type(error).__name__,message=str(error)))
        try:atomic_json(out/'mapping-cleanup.json',dict(views_remaining=sum(r()is not None for r in refs),handles_closed=not cleanup_errors,creator=False,cleanup_errors=cleanup_errors,primary_error=primary_error))
        except BaseException:
            if primary_error is None:raise
        if cleanup_errors and primary_error is None:raise BufferError('HYBRID_R0_MAPPING_CLEANUP_FAILED')


def cpu(payload_path):
    from ..engine.cpu_postprocess import service,executor,core
    from .allocation import Allocations
    from ..light.resources import workspace_limit
    m=strict_load(payload_path)['manifest']
    h,w,_=m['O']['shape']
    observer=Allocations(workspace_limit(h,w,m['core_rows']));observer.install();original_execute=executor.execute;original_compose=executor.compose;original_remap=core.remap;active={};pixels=0
    def execute(m,provider,sink,gate,ranges):
        observer.excluded=[(int(v.__array_interface__['data'][0]),v.nbytes)for v in(provider.o,provider.b)];active['gate']=gate
        try:return original_execute(m,provider,sink,gate,ranges)
        finally:active.clear()
    def compose(*args):active['gate'].take(scalar_compositions=1);return observer.track(original_compose(*args))
    def remap(f,tau):
        nonlocal pixels
        pixels+=f.numel();return original_remap(f,tau)
    executor.execute=execute;executor.compose=compose;core.remap=remap
    try:return service.worker(Path(payload_path))
    finally:
        executor.execute=original_execute;executor.compose=original_compose;core.remap=original_remap;observer.close()
        m=strict_load(Path(payload_path))['manifest']
        atomic_json(Path(m['output_root'])/'allocations.json',dict(observer.record(),base_remap_pixels=pixels))


def main(mode,payload):
    path=Path(payload);out=Path(strict_load(path)['operation_path'])/'R0' if mode=='R0' else Path(strict_load(path)['manifest']['output_root'])
    atomic_json(out/'entry.json',dict(identity=current_identity(),mode=mode))
    end=time.monotonic()+30
    while not(out/'actual-handle-retained.json').exists():
        if time.monotonic()>end:raise TimeoutError('HYBRID_ACTUAL_ENTRY_ACK')
        time.sleep(.005)
    try:return {'R0':r0,'CPU':cpu}[mode](path)
    except BaseException:
        atomic_json(out/'entry-failure.json',dict(traceback=traceback.format_exc()));raise
