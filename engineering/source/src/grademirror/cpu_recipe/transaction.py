"""One target lock and small owned staging; reuse existing lock/atomic JSON."""
import hashlib,os,time,uuid
from pathlib import Path
from ..store import locked
from ..winproc import current_identity
from .primitives import *
from .legacy import load_legacy
from .validation import inspect_cpu_recipe,verify_cpu_recipe,complete
from .guard import metadata_api
def _checkpoint(stage,record,**detail):pass
def _uuid(value):
    try:return type(value)is str and str(uuid.UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False
def _cancel(control,attempt):
    if (control/'cancel'/f'{attempt}.json').is_file():raise RecipeError('PACK_CANCELLED',stage='copy',exit_code=8)
def _copy(data,dest,record,control):
    dest.parent.mkdir(parents=True,exist_ok=True);part=dest.with_name(dest.name+'.partial');require(not dest.exists() and not part.exists(),'STAGING_COLLISION')
    with part.open('xb')as f:
        for offset in range(0,len(data),64*1024):
            _cancel(control,record['attempt_id']);f.write(data[offset:offset+64*1024]);f.flush();os.fsync(f.fileno());_checkpoint('partial_copy',record,path=str(part),offset=offset)
    os.rename(part,dest)
def _valid(path,asset):
    if not path.is_file():return False
    try:verify_file(path,asset);return True
    except RecipeError:return False
@metadata_api
def pack_cpu_recipe(cpu_fast,out,request_key,*,cpu_fine=None):
    require(type(request_key)is str and 1<=len(request_key)<=128,'REQUEST_KEY',stage='pack')
    manifest,payloads=load_legacy(cpu_fast,cpu_fine);target=root(out,write=True)
    inputs=[Path(cpu_fast).absolute(),*([]if cpu_fine is None else[Path(cpu_fine).absolute()])]
    require(not any(target==p or target.is_relative_to(p)for p in inputs),'INPUT_OUTPUT_OVERLAP')
    if target.exists():require(target.is_dir(),'TARGET_NOT_DIRECTORY')
    else:target.mkdir(parents=True)
    control=target/'.pack';reject_reparse(control);control.mkdir(exist_ok=True)
    record_path=control/'record.json';intent=manifest['bundle_id']
    with locked(control/'target.lock',timeout=60):
        old=read(record_path)if record_path.is_file()else None
        if old:
            require(old['request_key']==request_key,'TARGET_KEY_CONFLICT');require(old['intent']==intent,'REQUEST_KEY_INTENT_CONFLICT')
            require(_uuid(old['operation_id'])and _uuid(old['attempt_id']),'OPERATION_IDENTITY')
        elif any(p.name not in ('.pack',)for p in target.iterdir()):raise RecipeError('UNOWNED_TARGET',stage='pack')
        if (target/'COMPLETE.json').is_file():
            saved,mark=complete(target);require(saved['bundle_id']==intent and old is not None and mark['operation_id']==old['operation_id'],'COMPLETE_INTENT')
            recovered=old['state']!='SUCCEEDED';atomic_json(record_path,dict(old,state='SUCCEEDED',stage='complete',recovered_from_complete=recovered))
            return dict(inspect_cpu_recipe(target),idempotent_hit=True,recovered_from_complete=recovered)
        operation=old['operation_id']if old else str(uuid.uuid4());attempt=str(uuid.uuid4())
        if old:
            failure=control/'failures'/f'{old["attempt_id"]}.json';failure.parent.mkdir(exist_ok=True)
            if not failure.exists():new_json(failure,dict(old,recovery_classification='INTERRUPTED_OR_FAILED_RETAINED'))
        record=dict(schema='cpu-recipe-pack-operation-v1',operation_id=operation,attempt_id=attempt,request_key=request_key,intent=intent,mode=manifest['mode'],state='RUNNING',stage='copy',owner=current_identity(),started_at=time.time())
        atomic_json(record_path,record);stage=control/'staging'/attempt;stage.mkdir(parents=True,exist_ok=False)
        try:
            for asset in manifest['assets']:
                _cancel(control,attempt);dest=inside(target,asset['path'])
                if dest.exists():require(_valid(dest,asset),'PUBLISHED_ASSET_CONFLICT');continue
                staged=inside(stage,asset['path']);data=payloads[asset['path']]
                # Reuse only a byte-verified file from this operation's managed
                # older staging. Partial/corrupt files are preserved, not adopted.
                if old:
                    prior=inside(control/'staging'/old['attempt_id'],asset['path'])
                    if _valid(prior,asset):data=read_bytes(prior,BUNDLE_LIMIT)
                _copy(data,staged,record,control);verify_file(staged,asset);dest.parent.mkdir(parents=True,exist_ok=True);reject_reparse(dest);os.rename(staged,dest)
            record['stage']='payloads_written';atomic_json(record_path,record);_checkpoint('payloads_written',record,target=str(target))
            mp=target/'manifest.json'
            if mp.exists():require(read(mp)==manifest,'PUBLISHED_MANIFEST_CONFLICT')
            else:new_json(mp,manifest)
            for asset in manifest['assets']:verify_file(inside(target,asset['path']),asset)
            _cancel(control,attempt);mark=dict(format='cpu-recipe-complete-v1',bundle_id=intent,math_id=manifest['math_id'],manifest=file_record(mp,name='manifest.json'),operation_id=operation,attempt_id=attempt)
            atomic_json(target/'COMPLETE.json',mark);_checkpoint('complete_committed',record,target=str(target));complete(target)
            atomic_json(record_path,dict(record,state='SUCCEEDED',stage='complete'));return dict(inspect_cpu_recipe(target),idempotent_hit=False,recovered_from_complete=False)
        except BaseException as e:
            failure=control/'failures'/f'{attempt}.json';failure.parent.mkdir(exist_ok=True)
            if not failure.exists():new_json(failure,dict(record,error_type=type(e).__name__,error=str(e),state='CANCELLED'if isinstance(e,RecipeError)and e.code=='PACK_CANCELLED'else'FAILED'))
            atomic_json(record_path,dict(record,state='CANCELLED'if isinstance(e,RecipeError)and e.code=='PACK_CANCELLED'else'FAILED'));raise
@metadata_api
def cancel_pack_attempt(directory,operation_id,attempt_id):
    require(_uuid(operation_id)and _uuid(attempt_id),'OPERATION_IDENTITY');p=root(directory,write=True);control=p/'.pack';record=read(control/'record.json')
    require((record['operation_id'],record['attempt_id'])==(operation_id,attempt_id)and record['state']=='RUNNING','STALE_ATTEMPT',stage='cancel')
    cancel=control/'cancel';reject_reparse(cancel);cancel.mkdir(exist_ok=True);path=cancel/(attempt_id+'.json')
    if not path.exists():atomic_json(path,dict(operation_id=operation_id,attempt_id=attempt_id,requested_at=time.time()))
    return dict(cancel_requested=True,operation_id=operation_id,attempt_id=attempt_id,deleted_files=0)
@metadata_api
def relink_cpu_recipe_source(directory,source):
    p=root(directory,write=True);m,mark=complete(p);source=Path(source).expanduser().absolute();reject_reparse(source);expected=m['mathematical']['inputs']['source_identity']
    require(source.is_file() and source.stat().st_size==expected['file_size_bytes'],'SOURCE_LENGTH',stage='relink');b=file_record(source);require(b['sha256']==expected['file_sha256'],'SOURCE_HASH',stage='relink')
    with locked(p/'.pack/target.lock',timeout=60):
        value=read(p/'locator.json')if(p/'locator.json').is_file()else dict(version=1,source=None,reference=None)
        value['source']=dict(b,last_verified_at=time.time());atomic_json(p/'locator.json',value)
    return dict(inspect_cpu_recipe(p),relink=dict(status='HASH_VERIFIED',bytes=b['bytes'],sha256=b['sha256'],decode=False))
