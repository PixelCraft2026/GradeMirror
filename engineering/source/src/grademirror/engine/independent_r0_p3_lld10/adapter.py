"""Fine archives embed the unchanged R0 recipe plus their own b64/ranges."""
from dataclasses import dataclass
from pathlib import Path
import hashlib,io,json
import numpy as np
from ..independent_r0 import adapter as r0
from . import profile
from ...fine_contract import validate_recipe,read_coefficients,digest
from ...common import reject_reparse,writable_path

@dataclass
class FineRecipe:
    metadata:dict
    r0_recipe:r0.FittedRecipe
    coefficients:np.ndarray

def check_runtime():
    actual=r0.check_runtime()
    for name,expected in profile.EXECUTION_SHA256.items():
        from ...light.portable import source_sha
        if source_sha(Path(__file__).parent/name)!=expected:raise ValueError('FINE_MATH_HASH_MISMATCH')
    return actual

def save_recipe(recipe,coefficients,post,directory,*,bound_profile=None):
    check_runtime();r0.validate_recipe(recipe);directory=writable_path(Path(directory));directory.mkdir(parents=True,exist_ok=False)
    parameters=directory/'parameters.npz';np.savez(parameters,**recipe.parameters)
    if parameters.stat().st_size>r0.MAX_RECIPE_BYTES:raise ValueError('R0_PARAMETER_QUOTA')
    p3=directory/'p3.npz';np.savez_compressed(p3,b=np.ascontiguousarray(coefficients,dtype='<f8'))
    from ..profiles import get_profile
    bound=get_profile(profile.PROFILE_ID,(bound_profile or {}).get('contract_sha256'))
    m={**recipe.metadata,'recipe_schema_version':profile.RECIPE_SCHEMA_VERSION,'profile_id':profile.PROFILE_ID,'profile_contract_sha256':bound['contract_sha256'],'core_sha256':bound['core_sha256'],'r0_recipe':recipe.metadata,'postprocess':post,'p3_archive_sha256':hashlib.sha256(p3.read_bytes()).hexdigest(),'archive_sha256':hashlib.sha256(parameters.read_bytes()).hexdigest()}
    m['recipe_sha256']=digest(m);validate_recipe(m,p3)
    data=json.dumps(m,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if len(data)>r0.MAX_RECIPE_BYTES:raise ValueError('FINE_METADATA_QUOTA')
    (directory/'recipe.json').write_bytes(data)
    return FineRecipe(m,recipe,np.ascontiguousarray(coefficients,dtype='<f8'))

def load_recipe(directory):
    check_runtime();directory=Path(directory);reject_reparse(directory)
    for name in ('parameters.npz','p3.npz','recipe.json'):reject_reparse(directory/name)
    m=r0._strict_json(r0._bounded_read(directory/'recipe.json'));validate_recipe(m,directory/'p3.npz')
    data=r0._bounded_read(directory/'parameters.npz')
    if hashlib.sha256(data).hexdigest()!=m['archive_sha256']:raise ValueError('R0_ARCHIVE_HASH_INVALID')
    r0._preflight_npz(data)
    with np.load(io.BytesIO(data),allow_pickle=False)as archive:parameters={key:archive[key].copy()for key in archive.files}
    recipe=r0.FittedRecipe(m['r0_recipe'],parameters);r0.validate_recipe(recipe)
    payload=read_coefficients(directory/'p3.npz',m['postprocess']['coefficient_shape'],m['p3_archive_sha256'])
    b=np.frombuffer(payload,dtype='<f8').reshape(m['postprocess']['coefficient_shape']).copy()
    return FineRecipe(m,recipe,b)
