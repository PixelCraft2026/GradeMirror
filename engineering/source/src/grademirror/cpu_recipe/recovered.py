"""Explicit fast v3 saved-fit provenance. Metadata/bytes only, never a fit."""
import copy
import hashlib
import io
import math
import os
import struct
import uuid
import zipfile
from pathlib import Path
from ..common import atomic_json, canonical_json
from ..store import locked
from .primitives import root, read, read_bytes, fields, require, digest, inside, file_record, verify_file, reject_reparse
from .assets import parse_npz
from .guard import metadata_api

SCHEMA = 'saved-failed-fit-source-v1'
ARCHIVE = '460ec997cac00d6889e35c3938c2787f2abf262ca4b34380be1ebebbfca8255c'
SEMANTIC = 'f060fed595526cd0a0f3dd9d7cfef5a17a93cb4bd122c5950f4fd061688048e0'
PRODUCER = '05cdc5a102ec379fd690631ee2e171958afb970d1006d60f910a7c321ebd0c97'
ASSETS = {'r0/parameters.npz', 'provenance/fit-intent.json', 'provenance/fit-receipt.json', 'provenance/recovery-source.json'}


def parameter_structure(data):
    require(type(data) is bytes and len(data)<=1024**2,'RECOVERY_PARAMETER_CONTAINER_CAP')
    # Parse limits before expansion, then independently reject NaN/Inf by bits.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        require(len(entries) <= 128 and sum(x.file_size for x in entries) <= 1024**2, 'RECOVERY_PARAMETER_EXPANSION')
        for e in entries:
            require(e.file_size <= 256*1024 and e.compress_type in (0, 8) and not e.flag_bits & 1, 'RECOVERY_PARAMETER_MEMBER')
            raw = archive.read(e)
            require(raw[:6] == b'\x93NUMPY' and raw[6:8] in (b'\x01\x00',b'\x02\x00',b'\x03\x00'), 'RECOVERY_NPY_MAGIC')
            width = 2 if raw[6] == 1 else 4
            n = int.from_bytes(raw[8:8+width], 'little')
            require(0 < n <= 16384 and 8+width+n <= len(raw), 'RECOVERY_NPY_HEADER')
            payload = raw[8+width+n:]
            require(len(payload)%4 == 0 and all((v[0] & 0x7f800000) != 0x7f800000 for v in struct.iter_unpack('<I',payload)), 'RECOVERY_NONFINITE_BITS')
    parsed = parse_npz(data,'r0')
    require(len(parsed['members']) == 15, 'RECOVERY_PARAMETER_KEYS')
    return parsed


def parameter_bytes(data):
    require(type(data) is bytes and len(data) == 49768 and hashlib.sha256(data).hexdigest() == ARCHIVE, 'RECOVERY_PARAMETER_ARCHIVE')
    parsed=parameter_structure(data)
    require(parsed['value_sha256']==SEMANTIC,'RECOVERY_PARAMETER_SEMANTIC')
    return parsed


