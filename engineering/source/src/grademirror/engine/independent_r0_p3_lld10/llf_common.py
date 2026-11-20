import math,time

import numpy as np

import torch
DELTA=1/64

def recipe(arrays):
 out=[]
 for c in range(3):
  low=min(float(np.min(a[...,c])) for a in arrays);high=max(float(np.max(a[...,c])) for a in arrays)
  assert math.isfinite(low) and math.isfinite(high)
  a=math.floor((low-2/255)*64)-1;b=math.ceil((high+2/255)*64)+1
  assert 2<=b-a+1<=257,('ANCHOR_CAP',c,a,b)
  out.append(dict(kmin=a,kmax=b,count=b-a+1,min=low,max=high,delta=DELTA,sigma=.1,J=8))
 return out

def scalar_recipe(a,b):return recipe([np.repeat(a[...,None],3,axis=2),np.repeat(b[...,None],3,axis=2)])[0]

def remap(f,tau,amount=-1.):
 weight=f.clone().sub_(tau).div_(.1).square_().mul_(-.5).exp_();difference=f.clone().sub_(tau);weight.mul_(difference).mul_(amount).add_(f);return weight

