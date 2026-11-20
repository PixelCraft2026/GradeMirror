"""Stage adapters around frozen R0, area_prefix, P3 child and CPU executor."""
import ctypes,hashlib,math,os,time,traceback,uuid
from pathlib import Path
from ..common import atomic_json,strict_load,sha_file
from ..hybrid_replay.contract import require
from ..hybrid_replay.audit import science,start

def ah(value):return hashlib.sha256(memoryview(value).cast('B')).hexdigest()

def check(path,started,seconds):
    if(path/'cancel.signal').exists():
        from ..common import GMError
        raise GMError('CANCELLED','Cancelled in first-fit stage',8)
    if time.monotonic()-started>seconds:raise TimeoutError('HYBRID_FIT_STAGE_DEADLINE')

def gpu(plan,out):
    import torch
    from ..resource_policy import device_memory_snapshot,available_gpu_bytes
    from ..hybrid_replay.jobs import Nvml
    expected=plan['device'];vram=plan['R0_gpu']
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
    require(raw==expected['uuid']and available_gpu_bytes(free,snap)>=vram['required_free_bytes'],'HYBRID_R0_GPU_ADMISSION')
    atomic_json(out/'gpu-admission.json',dict(plan=vram,raw_uuid=raw,raw_free=free,raw_total=total,nvml=snap,metadata=metadata,fraction=.8))

