"""Differentiable float CIELAB(D65), with an explicit signed sRGB extension.

RGB/XYZ coefficients and D65 white follow the OpenCV conversion specification.
The CIE piecewise function uses exact delta=6/29. No image clipping or Q8 here.
Lab coordinates are scaled L/100, (a+128)/255, (b+128)/255.
"""
import numpy as np
import torch
from torch import nn

M=((.412453,.357580,.180423),(.212671,.715160,.072169),(.019334,.119193,.950227))
MI=tuple(tuple(float(x) for x in row) for row in np.linalg.inv(np.asarray(M,np.float64)))
WHITE=(.950456,1.,1.088754)
DELTA=6/29
def check(x):
 if x.ndim!=4 or x.shape[1]!=3 or not x.is_floating_point():raise ValueError('EXPECTED_FLOAT_NCHW_RGB_OR_LAB')
def mul(x,m):
 r,g,b=x.unbind(1)
 return torch.stack([a*r+c*g+d*b for a,c,d in m],1)
def linear(rgb):
 # Safe inactive branch; preserve signed extended values, including <0 and >1.
 a=rgb.abs();high=rgb.sign()*((a+.055)/1.055).pow(2.4)
 return torch.where(a>.04045,high,rgb/12.92)
def encoded(v):
 a=v.abs();high=v.sign()*(1.055*a.clamp_min(.0031308).pow(1/2.4)-.055)
 return torch.where(a>.0031308,high,v*12.92)
def rgb_to_lab(rgb):
 check(rgb);xyz=mul(linear(rgb),M);white=rgb.new_tensor(WHITE).view(1,3,1,1);t=xyz/white
 f=torch.where(t>DELTA**3,t.clamp_min(DELTA**3).pow(1/3),t/(3*DELTA**2)+4/29)
 x,y,z=f.unbind(1)
 return torch.stack(((116*y-16)/100,(500*(x-y)+128)/255,(200*(y-z)+128)/255),1)
def lab_to_rgb(lab):
 check(lab);l,a,b=lab.unbind(1);fy=(l*100+16)/116;fx=fy+(a*255-128)/500;fz=fy-(b*255-128)/200
 f=torch.stack((fx,fy,fz),1);t=torch.where(f>DELTA,f.pow(3),3*DELTA**2*(f-4/29))
 xyz=t*lab.new_tensor(WHITE).view(1,3,1,1)
 return encoded(mul(xyz,MI))
def array_to_lab(rgb):
 with torch.inference_mode():
  t=torch.from_numpy(rgb).permute(2,0,1)[None].cuda().contiguous(memory_format=torch.channels_last)
  return rgb_to_lab(t)[0].permute(1,2,0).cpu().numpy().copy()
class RGBFacade(nn.Module):
 def __init__(self,inner):super().__init__();self.inner=inner
 def forward(self,rgb,xy,valid=None,intensity=1.):
  return lab_to_rgb(self.inner(rgb_to_lab(rgb),xy,valid,intensity))
def display_model(field):return RGBFacade(field) if field.cfg.get('lab_mode')=='lab_domain' else field
def install_loss(ablation):
 original=ablation.loss_for
 def loss(pred,target,cfg,step):
  if cfg.get('lab_mode')=='lab_loss_only':
   return torch.nn.functional.l1_loss(rgb_to_lab(pred),rgb_to_lab(target))
  return original(pred,target,cfg,step)
 ablation.loss_for=loss
