"""Portable reads use only bundle-local canonical payloads and runtime metadata."""
from pathlib import Path
from .primitives import *
from .assets import parse_file
from .identity import runtime_status
from .guard import metadata_api
MANIFEST_FIELDS={'format','version','mode','mathematical','math_id','runtime_requirements','execution_build','qualification','assets','provenance','bundle_id'}
def validate_structure(m):
    if type(m) is dict and type(m.get('version')) is int and m['version']==3:
        if m.get('mode') == 'fast':
            from .recovered import structure
            return structure(m)
        from .generated_fine import structure
        return structure(m)
    if type(m) is dict and type(m.get('version')) is int and m['version'] == 2:
        from .generated import structure
        return structure(m)
    fields(m,MANIFEST_FIELDS,'MANIFEST_FIELDS');require(m['format']=='grademirror.cpu_recipe_bundle' and type(m['version'])is int and m['version']==1,'UNSUPPORTED_BUNDLE_VERSION');require(m['mode']in ('fast','fine'),'BUNDLE_MODE')
    math=m['mathematical'];fields(math,('profile','settings','normalization','inputs','color_policy','color_declaration','r0_parameters','math_files',*(['p3']if m['mode']=='fine'else[])),'MATHEMATICAL_FIELDS')
    fields(math['profile'],('id','contract_sha256','core_sha256'),'PROFILE_FIELDS');fields(math['r0_parameters'],('members','value_sha256'),'PARAMETER_FIELDS')
    fields(m['runtime_requirements'],('python','python_ABI','r0','CPU_setup','optimizer_policy','torch_build_ABI','precision','threads','interop','cv2_threads','codec','solver'),'RUNTIME_FIELDS')
    if m['mode']=='fine':
        fields(math['p3'],('b','reference_wh','H_sha256','T_sha256','normalization','constants','ranges','five_field_hashes','fast_parameter_sha256','solver_policy'),'P3_FIELDS')
        fields(math['p3']['constants'],('J','P3_h','composition','delta','range_margin','range_rule','sigma'),'CONSTANTS_FIELDS');fields(math['p3']['b'],('members','value_sha256'),'B_FIELDS')
    require(digest(m['mathematical'])==m['math_id'] and digest({k:v for k,v in m.items()if k!='bundle_id'})==m['bundle_id'],'BUNDLE_SEMANTIC')
    b=m['execution_build'];fields(b,('format','files','worker_files','execution_qualified','build_sha256'),'BUILD_FIELDS');require(b['format']=='cpu-recipe-execution-build-v1' and b['execution_qualified']is False and digest({k:v for k,v in b.items()if k!='build_sha256'})==b['build_sha256'],'BUILD_IDENTITY')
    for entries in (m['mathematical']['math_files'],b['files'],b['worker_files']):
        require(type(entries)is list and entries and len(entries)<=1024,'CLOSURE_LIST');seen=set()
        for v in entries:fields(v,('path','sha256'),'CLOSURE_FIELDS');relative(v['path']);require(sha(v['sha256'])and v['path']not in seen,'CLOSURE_IDENTITY');seen.add(v['path'])
    q=m['qualification'];fields(q,('status','execution_qualified','general_input_qualified','CPU_ENABLED','AUTO_ENABLED','PRODUCTION_ENABLED','D01_extended','user_visual','historical'),'QUALIFICATION_FIELDS')
    require(q['status']=='IMPORTED_SAVED_EVIDENCE_ONLY' and q['user_visual']=='PENDING' and all(q[k]is False for k in ('execution_qualified','general_input_qualified','CPU_ENABLED','AUTO_ENABLED','PRODUCTION_ENABLED','D01_extended')),'QUALIFICATION_ESCALATION')
    names=[];total=0
    for item in m['assets']:
        fields(item,('path','bytes','sha256'),'ASSET_FIELDS');relative(item['path']);require(type(item['bytes'])is int and 0<item['bytes']<=BUNDLE_LIMIT and sha(item['sha256']),'ASSET_RECORD');names.append(item['path']);total+=item['bytes']
    expected={'r0/parameters.npz','provenance/fast-recipe.json','provenance/fast-complete.json'}
    if m['mode']=='fine':expected|={'p3/b.npz','provenance/fine-fine-recipe.json','provenance/fine-COMPLETE.json','provenance/frozen-fine-version.json'}
    require(len(names)==len(set(names)) and set(names)==expected and total<=BUNDLE_LIMIT,'BUNDLE_ASSET_SET_OR_CAP')
    require(('p3'in m['mathematical'])==(m['mode']=='fine'),'MODE_DATA_CONFLICT');return m
