"""Complete global-footprint LLD10 on CPU, with unchanged primitive order.

Product math source: independent_r0_p3_lld10/{llf,render}.py (GM-015
baseline). Device-local geometry is explicit; no CUDA adapter is imported.
"""
import math,time
import numpy as np
import torch
from .geometry import stripe,plan,workspace_bound


class StopRequested(RuntimeError):
    pass


class Gate:
    def __init__(self, take, cancelled=lambda:False, progress=lambda **kw:None, deadline=None):
        self.take=take;self.cancelled=cancelled;self.progress=progress
        self.deadline=deadline or time.monotonic()+3600;self.last=-1.

    def check(self, **progress):
        if torch.cuda.is_initialized():raise RuntimeError('CPU_CUDA_INITIALIZED')
        if self.cancelled():raise StopRequested('CANCELLED')
        if time.monotonic()>self.deadline:raise TimeoutError('CPU_DEADLINE')
        now=time.monotonic()
        if progress or now-self.last>=1.:
            self.progress(**progress);self.last=now


class Geometry:
    def __init__(self,s):
        self.s=s;self.d={};self.u={}

    def down(self,j):
        if j not in self.d:
            h,w=self.s.shapes[j];oa,ob=self.s.gaussian[j+1];ia,_=self.s.gaussian[j]
            ix=torch.arange((w+1)//2)*2;iy=torch.arange(oa,ob)*2
            self.d[j]=([(ix+k).clamp(0,w-1) for k in (-1,0,1,2)],
                       [(iy+k).clamp(0,h-1)-ia for k in (-1,0,1,2)])
        return self.d[j]

    def up(self,j):
        # Every upsample target is a reconstruction band, never a crop shape.
        if j not in self.u:
            a,b=self.s.reconstruction[j];_,w=self.s.shapes[j];ch,cw=self.s.shapes[j+1]
            ca,_=self.s.gaussian[j+1]
            qx=(torch.arange(w,dtype=torch.float32)-.5)/2
            qy=(torch.arange(a,b,dtype=torch.float32)-.5)/2
            ix=qx.floor().long();iy=qy.floor().long()
            self.u[j]=(ix.clamp(0,cw-1),(ix+1).clamp(0,cw-1),
                       iy.clamp(0,ch-1)-ca,(iy+1).clamp(0,ch-1)-ca,
                       (qx-ix.float())[None],(qy-iy.float())[:,None])
        return self.u[j]


def down(f,j,G,gate):
    gate.check();gate.take(D_calls=1);xs,ys=G.down(j)
    x=f.index_select(1,xs[0])
    for index,m in zip(xs[1:],(3,3,1)):
        part=f.index_select(1,index);part.mul_(m);x.add_(part);del part
    x.div_(8);y=x.index_select(0,ys[0])
    for index,m in zip(ys[1:],(3,3,1)):
        part=x.index_select(0,index);part.mul_(m);y.add_(part);del part
    y.div_(8);return y


def up(f,j,G,gate,*,reconstruction=False):
    gate.check();gate.take(U_calls=1)
    ix0,ix1,iy0,iy1,wx,wy=G.up(j)
    if reconstruction:
        # f owns only R[j+1]; translate from Gaussian origin to R origin.
        shift=G.s.gaussian[j+1][0]-G.s.reconstruction[j+1][0]
        iy0=iy0+shift;iy1=iy1+shift
    a=f.index_select(1,ix0);b=f.index_select(1,ix1);b.sub_(a).mul_(wx);a.add_(b);del b
    out=a.index_select(0,iy0);b=a.index_select(0,iy1);b.sub_(out).mul_(wy);out.add_(b)
    return out


def pyramid(f,G,gate):
    g=[f]
    for j in range(7):g.append(down(g[-1],j,G,gate))
    return g


def cut(f,j,s):
    a,b=s.reconstruction[j];origin=s.gaussian[j][0]
    return f[a-origin:b-origin]


def lap(g,j,cache,G,gate):
    if j not in cache:
        a=up(g[j+1],j,G,gate);a.neg_().add_(cut(g[j],j,G.s));cache[j]=a
    return cache[j]


def remap(f,tau):
    weight=f.clone().sub_(tau).div_(.1).square_().mul_(-.5).exp_()
    difference=f.clone().sub_(tau);weight.mul_(difference).mul_(-1.).add_(f)
    return weight


def scalar_footprint(array,r,s,gate,*,capture=False):
    """Input is exactly G0; output and optional bands own only R[j]."""
    if (array.dtype!=np.float32 or array.shape!=(s.base_rows,s.shapes[0][1]) or
            not np.isfinite(array).all()):raise ValueError('INPUT_FIELD')
    if (r['count']!=r['kmax']-r['kmin']+1 or not 2<=r['count']<=257 or
            r['delta']!=1/64 or r['sigma']!=.1 or r['J']!=8):raise ValueError('LLF_CONSTANTS')
    from ...light.resources import workspace_limit
    if workspace_bound(s)['total_bytes']>workspace_limit(*s.shapes[0]):raise MemoryError('CPU_WORKSPACE_CAP')
    gate.check(stage='scalar',core=s.core);gate.take(scalar_LLF_footprints=1)
    f=torch.from_numpy(np.ascontiguousarray(array));G=Geometry(s);g=pyramid(f,G,gate)
    selectors=[cut(z,j,s) for j,z in enumerate(g)]
    bands=[torch.zeros_like(z) for z in selectors[:-1]];active=[]
    for z in selectors:
        if not bool(torch.isfinite(z).all()):raise ValueError('SELECTOR_FINITE')
        lo=float(z.min());hi=float(z.max())
        if not r['kmin']/64<=lo<=hi<=r['kmax']/64:raise ValueError('SELECTOR_RANGE')
        active.append((math.floor(lo*64),math.floor(hi*64)))
    gate.take(remap_pyramids=1);previous=pyramid(remap(f,r['kmin']/64),G,gate);previous_lap={}
    for k in range(r['kmin']+1,r['kmax']+1):
        gate.check(stage='anchor',anchor=k,core=s.core)
        gate.take(remap_pyramids=1);current=pyramid(remap(f,k/64),G,gate);current_lap={};low=(k-1)/64;high=k/64
        for j in range(7):
            if not active[j][0]<=k-1<=active[j][1]:continue
            mask=(selectors[j]>=low)&(selectors[j]<high)
            a=lap(previous,j,previous_lap,G,gate);b=lap(current,j,current_lap,G,gate).clone()
            b.sub_(a);weights=selectors[j].clone().sub_(low).mul_(64)
            b.mul_(weights).add_(a);bands[j].copy_(torch.where(mask,b,bands[j]));del a,b,weights,mask
        previous=current;previous_lap=current_lap
    del previous,current,previous_lap,current_lap
    out=selectors[-1].clone()
    levels=[z.numpy().copy() for z in bands]+[selectors[-1].numpy().copy()] if capture else None
    for j in range(6,-1,-1):
        u=up(out,j,G,gate,reconstruction=True);u.add_(bands[j]);out=u
    result=out.numpy().copy()
    if not np.isfinite(result).all():raise ValueError('OUTPUT_FINITE')
    gate.check(stage='scalar_complete',core=s.core)
    return (result,levels) if capture else result


def scalar(array,r,core_rows,gate,*,capture=False):
    """Small-test helper; production emits stripes instead of allocating this."""
    h,w=array.shape;out=np.empty_like(array);levels=[]
    for s in plan(h,w,core_rows):
        a,b=s.gaussian[0];value=scalar_footprint(np.ascontiguousarray(array[a:b]),r,s,gate,capture=capture)
        start,end=s.core
        if capture:out[start:end]=value[0];levels.append((s,value[1]))
        else:out[start:end]=value
    return (out,levels) if capture else out


def p3_region(base,b,rwh,global_shape,start,end,channel,gate):
    h,w=global_shape;rw,rh=rwh;nx=math.ceil(rw/8)+1;ny=math.ceil(rh/8)+1
    if (b.dtype!=np.float64 or b.shape!=(nx*ny,3) or not np.isfinite(b).all() or
            base.dtype!=np.float32 or base.shape!=(end-start,w) or not 0<=start<end<=h or channel not in (0,1,2)):
        raise ValueError('P3_INPUT')
    gate.check(stage='p3',channel=channel,core=(start,end));gate.take(P3_scalar_evaluations=(end-start)*w)
    x=np.arange(w,dtype=np.float64);u=(x+.5)*rw/w/8;ix=np.floor(u).astype(np.int64);fx=u-ix
    y=np.arange(start,end,dtype=np.float64);v=(y+.5)*rh/h/8;iy=np.floor(v).astype(np.int64);fy=v-iy
    grid=torch.from_numpy(b.reshape(ny,nx,3)[...,channel])
    xx=torch.from_numpy(ix)[None,:];yy=torch.from_numpy(iy)[:,None]
    wx=torch.from_numpy(fx)[None,:];wx0=torch.from_numpy(1-fx)[None,:]
    wy=torch.from_numpy(fy)[:,None];wy0=torch.from_numpy(1-fy)[:,None]
    z=grid[yy,xx].mul_(wx0).mul_(wy0)
    t=grid[yy,xx+1].mul_(wx).mul_(wy0);z.add_(t);del t
    t=grid[yy+1,xx].mul_(wx0).mul_(wy);z.add_(t);del t
    t=grid[yy+1,xx+1].mul_(wx).mul_(wy);z.add_(t);del t
    a=torch.from_numpy(np.ascontiguousarray(base)).to(torch.float64);a.add_(z)
    return a.to(torch.float32).numpy().copy()


def compose(o,lp,lo):
    return (o.astype(np.float64)+(lp.astype(np.float64)-lo.astype(np.float64))).astype(np.float32)