def source_structure(s):
    fields(s, ('schema','scope','session','nonce','original','adoption','authority','new_executor','current_runtime','source_id'))
    require(s['schema'] == SCHEMA and s['scope'] == 'GM064-T34-R3' and s['source_id'] == digest({k:v for k,v in s.items() if k!='source_id'}), 'RECOVERY_SOURCE_SCHEMA')
    require(type(s['session']) is str and uuid.UUID(s['session']).version == 4 and uuid.UUID(s['session']).hex==s['session'] and
            type(s['nonce']) is str and len(s['nonce']) == 32 and set(s['nonce'])<=set('0123456789abcdef'), 'RECOVERY_SESSION_NONCE')
    old=s['original'];fields(old, ('context','fit_receipt','fit_wait','bindings','failure','D3'))
    c=old['context'];receipt=old['fit_receipt'];wait=old['fit_wait']
    require(c['executor_build']['sha256'] == receipt['producer_build'] == PRODUCER and digest(c['executor_build']['files']) == PRODUCER, 'RECOVERY_ORIGINAL_PRODUCER')
    require(c['phase']=='fit' and c['mode']=='fast' and c['TEST_ONLY'] is False and receipt['status']=='COMPLETE' and receipt['parameter_sha256']==SEMANTIC and
            receipt['counts']==dict(fit=1,updates=1000,forward=2001,model_construct=1,optimizer_construct=1), 'RECOVERY_ORIGINAL_FIT')
    require(wait['exit_code']==7 and wait['actual_worker_handle_wait'] is True and wait['tree_empty'] is True and
            old['failure']==dict(fit_exit=7,T3_exit=1,final_runtime='NATIVE_RUNTIME_UNKNOWN_PATHS',loaded_bytes_and_cause='UNKNOWN'), 'RECOVERY_FAILURE_RETAINED')
    require(c['source']['sha256']=='64db59daa73c460dfdaa498c3ab29c865ea3032a39cd4e6d32beda092de4b60c' and
            c['reference']['sha256']=='d2dd38ad47fd3ee484e07c087d2783edb10cddc8f50afacf0d78a01efece6358' and
            c['source_identity']['shape']==[6000,4000,3] and c['reference_identity']['shape']==[1536,1024,3] and
            c['settings']['updates']==1000 and c['settings']['seed']==43, 'RECOVERY_ORIGINAL_INPUTS')
    require(s['adoption']['status']=='USER_ADOPTED_SCOPED_RECOVERY_CONTRACT' and s['adoption']['new_fit']==0 and
            s['adoption']['T3_recovery_R0']==1 and s['adoption']['new_science_window_seconds']==28800 and
            {x['sha256'] for x in s['adoption']['bindings']}=={
                '2e5b1f308471d559d259506123c9367d289d348089515567851f6fd9bb628f7b',
                'dd02a50ef3e085f7ae8ecf3b70896e2f0680df29dc82a78ab929ee667b790714'}, 'RECOVERY_ADOPTION')
    require(digest(s['new_executor']['files'])==s['new_executor']['sha256'] and s['current_runtime']['status']=='KNOWN_EXACT_RUNTIME', 'RECOVERY_NEW_EXECUTOR')
    require(type(old['bindings']) is dict and old['bindings'] and len(old['D3'])==3, 'RECOVERY_SOURCE_BINDINGS')
    return s


def mathematical(source, parsed):
    from .identity import math_closure
    c=source['original']['context']
    return dict(profile=c['profile'],settings=c['settings'],
        normalization={name:dict(dtype=c[name+'_identity']['dtype'],denominator=255,dtype_after='float32',resize='INTER_AREA',quantization='none') for name in ('source','reference')},
        inputs={name+'_identity':c[name+'_identity'] for name in ('source','reference')},color_policy=c['color_policy'],
        color_declaration='gamma-encoded-sRGB-float32',r0_parameters={k:v for k,v in parsed.items() if k!='expanded_bytes'},math_files=math_closure('fast'))


def structure(m):
    from .generated import MANIFEST_FIELDS
    fields(m, MANIFEST_FIELDS|{'recovery'})
    require(m['format']=='grademirror.cpu_recipe_bundle' and type(m['version']) is int and m['version']==3 and m['mode']=='fast', 'RECOVERED_FAST_VERSION')
    s=source_structure(m['recovery']);c=s['original']['context']
    require(m['producer_build']==c['executor_build'] and m['runtime_requirements']==c['runtime'], 'RECOVERY_OLD_PRODUCER_RUNTIME')
    fields(m['provenance'],('fit_intent_sha256','job_id','attempt_id','producer','TEST_ONLY'))
    require(m['provenance']['producer']=='original_cpu_fit_loop_saved_after_failed_runtime' and m['provenance']['TEST_ONLY'] is False, 'RECOVERY_PROVENANCE')
    require(m['qualification']==dict(status='RECOVERED_SOURCE_PENDING_SCOPED_QUALIFICATION',execution_qualified=False,general_input_qualified=False,
            CPU_ENABLED=False,AUTO_ENABLED=False,PRODUCTION_ENABLED=False,D01_extended=False,user_visual='PENDING'), 'RECOVERY_QUALIFICATION')
    require(type(m['assets']) is list and len(m['assets'])==4 and {x['path'] for x in m['assets']}==ASSETS, 'RECOVERY_ASSETS')
    for item in m['assets']:
        fields(item,('path','bytes','sha256'));require(type(item['bytes']) is int and 0<item['bytes']<=8*1024**2,'RECOVERY_ASSET_SIZE')
    require(digest(m['mathematical'])==m['math_id'] and digest({k:v for k,v in m.items() if k!='bundle_id'})==m['bundle_id'], 'RECOVERY_SEMANTIC_ID')
    return m


