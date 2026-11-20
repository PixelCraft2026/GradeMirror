"""Fixed R0 mathematical extraction. No research paths, checkpoint or queue imports."""
from __future__ import annotations
import hashlib,math,time
from types import SimpleNamespace
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from . import tensor_ops as frozen
from ._initialization import InitialSettings,InitializationTemplate
from ._sampling import WindowPicker
from .m05_lab import sampled_penalty_lab
from .mapping_penalty import REGULARIZATION
from .color_lab import rgb_to_lab,display_model

def ahash(a):
    if isinstance(a,torch.Tensor):a=a.detach().cpu().contiguous().numpy()
    return hashlib.sha256(memoryview(np.ascontiguousarray(a)).cast('B')).hexdigest()

def semseed(seed,name):return int.from_bytes(hashlib.sha256(f'{seed}|{name}'.encode()).digest()[:8],'little')%(2**63-1)

def uniform(shape,seed,name):
    g=torch.Generator().manual_seed(semseed(seed,name));return torch.rand(shape,generator=g)*2-1

def bound(name,shape,law):
    fan=int(np.prod(shape[1:]))
    if law=='conv_uniform':return 1/math.sqrt(fan)
    return 1/fan if name in ('color_features.weight','position_features.weight') else math.sqrt(6/fan)/30

class Field(InitializationTemplate):
    def __init__(self,cfg,seed):
        super().__init__(InitialSettings(seed=seed))
        base={k:v.detach().clone() for k,v in self.state_dict().items()}
        self.cfg=cfg;self.settings=SimpleNamespace(omega=cfg['omega'])
        w=cfg['rgb_width'];xy=cfg['xy_width'];c=cfg['context_width']
        if w!=64:
            self.color_features=nn.Conv2d(3,w,1)
            self.mix_features=nn.Conv2d(w+xy,c,1)
        if cfg['depthwise_bias']:self.neighborhood=nn.Conv2d(c,c,3,padding=1,groups=c,bias=True)
        self.rgb_extra=nn.ModuleList([nn.Conv2d(w,w,1) for _ in range(cfg['rgb_depth']-1)])
        self.xy_extra=nn.ModuleList([nn.Conv2d(xy,xy,1) for _ in range(cfg['xy_depth']-1)])
        with torch.no_grad():
            for name,p in self.named_parameters():
                ishead=name.startswith('edit_head.');isweight=name.endswith('weight')
                law=cfg['head_weight_init' if ishead and isweight else 'head_bias_init' if ishead else 'nonhead_weight_init' if isweight else 'nonhead_bias_init']
                if law=='zero' or (not isweight and law!='conv_uniform'):
                    p.zero_();continue
                if isweight:
                    if name in base and not ishead:
                        old=base[name]
                        if name=='color_features.weight':old=old[:w]
                        if name=='mix_features.weight' and w!=64:old=torch.cat((old[:,:w],old[:,64:96]),1)
                        b_old=bound(name,base[name].shape,'B0')
                        if law!='conv_uniform' and bound(name,p.shape,law)==b_old:p.copy_(old);continue
                        latent=old/b_old
                    else:latent=uniform(p.shape,seed,name)
                    p.copy_(latent*bound(name,p.shape,law))
                else:
                    weight=dict(self.named_parameters())[name[:-4]+'weight']
                    p.copy_(uniform(p.shape,seed,name)/math.sqrt(int(np.prod(weight.shape[1:]))))
        self.base_initial_hashes={k:ahash(v) for k,v in base.items()}
        self.initial_hashes={k:ahash(v) for k,v in self.state_dict().items()}
        self.initial_changes=[k for k,v in self.state_dict().items() if k not in base or tuple(v.shape)!=tuple(base[k].shape) or ahash(v)!=ahash(base[k])]
    def forward(self,rgb,xy,valid=None,intensity=1.):
        om=self.cfg['omega'];color=torch.sin(om*self.color_features(rgb));position=torch.sin(om*self.position_features(xy))
        for layer in self.rgb_extra:color=torch.sin(om*layer(color))
        for layer in self.xy_extra:position=torch.sin(om*layer(position))
        hidden=torch.sin(om*self.mix_features(torch.cat((color,position),1)))
        if valid is not None:
            hidden=hidden*valid
            if not self.cfg['halo']:
                mask=torch.ones_like(valid);mask[:,:,0,:]=0;mask[:,:,-1,:]=0;mask[:,:,:,0]=0;mask[:,:,:,-1]=0
                hidden=hidden*mask
        context=torch.sin(om*self.neighborhood(hidden));decoded=torch.sin(om*self.decode_features(context))
        return rgb+intensity*self.edit_head(decoded)
    def semantic(self,name):
        if name.startswith(('position_features','xy_extra')):return 'xy_weight' if name.endswith('weight') else 'xy_bias'
        if name.startswith(('color_features','rgb_extra')):return 'rgb'
        return 'fusion'
    def groups(self):
        # Preserve frozen B0 parameter/group order when all policy values are baseline.
        dec={'xy_weight':self.cfg['decay_xy_weight'],'xy_bias':self.cfg['decay_xy_bias'],'rgb':self.cfg['decay_rgb'],'fusion':self.cfg['decay_fusion']}
        buckets={};names={}
        for n,p in self.named_parameters():
            d=dec[self.semantic(n)];key='xy_weight' if self.semantic(n)=='xy_weight' else ('other' if d==0 else self.semantic(n))
            buckets.setdefault(key,{'params':[],'weight_decay':d});buckets[key]['params'].append(p);names.setdefault(key,[]).append(n)
        order=['other']+[k for k in buckets if k not in ('other','xy_weight')]+['xy_weight']
        return [buckets[k] for k in order if k in buckets],[dict(names=names[k],weight_decay=buckets[k]['weight_decay']) for k in order if k in buckets]

