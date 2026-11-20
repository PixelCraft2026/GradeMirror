"""Short original fit orchestration with CPU setup, cooperative boundaries and receipt."""
import hashlib,platform,sys,time
import numpy as np
import torch
from ..independent_r0 import adapter,core
from .contract import (BACKEND_ID,RECEIPT,PYTHON,PYTHON_LAUNCHER_SHA256,PYTHON_BASE_SHA256,
    validate_request,canonical,digest,file_hash,need)
from .optimizer_policy import configure_cpu,bind_cpu_optimizer_policy

class Cancelled(InterruptedError):
    def __init__(self,counts,phase,sections):self.counts=dict(counts);self.phase=phase;self.sections=list(sections);super().__init__('CPU_FIT_CANCELLED')

def validate(pair,request):
    validate_request(request)
    need(type(pair)is adapter.PreparedPair,'CPU_FIT_PREPARED_PAIR_TYPE')
    p=request['pair']
    for name,array in (('source',pair.before),('reference',pair.after)):
        need(type(array)is np.ndarray and array.dtype==np.float32 and list(array.shape)==p[name+'_identity']['shape']and array.flags.c_contiguous and not array.flags.writeable,'CPU_FIT_READONLY_ARRAY')
        need(np.isfinite(array).all()and (array>=0).all()and (array<=1).all(),'CPU_FIT_FLOAT_DOMAIN')
        actual=adapter.array_identity(array,file_sha256=p[name+'_file']['sha256'],file_size_bytes=p[name+'_file']['bytes'])
        need(actual==p[name+'_identity'],'CPU_FIT_INPUT_PIXELS')
    actual={k:getattr(pair,k)for k in ('source_sha256','reference_sha256','color_declaration','resize_geometry','source_identity','reference_identity','color_policy','io_runtime')}
    need(canonical(actual)==canonical({k:p[k]for k in actual}),'CPU_FIT_PREPARED_PAIR_BINDING')
    need(platform.python_version()==PYTHON and file_hash(sys.executable)==PYTHON_LAUNCHER_SHA256 and file_hash(sys._base_executable)==PYTHON_BASE_SHA256,'CPU_FIT_INTERPRETER')
    adapter.check_runtime();adapter._pair_metadata(pair,'synthetic_test')

def _fit_loop(before,after,settings,cancelled,progress):
    cfg={k:v for k,v in settings.items()if k not in ('seed','long_side','tile_side')};seed=settings['seed'];device='cpu'
    counts=dict(fit=1,model_construct=0,optimizer_construct=0,updates=0,forward=0);sections=[];started=time.perf_counter();last_progress=-float('inf');hook=None
    def check(phase):
        if cancelled():raise Cancelled(counts,phase,sections)
        need(time.perf_counter()-started<600,'CPU_FIT_TIMEOUT')
    def emit(stage,terminal=False,**data):
        nonlocal last_progress
        now=time.perf_counter()
        if progress and (terminal or now-last_progress>=1):progress(dict(stage=stage,elapsed=now-started,**data));last_progress=now
    def forward_count(module,args):counts['forward']+=1
    configure_cpu()
    def convert(array):
        with torch.inference_mode():
            tensor=torch.from_numpy(array).permute(2,0,1)[None].to(device).contiguous(memory_format=torch.channels_last)
            return core.rgb_to_lab(tensor)[0].permute(1,2,0).cpu().numpy().copy()
    try:
        before,after=convert(before),convert(after)
        training_hashes=dict(before=core.ahash(before),after=core.ahash(after))
        core.frozen.synchronize(device);prepared_at=time.perf_counter();check('before_model')
        model=core.Field(cfg,seed).to(device=device,memory_format=torch.channels_last);counts['model_construct']+=1
        hook=model.register_forward_pre_hook(forward_count)
        opts,schedules,groups=core.optimizers(model);counts['optimizer_construct']=len(opts);bind_cpu_optimizer_policy(opts)
        picker=core.Picker(core.frozen.to_tensor(before,device),core.frozen.to_tensor(after,device),cfg,seed)
        generator=torch.Generator(device=device).manual_seed(core.REGULARIZATION['generator_seed'])
        core.frozen.synchronize(device);initialized_at=time.perf_counter();trace=[];lrs=[];calls=0
        model.train()
        for step in range(1,cfg['updates']+1):
            check('before_sample');emit('sampling',update=step);section_started=time.perf_counter()
            patch,xy,valid,target=picker.sample();lrs.append(opts[0].param_groups[0]['lr'])
            for o in opts:o.zero_grad(set_to_none=True)
            pred=model(patch,xy,valid)[:,:,1:-1,1:-1];l1=core.F.l1_loss(pred,target)
            regularizer,curve,gain,fraction=core.sampled_penalty_lab(model,patch,xy,valid,generator);calls+=1
            loss=l1+cfg['mapping_coefficient']*regularizer
            if not torch.isfinite(loss):raise FloatingPointError('NUMERICAL_FAILURE_LOSS')
            loss.backward()
            for o in opts:o.step()
            for q in schedules:q.step()
            counts['updates']+=1;sections.append(dict(kind='cooperative_update_section_not_single_kernel',update=step,seconds=time.perf_counter()-section_started));check('after_update')
            if step==1 or step%100==0 or step==cfg['updates']:
                core.frozen.synchronize(device)
                entry=dict(update=step,sampled_l1=float(l1.detach()),objective=float(loss.detach()),regularizer=float(regularizer.detach()),curvature_penalty=float(curve.detach()),gain_penalty=float(gain.detach()),valid_regularizer_fraction=float(fraction.detach()),lr_used=lrs[-1])
                trace.append(entry);emit('training',**entry)
        core.frozen.synchronize(device);loop_end=time.perf_counter()
        if not all(torch.isfinite(p).all()for p in model.parameters()):raise FloatingPointError('NUMERICAL_FAILURE_WEIGHTS')
        check('before_terminal');model.eval()
        with torch.inference_mode():
            fitted=model(core.frozen.to_tensor(before,device),core.frozen.coordinates(*before.shape[:2],device))
            mae=float(core.F.l1_loss(fitted,core.frozen.to_tensor(after,device)))
        core.frozen.synchronize(device);check('before_loop_return')
        record=dict(parameters=sum(p.numel()for p in model.parameters()),trace=trace,training_seconds=loop_end-initialized_at,lab_prepare_seconds=prepared_at-started,initialization_seconds=initialized_at-prepared_at,training_service_seconds=time.perf_counter()-started,final_training_mae=mae,terminal_grid_hash=core.ahash(fitted),final_parameter_hashes={k:core.ahash(v)for k,v in model.state_dict().items()},initial_parameter_hashes=model.initial_hashes,base_initial_parameter_hashes=model.base_initial_hashes,training_array_hashes=training_hashes,lr_vector_sha256=core.ahash(np.asarray(lrs,dtype=np.float64)),window_base_sha256=picker.base_hash.hexdigest(),window_stream_sha256=hashlib.sha256(b''.join(bytes.fromhex(h)for h in picker.hashes)).hexdigest(),window_steps=len(picker.hashes),optimizer_groups=groups,mapping_regularization=dict(forward_batches=calls,probe_window_samples=calls*192,generator_state_sha256=core.ahash(generator.get_state())),model_forward_batches=cfg['updates']*2+1)
        return model,record,counts,sections
    except Cancelled:
        emit('cancelled',terminal=True,completed_updates=counts['updates'],total_updates=3)
        raise
    finally:
        if hook is not None:hook.remove()

