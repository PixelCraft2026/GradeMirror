"""Original CPU setup/transfer and Field/render_tiles, with v2 identity validation."""
import hashlib,platform,sys,time
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import torch
from ..independent_r0 import adapter,core
from ..profiles import normalization,settings_for
from ..cpu_r0_render.executor import configure_cpu,_transfer,Cancelled
from .contract import (BACKEND_ID,RECEIPT,SETTINGS,PYTHON,PYTHON_LAUNCHER_SHA256,PYTHON_BASE_SHA256,
    MAX_SOURCE,need,validate_request,bound_file,canonical,digest,file_hash)

@dataclass(frozen=True)
class Result:
    result:np.ndarray
    receipt:dict

def inspect_metadata(metadata):
    """Metadata/provenance only; never a lease or a development-image execution grant."""
    m=metadata
    need(type(m)is dict and m.get('recipe_schema_version')==adapter.RECIPE_SCHEMA_VERSION,'CPU_V2_RECIPE_VERSION')
    required={'recipe_schema_version','recipe_kind','profile_id','profile_contract_sha256','production_qualified','core_sha256','runtime','python_version','device','settings','source_sha256','reference_sha256','color_declaration','resize_geometry','parameter_sha256','recipe_sha256','source_identity','reference_identity','color_policy','io_runtime','training_shape','training_source_pixel_sha256','training_reference_pixel_sha256','input_normalization'}
    need(set(m)in (required,required|{'archive_sha256'}),'CPU_V2_RECIPE_FIELDS')
    need(m.get('recipe_kind')in ('synthetic_test','development_image'),'CPU_V2_RECIPE_KIND')
    need(m['profile_id']==adapter.PROFILE_ID and m['profile_contract_sha256']==adapter.PROFILE_CONTRACT_SHA256 and m['core_sha256']==adapter.CORE_SHA256 and canonical(m['runtime'])==canonical(adapter.RUNTIME)and m['python_version']==PYTHON and m['production_qualified']is False,'CPU_V2_RECIPE_PROFILE')
    adapter._validate_bindings(m,m['recipe_kind'])
    expected=settings_for(adapter.PROFILE_ID,m['training_shape'][:2])
    if m['recipe_kind']=='synthetic_test':expected={**expected,'updates':3}
    need(canonical(m['settings'])==canonical(expected),'CPU_V2_RECIPE_SETTINGS')
    need(m['source_sha256']==m['source_identity']['file_sha256']and m['reference_sha256']==m['reference_identity']['file_sha256'],'CPU_V2_RECIPE_SOURCE')
    need(canonical(m['input_normalization'])==canonical(normalization(m['source_identity'],m['reference_identity'])),'CPU_V2_NORMALIZATION')
    need(adapter.recipe_digest(m)==m['recipe_sha256'],'CPU_V2_RECIPE_DIGEST')
    return dict(recipe_kind=m['recipe_kind'],recipe_sha256=m['recipe_sha256'],source=m['source_identity'],reference=m['reference_identity'],metadata_only=True,execution_granted=False)