def r0(payload):
    import numpy as np,torch
    from multiprocessing import shared_memory
    from ..engine.independent_r0 import adapter as base
    p=strict_load(payload);path=Path(p['operation_path']);out=path/'R0';plan=p['plan'];shared=p['shared'];started=time.monotonic()
    od=shared['O'];shape=plan['source_identity']['shape']
    require(od['shape']==shape==shared['S']['shape']and len(shape)==3 and shape[2]==3 and all(type(n)is int and n>0 for n in shape),'HYBRID_R0_OUTPUT_SHAPE')
    require(np.dtype(od['dtype'])==np.dtype(np.float32)and np.dtype(od['dtype']).isnative,'HYBRID_R0_OUTPUT_DTYPE')
    require(isinstance(od['name'],str)and bool(od['name'])and all(od['name'].lstrip('/').casefold()!=shared[k]['name'].lstrip('/').casefold()for k in('S','R')if k in shared),'HYBRID_R0_OUTPUT_ALIAS')
    require(np.__version__=='1.26.4'and torch.__version__=='2.5.1+cu124','HYBRID_RUNTIME')
    gpu(plan,out);maps={};source=reference=target=pair=recipe=value=None;counts=base.EngineCounts();forward_calls=0
    original=base.core.Field.forward;last=-1.;primary_error=None
    def counted(self,*args,**kwargs):
        nonlocal forward_calls,last
        check(path,started,plan['durations']['R0']);forward_calls+=1;now=time.monotonic()
        if now-last>=1 and not light:
            atomic_json(out/'progress.json',dict(stage='R0',forwards=forward_calls,elapsed=now-started));last=now
        return original(self,*args,**kwargs)
    from ..light.runtime import operation
    from ..light.eta import emit
    light=operation() is not None
    report=(lambda done,total:emit(out/"progress.json","render",done,total)) if light else None
    try:
        for key in ('S','O')+ (('R',)if plan['operation']=='fit'else()):maps[key]=shared_memory.SharedMemory(name=shared[key]['name'])
        require(maps['O'].size>=math.prod(shape)*4,'HYBRID_R0_OUTPUT_CAPACITY')
        source=np.ndarray(shared['S']['shape'],np.dtype(shared['S']['dtype']),buffer=maps['S'].buf);source.flags.writeable=False
        target=np.ndarray(shared['O']['shape'],np.float32,buffer=maps['O'].buf)
        sid=base.array_identity(source,file_sha256=plan['source_identity']['file_sha256'],file_size_bytes=plan['source_identity']['file_size_bytes'])
        require(sid==shared['source_identity'],'HYBRID_FIT_SOURCE_PIXELS')
        base.core.Field.forward=counted
        if plan['operation']=='fit' and "saved_r0" not in plan:
            reference=np.ndarray(shared['R']['shape'],np.dtype(shared['R']['dtype']),buffer=maps['R'].buf);reference.flags.writeable=False
            rid=base.array_identity(reference,file_sha256=plan['reference_identity']['file_sha256'],file_size_bytes=plan['reference_identity']['file_size_bytes'])
            require(rid==shared['reference_identity'],'HYBRID_FIT_REFERENCE_PIXELS')
            before,sg=base.downscale_native(source);after,rg=base.downscale_native(reference)
            pair=base.prepared_pair(before,after,source_sha256=sid['file_sha256'],reference_sha256=rid['file_sha256'],
                color_declaration='gamma-encoded-sRGB-float32',resize_geometry=dict(source=sg,reference=rg),source_identity=sid,
                reference_identity=rid,color_policy=shared['color_policy'],io_runtime=shared['io_runtime'])
            del before,after
            science(path,'R0-fit',R0_fits=1,optimizer_updates=1000,backward_updates=1000,training_forwards=2001,model_forward_batches=2001)
            last_update=-1.
            def update(row):
                nonlocal last_update
                check(path,started,1200);now=time.monotonic()
                if (not light or row.get('stage')!='update_check') and (now-last_update>=1 or row.get('update')in (1,1000)):
                    atomic_json(out/'progress.json',dict(row,elapsed=now-started,total_updates=1000,
                        **({"gm066_monotonic":now} if light else {})));last_update=now
            recipe,training=base.fit(pair,allow_development_profile=True,device='cuda:0',recipe_kind='development_image',
                device_metadata={k:plan['device'][k]for k in ('uuid','driver_version')},progress=update,counts=counts)
            require(forward_calls==2001 and counts.fit==1,'HYBRID_FIT_TRAINING_COUNTS')
            atomic_json(out/'training-record.json',training);pair=None
            r0_directory = Path(p['recipe_dir'])/'r0'
            base.save_recipe(recipe,r0_directory)
            from ..stability_checkpoint import record
            record(out,r0_directory,{"fit": counts.fit, "updates": 1000})
        else:
            science(path,'R0-load',trained_R0_loads=1);recipe=base.load_recipe(Path(p['recipe_dir'])/'r0');training=None
            require(recipe.metadata['source_identity']==sid,'HYBRID_FIT_REPLAY_SOURCE')
            if "saved_r0" in plan:
                require(recipe.metadata["recipe_sha256"]==plan["saved_r0"]["recipe_sha256"]
                        and recipe.metadata["reference_identity"]==shared["reference_identity"],
                        "MIXED_RECOVERY_SEED_CHANGED")
        fit_forwards=forward_calls;science(path,'R0-render',R0_renders=1,model_forward_batches=plan['tiles'])
        value=base.render(source,recipe,source_identity=sid,tile_side=512,device='cuda:0',cancelled=lambda:(path/'cancel.signal').exists(),counts=counts,out=target,progress=report,execution_device=plan['device'] if light else None)
        check(path,started,plan['durations']['R0'])
        require(counts.render==1 and counts.render_tiles==plan['tiles']==forward_calls-fit_forwards,'HYBRID_R0_COUNTS')
        require(value.result is target,'HYBRID_R0_OUTPUT_OWNERSHIP');torch.cuda.synchronize()
        if "saved_r0" in plan:
            require(ah(target)==plan["saved_r0"]["O_sha256"],"MIXED_RECOVERY_O_HASH")
        if plan['operation']=='replay':require(ah(target)==strict_load(Path(p['recipe_dir'])/'recipe.json')['postprocess']['O_sha256'],'HYBRID_R0_HASH')
        from ..resource_policy import device_memory_snapshot
        final=dict(max_allocated=torch.cuda.max_memory_allocated(),max_reserved=torch.cuda.max_memory_reserved(),nvml=device_memory_snapshot(plan['device']['uuid']))
        atomic_json(out/'gpu-final.json',final)
        require(final['max_reserved']<=plan['R0_gpu']['reserved_bytes']and final['nvml']['free_bytes']>=512*1024**2,'HYBRID_R0_GPU_LIMIT')
        atomic_json(out/'result.json',dict(status='PASS',fit=counts.fit,render=counts.render,tiles=counts.render_tiles,
            model_forward_batches=forward_calls,training_forwards=fit_forwards,O_sha256=ah(target),
            parameter_sha256=recipe.metadata['parameter_sha256'],r0_recipe_sha256=recipe.metadata['recipe_sha256'],
            source_identity=sid,reference_identity=recipe.metadata['reference_identity'],elapsed=time.monotonic()-started))
    except BaseException as exc:
        primary_error=dict(error_type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc())
        try:atomic_json(out/'failure.json',primary_error)
        except BaseException:pass  # Preserve the primary exception if diagnostic IO also fails.
        traceback.clear_frames(exc.__traceback__);raise
    finally:
        base.core.Field.forward=original;source=reference=target=pair=recipe=value=None
        cleanup_errors=[]
        for mapping in maps.values():
            try:mapping.close()
            except BaseException as error:cleanup_errors.append(dict(type=type(error).__name__,message=str(error)))
        try:atomic_json(out/'mapping-cleanup.json',dict(handles_closed=not cleanup_errors,creator=False,cleanup_errors=cleanup_errors,primary_error=primary_error))
        except BaseException:
            if primary_error is None:raise
        if cleanup_errors and primary_error is None:raise BufferError('HYBRID_R0_MAPPING_CLEANUP_FAILED')

