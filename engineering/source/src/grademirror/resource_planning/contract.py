"""Bounded metadata contracts, shared by Python and the JSON CLI."""
import copy
import hashlib
import json
import math
import re
from ..common import GMError
from ..image_limits import check_geometry, MAX_IMAGE_BYTES

REQUEST = 'gm022-plan-request-v1'
SNAPSHOT = 'gm022-resource-snapshot-v1'
RESPONSE = 'gm022-resource-plan-v1'
MAX_BYTES = 1024**2
FORMATS = {('jpeg',8),('png',8),('png',16),('tiff',8),('tiff',16),('webp',8),('bmp',8)}

def require(ok, code='PLAN_REQUEST_INVALID', message='资源计划元数据无效'):
    if not ok: raise GMError(code, message, 2)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def bounded(value, depth=0):
    require(depth<=16, 'PLAN_QUOTA', 'JSON层级超过16')
    if isinstance(value,dict):
        require(len(value)<=128 and all(isinstance(k,str) and len(k)<=4096 for k in value),'PLAN_QUOTA')
        for item in value.values(): bounded(item,depth+1)
    elif isinstance(value,list):
        require(len(value)<=128,'PLAN_QUOTA')
        for item in value: bounded(item,depth+1)
    elif isinstance(value,str): require(len(value)<=4096,'PLAN_QUOTA')
    elif type(value) is int: require(abs(value)<=2**63-1,'PLAN_NUMBER_INVALID')
    elif type(value) is float: require(math.isfinite(value) and abs(value)<=2**63-1,'PLAN_NUMBER_INVALID')
    else: require(value is None or type(value) is bool)

def fields(value, required, optional=()):
    require(isinstance(value,dict) and set(required)<=value.keys() and value.keys()<=set(required)|set(optional))

def integer(value, minimum=0, maximum=2**63-1, nullable=False):
    require(nullable and value is None or type(value) is int and minimum<=value<=maximum,'PLAN_NUMBER_INVALID')

def hash_value(value, nullable=False):
    require(nullable and value is None or isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None)

def number(value, nullable=False):
    require(nullable and value is None or type(value) in (int,float) and math.isfinite(value) and 0<=value<=2**63-1,'PLAN_NUMBER_INVALID')

def text(value):
    require(isinstance(value,str) and 0<len(value)<=256 and not any(ord(c)<32 for c in value))

def image(value):
    fields(value, ('sha256','bytes','width','height','color','bits','format','icc_sha256','icc_policy','orientation_normalized'))
    hash_value(value['sha256']);hash_value(value['icc_sha256'],True)
    integer(value['bytes'],1,MAX_IMAGE_BYTES)
    check_geometry(value['width'],value['height'])
    require(value['color']=='RGB' and type(value['bits'])is int and isinstance(value['format'],str) and (value['format'],value['bits']) in FORMATS)
    require(value['orientation_normalized'] is True)
    require((value['icc_policy']=='embedded' and value['icc_sha256'] is not None) or
            (value['icc_policy']=='assume_sRGB' and value['icc_sha256'] is None))
    require(max(value['width'],value['height'])>=512,'IMAGE_TOO_SMALL')

def training_hw(image):
    w,h=image['width'],image['height']
    return [max(1,round(h*512/max(w,h))),max(1,round(w*512/max(w,h)))]

