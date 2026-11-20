"""Byte-bound CPU execution contract, separate from original fit provenance."""
import hashlib,json,math
from pathlib import Path
from ..independent_r0.profile import RUNTIME,MATH_SHA256,PROFILE_ID,PROFILE_CONTRACT_SHA256,CORE_SHA256

BACKEND_ID='r0-cpu-render-v1'
REQUEST='cpu-r0-render-request-v1';RECEIPT='cpu-r0-render-receipt-v1'
SCOPE='gm043-small-synthetic-20261029-v1'
CASES={'A':([513,65,3],'uint8'),'B':([17,19,3],'uint16'),'C':([513,517,3],'float32')}
SETTINGS=dict(tile_side=512,threads=4,interop=1,seed=43,dtype='float32',memory_format='channels_last',
    cudnn_benchmark=False,cudnn_deterministic=True,matmul_tf32=False,cudnn_tf32=False,deterministic_algorithms=False)
PYTHON='3.12.14'
PYTHON_LAUNCHER_SHA256='946012d7762f6a16d16e0a79af48aec2916cfe134aa9fedc09efef4998bec805'
PYTHON_BASE_SHA256='7a4102b91959bb234507850ea584e1d31d300a6831b171e4aadc59ff592f0234'

class RenderError(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)

def need(ok,code):
    if not ok:raise RenderError(code)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def file_hash(path):
    with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def fields(value,required):need(type(value)is dict and set(value)==set(required),'CPU_R0_FIELDS')
def bounded(value,depth=0):
    need(depth<=16,'CPU_R0_QUOTA')
    if type(value)is dict:
        need(len(value)<=128 and all(type(k)is str for k in value),'CPU_R0_QUOTA')
        for k,v in value.items():bounded(k,depth+1);bounded(v,depth+1)
    elif type(value)is list:
        need(len(value)<=128,'CPU_R0_QUOTA')
        for v in value:bounded(v,depth+1)
    elif type(value)is str:need(len(value)<=4096,'CPU_R0_QUOTA')
    elif type(value)is int:need(abs(value)<=2**63-1,'CPU_R0_NUMBER')
    elif type(value)is float:need(math.isfinite(value)and abs(value)<=2**63-1,'CPU_R0_NUMBER')
    else:need(value is None or type(value)is bool,'CPU_R0_TYPE')
def loads(raw):
    need(type(raw)is str and len(raw.encode('utf8'))<=1024**2,'CPU_R0_QUOTA')
    def pairs(items):
        out={}
        for k,v in items:need(k not in out,'CPU_R0_DUPLICATE_KEY');out[k]=v
        return out
    try:v=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda x:need(False,'CPU_R0_NUMBER'))
    except (RecursionError,json.JSONDecodeError)as error:raise RenderError('CPU_R0_JSON')from error
    bounded(v);return v

def implementation():
    root=Path(__file__).resolve().parents[2];old=root/'engine/independent_r0'
    paths=list(Path(__file__).parent.glob('*.py'))+[old/n for n in MATH_SHA256]
    paths += [old/'adapter.py',old/'profile.py',root/'engine/profiles.py',root/'common.py']
    project=root.parents[1];paths+=list((project/'schemas').glob('cpu-r0-render-*-v1.json'))
    sources={p.relative_to(project).as_posix():file_hash(p)for p in sorted(paths)}
    value=dict(backend_id=BACKEND_ID,sources=sources,mathematical_sources=MATH_SHA256,
        profile_id=PROFILE_ID,profile_contract_sha256=PROFILE_CONTRACT_SHA256,
        runtime=dict(RUNTIME,python=PYTHON,launcher_sha256=PYTHON_LAUNCHER_SHA256,base_sha256=PYTHON_BASE_SHA256),
        settings=SETTINGS,qualification_scope=SCOPE,cases=CASES,production_qualified=False)
    return dict(contract=value,sha256=digest(value))