def validate(source,recipe_directory,request,out):
    validate_request(request)
    directory=Path(recipe_directory).resolve();r=request['recipe']
    need(directory==(Path(r['metadata']['path']).parent.resolve())and directory==Path(r['archive']['path']).parent.resolve(),'CPU_V2_RECIPE_DIRECTORY')
    metadata=adapter._strict_json(adapter._bounded_read(directory/'recipe.json'))
    inspect_metadata(metadata)
    need(metadata['recipe_kind']=='synthetic_test','CPU_V2_DEVELOPMENT_EXECUTION_FORBIDDEN')
    recipe=adapter.load_recipe(directory,allow_synthetic_test=True);m=recipe.metadata
    need(m['recipe_sha256']==r['recipe_sha256']and m['parameter_sha256']==r['parameter_sha256'],'CPU_V2_RECIPE_BINDING')
    for key,actual in (('origin_device',m['device']),('normalization',m['input_normalization']),('color_policy',m['color_policy']),('color_declaration',m['color_declaration'])):
        need(canonical(request[key])==canonical(actual),'CPU_V2_RECIPE_'+key.upper())
    need(platform.python_version()==PYTHON and file_hash(sys.executable)==PYTHON_LAUNCHER_SHA256 and file_hash(sys._base_executable)==PYTHON_BASE_SHA256,'CPU_V2_INTERPRETER_IDENTITY')
    need(type(source)is np.ndarray and source.shape==tuple(request['source_identity']['shape'])and str(source.dtype)==request['source_identity']['dtype'],'CPU_V2_SOURCE_ARRAY')
    need(source.flags.c_contiguous and not source.flags.writeable and source.nbytes<=MAX_SOURCE,'CPU_V2_READONLY_SOURCE')
    if source.dtype==np.float32:
        for row in range(0,len(source),64):need(np.isfinite(source[row:row+64]).all()and (source[row:row+64]>=0).all()and (source[row:row+64]<=1).all(),'CPU_V2_FLOAT_DOMAIN')
    sid=adapter.array_identity(source,file_sha256=request['source_file']['sha256'],file_size_bytes=request['source_file']['bytes'])
    need(sid==request['source_identity']==m['source_identity'],'CPU_V2_SOURCE_IDENTITY')
    need(out is not None,'CPU_V2_BORROWED_OUTPUT_REQUIRED');adapter._validate_output(source,recipe,out)
    need(out.nbytes==request['output']['capacity_bytes']and out.nbytes<=MAX_SOURCE,'CPU_V2_OUTPUT_CAPACITY')
    return recipe

def _compute(source,recipe,request,out,cancelled,progress):
    started=time.monotonic();counts=dict(render=1,model_construct=0,state_load=0,forward_batches=0,completed_tiles=0)
    configure_cpu()
    if cancelled():raise Cancelled(counts)
    cfg={k:v for k,v in recipe.metadata['settings'].items()if k not in ('seed','long_side','tile_side')}
    counts['model_construct']+=1
    model=core.Field(cfg,43).to(device='cpu',dtype=torch.float32,memory_format=torch.channels_last)
    counts['state_load']+=1
    model.load_state_dict({key:torch.from_numpy(value.copy())for key,value in recipe.parameters.items()},strict=True)
    need(all(p.device.type=='cpu'for p in model.parameters()),'CPU_R0_MODEL_DEVICE')
    loaded_parameter_sha256=adapter._parameter_hash({key:value.detach().numpy()for key,value in model.state_dict().items()})
    need(loaded_parameter_sha256==request['recipe']['parameter_sha256'],'CPU_V2_ACTUAL_LOADED_STATE')
    total=((source.shape[0]+511)//512)*((source.shape[1]+511)//512)
    _transfer(core.render_tiles(model,source,512),out,total,counts,started,cancelled,progress)
    for row in range(0,len(out),64):need(np.isfinite(out[row:row+64]).all(),'CPU_R0_NONFINITE')
    if cancelled():raise Cancelled(counts)
    need(counts['forward_batches']==total and time.monotonic()-started<180,'CPU_R0_COMPLETION')
    receipt=dict(schema_version=RECEIPT,status='COMPLETE',backend_id=BACKEND_ID,backend_sha256=request['backend_sha256'],
        request_sha256=digest(request),scope=request['scope'],qualification_manifest=request['qualification_manifest'],numerical_contract=request['numerical_contract'],parent_recipe=request['recipe'],
        original_fit_device=recipe.metadata['device'],execution_device='cpu',settings=SETTINGS,counts=counts,
        loaded_parameter_sha256=loaded_parameter_sha256,
        source_identity=request['source_identity'],output_sha256=hashlib.sha256(memoryview(out).cast('B')).hexdigest(),
        output_shape=list(out.shape),output_dtype='float32',borrowed_output=True,output_pointer=int(out.ctypes.data),
        private_full_result_allocations=0,output_owner='caller',seconds=time.monotonic()-started,
        production_qualified=False,execution_authorized=False)
    return Result(out,receipt)

def render(source,recipe_directory,request,out,*,cancelled=lambda:False,progress=None):
    validate_request(request)
    if cancelled():raise Cancelled(dict(render=0,model_construct=0,state_load=0,forward_batches=0,completed_tiles=0))
    recipe=validate(source,recipe_directory,request,out)
    if cancelled():raise Cancelled(dict(render=0,model_construct=0,state_load=0,forward_batches=0,completed_tiles=0))
    return _compute(source,recipe,request,out,cancelled,progress)
