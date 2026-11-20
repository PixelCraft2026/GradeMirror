"""Frozen Field/render_tiles math, CPU initialization, borrowed result only."""
import hashlib,platform,sys,time
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import torch
from ..independent_r0 import adapter,core
from .contract import (BACKEND_ID,RECEIPT,SETTINGS,PYTHON,RenderError,need,validate_request,
    file_hash,digest,PYTHON_LAUNCHER_SHA256,PYTHON_BASE_SHA256)

@dataclass(frozen=True)
class Result:
    result:np.ndarray
    receipt:dict

class Cancelled(InterruptedError):
    def __init__(self,counts):self.counts=dict(counts);super().__init__('CPU_R0_CANCELLED')

def configure_cpu():
    torch.set_num_threads(4)
    if torch.get_num_interop_threads()!=1:torch.set_num_interop_threads(1)
    # torch.manual_seed also schedules CUDA seeding: use only the CPU generator.
    torch.default_generator.manual_seed(43)
    torch.backends.cudnn.benchmark=False;torch.backends.cudnn.deterministic=True
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    torch.use_deterministic_algorithms(False)

def validate_arrays(source,recipe,out,request):
    shape,dtype=request['source_identity']['shape'],request['source_identity']['dtype']
    need(type(source)is np.ndarray and source.shape==tuple(shape)and str(source.dtype)==dtype,'CPU_R0_SOURCE_ARRAY')
    need(source.flags.c_contiguous and not source.flags.writeable,'CPU_R0_READONLY_SOURCE_REQUIRED')
    need(source.nbytes<=4*1024**2,'CPU_R0_SOURCE_QUOTA')
    sid=adapter.array_identity(source,file_sha256=request['source_identity']['file_sha256'],file_size_bytes=request['source_identity']['file_size_bytes'])
    need(sid==request['source_identity']==recipe.metadata['source_identity'],'CPU_R0_SOURCE_IDENTITY')
    # Fixed small scope cannot be opened to photographs by relabelling metadata.
    width=shape[1]*3
    for row in range(0,shape[0],64):
        last=min(shape[0],row+64);expected=np.arange(row*width,last*width,dtype=np.int64)%251
        if dtype=='uint16':expected=expected*257
        elif dtype=='float32':expected=expected/250
        need(np.array_equal(source[row:last],expected.astype(dtype).reshape(last-row,shape[1],3)),'CPU_R0_SYNTHETIC_PIXELS_REQUIRED')
    need(out is not None,'CPU_R0_BORROWED_OUTPUT_REQUIRED')
    adapter._validate_output(source,recipe,out)
    for key,value in recipe.parameters.items():
        expected=(((np.arange(value.size,dtype=np.int64)%17)-8)/4096).astype(np.float32).reshape(value.shape)
        if key=='edit_head.weight':expected*=16
        elif key=='edit_head.bias':expected*=32
        need(np.array_equal(value,expected),'CPU_R0_SYNTHETIC_PARAMETERS_REQUIRED')

def _transfer(tiles,out,total,counts,started,cancelled,progress):
    last=-float('inf');iterator=iter(tiles)
    def check():
        if cancelled():raise Cancelled(counts)
        need(time.monotonic()-started<180,'CPU_R0_RENDER_TIMEOUT')
    try:
        while counts['completed_tiles']<total:
            check() # Before next(): cancellation cannot execute another forward.
            (y0,y1,x0,x1),block=next(iterator);counts['forward_batches']+=1;check()
            out[y0:y1,x0:x1]=block;counts['completed_tiles']+=1
            now=time.monotonic()
            if progress and (now-last>=1 or counts['completed_tiles']==total):
                progress(dict(stage='render',completed_tiles=counts['completed_tiles'],total_tiles=total,elapsed=now-started,ETA=None));last=now
            check()
        # Only the known fixed grid is consumed; no additional forward is probed.
    finally:
        if hasattr(iterator,'close'):iterator.close()

