"""Original CPU Field/render_tiles mathematics, caller-owned full result only."""
import hashlib,platform,sys,time
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import torch
from ..independent_r0 import core,adapter as a
from ..cpu_r0_fit.optimizer_policy import configure_cpu
from . import contract as k
from .recipe import load_recipe

class Cancelled(InterruptedError):
    def __init__(self,counts):self.counts=dict(counts);super().__init__('CPU_IMAGE_RENDER_CANCELLED')

@dataclass(frozen=True)
class Result:
    result:np.ndarray
    receipt:dict

def validate(source,directory,request,out):
    row=k.validate_request(request,'render');directory=Path(directory).resolve();q=request['recipe']
    k.need(directory==Path(q['metadata']['path']).parent.resolve()==Path(q['archive']['path']).parent.resolve(),'CPU_IMAGE_RECIPE_DIRECTORY')
    recipe=load_recipe(directory);m=recipe.metadata
    k.need(m['scope']['case']=='native'and m['scope']['arm']=='new'and m['recipe_sha256']==q['recipe_sha256']and m['parameter_sha256']==q['parameter_sha256']and k.canonical(m['pair'])==k.canonical(row),'CPU_IMAGE_NATIVE_RECIPE_REQUIRED')
    k.need(type(source)is np.ndarray and source.dtype==np.uint8 and list(source.shape)==row['source_identity']['shape']and source.flags.c_contiguous and not source.flags.writeable,'CPU_IMAGE_NATIVE_SOURCE')
    k.need(a.array_identity(source,file_sha256=row['source_file']['sha256'],file_size_bytes=row['source_file']['bytes'])==row['source_identity'],'CPU_IMAGE_NATIVE_PIXELS')
    k.need(out is not None,'CPU_IMAGE_CALLER_BUFFER_REQUIRED');a._validate_output(source,recipe,out)
    k.need(out.nbytes==request['output']['capacity_bytes'],'CPU_IMAGE_OUTPUT_CAPACITY')
    k.need(platform.python_version()==k.PYTHON and k.file_hash(sys.executable)==k.PYTHON_LAUNCHER_SHA256 and k.file_hash(sys._base_executable)==k.PYTHON_BASE_SHA256,'CPU_IMAGE_INTERPRETER');a.check_runtime();return recipe

def transfer(tiles,out,total,counts,started,cancelled,progress):
    last=-float('inf');iterator=iter(tiles);rows=[]
    def check():
        if cancelled():raise Cancelled(counts)
        k.need(time.monotonic()-started<900,'CPU_IMAGE_RENDER_TIMEOUT')
    try:
        while counts['completed_tiles']<total:
            check();(y0,y1,x0,x1),block=next(iterator);counts['forward_batches']+=1;check()
            k.need(np.isfinite(block).all(),'CPU_IMAGE_RENDER_NONFINITE');out[y0:y1,x0:x1]=block;counts['completed_tiles']+=1
            rows.append(dict(index=counts['completed_tiles'],region=[y0,y1,x0,x1],shape=list(block.shape),sha256=hashlib.sha256(block.tobytes(order='C')).hexdigest()))
            now=time.monotonic()
            if progress and (now-last>=1 or counts['completed_tiles']==total):progress(dict(stage='render',completed_tiles=counts['completed_tiles'],total_tiles=total,elapsed=now-started,ETA=None));last=now
            check()
    finally:
        if hasattr(iterator,'close'):iterator.close()
    return rows

def render(source,recipe_directory,request,*,out,cancelled=lambda:False,progress=None):
    k.validate_request(request,'render');counts=dict(render=0,model_construct=0,state_load=0,forward_batches=0,completed_tiles=0)
    if cancelled():raise Cancelled(counts)
    recipe=validate(source,recipe_directory,request,out)
    if cancelled():raise Cancelled(counts)
    started=time.monotonic();configure_cpu();counts['render']=1
    cfg={name:value for name,value in recipe.metadata['settings'].items()if name not in ('seed','long_side','tile_side')}
    model=core.Field(cfg,43).to(device='cpu',dtype=torch.float32,memory_format=torch.channels_last);counts['model_construct']=1
    try:
        model.load_state_dict({key:torch.from_numpy(value.copy())for key,value in recipe.parameters.items()},strict=True);counts['state_load']=1
        loaded=a._parameter_hash({key:value.detach().numpy()for key,value in model.state_dict().items()});k.need(loaded==recipe.metadata['parameter_sha256']and all(p.device.type=='cpu'for p in model.parameters()),'CPU_IMAGE_LOADED_STATE')
        tiles=transfer(core.render_tiles(model,source,512),out,140,counts,started,cancelled,progress)
        if cancelled():raise Cancelled(counts)
        receipt=dict(schema_version='cpu-r0-image-render-receipt-v1',status='COMPLETE',backend_id=k.BACKEND,backend_sha256=request['backend_sha256'],request_sha256=k.digest(request),qualification_manifest=request['qualification_manifest'],scope=request['scope'],parent_recipe=request['recipe'],execution_device='cpu',loaded_parameter_sha256=loaded,source_identity=request['pair']['source_identity'],counts=counts,tiles=tiles,output_sha256=hashlib.sha256(memoryview(out).cast('B')).hexdigest(),output_shape=list(out.shape),output_dtype='float32',borrowed_output=True,output_pointer=int(out.ctypes.data),private_full_result_allocations=0,output_owner='caller',seconds=time.monotonic()-started,flags=k.FLAGS)
        if progress:progress(dict(stage='complete',completed_tiles=140,total_tiles=140,elapsed=time.monotonic()-started))
        return Result(out,receipt)
    finally:del model
