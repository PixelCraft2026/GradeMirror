"""Strict new package type. Readers do not import numeric runtimes or old fine recipes."""
import hashlib,math
from pathlib import Path
from ..common import strict_load,sha_file,reject_reparse
from ..hybrid_replay import BACKEND_ID,CONTRACT_VERSION
from ..hybrid_replay.contract import require,digest,hash_value,RUNTIME,cpu_bindings,qualification_scope
from ..light.math_bridge import compatible_hybrid_execution, compatible_portable_d1
from ..engine.independent_r0 import profile as r0
from ..engine.independent_r0_p3_lld10 import profile as fine
from ..fine_contract import coefficient_shape,validate_limits,read_coefficients
from . import FIT_CONTRACT,RECIPE_SCHEMA,MANIFEST_SCHEMA,ASSETS

def exact(value,keys,code):
    require(isinstance(value,dict)and set(value)==set(keys),'HYBRID_FIT_'+code)

def identity(value):
    exact(value,('file_sha256','file_size_bytes','pixel_sha256','shape','dtype','bit_depth'),'INPUT_FIELDS')
    require(hash_value(value['file_sha256'])and hash_value(value['pixel_sha256'])and
        type(value['file_size_bytes'])is int and value['file_size_bytes']>0,'HYBRID_FIT_INPUT_HASH')
    shape=value['shape'];require(isinstance(shape,list)and len(shape)==3 and shape[2]==3 and
        all(type(n)is int and n>0 for n in shape),'HYBRID_FIT_INPUT_SHAPE')
    require(value['dtype']in ('uint8','uint16')and type(value['bit_depth'])is int and
        value['bit_depth']=={'uint8':8,'uint16':16}[value['dtype']],'HYBRID_FIT_INPUT_BITS')

def asset(root,name):
    require(name in ASSETS,'HYBRID_FIT_ASSET_PATH');root=Path(root).resolve();path=root/name
    reject_reparse(root);reject_reparse(path)
    require(path.resolve().is_relative_to(root)and path.is_file()and path.stat().st_size<=4*1024**2,'HYBRID_FIT_ASSET_MISSING_OR_QUOTA')
    return path

def assets(root):
    return {n:dict(path=n,bytes=asset(root,n).stat().st_size,sha256=sha_file(asset(root,n)))for n in ASSETS}

def ranges(value):
    require(isinstance(value,list)and len(value)==3,'HYBRID_FIT_RANGES')
    for v in value:
        exact(v,('kmin','kmax','count','min','max','delta','sigma','J'),'RANGE_FIELDS')
        require(all(type(v[k])is int for k in ('kmin','kmax','count','J'))and
            all(type(v[k])in (float,int)and math.isfinite(v[k])for k in ('min','max','delta','sigma')),'HYBRID_FIT_RANGE_NUMBERS')
        require(v['min']<=v['max']and v['kmin']==math.floor((v['min']-2/255)*64)-1 and
            v['kmax']==math.ceil((v['max']+2/255)*64)+1 and
            2<=v['count']==v['kmax']-v['kmin']+1<=257 and v['delta']==1/64 and v['sigma']==.1 and v['J']==8,'HYBRID_FIT_RANGE_CAP')

