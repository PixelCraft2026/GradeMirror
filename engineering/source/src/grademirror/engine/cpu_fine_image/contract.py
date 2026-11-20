"""Strict fixed-domain binding; no implicit fit, migration or device selection."""
import hashlib,json,math
from pathlib import Path
from ...common import strict_load,sha_file,canonical_json,writable_path,reject_reparse
ROOT=Path(__file__).resolve().parents[4]
I=ROOT/'docs/evidence/gm-050/20261029-plan-v1'
W=ROOT/'tests/workspaces/g50/20261029-v1'
BACKEND='cpu-fine-image-v1';RECIPE='cpu-fine-image-recipe-v1'
def need(ok,code):
    if not ok:raise ValueError(code)
def digest(value):return hashlib.sha256(canonical_json(value)).hexdigest()
def binding(path):
    p=Path(path).resolve();return dict(path=p.as_posix(),bytes=p.stat().st_size,sha256=sha_file(p))
def verify(b):
    need(type(b)is dict and set(b)=={'path','bytes','sha256'},'BINDING_FIELDS')
    need(type(b['bytes'])is int and b['bytes']>=0 and type(b['path'])is str and Path(b['path']).is_absolute(),'BINDING_TYPES')
    need(binding(b['path'])==b,'BINDING_CHANGED:'+b['path']);return b
def implementation():
    files=[]
    for p in (ROOT/'src/grademirror').rglob('*.py'):
        # Bind all actual product dependencies, including the newly added thin layer.
        files.append(binding(p))
    files.append(binding(ROOT/'tools/gm008_solver_worker.py'));files.append(binding(ROOT/'tools/gm050_stage_worker.py'))
    files.sort(key=lambda b:b['path']);return dict(backend_id=BACKEND,sha256=digest(files),files=files)
def fixed():return strict_load(I/'fixed-inputs.json')
def seed_binding():
    row=fixed()
    for b in row['fast_seed'].values():verify(b)
    return dict(files=row['fast_seed'],recipe_sha256=row['fast_recipe_sha256'],parameter_sha256=row['fast_parameter_sha256'],backend_sha256=row['fast_backend_sha256'])
def output(path):
    p=Path(path);reject_reparse(p);p=writable_path(p)
    need(p.is_relative_to(W),'FINE_WRITE_SCOPE');return p
def validate(request,operation):
    fields={'schema','backend','backend_sha256','qualification_manifest','case','key','seed','source','O','reference_wh','recipe_directory','work_root','core_rows'}
    need(type(request)is dict and set(request)==fields,'FINE_REQUEST_FIELDS')
    need(request['schema']=='cpu-fine-image-'+operation+'-request-v1' and request['backend']==BACKEND,'FINE_BACKEND')
    need(request['backend_sha256']==implementation()['sha256'],'FINE_VERSION')
    verify(request['qualification_manifest']);q=strict_load(Path(request['qualification_manifest']['path']))
    need(set(q)=={'schema','backend_sha256','cases','production_activation'} and q==dict(schema='cpu-fine-image-qualification-v1',backend_sha256=request['backend_sha256'],cases=['small-B','group15'],production_activation=False),'FINE_SCOPE_MANIFEST')
    need(request['case']in ('small-B','group15') and type(request['key'])is str and 1<=len(request['key'])<=128,'FINE_SCOPE')
    row=fixed();native=request['case']=='group15'
    expected_source=row['source'] if native else dict(sha256=row['small_case']['hashes']['O'])
    expected_o=dict(shape=[7008,4672,3]if native else[771,67,3],dtype='float32',sha256=row['expected_O_sha256']if native else row['small_case']['hashes']['O'])
    need(request['source']==expected_source and request['O']==expected_o and request['reference_wh']==([1024,1536]if native else[39,23]),'FINE_INPUT_IDENTITY')
    need(request['seed']==seed_binding() if native else request['seed']=={'small_formula':'GM019-B-v1'},'FINE_SEED')
    need(request['core_rows']==(512 if operation=='prepare' else 256),'FINE_EXECUTION_TIER')
    output(request['recipe_directory']);output(request['work_root']);return request
def service_budget(shape,ranges,rows,*,cancel=False):
    from ..cpu_postprocess.geometry import plan
    h,w,_=shape;s=plan(h,w,rows);s=s[:1]if cancel else s;n=len(s);anchors=sum(x['count']for x in ranges)
    need(all(type(x['count'])is int and 2<=x['count']<=257 for x in ranges),'FINE_RANGE_CAP')
    return dict(scalar_LLF_footprints=6*n,remap_pyramids=2*n*anchors,D_calls=14*n*(anchors+3),U_calls=2*n*(14*anchors+21),P3_scalar_evaluations=3*h*w+3*sum((x.gaussian[0][1]-x.gaussian[0][0])*w for x in s))
