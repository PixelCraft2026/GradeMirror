"""Explicit accepted GM049/050 import. Never invoke their implementation()."""
from pathlib import Path
from .primitives import read,read_bytes,loads,require,digest,verify_file,file_record,BUNDLE_LIMIT
from .assets import parse_npz
from .identity import math_closure,execution_build
def specification():return read(Path(__file__).with_name('frozen-origin.json'))
def load_legacy(fast_directory,fine_directory=None):
    s=specification();fixed=s['fixed'];payloads={};fpaths={}
    for name,identity in fixed['fast'].items():
        p=Path(fast_directory)/name;verify_file(p,identity);fpaths[name]=p
        payloads['r0/parameters.npz'if name=='parameters.npz'else'provenance/fast-'+name]=read_bytes(p,BUNDLE_LIMIT)
    f=loads(payloads['provenance/fast-recipe.json']);complete=loads(payloads['provenance/fast-complete.json'])
    require(f['recipe_schema_version']=='cpu-r0-image-recipe-v1' and f['backend_id']=='r0-cpu-image-v1' and f['backend_sha256']==s['old_fast_backend'],'LEGACY_FAST_VERSION')
    require(complete['status']=='COMPLETE' and complete['recipe_sha256']==f['recipe_sha256']==fixed['fast_recipe_sha256'],'LEGACY_FAST_COMPLETE')
    for key,name in (('metadata','recipe.json'),('archive','parameters.npz')):require(complete[key]==fixed['fast'][name],'LEGACY_FAST_BINDING')
    require(digest({k:v for k,v in f.items()if k not in ('recipe_sha256','archive_sha256')})==f['recipe_sha256'],'LEGACY_FAST_SEMANTIC')
    params=parse_npz(payloads['r0/parameters.npz'],'r0');require(params['value_sha256']==f['parameter_sha256']==fixed['fast_parameter_sha256'],'LEGACY_PARAMETER_VALUES')
    pair=f['pair'];math=dict(profile=f['profile'],settings=f['settings'],normalization=f['normalization'],inputs={k:pair[k]for k in ('source_identity','reference_identity','resize_geometry','training_shape','training_source_pixel_sha256','training_reference_pixel_sha256')},color_policy=pair['color_policy'],color_declaration=pair['color_declaration'],r0_parameters={k:v for k,v in params.items()if k!='expanded_bytes'},math_files=math_closure('fine'if fine_directory is not None else'fast'))
    runtime=dict(python=f['runtime']['python'],python_ABI='cpython-312',r0={k:v for k,v in f['runtime'].items()if k!='python'},CPU_setup=f['CPU_setup'],optimizer_policy=f['optimizer_policy'],torch_build_ABI='cu124',precision='FP32/channels_last',threads=4,interop=1,cv2_threads=4,codec={k:v for k,v in pair['io_runtime'].items()if k!='python'},solver=None)
    provenance=dict(accepted_scope='GM049_GROUP15_FIXED_CPU_FAST',old_fast_recipe_sha256=f['recipe_sha256'],old_fast_backend_sha256=f['backend_sha256'],old_fine_recipe_sha256=None,old_fine_backend_sha256=None,old_fine_digest_validation=None,legacy_paths_are_explanatory_only=True,legacy_CPU_setup=f['CPU_setup'])
    evidence=dict(fast_scope=f['scope'],fast_flags=f['flags'],fine=None,unknown=dict(minimum_machine='UNKNOWN',new_build_execution='UNKNOWN',cross_machine_execution='UNKNOWN'))
    if fine_directory is not None:
        for name,identity in fixed['fine'].items():
            p=Path(fine_directory)/name;verify_file(p,identity);payloads['p3/b.npz'if name=='b.npz'else'provenance/fine-'+name]=read_bytes(p,BUNDLE_LIMIT)
        fine=loads(payloads['provenance/fine-fine-recipe.json']);mark=loads(payloads['provenance/fine-COMPLETE.json'])
        require(fine['backend']=='cpu-fine-image-v1' and fine['backend_sha256']==s['old_fine_backend'] and fine['schema']=='cpu-fine-image-recipe-v1','LEGACY_FINE_VERSION')
        require(fine['seed']['files']==fixed['fast'] and fine['seed']['parameter_sha256']==params['value_sha256'] and fine['seed']['recipe_sha256']==f['recipe_sha256'],'LEGACY_FINE_SEED')
        require(digest({k:v for k,v in fine.items()if k!='recipe_sha256'})==fine['recipe_sha256']==fixed['fine_recipe_sha256']==mark['recipe_sha256'],'LEGACY_FINE_SEMANTIC')
        require(mark['schema']=='cpu-fine-image-complete-v1' and mark['recipe']==fixed['fine']['fine-recipe.json'] and mark['b']==fixed['fine']['b.npz'],'LEGACY_FINE_COMPLETE')
        version_file=s['fine_version'];verify_file(version_file['path'],version_file);raw=read_bytes(version_file['path']);version=loads(raw)['backend']
        require(version['sha256']==s['old_fine_backend'] and digest(version['files'])==version['sha256'],'HISTORICAL_FINE_DIGEST')
        payloads['provenance/frozen-fine-version.json']=raw
        b=parse_npz(payloads['p3/b.npz'],'p3');require(b['value_sha256']==fine['b_array']['sha256']==fixed['fine_b_array']['sha256'],'LEGACY_FINE_COEFFICIENTS')
        math['p3']=dict(b={k:v for k,v in b.items()if k!='expanded_bytes'},reference_wh=fine['reference_wh'],H_sha256=fine['H_sha256'],T_sha256=fine['T_sha256'],normalization='integer_reference.astype(float64)/255',constants=fine['execution']['mathematical_constants'],ranges=fine['ranges'],five_field_hashes=fine['hashes'],fast_parameter_sha256=params['value_sha256'],solver_policy=dict(rtol=1e-8,atol=1e-12,maxiter=2000,channel_deadline_seconds=300))
        runtime['solver']=fine['solver']['runtime'];provenance.update(accepted_scope='GM049_GM050_GROUP15_FIXED_CPU_FINE',old_fine_recipe_sha256=fine['recipe_sha256'],old_fine_backend_sha256=fine['backend_sha256'],old_fine_digest_validation='EXACT_FROZEN_FILE_LIST_VERIFIED_NOT_CURRENT_TREE');evidence['fine']=dict(solver=fine['solver'],first_core_rows=fine['execution']['first_core_rows'],saved_hashes=fine['hashes'],execution_receipts=fine['execution'])
    require(params['expanded_bytes']+(b['expanded_bytes']if fine_directory is not None else 0)<=4*1024**2,'BUNDLE_EXPANDED_LIMIT')
    assets=[dict(path=name,bytes=len(raw),sha256=__import__('hashlib').sha256(raw).hexdigest())for name,raw in sorted(payloads.items())]
    mode='fine'if fine_directory is not None else'fast'
    manifest=dict(format='grademirror.cpu_recipe_bundle',version=1,mode=mode,mathematical=math,math_id=digest(math),runtime_requirements=runtime,execution_build=execution_build(mode),qualification=dict(status='IMPORTED_SAVED_EVIDENCE_ONLY',execution_qualified=False,general_input_qualified=False,CPU_ENABLED=False,AUTO_ENABLED=False,PRODUCTION_ENABLED=False,D01_extended=False,user_visual='PENDING',historical=evidence),assets=assets,provenance=provenance)
    manifest['bundle_id']=digest(manifest);return manifest,payloads