class Picker(WindowPicker):
    def __init__(self,before,after,cfg,seed):
        super().__init__(before,after,cfg['window_side'],seed)
        self.cfg=cfg;self.steps=0;self.hashes=[];self.base_hash=hashlib.sha256()
        self.aux=torch.Generator(device=before.device).manual_seed(semseed(seed,'geometry_coupling'))
    def indices(self):
        count=484;d=self.before.device
        base_h,base_w=self.cfg['baseline_training_hw'];mh,mw=base_h-13+1,base_w-13+1
        tops=torch.randint(mh,(count,),device=d,generator=self.generator)
        lefts=torch.randint(mw,(count,),device=d,generator=self.generator)
        self.base_hash.update(tops.cpu().numpy().tobytes());self.base_hash.update(lefts.cpu().numpy().tobytes())
        tops=torch.where(torch.randint(mh+1,(count,),device=d,generator=self.aux)==0,mh,tops)
        lefts=torch.where(torch.randint(mw+1,(count,),device=d,generator=self.aux)==0,mw,lefts)
        self.steps+=1;self.hashes.append(ahash(torch.stack((tops,lefts))))
        return tops,lefts
    def sample(self,count=None):return self.extract(*self.indices())

def optimizers(model):
    cfg=model.cfg;groups,description=model.groups()
    opt=torch.optim.Adam(groups,lr=cfg['learning_rate'],betas=(cfg['beta1'],cfg['beta2']),
        eps=cfg['epsilon'],foreach=None,fused=None)
    schedule=torch.optim.lr_scheduler.CosineAnnealingLR(opt,T_max=cfg['scheduler_T_max'],eta_min=cfg['final_learning_rate'])
    return [opt],[schedule],description

def configure_torch(seed=43):
    frozen.configure_torch(seed)
    import cv2
    cv2.setNumThreads(4)