def _execute(source,recipe,request,out,cancelled,progress):
    started=time.monotonic();counts=dict(render=1,model_construct=0,state_load=0,forward_batches=0,completed_tiles=0)
    configure_cpu()
    cfg={k:v for k,v in recipe.metadata['settings'].items()if k not in ('seed','long_side','tile_side')}
    counts['model_construct']+=1
    model=core.Field(cfg,43).to(device='cpu',dtype=torch.float32,memory_format=torch.channels_last)
    counts['state_load']+=1
    model.load_state_dict({key:torch.from_numpy(value.copy())for key,value in recipe.parameters.items()},strict=True)
    need(all(p.device.type=='cpu'for p in model.parameters()),'CPU_R0_MODEL_DEVICE')
    total=((source.shape[0]+511)//512)*((source.shape[1]+511)//512)
    _transfer(core.render_tiles(model,source,512),out,total,counts,started,cancelled,progress)
    for row in range(0,len(out),64):need(np.isfinite(out[row:row+64]).all(),'CPU_R0_NONFINITE')
    if cancelled():raise Cancelled(counts)
    need(counts['forward_batches']==total and time.monotonic()-started<180,'CPU_R0_COMPLETION')
    receipt=dict(schema_version=RECEIPT,status='COMPLETE',backend_id=BACKEND_ID,backend_sha256=request['backend_sha256'],
        request_sha256=digest(request),scope=request['scope'],original_recipe=request['recipe'],
        original_fit_device=recipe.metadata['device'],execution_device='cpu',settings=SETTINGS,counts=counts,
        source_identity=request['source_identity'],output_sha256=hashlib.sha256(memoryview(out).cast('B')).hexdigest(),
        output_shape=list(out.shape),output_dtype='float32',borrowed_output=True,
        private_full_result_allocations=0,output_owner='caller',seconds=time.monotonic()-started,
        production_qualified=False,execution_authorized=False)
    return Result(out,receipt)

def render(source,recipe_directory,request,out,*,cancelled=lambda:False,progress=None):
    validate_request(request)
    if cancelled():raise Cancelled(dict(render=0,model_construct=0,state_load=0,forward_batches=0,completed_tiles=0))
    directory=Path(recipe_directory)
    need(file_hash(directory/'recipe.json')==request['recipe']['metadata_sha256']and file_hash(directory/'parameters.npz')==request['recipe']['archive_sha256'],'CPU_R0_RECIPE_FILE_IDENTITY')
    recipe=adapter.load_recipe(directory,allow_synthetic_test=True)
    m=recipe.metadata
    need(m['recipe_kind']=='synthetic_test','CPU_R0_SYNTHETIC_RECIPE_REQUIRED')
    need(m['recipe_sha256']==request['recipe']['recipe_sha256']and m['parameter_sha256']==request['recipe']['parameter_sha256'],'CPU_R0_RECIPE_BINDING')
    need(digest(m['device'])==digest(request['origin_device'])and m['device']['id'].startswith('cuda:'),'CPU_R0_ORIGIN_DEVICE')
    need(digest(request['normalization'])==digest(m['input_normalization'])and digest(request['color_policy'])==digest(m['color_policy'])and request['color_declaration']==m['color_declaration'],'CPU_R0_COLOR_BINDING')
    need(platform.python_version()==PYTHON,'CPU_R0_PYTHON')
    need(file_hash(sys.executable)==PYTHON_LAUNCHER_SHA256 and file_hash(sys._base_executable)==PYTHON_BASE_SHA256,'CPU_R0_INTERPRETER_IDENTITY')
    validate_arrays(source,recipe,out,request)
    if cancelled():raise Cancelled(dict(render=0,model_construct=0,state_load=0,forward_batches=0,completed_tiles=0))
    return _execute(source,recipe,request,out,cancelled,progress)
