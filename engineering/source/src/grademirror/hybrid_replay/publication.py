"""Hybrid completion in the existing operation lock/journal, no second Store."""
import os, shutil, time
from pathlib import Path
from ..common import atomic_json, strict_load, sha_file
from ..winproc import identity_alive
from . import BACKEND_ID, MANIFEST_SCHEMA, LEASE_SCHEMA, VERSION_FIELDS
from .contract import require, digest, parent, read_manifest, versions, RUNTIME, parent_bindings, cpu_bindings, qualification_scope
from .contract import read_execution_manifest


def is_hybrid(spec):
    from ..hybrid_fit import is_fit_type
    if is_fit_type(spec):return True
    return spec.get('kind')=='replay' and spec.get('semantics',{}).get('backend_id')==BACKEND_ID


def make_package(path, spec, recipe_dir, ready):
    recipe,assets=parent(recipe_dir);sem=spec['semantics'];out=Path(sem['output_root'])
    temp=out/('.hybrid-private-'+spec['attempt_id']);temp.mkdir(exist_ok=False)
    for n in assets:shutil.copyfile(Path(recipe_dir)/n,temp/n)
    m=dict(schema_version=MANIFEST_SCHEMA,backend_id=BACKEND_ID,
        execution_contract_version=sem['execution_contract_version'],
        execution_contract_sha256=sem['execution_contract_sha256'],
        parent_recipe_sha256=recipe['recipe_sha256'],assets=assets,
        source_identity=recipe['source_identity'],reference_identity=recipe['reference_identity'],
        ranges=recipe['postprocess']['ranges'],device=recipe['device'],runtime=RUNTIME,
        R0_runtime=recipe['runtime'],parent_bindings=parent_bindings(recipe),cpu_bindings=cpu_bindings(),
        color_policy=recipe['color_policy'],core_rows=512,results=ready['hashes'],
        quantization=ready['quantization'],scope=qualification_scope())
    m['manifest_sha256']=digest(m)
    atomic_json(temp/'execution-manifest.json',m)
    atomic_json(temp/'published.json',dict(schema_version=MANIFEST_SCHEMA,
        manifest_sha256=m['manifest_sha256'],manifest_file_sha256=sha_file(temp/'execution-manifest.json')))
    read_manifest(temp/'execution-manifest.json')
    lease=dict(handle_id=ready['handle_id'],generation=1,owner_instance_id=ready['session_id'],
        project_revision=sem['revision'],recipe_sha256=sem['recipe_sha256'],state='RESIDENT',
        expires_at=time.time()+300,bytes_reserved=ready['resident_bytes'],capabilities=['blend','export'],
        lease_schema_version=LEASE_SCHEMA,backend_id=BACKEND_ID,
        execution_contract_sha256=sem['execution_contract_sha256'],execution_manifest_sha256=m['manifest_sha256'])
    atomic_json(path/'hybrid-prepared.json',dict(job_id=spec['job_id'],attempt_id=spec['attempt_id'],
        intent_sha256=spec['intent_sha256'],private_package=str(temp),
        published_package=str(out/'execution'),manifest_sha256=m['manifest_sha256'],lease=lease))
    return lease