def discover(provider,m,gate):
    # Exact original executor range loop, including 128-row traversal, FP64
    # p3_region addition, channel ordering and original absolute anchor rule.
    from ..engine.cpu_postprocess.core import p3_region
    o=provider.o;b=provider.b;h,w,_=o.shape
    ranges=[]
    for c in range(3):
        lo=float('inf');hi=float('-inf')
        for start in range(0,h,128):
            end=min(h,start+128);a=provider.read(start,end,c)
            p=p3_region(a,b,m['reference_wh'],(h,w),start,end,c,gate)
            lo=min(lo,float(a.min()),float(p.min()));hi=max(hi,float(a.max()),float(p.max()));del p
        kmin=math.floor((lo-2/255)*64)-1;kmax=math.ceil((hi+2/255)*64)+1
        ranges.append(dict(kmin=kmin,kmax=kmax,count=kmax-kmin+1,min=lo,max=hi,delta=1/64,sigma=.1,J=8))
    return ranges

def prepare(payload):
    require(os.environ.get('CUDA_VISIBLE_DEVICES')=='-1','HYBRID_FIT_CPU_ENV')
    import numpy as np,torch
    from multiprocessing import shared_memory
    from ..engine.independent_r0_p3_lld10 import area,profile
    from ..engine.independent_r0_p3_lld10.solver_process import solve
    from ..engine.cpu_postprocess.executor import ArrayProvider
    from ..engine.cpu_postprocess.core import Gate
    from ..hybrid_replay.contract import RUNTIME
    from .contract import ranges as validate_ranges
    from . import FIT_CONTRACT,RECIPE_SCHEMA
    p=strict_load(payload);path=Path(p['operation_path']);out=path/'P3';plan=p['plan'];shared=p['shared'];directory=Path(p['recipe_dir'])
    torch.set_num_threads(4);torch.set_num_interop_threads(1);require(np.__version__=='1.26.4'and not torch.cuda.is_initialized(),'HYBRID_FIT_CPU_RUNTIME')
    for name,expected in profile.EXECUTION_SHA256.items():
        from ..light.portable import source_sha
        require(source_sha(Path(profile.__file__).parent/name)==expected,'HYBRID_FIT_P3_MATH_CHANGED')
    started=time.monotonic();maps={};o=reference=H=T=b=provider=None;actual={}
    def cancelled():return(path/'cancel.signal').exists()
    def taking(**counts):
        check(path,started,600)
        for key,n in counts.items():actual[key]=actual.get(key,0)+n
        require(actual.get('P3_scalar_evaluations',0)<=3*plan['source_identity']['shape'][0]*plan['source_identity']['shape'][1],'HYBRID_FIT_RANGE_BUDGET')
    try:
        for key in ('O','R'):maps[key]=shared_memory.SharedMemory(name=shared[key]['name'])
        o=np.ndarray(shared['O']['shape'],np.float32,buffer=maps['O'].buf);o.flags.writeable=False
        reference=np.ndarray(shared['R']['shape'],np.dtype(shared['R']['dtype']),buffer=maps['R'].buf);reference.flags.writeable=False
        r0_result=strict_load(path/'R0/result.json');require(ah(o)==r0_result['O_sha256'],'HYBRID_FIT_PREPARE_O')
        ref=shared['reference_identity'];rh,rw,_=ref['shape'];h,w,_=o.shape
        science(path,'P3-H64',H64_builds=1);H=area.area_prefix(o,[rw,rh],cancel=lambda:check(path,started,600))
        science(path,'P3-T64',T64_normalizations=1);T=np.ascontiguousarray(reference.astype(np.float64)/np.iinfo(reference.dtype).max)
        H_sha=ah(H);T_sha=ah(T)
        atomic_json(out/'H-T-identities.json',dict(H64_sha256=H_sha,T64_sha256=T_sha,shape=list(H.shape),dtype='float64',reference_intermediate_float32=False))
        from .audit import bridge
        bridge(path,'H-T')
        science(path,'P3-solve',P3_solvers=1,scalar_CG=3);start('P3-solver',4)
        from .audit import record_launch
        from ..engine.independent_r0_p3_lld10.solver_process import P3_PY,ROOT
        record_launch('P3-solver',[P3_PY,'-B',ROOT/'tools/gm008_solver_worker.py',out],ROOT)
        b,solver=solve(out,H,T,[w,h],cancelled=cancelled,deadline=started+600)
        solver.update(H64_sha256=H_sha,T64_sha256=T_sha);H=T=None;reference=None
        cleanup=strict_load(out/'solver-cleanup.json')
        require(cleanup['actual_wait']and cleanup['owned_tree_empty']and cleanup['identity_exited']and not cleanup['forced']and cleanup['exit_code']==0,'HYBRID_FIT_SOLVER_EXIT')
        worker=cleanup['actual_worker']
        require(worker['actual_handle_wait'] and worker['exit_code']==0,'HYBRID_FIT_SOLVER_ACTUAL_WAIT')
        atomic_json(out/'solver-individual-wait.json',worker)
        if "saved_r0" in plan:
            require(ah(b)==plan["saved_r0"]["coefficient_sha256"],"MIXED_RECOVERY_P3_HASH")
        np.savez_compressed(directory/'p3.npz',b=np.ascontiguousarray(b,dtype='<f8'))
        atomic_json(out/'coefficient-identity.json',dict(coefficient_sha256=ah(b),shape=list(b.shape),solver=solver))
        from ..stability_checkpoint import record
        record(out,directory/'r0',{"fit": r0_result['fit'], "R0": r0_result['render'],
                                   "tiles": r0_result['tiles'], "CG": solver['counts']['scalar_CG']},
               p3_path=directory/'p3.npz',solver=solver)
        bridge(path,'b')
        science(path,'range-discovery',CPU_range_discoveries=1,P3_scalar_evaluations=3*h*w)
        last_range=-1.
        def range_progress(**v):
            nonlocal last_range
            now=time.monotonic()
            if now-last_range>=1:
                atomic_json(out/'progress.json',dict(v,stage='range_scan',elapsed=now-started));last_range=now
        gate=Gate(taking,cancelled=cancelled,progress=range_progress,deadline=started+600)
        provider=ArrayProvider(o,b);found=discover(provider,dict(reference_wh=[rw,rh]),gate);validate_ranges(found)
        base=strict_load(directory/'r0/recipe.json')
        post=dict(P3=profile.P3,LLF=profile.LLF,solver_runtime=profile.RUNTIME['solver'],coefficient_shape=list(b.shape),
            coefficient_sha256=ah(b),p3_archive_sha256=sha_file(directory/'p3.npz'),
            ranges=found,reference_normalization=dict(dtype='float64',denominator=2**ref['bit_depth']-1,grid=ref['shape'],intermediate_float32=False),
            solver=solver,H64_sha256=H_sha,T64_sha256=T_sha,O_sha256=r0_result['O_sha256'])
        m=dict(schema_version=RECIPE_SCHEMA,fit_contract=FIT_CONTRACT,profile_id=profile.PROFILE_ID,
            profile_contract_sha256=plan['profile_contract_sha256'],core_sha256=profile.CORE_SHA256,
            r0_recipe_sha256=base['recipe_sha256'],r0_parameter_sha256=base['parameter_sha256'],source_identity=base['source_identity'],
            reference_identity=base['reference_identity'],device=base['device'],runtime=RUNTIME,R0_runtime=base['runtime'],
            color_policy=base['color_policy'],postprocess=post,production_qualified=False)
        from ..hybrid_replay.contract import digest
        m['recipe_sha256']=digest(m);atomic_json(directory/'recipe.json',m)
        atomic_json(out/'result.json',dict(status='PASS',root_recipe_sha256=m['recipe_sha256'],H64_sha256=H_sha,T64_sha256=T_sha,
            coefficient_sha256=post['coefficient_sha256'],ranges=found,range_actual=actual,solver=solver,elapsed=time.monotonic()-started,cuda_initialized=torch.cuda.is_initialized()))
    finally:
        provider=o=reference=H=T=b=None
        for mapping in maps.values():mapping.close()
        atomic_json(out/'mapping-cleanup.json',dict(handles_closed=True,creator=False,H_T_released=True))

def main(mode,payload):
    from ..winproc import current_identity
    path=Path(payload);p=strict_load(path);out=Path(p['operation_path'])/mode
    atomic_json(out/'entry.json',dict(identity=current_identity(),mode=mode))
    end=time.monotonic()+30
    while not(out/'actual-handle-retained.json').exists():
        if time.monotonic()>end:raise TimeoutError('HYBRID_FIT_ENTRY_ACK')
        time.sleep(.005)
    try:return {'R0':r0,'P3':prepare}[mode](path)
    except BaseException:
        atomic_json(out/'entry-failure.json',dict(traceback=traceback.format_exc()));raise