def complete(directory, *, pending=None):
    p=root(directory);mark=read(p/'COMPLETE.json') if pending is None else pending
    fields(mark,('format','bundle_id','math_id','manifest','operation_id','attempt_id'))
    require(mark['format']=='cpu-recipe-complete-recovered-fast-v3' and mark['manifest']['path']=='manifest.json', 'RECOVERY_COMPLETE_VERSION')
    verify_file(p/'manifest.json',mark['manifest']);m=structure(read(p/'manifest.json'))
    require((mark['bundle_id'],mark['math_id'])==(m['bundle_id'],m['math_id']), 'RECOVERY_COMPLETE_ID')
    for item in m['assets']:verify_file(inside(p,item['path']),item)
    s=read(p/'provenance/recovery-source.json');require(s==m['recovery'],'RECOVERY_SOURCE_ASSET')
    parsed=parameter_bytes(read_bytes(p/'r0/parameters.npz',1024**2))
    require(m['mathematical']==mathematical(s,parsed), 'RECOVERY_MATH_CHANGED')
    receipt=read(p/'provenance/fit-receipt.json');intent=read(p/'provenance/fit-intent.json')
    require(receipt==s['original']['fit_receipt'] and digest(intent)==m['provenance']['fit_intent_sha256'] and
            all(receipt[k]==m['provenance'][k] for k in ('job_id','attempt_id')), 'RECOVERY_FIT_ASSETS')
    total=0
    for f in p.rglob('*'):
        reject_reparse(f)
        if f.is_file():
            require(f.stat().st_size<=8*1024**2,'RECOVERY_MEMBER_LIMIT');total+=f.stat().st_size
    require(total<=16*1024**2,'RECOVERY_BUNDLE_LIMIT')
    return m,mark


@metadata_api
def inspect(directory, *, runtime_inventory=None):
    from .validation import _locator
    from .identity import runtime_status
    p=root(directory);m,mark=complete(p)
    return dict(format=m['format'],version=3,mode='fast',bundle_id=m['bundle_id'],math_id=m['math_id'],
        execution_build_id=m['recovery']['new_executor']['sha256'],original_producer_build_id=m['producer_build']['sha256'],
        persistent_complete=True,assets_verified=True,state='PERSISTENT_COMPLETE',source_status=_locator(p,'source'),reference_status=_locator(p,'reference'),
        runtime_compatibility=runtime_status(m['recovery']['current_runtime'],runtime_inventory),execution_qualification=m['qualification'],
        resident=False,availability='REPLAY_REQUIRED',operation_id=mark['operation_id'],attempt_id=mark['attempt_id'])


