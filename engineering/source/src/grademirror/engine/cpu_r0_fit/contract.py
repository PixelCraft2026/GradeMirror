"""Identity and scope validation for the two frozen three-update CPU pairs."""
from pathlib import Path
from ..cpu_r0_render.contract import canonical,digest,file_hash,loads,bounded,fields,need,RenderError,PYTHON,PYTHON_LAUNCHER_SHA256,PYTHON_BASE_SHA256
from ..independent_r0.profile import PROFILE_ID,PROFILE_CONTRACT_SHA256,CORE_SHA256,MATH_SHA256,RUNTIME
from ..profiles import settings_for,normalization

BACKEND_ID='r0-cpu-fit-v1';REQUEST='cpu-r0-fit-request-v1';RECEIPT='cpu-r0-fit-receipt-v1'
SCOPE='gm048-fixed-small-first-fit-20261029-v1'
ROOT=Path(__file__).resolve().parents[4]
MANIFEST=ROOT/'docs/evidence/gm-048/20261029-v1/qualification-manifest.json'
WORK=ROOT/'tests/workspaces/g48/20261029-v1'
TORCH=ROOT.parent/'color-matching-research/.venv-feature-matching-v1/Lib/site-packages/torch'
CASES={'F1':[13,17,3],'F2':[33,47,3]}

def binding(path):
    p=Path(path).resolve();return dict(path=p.as_posix(),bytes=p.stat().st_size,sha256=file_hash(p))
def bound_file(b,cap=1024**2):
    fields(b,('path','bytes','sha256'));need(type(b['path'])is str and Path(b['path']).is_absolute(),'CPU_FIT_FILE_PATH')
    need(type(b['bytes'])is int and 0<b['bytes']<=cap,'CPU_FIT_FILE_QUOTA')
    need(type(b['sha256'])is str and len(b['sha256'])==64 and all(x in '0123456789abcdef'for x in b['sha256']),'CPU_FIT_HASH')
    from grademirror.common import reject_reparse
    p=Path(b['path']);reject_reparse(p);need(binding(p)==b,'CPU_FIT_FILE_IDENTITY');return p
def implementation():
    old=ROOT/'src/grademirror/engine/independent_r0'
    paths=list(Path(__file__).parent.glob('*.py'))+[old/n for n in MATH_SHA256]+[old/'adapter.py',old/'profile.py',ROOT/'src/grademirror/engine/profiles.py',ROOT/'src/grademirror/common.py',ROOT/'src/grademirror/engine/cpu_r0_render/executor.py',ROOT/'src/grademirror/engine/cpu_r0_render/contract.py']
    paths+=list((ROOT/'schemas').glob('cpu-r0-fit-*-v1.json'))
    runtime_paths=[TORCH/n for n in ('optim/adam.py','optim/optimizer.py','optim/lr_scheduler.py','_compile.py')]
    contract=dict(backend_id=BACKEND_ID,sources={p.relative_to(ROOT).as_posix():file_hash(p)for p in sorted(paths)},runtime_sources={p.as_posix():file_hash(p)for p in runtime_paths},math=MATH_SHA256,profile_contract_sha256=PROFILE_CONTRACT_SHA256,runtime=dict(RUNTIME,python=PYTHON,launcher_sha256=PYTHON_LAUNCHER_SHA256,base_sha256=PYTHON_BASE_SHA256),scope=SCOPE,cases=CASES,seed=43,updates=3,CPU_setup='original default CPU generator,4threads,interop1,cv2threads4',optimizer_policy='original Adam/step; instance-only all-CPU noncapturable graph health check; no global patch',production_qualified=False)
    return dict(contract=contract,sha256=digest(contract))
