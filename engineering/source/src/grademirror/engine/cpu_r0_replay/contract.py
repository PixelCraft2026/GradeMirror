"""Strict input identities; algorithm capability does not grant execution scope."""
import math
from pathlib import Path
from ..cpu_r0_render.contract import (
    bounded,canonical,digest,file_hash,fields,loads,need,RenderError,SETTINGS,
    PYTHON,PYTHON_LAUNCHER_SHA256,PYTHON_BASE_SHA256)
from ..independent_r0.profile import RUNTIME,MATH_SHA256,PROFILE_ID,PROFILE_CONTRACT_SHA256,CORE_SHA256

BACKEND_ID='r0-cpu-render-v2'
REQUEST='cpu-r0-render-request-v2';RECEIPT='cpu-r0-render-receipt-v2'
SCOPE='gm047-four-case-migration-20261029-v1'
ROOT=Path(__file__).resolve().parents[4]
MANIFEST=ROOT/'docs/evidence/gm-047/20261029-v1/qualification-manifest.json'
ADOPTION=ROOT/'docs/evidence/gm-047/20261029-adoption-v1/user-decision.json'
CASES=('B','H1','H2','M1')
MAX_SOURCE=4*1024**2;MAX_FILE=8*1024**2

def binding(path):
    p=Path(path).resolve();return dict(path=p.as_posix(),bytes=p.stat().st_size,sha256=file_hash(p))

def bound_file(value,cap=MAX_FILE):
    fields(value,('path','bytes','sha256'))
    need(type(value['path'])is str and Path(value['path']).is_absolute(),'CPU_V2_FILE_PATH')
    need(type(value['bytes'])is int and 0<value['bytes']<=cap,'CPU_V2_FILE_QUOTA')
    h=value['sha256'];need(type(h)is str and len(h)==64 and all(c in '0123456789abcdef'for c in h),'CPU_V2_HASH')
    from grademirror.common import reject_reparse
    p=Path(value['path']);reject_reparse(p)
    need(binding(p)==value,'CPU_V2_FILE_IDENTITY');return p

def implementation():
    old=ROOT/'src/grademirror/engine/independent_r0'
    paths=list(Path(__file__).parent.glob('*.py'))+[old/n for n in MATH_SHA256]
    paths += [old/'adapter.py',old/'profile.py',ROOT/'src/grademirror/common.py',ROOT/'src/grademirror/engine/profiles.py']
    paths += [ROOT/'src/grademirror/engine/cpu_r0_render'/n for n in ('contract.py','executor.py')]
    paths += list((ROOT/'schemas').glob('cpu-r0-render-*-v2.json'))
    value=dict(backend_id=BACKEND_ID,sources={p.relative_to(ROOT).as_posix():file_hash(p)for p in sorted(paths)},
        mathematical_sources=MATH_SHA256,settings=SETTINGS,scope=SCOPE,
        runtime=dict(RUNTIME,python=PYTHON,launcher_sha256=PYTHON_LAUNCHER_SHA256,base_sha256=PYTHON_BASE_SHA256),
        qualification_manifest=MANIFEST.as_posix(),production_qualified=False)
    return dict(contract=value,sha256=digest(value))

