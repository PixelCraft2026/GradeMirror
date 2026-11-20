"""Original GM048 mathematical loop, independently scoped for full CPU photographs."""
import hashlib,platform,sys,time
import numpy as np
import torch
from ..independent_r0 import adapter,core
from ..cpu_r0_fit.optimizer_policy import configure_cpu,bind_cpu_optimizer_policy
from . import contract as k
from .recipe import ImageRecipe,semantic,validate as validate_recipe
need=k.need
class Cancelled(InterruptedError):
    def __init__(self,counts,phase,sections):self.counts=dict(counts);self.phase=phase;self.sections=list(sections);super().__init__('CPU_FIT_CANCELLED')

def _fit_loop(before,after,settings,cancelled,progress):
    cfg={k:v for k,v in settings.items()if k not in ('seed','long_side','tile_side')};seed=settings['seed'];device='cpu'
    counts=dict(fit=1,model_construct=0,optimizer_construct=0,updates=0,forward=0);sections=[];started=time.perf_counter();last_progress=-float('inf');hook=None
    def check(phase):
        if cancelled():raise Cancelled(counts,phase,sections)
        need(time.perf_counter()-started<(3600 if settings['updates']==1000 else 600),'CPU_FIT_TIMEOUT')
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
        emit('cancelled',terminal=True,completed_updates=counts['updates'],total_updates=settings['updates'])
        raise
    finally:
        if hook is not None:hook.remove()

def validate(pair,request):
    row=k.validate_request(request,'fit');need(type(pair)is adapter.PreparedPair,'CPU_IMAGE_PAIR_TYPE')
    kind='synthetic_test'if request['scope']['case']=='small'else'development_image'
    for name,array in (('source',pair.before),('reference',pair.after)):
        need(type(array)is np.ndarray and array.dtype==np.float32 and list(array.shape)==row['training_shape']and array.flags.c_contiguous and not array.flags.writeable,'CPU_IMAGE_TRAINING_ARRAY')
        need(np.isfinite(array).all()and (array>=0).all()and (array<=1).all()and hashlib.sha256(memoryview(array).cast('B')).hexdigest()==row['training_'+name+'_pixel_sha256'],'CPU_IMAGE_TRAINING_PIXELS')
    meta=adapter._pair_metadata(pair,kind)
    need(k.canonical(meta)==k.canonical({name:row[name]for name in meta}),'CPU_IMAGE_PREPARED_PAIR_METADATA')
    need(pair.source_sha256==row['source_file']['sha256']and pair.reference_sha256==row['reference_file']['sha256']and pair.color_declaration==row['color_declaration'],'CPU_IMAGE_PREPARED_PAIR_FILES')
    need(platform.python_version()==k.PYTHON and k.file_hash(sys.executable)==k.PYTHON_LAUNCHER_SHA256 and k.file_hash(sys._base_executable)==k.PYTHON_BASE_SHA256,'CPU_IMAGE_INTERPRETER');adapter.check_runtime()
    return row

def fit(pair,request,*,cancelled=lambda:False,progress=None):
    k.validate_request(request,'fit')
    if cancelled():raise Cancelled(dict(fit=0,model_construct=0,optimizer_construct=0,updates=0,forward=0),'before_entry',[])
    row=validate(pair,request)
    if cancelled():raise Cancelled(dict(fit=0,model_construct=0,optimizer_construct=0,updates=0,forward=0),'before_loop',[])
    model,record,counts,sections=_fit_loop(pair.before,pair.after,request['settings'],cancelled,progress)
    try:
        if cancelled():raise Cancelled(counts,'before_recipe',sections)
        parameters=adapter._parameters(model)
        meta=dict(recipe_schema_version=k.RECIPE,backend_id=k.BACKEND,backend_sha256=request['backend_sha256'],qualification_manifest=request['qualification_manifest'],scope=request['scope'],profile=dict(id=k.PROFILE_ID,contract_sha256=k.PROFILE_CONTRACT_SHA256,core_sha256=k.CORE_SHA256),execution_device='cpu',CPU_setup='default CPU generator seed43;threads4/interop1;cv2threads4;FP32/channels_last',optimizer_policy='original Adam.step; instance-only CPU noncapturable graph check',runtime=request['runtime'],settings=request['settings'],pair=row,normalization=request['normalization'],parameter_sha256=adapter._parameter_hash(parameters),flags=k.FLAGS)
        meta['recipe_sha256']=semantic(meta);recipe=validate_recipe(ImageRecipe(meta,parameters))
        if cancelled():raise Cancelled(counts,'before_success_return',sections)
        receipt=dict(schema_version='cpu-r0-image-fit-receipt-v1',status='COMPLETE',backend_id=k.BACKEND,backend_sha256=request['backend_sha256'],request_sha256=k.digest(request),qualification_manifest=request['qualification_manifest'],scope=request['scope'],recipe_sha256=meta['recipe_sha256'],parameter_sha256=meta['parameter_sha256'],counts=counts,record=record,uninterruptible_sections=sections,flags=k.FLAGS)
        if progress:progress(dict(stage='complete',completed_updates=counts['updates'],total_updates=request['settings']['updates']))
        return recipe,receipt
    finally:del model
