"""New CPU recipe envelope; original bounded JSON/NPZ/parameter primitives."""
import io,json,os,platform
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from ..independent_r0 import adapter as a
from ..independent_r0.profile import PARAMETER_SHAPES
from . import contract as k

@dataclass
class ImageRecipe:
    metadata:dict
    parameters:dict

def semantic(m):return k.digest({name:value for name,value in m.items()if name not in ('recipe_sha256','archive_sha256')})

def validate(recipe):
    k.need(type(recipe)is ImageRecipe,'CPU_IMAGE_RECIPE_TYPE');m=recipe.metadata
    k.fields(m,('recipe_schema_version','backend_id','backend_sha256','qualification_manifest','scope','profile','execution_device','CPU_setup','optimizer_policy','runtime','settings','pair','normalization','parameter_sha256','recipe_sha256','flags'))
    k.need(m['recipe_schema_version']==k.RECIPE and m['backend_id']==k.BACKEND and m['backend_sha256']==k.implementation()['sha256'],'CPU_IMAGE_RECIPE_VERSION')
    manifest=k.manifest_check(m['qualification_manifest']);s=m['scope'];k.fields(s,('id','case','arm'))
    k.need(s['id']==k.SCOPE and s['case']in ('small','native')and s['arm']in (('new','cold')if s['case']=='small'else('new',)),'CPU_IMAGE_RECIPE_SCOPE')
    row=manifest['cases'][s['case']]
    k.need(k.canonical(m['pair'])==k.canonical(row)and k.canonical(m['settings'])==k.canonical(k.settings(s['case']))and k.canonical(m['normalization'])==k.canonical(k.normalization(row['source_identity'],row['reference_identity'])),'CPU_IMAGE_RECIPE_PAIR_SETTINGS')
    k.need(k.canonical(m['profile'])==k.canonical(dict(id=k.PROFILE_ID,contract_sha256=k.PROFILE_CONTRACT_SHA256,core_sha256=k.CORE_SHA256))and k.canonical(m['runtime'])==k.canonical(dict(k.RUNTIME,python=k.PYTHON))and m['execution_device']=='cpu'and k.canonical(m['flags'])==k.canonical(k.FLAGS),'CPU_IMAGE_RECIPE_CPU_PROFILE')
    k.need(m['CPU_setup']=='default CPU generator seed43;threads4/interop1;cv2threads4;FP32/channels_last'and m['optimizer_policy']=='original Adam.step; instance-only CPU noncapturable graph check','CPU_IMAGE_RECIPE_POLICY')
    k.need(type(recipe.parameters)is dict and set(recipe.parameters)==set(PARAMETER_SHAPES),'CPU_IMAGE_PARAMETER_KEYS')
    total=0
    for key,shape in PARAMETER_SHAPES.items():
        v=recipe.parameters[key];k.need(type(v)is np.ndarray and v.dtype==np.float32 and v.shape==shape and v.flags.c_contiguous and np.isfinite(v).all(),'CPU_IMAGE_PARAMETER_ARRAY');total+=v.nbytes
    k.need(total<=a.MAX_RECIPE_BYTES and a._parameter_hash(recipe.parameters)==m['parameter_sha256']and semantic(m)==m['recipe_sha256'],'CPU_IMAGE_RECIPE_DIGEST');return recipe

def save_recipe(recipe,directory):
    validate(recipe);directory=Path(directory)
    from grademirror.common import writable_path
    directory=writable_path(directory);s=recipe.metadata['scope'];k.need(directory.resolve()==(k.W/'recipes'/(s['case']+'-'+s['arm'])).resolve(),'CPU_IMAGE_SAVE_DIRECTORY')
    directory.mkdir(parents=True,exist_ok=False)
    weights=directory/'parameters.npz'
    with weights.open('xb')as f:np.savez(f,**recipe.parameters);f.flush();os.fsync(f.fileno())
    k.need(weights.stat().st_size<=a.MAX_RECIPE_BYTES,'CPU_IMAGE_RECIPE_QUOTA');data=weights.read_bytes();a._preflight_npz(data)
    meta=dict(recipe.metadata,archive_sha256=k.file_hash(weights));blob=json.dumps(meta,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    k.need(len(blob)<=a.MAX_RECIPE_BYTES,'CPU_IMAGE_RECIPE_QUOTA')
    with(directory/'recipe.json').open('xb')as f:f.write(blob);f.flush();os.fsync(f.fileno())
    receipt=dict(schema_version='cpu-r0-image-persistence-receipt-v1',status='COMPLETE',metadata=k.binding(directory/'recipe.json'),archive=k.binding(weights),recipe_sha256=recipe.metadata['recipe_sha256'],parameter_sha256=recipe.metadata['parameter_sha256'],flags=k.FLAGS)
    with(directory/'complete.json').open('xb')as f:f.write(k.canonical(receipt));f.flush();os.fsync(f.fileno())
    return receipt

def load_recipe(directory):
    directory=Path(directory)
    from grademirror.common import reject_reparse
    reject_reparse(directory)
    for name in ('recipe.json','parameters.npz','complete.json'):reject_reparse(directory/name)
    meta=a._strict_json(a._bounded_read(directory/'recipe.json'))
    k.need(meta.get('recipe_schema_version')==k.RECIPE,'CPU_IMAGE_OLD_RECIPE_REJECTED')
    complete=a._strict_json(a._bounded_read(directory/'complete.json'));k.fields(complete,('schema_version','status','metadata','archive','recipe_sha256','parameter_sha256','flags'))
    k.need(complete['schema_version']=='cpu-r0-image-persistence-receipt-v1'and complete['status']=='COMPLETE'and k.canonical(complete['flags'])==k.canonical(k.FLAGS),'CPU_IMAGE_COMPLETION_MARKER')
    for name in ('metadata','archive'):k.bound_file(complete[name]);k.need(Path(complete[name]['path']).parent.resolve()==directory.resolve(),'CPU_IMAGE_PERSISTENCE_PARENT')
    data=a._bounded_read(directory/'parameters.npz');k.need(k.file_hash(directory/'parameters.npz')==meta.pop('archive_sha256',None),'CPU_IMAGE_ARCHIVE_HASH');a._preflight_npz(data)
    with np.load(io.BytesIO(data),allow_pickle=False)as z:parameters={key:z[key].copy()for key in z.files}
    result=validate(ImageRecipe(meta,parameters));k.need(meta['recipe_sha256']==complete['recipe_sha256']and meta['parameter_sha256']==complete['parameter_sha256'],'CPU_IMAGE_PERSISTENCE_DIGEST');return result