def verify(manifest,spec,path,by_path,*,private_directory=None):
    from ..hybrid_fit import is_fit_type
    if is_fit_type(spec):
        from ..hybrid_fit.publication import verify as verify_new
        return verify_new(manifest,spec,path,by_path,private_directory=private_directory)
    from ..gm004a_operations import OperationStore
    require(is_hybrid(spec),'HYBRID_OPERATION_KIND')
    sem=spec['semantics'];prep=strict_load(path/'hybrid-prepared.json')
    require(all(prep.get(k)==spec[k] for k in('job_id','attempt_id','intent_sha256')),'HYBRID_PREPARED_BINDING')
    directory=Path(sem['output_root'])/'execution'
    require(Path(prep['published_package'])==directory,'HYBRID_PUBLISH_PATH')
    m,recipe=read_manifest((directory if private_directory is None else private_directory)/'execution-manifest.json')
    lease=manifest['result_lease'];versions(lease)
    require(lease==prep['lease'] and lease['execution_manifest_sha256']==m['manifest_sha256'] and
        m['parent_recipe_sha256']==sem['recipe_sha256'] and
        m['execution_contract_sha256']==sem['execution_contract_sha256'], 'HYBRID_COMMIT_BINDING')
    required=('compute-result.json','cleanup.json','replay-identity.json','hybrid-ready.json',
              'executor-result.json','executor-cleanup.json','execution-plan.json','R0/result.json','R0/wait.json',
              'CPU/wait.json','CPU/actual.json','session/source-decode.json')
    data={n:OperationStore._receipt(by_path,path/n) for n in required}
    cpu=data['executor-result.json'];cw=data['executor-cleanup.json'];r0=data['R0/result.json'];rw=data['R0/wait.json']
    require(cpu['coverage_complete']is True and cpu['sender_complete']is True and cpu['readonly']is True and
        cpu['hashes']==cpu['sender_hashes']=={k:m['results'][k] for k in('OP','LO','LP','Y')} and
        cpu['core_rows']==512 and cpu['shape']==m['source_identity']['shape'], 'HYBRID_CPU_COMPLETION')
    for receipt in(cw,rw,data['CPU/wait.json']):
        require(receipt.get('actual_wait')is True and receipt.get('actual_worker_handle_wait')is True and
            receipt.get('tree_empty')is True and receipt.get('exit_code')==0 and receipt.get('forced')is False,
            'HYBRID_ACTUAL_WAIT_REQUIRED')
    require(r0.get('fit')==0 and r0.get('render')==1 and r0.get('tiles')==sem['tiles'] and
        r0.get('model_forward_batches')==sem['tiles'] and r0['O_sha256']==m['results']['O'] and
        m['results']['O']==recipe['postprocess']['hashes']['O'] and
        m['results']['OP']==recipe['postprocess']['hashes']['OP'], 'HYBRID_PARENT_HASH_BRIDGE')
    require(data['hybrid-ready.json']['hashes']==m['results'] and
        data['hybrid-ready.json']['quantization']==m['quantization'] and
        data['session/source-decode.json']['identity']==m['source_identity'], 'HYBRID_READY_BRIDGE')
    expected=data['execution-plan.json']['CPU_budget']
    actual=data['CPU/actual.json']
    require(actual.get('status')=='COMPLETE' and all(0<=v<=expected[k] for k,v in actual['actual'].items()),
        'HYBRID_CPU_BUDGET')
    require(data['compute-result.json']['lease']==lease and
        data['compute-result.json']['float32_sha256']==m['results']['Y'] and
        data['replay-identity.json']['execution_manifest_sha256']==m['manifest_sha256'], 'HYBRID_COMPUTE_BINDING')


def _manifest(path,spec,artifacts,lease):
    from ..gm004a_operations import artifact
    package=Path(spec['semantics']['output_root'])/'execution'
    from ..hybrid_fit import is_fit_type,ASSETS
    names=(*ASSETS,'execution-manifest.json','published.json')if is_fit_type(spec)else('recipe.json','parameters.npz','p3.npz','execution-manifest.json','published.json')
    items=list(artifacts)+[artifact(package/n)for n in names]
    return dict(state='COMPLETE',job_id=spec['job_id'],attempt_id=spec['attempt_id'],
        intent_sha256=spec['intent_sha256'],kind=spec['kind'],artifacts=items,result_lease=lease)


