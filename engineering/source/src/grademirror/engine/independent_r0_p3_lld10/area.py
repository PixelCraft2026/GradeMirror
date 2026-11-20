import math

import numpy as np

def overlaps(n,r):
 starts=np.arange(r,dtype=np.float64)*n/r;ends=np.arange(1,r+1,dtype=np.float64)*n/r;first=np.floor(starts).astype(np.int64);count=int(math.ceil(n/r))+1
 ix=first[:,None]+np.arange(count);weights=np.maximum(0,np.minimum(ends[:,None],ix+1)-np.maximum(starts[:,None],ix))/(n/r)
 return np.clip(ix,0,n-1),weights

def area_prefix(image,reference_wh,cancel=lambda:None):
 h,w=image.shape[:2];rw,rh=reference_wh;iy,wy=overlaps(h,rh);edges=np.arange(rw+1,dtype=np.float64)*w/rw;idx=np.minimum(np.floor(edges).astype(np.int64),w);frac=edges-idx
 out=np.empty((rh,rw,image.shape[2]),np.float64)
 for y in range(rh):
  cancel()
  row=np.einsum('i,iwc->wc',wy[y],image[iy[y]].astype(np.float64));s=np.empty((w+1,row.shape[1]),np.float64);s[0]=0;np.cumsum(row,axis=0,out=s[1:]);integral=s[idx]+frac[:,None]*row[np.minimum(idx,w-1)];out[y]=np.diff(integral,axis=0)/(w/rw)
 return out

def offsets_at(b,wh,rwh,xy):
 w,h=wh;rw,rh=rwh;nx=math.ceil(rw/8)+1;ny=math.ceil(rh/8)+1;grid=b.reshape(ny,nx,3);xy=np.asarray(xy,np.float64);u=(xy[:,0]+.5)*rw/w/8;v=(xy[:,1]+.5)*rh/h/8;ix=np.floor(u).astype(np.int64);iy=np.floor(v).astype(np.int64);fx=(u-ix)[:,None];fy=(v-iy)[:,None]
 z=grid[iy,ix]*(1-fx)*(1-fy);z+=grid[iy,ix+1]*fx*(1-fy);z+=grid[iy+1,ix]*(1-fx)*fy;z+=grid[iy+1,ix+1]*fx*fy;return z

def witness_xy(wh):
 w,h=wh;return np.array([(int((i+.5)*w/8),int((j+.5)*h/8)) for j in range(8) for i in range(8)],np.int64)