def normalize_request(value):
    bounded(value)
    require(len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())<=MAX_BYTES,'PLAN_QUOTA')
    fields(value,('schema_version','operation','mode','profile','source'),
           ('reference','recipe','result','export','limits','allow_cpu','device_uuid','existing','request_key','job_id','attempt_id'))
    require(value['schema_version']==REQUEST,'PLAN_SCHEMA_UNSUPPORTED')
    require(value['operation'] in ('fit','replay','export') and value['mode'] in ('fast','fine'))
    fields(value['profile'],('id','contract_sha256'));text(value['profile']['id']);hash_value(value['profile']['contract_sha256'])
    image(value['source']);op=value['operation']
    if op!='export':
        require('reference'in value);image(value['reference'])
        require(training_hw(value['source'])==training_hw(value['reference']),'PAIR_ASPECT_RATIO_MISMATCH')
        require(min(training_hw(value['source']))>=13,'TRAINING_GEOMETRY_INVALID')
    else: require('reference'not in value)
    if op=='replay':
        require('recipe'in value)
        fields(value['recipe'],('recipe_sha256','profile_id','profile_contract_sha256','core_sha256','runtime','device'),('execution_contract_sha256',))
        for key in ('recipe_sha256','profile_contract_sha256','core_sha256'): hash_value(value['recipe'][key])
        text(value['recipe']['profile_id']);require(isinstance(value['recipe']['runtime'],dict))
        fields(value['recipe']['device'],('uuid','driver','cuda_runtime'))
        for item in value['recipe']['device'].values(): text(item)
        if 'execution_contract_sha256'in value['recipe']:hash_value(value['recipe']['execution_contract_sha256'])
    else: require('recipe'not in value)
    if op=='export':
        require('result'in value and 'export'in value)
        fields(value['result'],('result_sha256','lease_id','lease_generation','lease_expires_wall','lease_state','owner_alive','backend'),('execution_contract_sha256',))
        hash_value(value['result']['result_sha256']);text(value['result']['backend'])
        text(value['result']['lease_id']);integer(value['result']['lease_generation']);number(value['result']['lease_expires_wall'])
        require(value['result']['lease_state'] in ('active','released','expired','unknown'))
        require(value['result']['owner_alive'] is None or type(value['result']['owner_alive'])is bool)
        if 'execution_contract_sha256'in value['result']:hash_value(value['result']['execution_contract_sha256'])
        fields(value['export'],('format','bits','icc_sha256','icc_policy','alpha'))
        e=value['export'];s=value['source'];number(e['alpha']);require(e['alpha']<=1)
        require(all(e[k]==s[k] for k in ('format','bits','icc_sha256')) and
            e['icc_policy']==('preserve_source'if s['icc_sha256'] is not None else'none'),'EXPORT_SOURCE_FORMAT_REQUIRED')
    else: require('result'not in value and 'export'not in value)
    if 'limits'in value:
        fields(value['limits'],(),('ram_bytes','commit_bytes','vram_bytes'))
        for item in value['limits'].values(): integer(item,1)
    if 'allow_cpu'in value:require(type(value['allow_cpu'])is bool)
    if 'device_uuid'in value:text(value['device_uuid'])
    for key in ('request_key','job_id','attempt_id'):
        if key in value:text(value[key])
    if 'existing'in value:
        fields(value['existing'],('state','rss_bytes','private_bytes','coverage'))
        require(value['existing']['state'] in ('none','same_operation','busy','unproven_exit'))
        require(value['existing']['coverage'] in ('full_tree','unknown'))
        integer(value['existing']['rss_bytes'],nullable=True);integer(value['existing']['private_bytes'],nullable=True)
    out=copy.deepcopy(value);out.setdefault('limits',{});out.setdefault('allow_cpu',False)
    return out

def normalize_snapshot(value):
    bounded(value)
    fields(value,('schema_version','machine_id','collected_wall','collected_monotonic','clock','sources','complete','physical','commit','product','gpus','cuda_available','diagnostics'))
    require(value['schema_version']==SNAPSHOT,'PLAN_SNAPSHOT_SCHEMA')
    text(value['machine_id']);number(value['collected_wall']);number(value['collected_monotonic'])
    fields(value['clock'],('wall','monotonic','machine_id'))
    number(value['clock']['wall']);number(value['clock']['monotonic']);text(value['clock']['machine_id'])
    fields(value['sources'],('physical','commit','gpu','product'))
    for item in value['sources'].values():text(item)
    fields(value['complete'],('physical','commit','gpu','product'))
    require(all(type(x)is bool for x in value['complete'].values()))
    fields(value['physical'],('total_bytes','available_bytes'))
    fields(value['commit'],('limit_bytes','used_bytes','headroom_bytes'))
    fields(value['product'],('rss_bytes','private_bytes','coverage'))
    require(value['product']['coverage'] in ('full_tree','unknown'))
    for group in ('physical','commit','product'):
        for key,item in value[group].items():
            if key!='coverage':integer(item,nullable=True)
    require(value['cuda_available'] is None or type(value['cuda_available'])is bool)
    require(isinstance(value['gpus'],list) and isinstance(value['diagnostics'],list))
    for g in value['gpus']:
        fields(g,('uuid','name','driver','cuda_runtime','total_bytes','nvml_free_bytes','raw_cuda_free_bytes','raw_cuda_uuid','allocator_reserved_bytes'))
        for key in ('uuid','name','driver'):text(g[key])
        for key in ('cuda_runtime','raw_cuda_uuid'):
            if g[key] is not None:text(g[key])
        for key in ('total_bytes','nvml_free_bytes','raw_cuda_free_bytes','allocator_reserved_bytes'):integer(g[key],nullable=True)
    for d in value['diagnostics']:fields(d,('code','detail'));text(d['code']);require(isinstance(d['detail'],str))
    return copy.deepcopy(value)

def intent(request):
    out={k:copy.deepcopy(v) for k,v in request.items() if k not in ('request_key','job_id','attempt_id','existing')}
    if 'result'in out:
        for key in ('lease_state','owner_alive','lease_expires_wall'):out['result'].pop(key)
    return out