def complete(directory):
    candidate = read(root(directory) / 'COMPLETE.json')
    if candidate.get('format') == 'cpu-recipe-complete-recovered-fast-v3':
        from .recovered import complete as recovered_complete
        return recovered_complete(directory)
    if candidate.get('format') == 'cpu-recipe-complete-v3':
        from .generated_fine import complete as fine_complete
        return fine_complete(directory)
    if candidate.get('format') == 'cpu-recipe-complete-v2':
        from .generated import complete as generated_complete
        return generated_complete(directory)
    p=root(directory);require(p.is_dir(),'BUNDLE_NOT_FOUND',stage='inspect');mark=read(p/'COMPLETE.json');fields(mark,('format','bundle_id','math_id','manifest','operation_id','attempt_id'),'COMPLETE_FIELDS');require(mark['format']=='cpu-recipe-complete-v1','COMPLETE_VERSION');verify_file(p/'manifest.json',mark['manifest']);require(mark['manifest']['path']=='manifest.json','COMPLETE_MANIFEST_PATH')
    m=validate_structure(read(p/'manifest.json'));require(mark['bundle_id']==m['bundle_id'] and mark['math_id']==m['math_id'],'COMPLETE_IDENTITY');total=0
    for item in m['assets']:verify_file(inside(p,item['path']),item);total+=item['bytes']
    params=parse_file(inside(p,'r0/parameters.npz'),'r0');require({k:v for k,v in params.items()if k!='expanded_bytes'}==m['mathematical']['r0_parameters'],'R0_DATA_IDENTITY');expanded=params['expanded_bytes']
    if m['mode']=='fine':
        b=parse_file(inside(p,'p3/b.npz'),'p3');require({k:v for k,v in b.items()if k!='expanded_bytes'}==m['mathematical']['p3']['b'] and m['mathematical']['p3']['fast_parameter_sha256']==params['value_sha256'],'P3_DATA_IDENTITY');expanded+=b['expanded_bytes']
    require(expanded<=EXPANDED_LIMIT,'BUNDLE_EXPANDED_LIMIT')
    visible=0
    for f in p.rglob('*'):
        reject_reparse(f)
        if f.is_file():visible+=f.stat().st_size
    require(visible<=BUNDLE_LIMIT,'BUNDLE_SMALL_FILES_LIMIT');return m,mark
def _locator(p,name):
    if not(p/'locator.json').is_file():return dict(status='UNLINKED',path=None)
    v=read(p/'locator.json');fields(v,('version','source','reference'),'LOCATOR_FIELDS');require(v['version']==1,'LOCATOR_VERSION');item=v[name]
    if item is None:return dict(status='UNLINKED',path=None)
    fields(item,('path','bytes','sha256','last_verified_at'),'LOCATOR_ITEM');require(type(item['path'])is str and Path(item['path']).is_absolute() and sha(item['sha256']),'LOCATOR_TYPE')
    exists=Path(item['path']).is_file();return dict(status='LOCATED_HASH_NOT_RECHECKED'if exists else'MISSING',path=item['path'],last_verified_at=item['last_verified_at'])
@metadata_api
def inspect_cpu_recipe(directory,*,runtime_inventory=None):
    p=root(directory);require(p.is_dir(),'BUNDLE_NOT_FOUND',stage='inspect')
    if not(p/'COMPLETE.json').is_file():
        return dict(persistent_complete=False,assets_verified=False,state='INTERRUPTED_OR_UNCOMMITTED',source_status=dict(status='UNKNOWN'),reference_status=dict(status='UNKNOWN'),runtime_compatibility=dict(status='UNKNOWN'),execution_qualification=dict(execution_qualified=False),resident=False,availability='REPLAY_REQUIRED')
    m,mark=complete(p)
    if m['version']==3:
        if m['mode']=='fast':
            from .recovered import inspect
            return inspect(p,runtime_inventory=runtime_inventory)
        from .generated_fine import inspect
        return inspect(p,runtime_inventory=runtime_inventory)
    if m['version'] == 2:
        from .generated import inspect
        return inspect(p, runtime_inventory=runtime_inventory)
    return dict(format=m['format'],version=1,mode=m['mode'],bundle_id=m['bundle_id'],math_id=m['math_id'],execution_build_id=m['execution_build']['build_sha256'],persistent_complete=True,assets_verified=True,state='PERSISTENT_COMPLETE',source_status=_locator(p,'source'),reference_status=_locator(p,'reference'),runtime_compatibility=runtime_status(m['runtime_requirements'],runtime_inventory),execution_qualification=m['qualification'],resident=False,availability='REPLAY_REQUIRED',operation_id=mark['operation_id'],attempt_id=mark['attempt_id'])
@metadata_api
def verify_cpu_recipe(directory,*,runtime_inventory=None):
    require((root(directory)/'COMPLETE.json').is_file(),'INCOMPLETE_BUNDLE',stage='verify');return inspect_cpu_recipe(directory,runtime_inventory=runtime_inventory)