def validate_request(value):
    bounded(value);need(len(canonical(value))<=1024**2,'CPU_R0_QUOTA')
    fields(value,('schema_version','backend_id','backend_sha256','scope','recipe','source_identity','origin_device',
        'profile','runtime','settings','normalization','color_policy','color_declaration','output'))
    need(value['schema_version']==REQUEST and value['backend_id']==BACKEND_ID,'CPU_R0_SCHEMA')
    scope=value['scope'];fields(scope,('kind','id','case'))
    need(scope['kind']=='synthetic_validation'and scope['id']==SCOPE and scope['case']in CASES,'CPU_R0_SCOPE_REQUIRED')
    need(value['backend_sha256']==implementation()['sha256'],'CPU_R0_IMPLEMENTATION_CHANGED')
    fields(value['recipe'],('recipe_sha256','parameter_sha256','metadata_sha256','archive_sha256'))
    for h in value['recipe'].values():need(type(h)is str and len(h)==64 and all(c in '0123456789abcdef'for c in h),'CPU_R0_HASH')
    fields(value['settings'],SETTINGS)
    for name,expected in SETTINGS.items():need(type(value['settings'][name])is type(expected)and value['settings'][name]==expected,'CPU_R0_EXECUTION_SETTINGS')
    need(value['profile']==dict(id=PROFILE_ID,contract_sha256=PROFILE_CONTRACT_SHA256,core_sha256=CORE_SHA256),'CPU_R0_PROFILE')
    need(value['runtime']==dict(RUNTIME,python=PYTHON),'CPU_R0_RUNTIME')
    shape,dtype=CASES[scope['case']];identity=value['source_identity']
    fields(identity,('file_sha256','file_size_bytes','pixel_sha256','shape','dtype','bit_depth'))
    need(type(identity['shape'])is list and all(type(n)is int for n in identity['shape'])and identity['shape']==shape and identity['dtype']==dtype,'CPU_R0_SOURCE_SCOPE')
    need(type(identity['bit_depth'])is int and identity['bit_depth']=={'uint8':8,'uint16':16,'float32':32}[dtype],'CPU_R0_SOURCE_SCOPE')
    need(type(identity['file_size_bytes'])is int and identity['file_size_bytes']>=0,'CPU_R0_SOURCE_SCOPE')
    for name in ('file_sha256','pixel_sha256'):need(type(identity[name])is str and len(identity[name])==64 and all(c in '0123456789abcdef'for c in identity[name]),'CPU_R0_HASH')
    fields(value['output'],('shape','dtype','borrowed'))
    need(type(value['output']['shape'])is list and all(type(n)is int for n in value['output']['shape']),'CPU_R0_OUTPUT_CONTRACT')
    need(value['output']==dict(shape=shape,dtype='float32',borrowed=True),'CPU_R0_OUTPUT_CONTRACT')
    need(type(value['output']['borrowed'])is bool and value['output']['borrowed']is True,'CPU_R0_OUTPUT_CONTRACT')
    fields(value['origin_device'],('cuda_version','cudnn_benchmark','cudnn_deterministic','cudnn_tf32','deterministic_algorithms','driver_version','dtype','id','matmul_tf32','memory_format','name','uuid'))
    for name in ('cudnn_benchmark','cudnn_deterministic','cudnn_tf32','deterministic_algorithms','matmul_tf32'):
        need(type(value['origin_device'][name])is bool and value['origin_device'][name]==SETTINGS[name],'CPU_R0_ORIGIN_DEVICE')
    for name in ('cuda_version','driver_version','dtype','id','memory_format','name','uuid'):
        need(type(value['origin_device'][name])is str and value['origin_device'][name],'CPU_R0_ORIGIN_DEVICE')
    need(value['origin_device']['id']=='cuda:0' and value['origin_device']['dtype']=='float32' and value['origin_device']['memory_format']=='channels_last','CPU_R0_ORIGIN_DEVICE')
    need(type(value['normalization'])is dict and type(value['color_policy'])is dict and type(value['color_declaration'])is str,'CPU_R0_COLOR_BINDING')
    expected_normalization={name:dict(dtype=dtype,bit_depth=identity['bit_depth'],denominator={'uint8':255,'uint16':65535}.get(dtype),dtype_after='float32',resize='INTER_AREA',quantization='none')for name in ('source','reference')}
    expected_color=dict(color_policy_id='synthetic-test',source=dict(interpretation='synthetic_sRGB'),reference=dict(interpretation='synthetic_sRGB'))
    need(canonical(value['normalization'])==canonical(expected_normalization)and canonical(value['color_policy'])==canonical(expected_color)and value['color_declaration']=='gamma-encoded-sRGB-float32','CPU_R0_COLOR_BINDING')
    return value
