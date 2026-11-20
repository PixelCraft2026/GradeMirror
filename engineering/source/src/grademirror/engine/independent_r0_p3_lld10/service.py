"""Managed fine composition around the unchanged R0 fit/render service."""
from pathlib import Path
import hashlib,math,time
import numpy as np
import torch
from ..independent_r0 import adapter as r0
from . import adapter,area,render,llf,profile
from .compose import compose
from .ledger import Ledger
from .solver_process import solve
from ...common import atomic_json
from ...engine.profiles import get_profile
from ...fine_observation import BufferObservation

def ahash(a):return hashlib.sha256(memoryview(np.ascontiguousarray(a)).cast('B')).hexdigest()
def reference_float64(reference):
    if reference.dtype not in (np.uint8,np.uint16):raise ValueError('P3_REFERENCE_DTYPE')
    return np.ascontiguousarray(reference.astype(np.float64)/np.iinfo(reference.dtype).max)

def run_fine(run,mode,spec,session,*,fetch,put_y,gpu_preflight,progress):
    adapter.check_runtime();gpu=gpu_preflight(spec);started=time.monotonic();times={};hashes={};witnesses={};L=None
    from ...light.runtime import operation
    from ...light.eta import emit
    light=operation() is not None
    report=(lambda done,total:emit(run/"render-progress.json","render",done,total)) if light else None
    bound_profile=get_profile(profile.PROFILE_ID,spec.get('profile_contract_sha256'))
    observation=BufferObservation(run/'buffer-memory.jsonl')
    def observed(event):
        observation.event(event,dict(allocated_bytes=int(torch.cuda.memory_allocated()),reserved_bytes=int(torch.cuda.memory_reserved()),lifetime_peak_allocated_bytes=int(torch.cuda.max_memory_allocated()),lifetime_peak_reserved_bytes=int(torch.cuda.max_memory_reserved())))
    cancelled=lambda:(run/'cancel.signal').exists()
    def check():
        if cancelled():raise InterruptedError('FINE_CANCELLED')
        if time.monotonic()-started>session['operation_seconds']:raise TimeoutError('FINE_OPERATION_DEADLINE')
    def phase(stage):
        check();row=dict(stage=stage,profile_id=profile.PROFILE_ID,elapsed_seconds=time.monotonic()-started)
        if stage=='fit':row['fit_started_wall']=time.time()
        if stage=='render':row['render_started_wall']=time.time()
        if light:row['gm066_monotonic']=time.monotonic()
        atomic_json(run/'phase.json',row)
    def measure(name,callback):
        phase(name);observed(name+':before');torch.cuda.synchronize();at=time.perf_counter();value=callback();torch.cuda.synchronize();times[name]=time.perf_counter()-at;observed(name+':after');return value
    def proof(name,value,xy):
        at=time.perf_counter();hashes[name]=ahash(value);witnesses[name]=dict(xy=xy.tolist(),values=value[xy[:,1],xy[:,0]].tolist());times[name+'_hash_scan']=time.perf_counter()-at
    try:
        source,source_identity,source_info=measure('fetch_source',lambda:fetch(session,'FETCH_SOURCE',None,None))
        observation.track('source',source);native_wh=[source.shape[1],source.shape[0]];observed('source_acquired')
        from ...fine_resources import gpu_plan
        resource_plan=gpu_plan(source.shape[0]*source.shape[1],native_wh=native_wh if spec.get('gpu_resource') is not None else None)
        from ...light.resources import active_plan, check_gpu_stage, check_host, owned_object, scoped_gpu_plan
        active = active_plan("gpu")
        if active is not None:
            resource_plan = scoped_gpu_plan(dict(width=native_wh[0], height=native_wh[1]), resource_plan,
                                            fine=True, operation="replay" if mode == "replay" else "fit")
        if spec.get('fine_gpu_plan',resource_plan)!=resource_plan:raise ValueError('FINE_GPU_PLAN_MISMATCH')
        counts=r0.EngineCounts();fit_record=None
        if mode=='development_pair':
            reference,reference_identity,reference_info=measure('fetch_reference',lambda:fetch(session,'FETCH_REFERENCE',None,None))
            observation.track('reference',reference)
            def prepare():
                before,sg=r0.downscale_native(source);after,rg=r0.downscale_native(reference)
                color={'color_policy_id':'untagged-assume-srgb-user-20261017','source':{'interpretation':source_info['color']['interpretation'],'icc_sha256':source_info['color']['icc_sha256'],'file_sha256':source_identity['file_sha256']},'reference':{'interpretation':reference_info['color']['interpretation'],'marker':'present'if reference_info['color']['icc_present']or reference_info['color']['srgb_present']else'absent','file_sha256':reference_identity['file_sha256']}}
                return r0.prepared_pair(before,after,source_sha256=source_identity['file_sha256'],reference_sha256=reference_identity['file_sha256'],color_declaration='gamma-encoded-sRGB-float32',resize_geometry={'source':sg,'reference':rg},source_identity=source_identity,reference_identity=reference_identity,color_policy=color,io_runtime=spec['io_runtime'])
            seed=spec.get('refinement_seed')
            if seed is not None:
                def reuse():
                    from ...common import sha_file
                    folder=Path(seed['directory']);binding=seed['binding']
                    if sha_file(folder/'recipe.json')!=binding['recipe_file_sha256']or sha_file(folder/'parameters.npz')!=binding['parameters_file_sha256']:raise ValueError('REFINEMENT_SEED_CHANGED')
                    value=r0.load_recipe(folder)
                    if value.metadata['recipe_sha256']!=binding['recipe_sha256']or value.metadata['source_identity']!=source_identity or value.metadata['reference_identity']!=reference_identity:raise ValueError('REFINEMENT_INPUT_CHANGED')
                    return value
                recipe=measure('reuse_fast_parameters',reuse)
            else:
                if active is not None:
                    check_host(run, active, "training_prepare", point="gpu-training-prepare")
                pair=measure('input_prepare',prepare)
                if active is not None:
                    check_gpu_stage(run, active, torch, "training", point="gpu-training")
                recipe,fit_record=measure('fit',lambda:r0.fit(pair,allow_development_profile=True,device='cuda:0',recipe_kind='development_image',device_metadata={k:gpu[k]for k in ('uuid','driver_version')},progress=lambda row:progress(run,row),counts=counts))
                atomic_json(run/'training-record.json',fit_record);del pair
        else:
            if light:phase('reuse_fast_parameters')
            reuse_started=time.perf_counter()
            saved=adapter.load_recipe(Path(spec['recipe_dir']));recipe=saved.r0_recipe;reference_identity=recipe.metadata['reference_identity'];reference=None
            if light:times['reuse_fast_parameters']=time.perf_counter()-reuse_started
        if mode == 'development_pair' and active is not None:
            from ...stability_checkpoint import record
            checkpoint_r0 = run / 'checkpoint-r0'
            r0.save_recipe(recipe, checkpoint_r0)
            record(run, checkpoint_r0, {"fit": counts.fit, "updates": 1000 if fit_record else 0})
        if active is not None:
            check_gpu_stage(run, active, torch, "R0", point="gpu-r0",
                            buffers=(("source_ipc_copy",source),), release_unused_cache=True)
        render_device={k:gpu[k] for k in ("name","uuid","driver_version")} if light else None
        O=measure('render',lambda:r0.render(source,recipe,source_identity=source_identity,device='cuda:0',cancelled=cancelled,counts=counts,progress=report,execution_device=render_device)).result
        observation.track('O',O);del source;observed('source_released_after_render')
        torch.cuda.empty_cache();xy=area.witness_xy(native_wh);proof('O',O,xy);observed('proof_O_saved')
        if mode=='development_pair'and spec.get('refinement_seed')is not None and hashes['O']!=spec['refinement_seed']['binding']['float32_sha256']:raise ValueError('REFINEMENT_FAST_RESULT_CHANGED')
        rwh=[reference_identity['shape'][1],reference_identity['shape'][0]]
        retained=None
        if mode=='development_pair' and spec.get('saved_p3') is not None:
            from ...saved_post import gpu as saved_gpu
            seed=spec['refinement_seed']
            binding,retained,payload=saved_gpu(seed['directory'],seed['binding'],spec['saved_p3'])
            if hashes['O']!=retained['O']:raise ValueError('GPU_SAVED_P3_O_CHANGED')
            solver=retained['solver'];b=np.frombuffer(payload,dtype='<f8').reshape(solver['shape'])
            del payload,reference
            atomic_json(run/'saved-p3-consumption.json',dict(binding=binding,scalar_CG=0,range_discovery=0))
        elif mode=='development_pair':
            if active is not None:
                check_host(run, active, "P3_prepare", point="gpu-p3-prepare",
                           buffers=(owned_object("O",O),))
            H=measure('H_area',lambda:area.area_prefix(O,rwh,cancel=check))
            observation.track('H',H)
            T=measure('T_float64',lambda:reference_float64(reference))
            observation.track('T',T)
            del reference
            if active is not None:
                check_host(run, active, "P3", point="gpu-p3-solve",
                           buffers=tuple(owned_object(name, value) for name,value in (("O",O),("H64",H),("T64",T))))
            atomic_json(run/'p3-solve-prepayment.json',dict(scalar_CG=6 if spec.get('gm008_acceptance_identity')else 3,H64_sha256=ahash(H),T64_sha256=ahash(T),array_payload_bytes=H.nbytes+T.nbytes,identity_control=bool(spec.get('gm008_acceptance_identity'))))
            b,solver=measure('P3_solve',lambda:solve(run,H,T,native_wh,identity_control=bool(spec.get('gm008_acceptance_identity')),cancelled=cancelled,deadline=started+session['operation_seconds']))
            solver['H64_sha256']=ahash(H);solver['T64_sha256']=ahash(T);del H,T
            if active is not None:
                checkpoint_p3 = run / 'checkpoint-p3.npz'
                with checkpoint_p3.open('xb') as stream:
                    np.savez_compressed(stream, b=np.ascontiguousarray(b, dtype='<f8'))
                record(run, checkpoint_r0,
                       {"fit": counts.fit, "R0": counts.render, "tiles": counts.render_tiles,
                        "CG": solver['counts']['scalar_CG']},
                       p3_path=checkpoint_p3, solver=solver)
        else:b=saved.coefficients;solver=saved.metadata['postprocess']['solver']
        observation.track('b',b)
        L=Ledger(run/'fine-counts.json',dict(P3_native_fields=1,P3_128row_strips=math.ceil(len(O)/128),native_RGB_LLF=2,native_scalar_LLF=6,native_remap_pyramids=1542,D_calls=10836,U_calls=21630,final_compositions=1),cancel_path=run/'cancel.signal',deadline=started+session['operation_seconds'])
        L.gpu_reserved_bytes=resource_plan['reserved_bytes'];L.rgb_llf_seconds=resource_plan['rgb_seconds']
        if active is not None:
            check_gpu_stage(run, active, torch, "P3_expand", point="gpu-p3-expand", buffers=(("O",O),))
        P=measure('P3_expand',lambda:render.render(O,b,rwh,L));proof('OP',P,xy)
        observation.track('P',P);observed('proof_OP_saved')
        if retained is not None and hashes['OP']!=retained['OP']:raise ValueError('GPU_SAVED_P3_OP_CHANGED')
        ranges=retained['ranges'] if retained is not None else measure('pair_range_scan',lambda:llf.recipe([O,P]))
        if mode=='replay'and ranges!=saved.metadata['postprocess']['ranges']:raise ValueError('FINE_REPLAY_RANGE_MISMATCH')
        remaps=2*sum(row['count']for row in ranges)
        acceptance=spec.get('gm008_acceptance_expected')
        if acceptance:
            # Supplied only by the prepaid acceptance harness; no research paths.
            if ranges!=acceptance['ranges'] or any(hashes[k]!=acceptance['hashes'][k]for k in ('O','OP')):
                raise ValueError('FINE_ACCEPTANCE_PRE_LLF_BRIDGE_MISMATCH')
            if ahash(b)!=acceptance['b64_sha256'] or solver.get('T64_sha256')!=acceptance['T64_sha256']:
                raise ValueError('FINE_ACCEPTANCE_P3_BRIDGE_MISMATCH')
        # Tighten predeclared general maxima before any native LLF call.
        L.caps.update(native_remap_pyramids=remaps,D_calls=(remaps+6)*7,U_calls=remaps*14+42);L.save()
        atomic_json(run/'llf-service-prepayment.json',dict(ranges=ranges,native_RGB_LLF=2,native_scalar_LLF=6,native_remap_pyramids=remaps,D_calls=(remaps+6)*7,U_calls=remaps*14+42,composition=1))
        if active is not None:
            check_gpu_stage(run, active, torch, "GPU_LLF", point="LLF-O", buffers=(("O",O),("P_or_LP",P)))
        LO=measure('LLF_O',lambda:llf.rgb(O,ranges,L));proof('LO',LO,xy)
        observation.track('LO',LO);observed('proof_LO_saved_before_overwrite')
        if active is not None:
            check_gpu_stage(run, active, torch, "GPU_LLF", point="LLF-P",
                          buffers=(("O",O),("P_or_LP",P),("LO_or_Y",LO)), release_unused_cache=True)
        LP=measure('LLF_P',lambda:llf.rgb(P,ranges,L,out=P));proof('LP',LP,xy)
        observation.track('LP',LP);observed('P_is_LP');del P
        if active is not None:
            check_host(run, active, "compose", point="gpu-compose",
                       buffers=tuple(owned_object(name, value) for name,value in (("O",O),("P_or_LP",LP),("LO_or_Y",LO))))
        Y=measure('compose',lambda:compose(O,LP,LO,L,out=LO));proof('Y',Y,xy)
        observation.track('Y',Y);observed('LO_is_Y');del O,LP,LO
        observed('postprocess_inputs_released')
        if mode=='development_pair':
            denom={'uint8':255,'uint16':65535}[reference_identity['dtype']]
            post=dict(version='gm008-post-v1',P3=profile.P3,LLF=profile.LLF,solver_runtime=profile.RUNTIME['solver'],ranges=ranges,hashes=hashes,witnesses=witnesses,coefficient_shape=list(b.shape),coefficient_sha256=ahash(b),reference_normalization=dict(dtype='float64',denominator=denom,grid=reference_identity['shape'],intermediate_float32=False),solver=solver)
            if light:phase('save_fine_recipe')
            save_started=time.perf_counter()
            saved=adapter.save_recipe(recipe,b,post,Path(spec['recipe_dir']),bound_profile=bound_profile)
            if light:times['save_fine_recipe']=time.perf_counter()-save_started
        elif hashes!=saved.metadata['postprocess']['hashes']or witnesses!=saved.metadata['postprocess']['witnesses']:raise ValueError('FINE_REPLAY_HASH_MISMATCH')
        L.save('PASS');atomic_json(run/'fine-proof.json',dict(status='PASS',hashes=hashes,witnesses=witnesses,ranges=ranges,coefficient_sha256=ahash(b),times=times,counts=L.counts,solver=solver))
        meta=saved.metadata;del b,recipe,saved
        gpu.update(peak_allocated_bytes=int(torch.cuda.max_memory_allocated()),peak_reserved_bytes=int(torch.cuda.max_memory_reserved()))
        if gpu['peak_reserved_bytes']>resource_plan['reserved_bytes']:raise MemoryError('FINE_GPU_RESERVED_LIMIT')
        atomic_json(run/'pre-handoff.json',dict(profile_id=profile.PROFILE_ID,recipe_sha256=meta['recipe_sha256'],float32_sha256=hashes['Y'],fit_count=counts.fit,render_count=counts.render,render_tiles=counts.render_tiles,model_forward_batches=(2001 if fit_record else 0)+counts.render_tiles,gpu=gpu))
        reply=measure('host_handoff',lambda:put_y(session,Y,meta['recipe_sha256'],source_identity['pixel_sha256'],counts.fit,counts.render,profile=bound_profile))
        result=dict(status='PASS',mode=mode,profile_id=profile.PROFILE_ID,profile_contract_sha256=bound_profile['contract_sha256'],fit_count=counts.fit,render_count=counts.render,render_tiles=counts.render_tiles,model_forward_batches=(2001 if fit_record else 0)+counts.render_tiles,recipe_sha256=meta['recipe_sha256'],float32_sha256=hashes['Y'],source_identity=source_identity,reference_identity=reference_identity,lease=reply['lease'],gpu=gpu,fit_seconds=times.get('fit',0),render_seconds=times['render'],input_fetch_seconds=times['fetch_source']+times.get('fetch_reference',0),input_prepare_seconds=times.get('input_prepare',0),host_handoff_seconds=times['host_handoff'],training_record=fit_record,postprocess_counts=L.counts,postprocess_times=times,postprocess_hashes=hashes,scalar_CG=solver['counts']['scalar_CG']if mode=='development_pair' and retained is None else 0,bridge={})
        if mode=='development_pair'and spec.get('refinement_seed')is not None:result['refinement_seed']=spec['refinement_seed']['binding']
        from ...light.portable import source_sha
        result['execution_implementation']=dict(id='gm009-fine-memory-v2',core_sha256=profile.CORE_SHA256,contract_sha256=profile.PROFILE_CONTRACT_SHA256,files={name:source_sha(Path(__file__).parent/name)for name in [*profile.MATH_SHA256,'service.py']},recipe_contract_sha256=bound_profile['contract_sha256'])
        result['memory_observation']=dict(events=observation.count,seconds=observation.seconds,replay_skipped=['fetch_reference','fit','H_area','T_float64','P3_solve']if mode=='replay'else[],main_RGB_capacity_rule='private HWC float32 buffers; scalar/D2H/block/IPC tracked separately in capacity audit')
        atomic_json(run/'compute-result.json',result);return result
    except BaseException:
        observed('failed_private_buffers_not_published')
        if L:L.save('FAILED')
        atomic_json(run/'fine-partial.json',dict(times=times,hashes=hashes,counts=L.counts if L else {},failed=True));raise