def recover_locked(store,path,spec,current):
    if not is_hybrid(spec):return None
    target=Path(spec['semantics']['output_root'])/'execution'
    if not(target/'published.json').is_file():return None
    from ..gm004a_operations import _append
    prep=strict_load(path/'hybrid-prepared.json');prepared=strict_load(path/'hybrid-business-ready.json')
    require(all(prepared.get(k)==spec[k] for k in('job_id','attempt_id','intent_sha256')),
            'HYBRID_RECOVERY_INTENT')
    manifest=_manifest(path,spec,prepared['artifacts'],prep['lease'])
    store._verify_manifest(manifest,spec,path)
    if (path/'commit.json').is_file():require(strict_load(path/'commit.json')==manifest,'HYBRID_COMMIT_CONFLICT')
    else:atomic_json(path/'commit.json',manifest)
    if current['state']!='SUCCEEDED':
        current.update(state='SUCCEEDED',stage='recovered',exit_code=0,error_code=None,result_lease=prep['lease'])
        _append(path,current,'HYBRID_COMPLETE_PUBLICATION_RECOVERED')
    return current


def commit_locked(store,path,spec,current,artifacts,lease):
    recovered=recover_locked(store,path,spec,current)
    if recovered is not None:return recovered
    if current['state']!='RUNNING' or(path/'cancel.signal').exists():
        from ..common import GMError
        raise GMError('CANCELLED','Cancel precedes hybrid publication',8)
    prep=strict_load(path/'hybrid-prepared.json')
    require(lease==prep['lease'],'HYBRID_LEASE_CHANGED')
    private=Path(prep['private_package']);target=Path(spec['semantics']['output_root'])/'execution'
    require(private.parent==target.parent and private.name=='.hybrid-private-'+spec['attempt_id'] and
            not target.exists(),'HYBRID_PUBLICATION_PATH')
    # Complete receipts and package identities are checked before entering this
    # function and again in recovery. This move is the cancellation linearization.
    read_execution_manifest(private/'execution-manifest.json')
    by_path={}
    for item in artifacts:
        p=Path(item['path'])
        require(p.is_absolute() and p.is_file() and p.stat().st_size==item['bytes'] and
                sha_file(p)==item['sha256'],'HYBRID_PREPUBLISH_ARTIFACT')
        by_path[os.path.normcase(str(p))]=item
    verify(dict(result_lease=lease),spec,path,by_path,private_directory=private)
    atomic_json(path/'hybrid-business-ready.json',dict(job_id=spec['job_id'],attempt_id=spec['attempt_id'],
        intent_sha256=spec['intent_sha256'],artifacts=artifacts))
    os.rename(private,target)
    return recover_locked(store,path,spec,current)


def residency(path,spec,state):
    lease=state.get('result_lease');resident=False
    try:
        actual=strict_load(path/'session/lease.json')
        from ..lease import validate_lease_handle, BINDING_FIELDS
        binding={k:lease[k] for k in BINDING_FIELDS|VERSION_FIELDS}
        validate_lease_handle(actual,binding=binding)
        resident=(state['state']=='SUCCEEDED' and not(path/'hybrid-release.json').exists() and
            all(identity_alive(strict_load(path/'session'/n))is True for n in('owner.json','host-process.json','host-entry.json')))
    except (OSError,ValueError,KeyError,TypeError):pass
    except Exception as exc:
        from ..common import GMError
        if not isinstance(exc,GMError):raise
    result=dict(state,backend_id=BACKEND_ID,persistent_complete=state['state']=='SUCCEEDED',resident=resident,
        binding={k:lease[k] for k in ('handle_id','generation','owner_instance_id','project_revision','recipe_sha256',*sorted(VERSION_FIELDS))} if resident else None,
        execution_manifest=str(Path(spec['semantics']['output_root'])/'execution/execution-manifest.json'),
        execution_manifest_sha256=lease['execution_manifest_sha256'] if lease else None,
        CPU_QUALIFIED=False,AUTO_SELECTION=False,PRODUCTION_ACTIVATION=False)
    from .monitor_completion import project
    return project(path,spec,result,validated=True)
