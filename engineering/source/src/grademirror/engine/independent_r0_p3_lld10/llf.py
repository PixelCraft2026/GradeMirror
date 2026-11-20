"""Exact anchor LLF; within-call geometry and lazy Laplacian-band reuse."""
import math,time
import numpy as np
import torch
from .llf_common import recipe,scalar_recipe,remap,DELTA
class Geometry:
 def __init__(self,device):self.device=device;self.d={};self.u={}
 def down(self,shape):
  if shape not in self.d:
   h,w=shape;ix=torch.arange((w+1)//2,device=self.device)*2;iy=torch.arange((h+1)//2,device=self.device)*2
   self.d[shape]=([ (ix+k).clamp(0,w-1) for k in [-1,0,1,2] ],[ (iy+k).clamp(0,h-1) for k in [-1,0,1,2] ])
  return self.d[shape]
 def up(self,coarse,shape):
  key=tuple(coarse)+tuple(shape)
  if key not in self.u:
   h,w=shape;ch,cw=coarse;qx=(torch.arange(w,device=self.device,dtype=torch.float32)-.5)/2;qy=(torch.arange(h,device=self.device,dtype=torch.float32)-.5)/2;ix=qx.floor().to(torch.long);iy=qy.floor().to(torch.long);wx=(qx-ix.float())[None];wy=(qy-iy.float())[:,None]
   self.u[key]=(ix.clamp(0,cw-1),(ix+1).clamp(0,cw-1),iy.clamp(0,ch-1),(iy+1).clamp(0,ch-1),wx,wy)
  return self.u[key]
def down(f,L,G):
 L.take(D_calls=1);xs,ys=G.down(tuple(f.shape));x=f.index_select(1,xs[0])
 for index,m in zip(xs[1:],[3,3,1]):part=f.index_select(1,index);part.mul_(m);x.add_(part);del part
 x.div_(8);y=x.index_select(0,ys[0])
 for index,m in zip(ys[1:],[3,3,1]):part=x.index_select(0,index);part.mul_(m);y.add_(part);del part
 y.div_(8);return y
def up(f,shape,L,G):
 L.take(U_calls=1);ix0,ix1,iy0,iy1,wx,wy=G.up(tuple(f.shape),tuple(shape));a=f.index_select(1,ix0);b=f.index_select(1,ix1);b.sub_(a).mul_(wx);a.add_(b);del b
 out=a.index_select(0,iy0);b=a.index_select(0,iy1);b.sub_(out).mul_(wy);out.add_(b);return out
def pyramid(f,L,G):
 g=[f]
 for _ in range(7):g.append(down(g[-1],L,G))
 return g
def lap(g,j,cache,L,G):
 if j not in cache:
  a=up(g[j+1],g[j].shape,L,G);a.neg_().add_(g[j]);cache[j]=a
 return cache[j]
def scalar(array,r,L,kind='tiny',capture=False,variant=None,selector=None,amount=-1.,geometry=None,*,destination=None):
 assert variant is None and selector is None and array.ndim==2 and array.dtype==np.float32 and np.isfinite(array).all()
 assert r['count']==r['kmax']-r['kmin']+1<=257 and r['delta']==DELTA and r['sigma']==.1 and r['J']==8
 L.take(**{kind+'_scalar_LLF':1});start=time.perf_counter();f=torch.from_numpy(np.ascontiguousarray(array)).cuda();G=geometry or Geometry(f.device);g=pyramid(f,L,G);bands=[torch.zeros_like(z) for z in g[:-1]];active=[]
 for z in g:
  assert bool(torch.isfinite(z).all());lo=float(z.min());hi=float(z.max());assert lo>=r['kmin']*DELTA and hi<=r['kmax']*DELTA,'SELECTOR_RANGE';active.append((math.floor(lo*64),math.floor(hi*64)))
 L.take(**{kind+'_remap_pyramids':1});previous=pyramid(remap(f,r['kmin']*DELTA,amount),L,G);previous_lap={}
 for k in range(r['kmin']+1,r['kmax']+1):
  assert torch.cuda.max_memory_reserved()<=getattr(L,'gpu_reserved_bytes',3*1024**3),'LLF_RESERVED_ENVELOPE';L.take(**{kind+'_remap_pyramids':1});current=pyramid(remap(f,k*DELTA,amount),L,G);current_lap={};low=(k-1)*DELTA;high=k*DELTA
  for j in range(7):
   if not active[j][0]<=k-1<=active[j][1]:continue
   mask=(g[j]>=low)&(g[j]<high)
   a=lap(previous,j,previous_lap,L,G);b=lap(current,j,current_lap,L,G).clone();b.sub_(a);weights=g[j].clone().sub_(low).mul_(64);b.mul_(weights).add_(a);bands[j].copy_(torch.where(mask,b,bands[j]));del a,b,weights,mask
  previous=current;previous_lap=current_lap
 out=g[-1].clone();levels=[z.detach().cpu().numpy().copy() for z in bands]+[g[-1].detach().cpu().numpy().copy()] if capture else None
 for j in range(6,-1,-1):u=up(out,bands[j].shape,L,G);u.add_(bands[j]);out=u
 torch.cuda.synchronize()
 if destination is not None:
  # Keep the full CUDA result, but transfer and validate bounded row blocks.
  # Each channel's complete input has already been copied before in-place output.
  for first in range(0, out.shape[0], 64):
   block = out[first:first + 64].cpu().numpy()
   assert np.isfinite(block).all()
   destination[first:first + len(block)] = block
   del block
  assert time.perf_counter()-start<=getattr(L,'rgb_llf_seconds',900)
  return None
 result=out.cpu().numpy().copy();assert np.isfinite(result).all() and time.perf_counter()-start<=getattr(L,'rgb_llf_seconds',900)
 return (result,levels) if capture else result
def rgb(image,ranges,L,progress=None,*,out=None):
 if out is not None:
  if (not isinstance(out,np.ndarray)or out.dtype!=np.float32 or out.ndim!=3 or out.shape[-1]!=3 or out.shape!=image.shape or not out.flags.c_contiguous or not out.flags.writeable or not out.flags.owndata):raise ValueError('LLF_OUTPUT_OWNERSHIP')
  if out is not image and np.shares_memory(out,image):raise ValueError('LLF_OUTPUT_ALIAS')
 else:out=np.empty_like(image)
 L.take(native_RGB_LLF=1);start=time.perf_counter();G=Geometry(torch.device('cuda:0'))
 for c in range(3):
  scalar(np.array(image[...,c],dtype=np.float32,copy=True,order='C'),
         ranges[c],L,'native',geometry=G,destination=out[...,c])
  if progress:progress(c)
 assert time.perf_counter()-start<=getattr(L,'rgb_llf_seconds',900),'RGB_LLF_DEADLINE';return out