def validate_request(request):
    bounded(request);fields(request,('schema_version','backend_id','backend_sha256','qualification_manifest','scope','pair','settings','runtime','profile','execution_device','normalization','output_intent'))
    need(request['schema_version']==REQUEST and request['backend_id']==BACKEND_ID,'CPU_FIT_SCHEMA')
    need(request['backend_sha256']==implementation()['sha256'],'CPU_FIT_IMPLEMENTATION')
    fields(request['scope'],('kind','id','case','arm'));s=request['scope']
    need(s['kind']=='synthetic_test'and s['id']==SCOPE and s['case']in CASES,'CPU_FIT_SCOPE')
    path=bound_file(request['qualification_manifest']);need(path.resolve()==MANIFEST.resolve(),'CPU_FIT_MANIFEST_PATH')
    manifest=loads(path.read_text('utf8'));fields(manifest,('schema','id','backend_sha256','cases','resource_policy','frozen_at','CPU_QUALIFIED','AUTO_SELECTION','PRODUCTION_ACTIVATION'))
    need(manifest['schema']=='cpu-first-fit-qualification-manifest-v1'and manifest['id']==SCOPE and manifest['backend_sha256']==request['backend_sha256'],'CPU_FIT_MANIFEST_IDENTITY')
    fields(manifest['cases'],CASES);need(type(manifest['frozen_at'])is int and manifest['frozen_at']>0,'CPU_FIT_FREEZE_TIME')
    need(all(manifest[k]is False for k in ('CPU_QUALIFIED','AUTO_SELECTION','PRODUCTION_ACTIVATION')),'CPU_FIT_NO_PRODUCTION')
    need(canonical(manifest['resource_policy'])==canonical(dict(physical_min=4*1024**3,commit_min=6*1024**3,job_cap=3*1024**3,tree_cap=3*1024**3,margin=1024**3,root_seconds=900,fit_seconds=600)),'CPU_FIT_RESOURCE_POLICY')
    row=manifest['cases'][s['case']];fields(row,('pair','arms'))
    need(row['arms']==(['new','cold']if s['case']=='F1'else['new','cold','cancel1','cancel3'])and s['arm']in row['arms'],'CPU_FIT_ARM')
    pair=request['pair'];fields(pair,('source_file','reference_file','source_identity','reference_identity','source_sha256','reference_sha256','color_declaration','resize_geometry','color_policy','io_runtime'))
    need(canonical(pair)==canonical(row['pair']),'CPU_FIT_PAIR_BINDING')
    from ..independent_r0 import profile
    for label in ('source','reference'):
        p=bound_file(pair[label+'_file']);need(p.suffix=='.npy','CPU_FIT_ARRAY_FILE_ONLY');i=pair[label+'_identity']
        fields(i,('file_sha256','file_size_bytes','pixel_sha256','shape','dtype','bit_depth'))
        need(type(i['shape'])is list and all(type(n)is int for n in i['shape'])and i['shape']==CASES[s['case']]and i['dtype']=='float32'and type(i['bit_depth'])is int and i['bit_depth']==32,'CPU_FIT_INPUT_SHAPE_DTYPE')
        need(type(i['file_size_bytes'])is int and i['file_size_bytes']==pair[label+'_file']['bytes']and i['file_sha256']==pair[label+'_file']['sha256']==pair[label+'_sha256'],'CPU_FIT_SOURCE_FILE_IDENTITY')
        need(type(i['pixel_sha256'])is str and len(i['pixel_sha256'])==64 and all(x in '0123456789abcdef'for x in i['pixel_sha256']),'CPU_FIT_PIXEL_HASH')
    need(request['execution_device']=='cpu','CPU_FIT_DEVICE')
    expected={**settings_for(PROFILE_ID,CASES[s['case']][:2]),'updates':3}
    need(canonical(request['settings'])==canonical(expected),'CPU_FIT_SETTINGS')
    need(canonical(request['runtime'])==canonical(dict(RUNTIME,python=PYTHON)),'CPU_FIT_RUNTIME')
    need(canonical(request['profile'])==canonical(dict(id=PROFILE_ID,contract_sha256=PROFILE_CONTRACT_SHA256,core_sha256=CORE_SHA256)),'CPU_FIT_PROFILE')
    need(canonical(request['normalization'])==canonical(normalization(pair['source_identity'],pair['reference_identity'])),'CPU_FIT_NORMALIZATION')
    fields(request['output_intent'],('directory','kind','owner','overwrite'));out=request['output_intent']
    need(type(out['directory'])is str,'CPU_FIT_OUTPUT_INTENT')
    target=Path(out['directory']).resolve();parents={(WORK/'outputs'/s['case']/s['arm']/attempt).resolve()for attempt in ('initial','conditional')}
    need(target.name=='recipe'and target.parent in parents and out['kind']=='new-private-synthetic-recipe'and out['owner']=='caller'and out['overwrite']is False,'CPU_FIT_OUTPUT_INTENT')
    need(not target.exists(),'CPU_FIT_OUTPUT_EXISTS')
    return request