def fit_pair(before,after,settings,device,progress=None):
    cfg={k:v for k,v in settings.items() if k not in ('seed','long_side','tile_side')};seed=settings['seed']
    configure_torch(seed);started=time.perf_counter()
    # CPU RGB is converted on the selected device exactly as array_to_lab does.
    def convert(array):
        with torch.inference_mode():
            tensor=torch.from_numpy(array).permute(2,0,1)[None].to(device).contiguous(memory_format=torch.channels_last)
            return rgb_to_lab(tensor)[0].permute(1,2,0).cpu().numpy().copy()
    before,after=convert(before),convert(after)
    training_hashes=dict(before=ahash(before),after=ahash(after))
    frozen.synchronize(device);prepared_at=time.perf_counter()
    model=Field(cfg,seed).to(device=device,memory_format=torch.channels_last)
    opts,schedules,groups=optimizers(model)
    picker=Picker(frozen.to_tensor(before,device),frozen.to_tensor(after,device),cfg,seed)
    generator=torch.Generator(device=device).manual_seed(REGULARIZATION['generator_seed'])
    frozen.synchronize(device);initialized_at=time.perf_counter();trace=[];lrs=[];calls=0
    model.train()
    for step in range(1,cfg['updates']+1):
        # Check every update; progress logging remains bounded and identical for both engines.
        if progress:progress(dict(stage='update_check',update=step))
        patch,xy,valid,target=picker.sample();lrs.append(opts[0].param_groups[0]['lr'])
        for o in opts:o.zero_grad(set_to_none=True)
        pred=model(patch,xy,valid)[:,:,1:-1,1:-1];l1=F.l1_loss(pred,target)
        regularizer,curve,gain,fraction=sampled_penalty_lab(model,patch,xy,valid,generator);calls+=1
        loss=l1+cfg['mapping_coefficient']*regularizer
        if not torch.isfinite(loss):raise FloatingPointError('NUMERICAL_FAILURE_LOSS')
        loss.backward()
        for o in opts:o.step()
        for q in schedules:q.step()
        if step==1 or step%100==0 or step==cfg['updates']:
            frozen.synchronize(device)
            entry=dict(update=step,sampled_l1=float(l1.detach()),objective=float(loss.detach()),
                regularizer=float(regularizer.detach()),curvature_penalty=float(curve.detach()),
                gain_penalty=float(gain.detach()),valid_regularizer_fraction=float(fraction.detach()),lr_used=lrs[-1])
            trace.append(entry)
            if progress:progress(entry)
    frozen.synchronize(device);loop_end=time.perf_counter()
    if not all(torch.isfinite(p).all() for p in model.parameters()):raise FloatingPointError('NUMERICAL_FAILURE_WEIGHTS')
    model.eval()
    with torch.inference_mode():
        fitted=model(frozen.to_tensor(before,device),frozen.coordinates(*before.shape[:2],device))
        mae=float(F.l1_loss(fitted,frozen.to_tensor(after,device)))
    frozen.synchronize(device)
    return model,dict(parameters=sum(p.numel() for p in model.parameters()),trace=trace,
        training_seconds=loop_end-initialized_at,lab_prepare_seconds=prepared_at-started,
        initialization_seconds=initialized_at-prepared_at,training_service_seconds=time.perf_counter()-started,
        final_training_mae=mae,terminal_grid_hash=ahash(fitted),
        final_parameter_hashes={k:ahash(v) for k,v in model.state_dict().items()},initial_parameter_hashes=model.initial_hashes,base_initial_parameter_hashes=model.base_initial_hashes,
        training_array_hashes=training_hashes,lr_vector_sha256=ahash(np.asarray(lrs,dtype=np.float64)),
        window_base_sha256=picker.base_hash.hexdigest(),window_stream_sha256=hashlib.sha256(b''.join(bytes.fromhex(h) for h in picker.hashes)).hexdigest(),
        window_steps=len(picker.hashes),optimizer_groups=groups,
        mapping_regularization=dict(forward_batches=calls,probe_window_samples=calls*192,generator_state_sha256=ahash(generator.get_state())),
        model_forward_batches=cfg['updates']*2+1)

def render_tiles(model,native,tile_side=512):
    yield from frozen.render_tiles(display_model(model),native,tile_side)