def fit(pair,request,*,cancelled=lambda:False,progress=None):
    validate_request(request)
    if cancelled():raise Cancelled(dict(fit=0,model_construct=0,optimizer_construct=0,updates=0,forward=0),'before_entry',[])
    validate(pair,request)
    if cancelled():raise Cancelled(dict(fit=0,model_construct=0,optimizer_construct=0,updates=0,forward=0),'before_loop',[])
    model,record,counts,sections=_fit_loop(pair.before,pair.after,request['settings'],cancelled,progress)
    try:
        if cancelled():raise Cancelled(counts,'before_recipe',sections)
        parameters=adapter._parameters(model)
        metadata=dict(recipe_schema_version=adapter.RECIPE_SCHEMA_VERSION,recipe_kind='synthetic_test',profile_id=adapter.PROFILE_ID,profile_contract_sha256=adapter.PROFILE_CONTRACT_SHA256,production_qualified=False,core_sha256=adapter.CORE_SHA256,runtime=adapter.RUNTIME,python_version=platform.python_version(),device=adapter._device('cpu',recipe_kind='synthetic_test'),settings=request['settings'],source_sha256=pair.source_sha256,reference_sha256=pair.reference_sha256,color_declaration=pair.color_declaration,**adapter._pair_metadata(pair,'synthetic_test'),input_normalization=request['normalization'],parameter_sha256=adapter._parameter_hash(parameters))
        metadata['recipe_sha256']=adapter.recipe_digest(metadata);recipe=adapter.FittedRecipe(metadata,parameters);adapter.validate_recipe(recipe,allow_synthetic_test=True)
        if cancelled():raise Cancelled(counts,'before_success_return',sections)
        receipt=dict(schema_version=RECEIPT,status='COMPLETE',backend_id=BACKEND_ID,backend_sha256=request['backend_sha256'],request_sha256=digest(request),scope=request['scope'],qualification_manifest=request['qualification_manifest'],pair=request['pair'],execution_device='cpu',settings=request['settings'],CPU_setup='default CPU generator seed43;threads4/interop1;cv2threads4',optimizer_policy='instance-only all-CPU noncapturable health check; original Adam.step',recipe_semantic_sha256=metadata['recipe_sha256'],parameter_sha256=metadata['parameter_sha256'],counts=counts,record=record,uninterruptible_sections=sections,production_qualified=False,execution_authorized=False)
        if progress:progress(dict(stage='complete',completed_updates=3,total_updates=3))
        return recipe,receipt
    finally:del model