def read_recipe(directory):
    directory=Path(directory).resolve();a=assets(directory);m=strict_load(asset(directory,'recipe.json'))
    exact(m,('schema_version','fit_contract','profile_id','profile_contract_sha256','core_sha256','recipe_sha256',
        'r0_recipe_sha256','r0_parameter_sha256','source_identity','reference_identity','device','runtime','R0_runtime',
        'color_policy','postprocess','production_qualified'),'RECIPE_FIELDS')
    require(m['schema_version']==RECIPE_SCHEMA and m['fit_contract']==FIT_CONTRACT and m['production_qualified']is False,'HYBRID_FIT_RECIPE_TYPE')
    require(m['recipe_sha256']==digest({k:v for k,v in m.items()if k!='recipe_sha256'}),'HYBRID_FIT_RECIPE_DIGEST')
    from ..engine.profiles import get_profile
    p=get_profile(m['profile_id'],m['profile_contract_sha256'])
    require(p['id']==fine.PROFILE_ID and m['core_sha256']==p['core_sha256']and m['runtime']==RUNTIME,'HYBRID_FIT_PROFILE_RUNTIME')
    for key in ('source_identity','reference_identity'):identity(m[key])
    s=m['source_identity']['shape'];ref=m['reference_identity']['shape']
    validate_limits(dict(width=s[1],height=s[0]),dict(width=ref[1],height=ref[0]))
    base=strict_load(asset(directory,'r0/recipe.json'))
    exact(base,('recipe_schema_version','recipe_kind','profile_id','profile_contract_sha256','production_qualified','core_sha256',
        'runtime','python_version','device','settings','source_sha256','reference_sha256','color_declaration','resize_geometry',
        'parameter_sha256','recipe_sha256','source_identity','reference_identity','color_policy','io_runtime','training_shape',
        'training_source_pixel_sha256','training_reference_pixel_sha256','input_normalization','archive_sha256'),'R0_FIELDS')
    from ..engine.profiles import normalization,settings_for
    training=base['training_shape']
    require(isinstance(training,list)and len(training)==3 and training[-1]==3 and min(training[:2])>=13 and max(training[:2])==512 and
        base['settings']==settings_for(r0.PROFILE_ID,training[:2])and base['input_normalization']==normalization(m['source_identity'],m['reference_identity']),
        'HYBRID_FIT_R0_SETTINGS')
    for label in ('source','reference'):
        h,w,_=m[label+'_identity']['shape'];expected=[max(1,round(h*512/max(w,h))),max(1,round(w*512/max(w,h))),3]
        require(expected==training and base[label+'_sha256']==m[label+'_identity']['file_sha256']and
            hash_value(base['training_'+label+'_pixel_sha256']),'HYBRID_FIT_R0_GRID')
    require(base['color_declaration']=='gamma-encoded-sRGB-float32'and base['python_version']=='3.12.14','HYBRID_FIT_R0_COLOR_RUNTIME')
    device=base['device']
    exact(device,('id','name','uuid','driver_version','cuda_version','memory_format','dtype','cudnn_benchmark','cudnn_deterministic',
        'matmul_tf32','cudnn_tf32','deterministic_algorithms'),'DEVICE_FIELDS')
    require(device['id']=='cuda:0'and device['cuda_version']=='12.4'and device['memory_format']=='channels_last'and device['dtype']=='float32'and
        device['cudnn_benchmark']is False and device['cudnn_deterministic']is True and device['matmul_tf32']is False and
        device['cudnn_tf32']is False and type(device['deterministic_algorithms'])is bool and
        all(isinstance(device[k],str)and device[k]for k in ('uuid','name','driver_version')),'HYBRID_FIT_DEVICE')
    require(base.get('recipe_schema_version')=='0.4.0'and base.get('profile_id')==r0.PROFILE_ID and
        base.get('profile_contract_sha256')==r0.PROFILE_CONTRACT_SHA256 and base.get('core_sha256')==r0.CORE_SHA256 and
        base.get('recipe_kind')=='development_image'and base.get('production_qualified')is False and
        base.get('recipe_sha256')==digest({k:v for k,v in base.items()if k not in ('recipe_sha256','archive_sha256')})==m['r0_recipe_sha256'],
        'HYBRID_FIT_R0_CONTRACT')
    require(all(m[k]==base[k]for k in ('source_identity','reference_identity','device','color_policy'))and
        m['R0_runtime']==base['runtime']==r0.RUNTIME and m['r0_parameter_sha256']==base['parameter_sha256'],'HYBRID_FIT_R0_BINDING')
    require(a['r0/parameters.npz']['sha256']==base['archive_sha256'],'HYBRID_FIT_R0_ARCHIVE')
    from ..fit_publication import validate_archive
    validate_archive(asset(directory,'r0/parameters.npz'),base['parameter_sha256'],shapes=r0.PARAMETER_SHAPES)
    post=m['postprocess']
    exact(post,('P3','LLF','solver_runtime','coefficient_shape','coefficient_sha256','p3_archive_sha256','ranges',
        'reference_normalization','solver','H64_sha256','T64_sha256','O_sha256'),'POST_FIELDS')
    require(post['P3']==fine.P3 and post['LLF']==fine.LLF and post['solver_runtime']==fine.RUNTIME['solver']and
        post['coefficient_shape']==coefficient_shape(ref),'HYBRID_FIT_POST_CONTRACT')
    require(post['reference_normalization']==dict(dtype='float64',denominator=2**m['reference_identity']['bit_depth']-1,
        grid=ref,intermediate_float32=False),'HYBRID_FIT_REFERENCE_PRECISION')
    ranges(post['ranges'])
    require(all(hash_value(post[k])for k in ('coefficient_sha256','p3_archive_sha256','H64_sha256','T64_sha256','O_sha256')),'HYBRID_FIT_POST_HASH')
    payload=read_coefficients(asset(directory,'p3.npz'),post['coefficient_shape'],post['p3_archive_sha256'])
    require(hashlib.sha256(payload).hexdigest()==post['coefficient_sha256'],'HYBRID_FIT_B_VALUES')
    solver=post['solver'];require(solver.get('status')=='PASS'and solver.get('runtime')==fine.RUNTIME['solver']and
        solver.get('shape')==post['coefficient_shape']and solver.get('coefficient_sha256')==post['coefficient_sha256']and
        solver.get('counts',{}).get('scalar_CG')==3 and solver.get('identity')==[]and
        solver.get('H64_sha256')==post['H64_sha256']and solver.get('T64_sha256')==post['T64_sha256'], 'HYBRID_FIT_SOLVER_BINDING')
    require(isinstance(solver.get('main'),list)and len(solver['main'])==3,'HYBRID_FIT_SOLVER_CHANNELS')
    for row in solver['main']:
        require(type(row.get('info'))is int and row['info']==0 and type(row.get('iterations'))is int and 0<=row['iterations']<=2000 and
            all(type(row.get(k))in (int,float)and math.isfinite(row[k])for k in ('residual_norm','residual_gate','rhs_norm','J','J0','seconds'))and
            0<=row['seconds']<=300 and 0<=row['residual_norm']<=row['residual_gate']==max(1e-8*row['rhs_norm'],1e-12)and
            row['J']<=row['J0']+1e-10+1e-8*abs(row['J0']),'HYBRID_FIT_SOLVER_NUMERICAL_GATE')
    return m,a