@metadata_api
def publish(directory,data,source,request_key,*,checkpoint=lambda point:None):
    from .transaction import _copy
    source_structure(source);parsed=parameter_bytes(data)
    require(type(request_key) is str and 0<len(request_key)<=128,'RECOVERY_REQUEST_KEY')
    c=source['original']['context'];receipt=source['original']['fit_receipt']
    intent=dict(schema='cpu-first-fit-intent-v1',semantics=source['original']['bindings']['intent_semantics'],job_id=receipt['job_id'],attempt_id=receipt['attempt_id'])
    payloads={'r0/parameters.npz':data,'provenance/fit-intent.json':canonical_json(intent)+b'\n',
              'provenance/fit-receipt.json':canonical_json(receipt)+b'\n','provenance/recovery-source.json':canonical_json(source)+b'\n'}
    m=dict(format='grademirror.cpu_recipe_bundle',version=3,mode='fast',mathematical=mathematical(source,parsed),
        runtime_requirements=c['runtime'],producer_build=c['executor_build'],recovery=source,
        qualification=dict(status='RECOVERED_SOURCE_PENDING_SCOPED_QUALIFICATION',execution_qualified=False,general_input_qualified=False,
            CPU_ENABLED=False,AUTO_ENABLED=False,PRODUCTION_ENABLED=False,D01_extended=False,user_visual='PENDING'),
        assets=[dict(path=k,bytes=len(v),sha256=hashlib.sha256(v).hexdigest()) for k,v in sorted(payloads.items())],
        provenance=dict(fit_intent_sha256=digest(intent),job_id=receipt['job_id'],attempt_id=receipt['attempt_id'],
                        producer='original_cpu_fit_loop_saved_after_failed_runtime',TEST_ONLY=False))
    m['math_id']=digest(m['mathematical']);m['bundle_id']=digest(m);structure(m)
    target=root(directory,write=True);target.mkdir(parents=True,exist_ok=True);control=target/'.pack';control.mkdir(exist_ok=True)
    with locked(control/'target.lock',timeout=60):
        rp=control/'record.json';prior=read(rp) if rp.is_file() else None
        if prior:require(prior['request_key']==request_key and prior['intent']==m['bundle_id'],'RECOVERY_SAME_KEY_CONFLICT')
        if (target/'COMPLETE.json').is_file():
            old,_=complete(target);require(old==m and prior is not None,'RECOVERY_COMPLETE_CONFLICT')
            atomic_json(rp,dict(prior,state='SUCCEEDED',recovered_from_complete=True))
            return dict(inspect(target),idempotent_hit=True)
        require(not any(p.name not in ('.pack','r0','provenance','manifest.json') for p in target.iterdir()),'RECOVERY_UNOWNED_TARGET')
        record=prior or dict(request_key=request_key,intent=m['bundle_id'],operation_id=str(uuid.uuid4()),attempt_id=str(uuid.uuid4()))
        atomic_json(rp,dict(record,state='RUNNING'))
        stage=control/'staging'/str(uuid.uuid4());stage.mkdir(parents=True)
        try:
            for asset in m['assets']:
                dest=inside(target,asset['path'])
                if dest.exists():verify_file(dest,asset);continue
                staged=inside(stage,asset['path']);_copy(payloads[asset['path']],staged,record,control);verify_file(staged,asset)
                dest.parent.mkdir(parents=True,exist_ok=True);reject_reparse(dest);os.rename(staged,dest)
            if (target/'manifest.json').exists():require(read(target/'manifest.json')==m,'RECOVERY_MANIFEST_CONFLICT')
            else:atomic_json(target/'manifest.json',m)
            checkpoint('bundle_before_complete')
            mark=dict(format='cpu-recipe-complete-recovered-fast-v3',bundle_id=m['bundle_id'],math_id=m['math_id'],
                      manifest=file_record(target/'manifest.json',name='manifest.json'),operation_id=record['operation_id'],attempt_id=record['attempt_id'])
            complete(target,pending=mark);atomic_json(target/'COMPLETE.json',mark);checkpoint('bundle_after_complete')
            atomic_json(rp,dict(record,state='SUCCEEDED'));return dict(inspect(target),idempotent_hit=False)
        except BaseException as error:
            atomic_json(control/'failures'/(uuid.uuid4().hex+'.json'),dict(error=repr(error),complete_exists=(target/'COMPLETE.json').is_file()))
            atomic_json(rp,dict(record,state='INTERRUPTED'));raise