def identity(value):
    fields(value,('file_sha256','file_size_bytes','pixel_sha256','shape','dtype','bit_depth'))
    shape=value['shape'];dtype=value['dtype']
    need(type(shape)is list and len(shape)==3 and all(type(n)is int and n>0 for n in shape)and shape[2]==3,'CPU_V2_SOURCE_SHAPE')
    need(dtype in ('uint8','uint16','float32')and type(value['bit_depth'])is int and value['bit_depth']=={'uint8':8,'uint16':16,'float32':32}[dtype],'CPU_V2_SOURCE_DTYPE')
    need(type(value['file_size_bytes'])is int and 0<value['file_size_bytes']<=MAX_FILE,'CPU_V2_SOURCE_FILE_QUOTA')
    need(shape[0]<=513 and shape[1]<=517 and math.prod(shape)*value['bit_depth']//8<=MAX_SOURCE,'CPU_V2_SOURCE_QUOTA')
    for key in ('file_sha256','pixel_sha256'):
        h=value[key];need(type(h)is str and len(h)==64 and all(c in '0123456789abcdef'for c in h),'CPU_V2_HASH')

def adoption(value):
    fields(value,('user_decision','contract_md','contract_json'))
    p=bound_file(value['user_decision']);need(p.resolve()==ADOPTION.resolve(),'CPU_V2_ADOPTION_SOURCE')
    decision=loads(p.read_text('utf8'))
    need(decision['D01_adopted']is True and decision['answer']=='同意采用上述限定合同（推荐）'and decision['effective_only_for']['cases']==['B','H1','H2'],'CPU_V2_D01_REQUIRED')
    for key,original in (('contract_md','approved_contract_md'),('contract_json','approved_contract_json')):
        bound_file(value[key]);need(value[key]==decision[original],'CPU_V2_ADOPTED_CONTRACT_CHANGED')
    return decision

def manifest(value):
    p=bound_file(value,1024**2);need(p.resolve()==MANIFEST.resolve(),'CPU_V2_MANIFEST_SCOPE')
    m=loads(p.read_text('utf8'));fields(m,('schema','id','backend_sha256','adoption','cases','limits','resource_policy','frozen_at','CPU_QUALIFIED','AUTO_SELECTION','PRODUCTION_ACTIVATION'))
    need(m['schema']=='cpu-r0-qualification-manifest-v2'and m['id']==SCOPE and m['backend_sha256']==implementation()['sha256'],'CPU_V2_MANIFEST_CODE')
    adoption(m['adoption']);fields(m['cases'],CASES)
    need(type(m['frozen_at'])is int and m['frozen_at']>0,'CPU_V2_FREEZE_TIME')
    need(all(m[k]is False for k in ('CPU_QUALIFIED','AUTO_SELECTION','PRODUCTION_ACTIVATION')),'CPU_V2_NO_PRODUCTION')
    limits=dict(source_bytes=MAX_SOURCE,output_bytes=MAX_SOURCE,height=513,width=517,render_seconds=180)
    need(canonical(m['limits'])==canonical(limits),'CPU_V2_LIMITS')
    need(canonical(m['resource_policy'])==canonical(dict(physical_min=4*1024**3,commit_min=6*1024**3,job_cap=3*1024**3,tree_cap=3*1024**3,margin=1024**3,root_seconds=300)),'CPU_V2_RESOURCE_POLICY')
    for case,row in m['cases'].items():
        fields(row,('source_file','source_identity','recipe','purpose','arms'))
        identity(row['source_identity']);fields(row['recipe'],('metadata','archive','recipe_sha256','parameter_sha256'))
        need(row['purpose']==('ENGINEERING_ONLY_NO_Q_GPU'if case=='M1'else'SAVED_Q_M1_NUMERICAL'),'CPU_V2_PURPOSE')
        need(row['arms']==(['new','cold','cancel']if case=='M1'else['new','cold']),'CPU_V2_ARMS')
    return m

def validate_request(value):
    bounded(value);need(len(canonical(value))<=1024**2,'CPU_V2_QUOTA')
    fields(value,('schema_version','backend_id','backend_sha256','qualification_manifest','scope','source_file','source_identity','recipe','origin_device','profile','runtime','settings','normalization','color_policy','color_declaration','output','numerical_contract'))
    need(value['schema_version']==REQUEST and value['backend_id']==BACKEND_ID,'CPU_V2_SCHEMA')
    need(value['backend_sha256']==implementation()['sha256'],'CPU_V2_IMPLEMENTATION_CHANGED')
    fields(value['scope'],('kind','id','case','arm','purpose'));s=value['scope']
    need(s['kind']=='synthetic_validation'and s['id']==SCOPE and s['case']in CASES,'CPU_V2_SCOPE_REQUIRED')
    m=manifest(value['qualification_manifest']);row=m['cases'][s['case']]
    need(s['arm']in row['arms']and s['purpose']==row['purpose'],'CPU_V2_EXECUTION_SCOPE')
    for key in ('source_file','source_identity','recipe'):need(canonical(value[key])==canonical(row[key]),'CPU_V2_MANIFEST_INPUT')
    identity(value['source_identity']);p=bound_file(value['source_file']);need(p.suffix=='.npy','CPU_V2_ARRAY_FILE_ONLY')
    fields(value['recipe'],('metadata','archive','recipe_sha256','parameter_sha256'))
    for key in ('metadata','archive'):bound_file(value['recipe'][key],256*1024)
    need(value['profile']==dict(id=PROFILE_ID,contract_sha256=PROFILE_CONTRACT_SHA256,core_sha256=CORE_SHA256),'CPU_V2_PROFILE')
    need(canonical(value['runtime'])==canonical(dict(RUNTIME,python=PYTHON)),'CPU_V2_RUNTIME')
    need(canonical(value['settings'])==canonical(SETTINGS),'CPU_V2_SETTINGS')
    expected=m['adoption']['contract_json']if s['case']!='M1'else None
    need(canonical(value['numerical_contract'])==canonical(expected),'CPU_V2_NUMERICAL_CONTRACT')
    fields(value['output'],('shape','dtype','borrowed','owner','capacity_bytes'));out=value['output'];shape=value['source_identity']['shape']
    need(type(out['shape'])is list and all(type(n)is int for n in out['shape'])and out['shape']==shape,'CPU_V2_OUTPUT_SHAPE')
    need(out['dtype']=='float32'and out['borrowed']is True and out['owner']=='caller'and type(out['capacity_bytes'])is int and out['capacity_bytes']==math.prod(shape)*4,'CPU_V2_OUTPUT_CONTRACT')
    for key in ('origin_device','normalization','color_policy'):need(type(value[key])is dict,'CPU_V2_NESTED_TYPE')
    need(value['color_declaration']=='gamma-encoded-sRGB-float32','CPU_V2_COLOR')
    return row