def read_manifest(path):
    from .tiers import rows
    path=Path(path).resolve();reject_reparse(path);m=strict_load(path)
    exact(m,('schema_version','fit_contract','backend_id','execution_contract_version','execution_contract_sha256',
        'parent_recipe_sha256','assets','source_identity','reference_identity','ranges','device','runtime','R0_runtime',
        'color_policy','cpu_bindings','core_rows','results','quantization','scope','execution_counts','data_binding_sha256','manifest_sha256'),'MANIFEST_FIELDS')
    require(m['schema_version']==MANIFEST_SCHEMA and m['fit_contract']==FIT_CONTRACT and m['backend_id']==BACKEND_ID and
        m['execution_contract_version']==CONTRACT_VERSION,'HYBRID_FIT_MANIFEST_TYPE')
    require(m['manifest_sha256']==digest({k:v for k,v in m.items()if k!='manifest_sha256'}),'HYBRID_FIT_MANIFEST_DIGEST')
    require((compatible_portable_d1(m,path) or compatible_hybrid_execution(m['execution_contract_sha256']))and m['runtime']==RUNTIME and
        m['cpu_bindings']==cpu_bindings()and rows(m['core_rows'])in (256,512) and m['scope']==qualification_scope(),'HYBRID_IMPLEMENTATION_CHANGED')
    recipe,a=read_recipe(path.parent)
    require(a==m['assets']and recipe['recipe_sha256']==m['parent_recipe_sha256']and
        all(m[k]==recipe[k]for k in ('source_identity','reference_identity','device','runtime','R0_runtime','color_policy'))and
        m['ranges']==recipe['postprocess']['ranges'],'HYBRID_FIT_MANIFEST_BINDING')
    require(set(m['results'])=={'O','OP','LO','LP','Y'}and all(hash_value(v)for v in m['results'].values())and
        m['results']['O']==recipe['postprocess']['O_sha256'],'HYBRID_FIT_RESULTS')
    require(set(m['quantization'])=={str(b)+':'+str(a)for b in (8,16)for a in (0.,.5,1.)}and
        all(hash_value(v)for v in m['quantization'].values()),'HYBRID_FIT_QUANTIZATION')
    require(isinstance(m['execution_counts'],dict)and all(type(v)is int for v in m['execution_counts'].values())and m['execution_counts']in (dict(fit=1,render=1,scalar_CG=3,range_discovery=1),
        dict(fit=0,render=1,scalar_CG=3,range_discovery=1),
        dict(fit=0,render=1,scalar_CG=0,range_discovery=0))and hash_value(m['data_binding_sha256']),'HYBRID_FIT_COUNTS')
    marker=strict_load(path.parent/'published.json')
    exact(marker,('schema_version','manifest_sha256','manifest_file_sha256','intent_sha256','job_id','attempt_id','data_binding_sha256'),'PUBLISHED_FIELDS')
    require(marker['schema_version']==MANIFEST_SCHEMA and marker['manifest_sha256']==m['manifest_sha256']and
        marker['manifest_file_sha256']==sha_file(path)and marker['data_binding_sha256']==m['data_binding_sha256']and
        hash_value(marker['intent_sha256']),'HYBRID_FIT_PUBLICATION')
    return m,recipe
