"""P3 exact four-term FP64 evaluation; precomputed 1D CPU coordinates."""
import math
import numpy as np
import torch
def render(base,b,rwh,L):
 assert base.dtype==np.float32 and b.dtype==np.float64 and np.isfinite(b).all()
 L.take(P3_native_fields=1);h,w=base.shape[:2];rw,rh=rwh;nx=math.ceil(rw/8)+1;ny=math.ceil(rh/8)+1
 # Match the original NumPy FP64 coordinate arithmetic, including every division.
 x=np.arange(w,dtype=np.float64);u=(x+.5)*rw/w/8;ix=np.floor(u).astype(np.int64);fx=u-ix
 y=np.arange(h,dtype=np.float64);v=(y+.5)*rh/h/8;iy=np.floor(v).astype(np.int64);fy=v-iy
 grid=torch.from_numpy(np.ascontiguousarray(b.reshape(ny,nx,3))).cuda()
 xx=torch.from_numpy(ix).cuda()[None,:];wx=torch.from_numpy(fx).cuda()[None,:,None];wx0=torch.from_numpy(1-fx).cuda()[None,:,None]
 out=np.empty_like(base)
 for start in range(0,h,128):
  L.take(P3_128row_strips=1);end=min(h,start+128);yy=torch.from_numpy(iy[start:end]).cuda()[:,None];wy=torch.from_numpy(fy[start:end]).cuda()[:,None,None];wy0=torch.from_numpy(1-fy[start:end]).cuda()[:,None,None]
  z=grid[yy,xx].mul_(wx0).mul_(wy0)
  t=grid[yy,xx+1].mul_(wx).mul_(wy0);z.add_(t);del t
  t=grid[yy+1,xx].mul_(wx0).mul_(wy);z.add_(t);del t
  t=grid[yy+1,xx+1].mul_(wx).mul_(wy);z.add_(t);del t
  a=torch.from_numpy(np.ascontiguousarray(base[start:end])).cuda().to(torch.float64);a.add_(z);out[start:end]=a.to(torch.float32).cpu().numpy();del a,z,yy,wy,wy0
 return out
