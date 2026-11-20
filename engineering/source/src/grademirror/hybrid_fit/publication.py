"""Typed completion checks beneath the existing atomic hybrid transaction."""
import shutil,time
from pathlib import Path
from ..common import atomic_json,strict_load,sha_file
from ..hybrid_replay import BACKEND_ID,CONTRACT_VERSION,LEASE_SCHEMA
from ..hybrid_replay.contract import require,digest,RUNTIME,cpu_bindings,qualification_scope,versions
from . import FIT_CONTRACT,MANIFEST_SCHEMA,ASSETS,is_fit_type
from .contract import read_recipe,read_manifest

def make_package(path,spec,recipe_dir,ready):
    recipe,a=read_recipe(recipe_dir);sem=spec['semantics'];out=Path(sem['output_root'])
    data=strict_load(path/'data-binding.json');temp=out/('.hybrid-private-'+spec['attempt_id']);temp.mkdir(exist_ok=False)
    from .tiers import for_spec,consistent
    core_rows=consistent(for_spec(spec),data['core_rows'],ready['core_rows'])
    (temp/'r0').mkdir()
    for n in ASSETS:shutil.copyfile(Path(recipe_dir)/n,temp/n)
    fit=spec['kind']=='fit'
    m=dict(schema_version=MANIFEST_SCHEMA,fit_contract=FIT_CONTRACT,backend_id=BACKEND_ID,
        execution_contract_version=CONTRACT_VERSION,execution_contract_sha256=sem['execution_contract_sha256'],
        parent_recipe_sha256=recipe['recipe_sha256'],assets=a,source_identity=recipe['source_identity'],reference_identity=recipe['reference_identity'],
        ranges=recipe['postprocess']['ranges'],device=recipe['device'],runtime=RUNTIME,R0_runtime=recipe['R0_runtime'],
        color_policy=recipe['color_policy'],cpu_bindings=cpu_bindings(),core_rows=core_rows,results=ready['hashes'],quantization=ready['quantization'],
        scope=qualification_scope(),execution_counts=dict(fit=int(fit and "saved_r0" not in sem),render=1,scalar_CG=3 if fit and 'saved_post' not in sem else 0,range_discovery=int(fit and 'saved_post' not in sem)),data_binding_sha256=data['data_binding_sha256'])
    m['manifest_sha256']=digest(m);atomic_json(temp/'execution-manifest.json',m)
    atomic_json(temp/'published.json',dict(schema_version=MANIFEST_SCHEMA,manifest_sha256=m['manifest_sha256'],
        manifest_file_sha256=sha_file(temp/'execution-manifest.json'),intent_sha256=spec['intent_sha256'],job_id=spec['job_id'],
        attempt_id=spec['attempt_id'],data_binding_sha256=m['data_binding_sha256']))
    read_manifest(temp/'execution-manifest.json')
    lease=dict(handle_id=ready['handle_id'],generation=1,owner_instance_id=ready['session_id'],project_revision=sem['revision'],
        recipe_sha256=recipe['recipe_sha256'],state='RESIDENT',expires_at=time.time()+300,bytes_reserved=ready['resident_bytes'],capabilities=['blend','export'],
        lease_schema_version=LEASE_SCHEMA,backend_id=BACKEND_ID,execution_contract_sha256=sem['execution_contract_sha256'],execution_manifest_sha256=m['manifest_sha256'])
    atomic_json(path/'hybrid-prepared.json',dict(job_id=spec['job_id'],attempt_id=spec['attempt_id'],intent_sha256=spec['intent_sha256'],
        private_package=str(temp),published_package=str(out/'execution'),manifest_sha256=m['manifest_sha256'],lease=lease))
    return lease

def verify(manifest,spec,path,by_path,*,private_directory=None):
    from ..gm004a_operations import OperationStore
    require(is_fit_type(spec),'HYBRID_FIT_OPERATION_KIND');sem=spec['semantics'];prep=strict_load(path/'hybrid-prepared.json')
    from .tiers import for_spec,consistent
    core_rows=for_spec(spec)
    require(all(prep.get(k)==spec[k]for k in ('job_id','attempt_id','intent_sha256')),'HYBRID_FIT_PREPARED_BINDING')
    directory=Path(sem['output_root'])/'execution'
    require(Path(prep['published_package'])==directory,'HYBRID_FIT_PUBLISH_PATH')
    root=directory if private_directory is None else private_directory;m,recipe=read_manifest(root/'execution-manifest.json')
    marker=strict_load(root/'published.json')
    consistent(core_rows,m['core_rows'])
    require(all(marker[k]==spec[k]for k in ('job_id','attempt_id','intent_sha256')),'HYBRID_FIT_PUBLISHED_INTENT')
    lease=manifest['result_lease'];versions(lease)
    require(lease==prep['lease']and lease['execution_manifest_sha256']==m['manifest_sha256']and
        lease['recipe_sha256']==recipe['recipe_sha256']and m['execution_contract_sha256']==sem['execution_contract_sha256'],'HYBRID_FIT_COMMIT_BINDING')
    require(recipe['source_identity']['file_sha256']==sem['source_sha256']and recipe['reference_identity']['file_sha256']==sem['reference_sha256'], 'HYBRID_FIT_COMMIT_INPUTS')
    required=['compute-result.json','cleanup.json','replay-identity.json','hybrid-ready.json','executor-result.json','executor-cleanup.json',
        'execution-plan.json','data-binding.json','R0/result.json','R0/wait.json','CPU/wait.json','CPU/actual.json','session/source-decode.json','reference-released.json']
    if spec['kind']=='fit':
        if "saved_r0" not in sem:required+=['R0/training-record.json']
        required+=['saved-post-consumption.json'] if 'saved_post' in sem else ['P3/result.json','P3/wait.json','P3/solver-cleanup.json','P3/solver-individual-wait.json']
    data={n:OperationStore._receipt(by_path,path/n)for n in required};cpu=data['executor-result.json'];r0=data['R0/result.json']
    require(cpu.get('coverage_complete')is True and cpu.get('sender_complete')is True and cpu.get('readonly')is True and
        cpu['hashes']==cpu['sender_hashes']=={k:m['results'][k]for k in ('OP','LO','LP','Y')}and cpu['core_rows']==core_rows and
        cpu['shape']==m['source_identity']['shape'],'HYBRID_FIT_CPU_COMPLETION')
    for key in ('executor-cleanup.json','R0/wait.json','CPU/wait.json')+ (('P3/wait.json',)if spec['kind']=='fit' and 'saved_post' not in sem else()):
        row=data[key];require(row.get('actual_wait')is True and row.get('actual_worker_handle_wait')is True and row.get('tree_empty')is True and
            row.get('exit_code')==0 and row.get('forced')is False,'HYBRID_FIT_ACTUAL_WAIT_REQUIRED')
    fit=int(spec['kind']=='fit' and "saved_r0" not in sem);tiles=data['execution-plan.json']['tiles']
    require(all(type(r0.get(k))is int for k in ('fit','render','tiles','model_forward_batches'))and
        r0.get('fit')==fit and r0.get('render')==1 and r0.get('tiles')==tiles and r0.get('model_forward_batches')==tiles+2001*fit and
        r0.get('parameter_sha256')==recipe['r0_parameter_sha256']and r0.get('O_sha256')==m['results']['O'],'HYBRID_FIT_R0_PROOF')
    bound=data['data-binding.json'];require(bound['data_binding_sha256']==digest({k:v for k,v in bound.items()if k!='data_binding_sha256'})==m['data_binding_sha256']and
        bound['recipe_sha256']==recipe['recipe_sha256']and bound['ranges']==recipe['postprocess']['ranges'],'HYBRID_FIT_DATA_BINDING')
    consistent(core_rows,bound['core_rows'],data['execution-plan.json']['core_rows'],strict_load(path/'CPU-payload.json')['manifest']['core_rows'],
        cpu['binding']['core_rows'],data['hybrid-ready.json']['core_rows'])
    if 'resource_intent'in sem:
        from ..resource_execution.binding import validate
        consistent(core_rows,validate(path,spec)['core_rows'])
    require(data['hybrid-ready.json']['hashes']==m['results']and data['hybrid-ready.json']['quantization']==m['quantization']and
        data['session/source-decode.json']['identity']==m['source_identity']and data['reference-released.json']['reference_mapping_closed']is True,'HYBRID_FIT_READY_BRIDGE')
    actual=data['CPU/actual.json'];require(actual.get('status')=='COMPLETE'and all(0<=v<=bound['CPU_budget'][k]for k,v in actual['actual'].items()),'HYBRID_FIT_CPU_BUDGET')
    if 'saved_post' in sem:
        saved=sem['saved_post']
        require(spec['kind']=='fit' and data['saved-post-consumption.json']==dict(binding=saved,scalar_CG=0,range_discovery=0)
                and recipe['recipe_sha256']==saved['recipe_sha256']
                and m['assets']==saved['assets'] and bound['ranges']==saved['ranges']
                and r0['O_sha256']==sem['saved_r0']['O_sha256']
                and recipe['postprocess']['coefficient_sha256']==sem['saved_r0']['coefficient_sha256'],
                'HYBRID_SAVED_POST_COMMIT')
    elif spec['kind']=='fit':
        p3=data['P3/result.json'];cleanup=data['P3/solver-cleanup.json'];held=data['P3/solver-individual-wait.json']
        require(p3.get('status')=='PASS'and p3.get('cuda_initialized')is False and p3['ranges']==bound['ranges']and
            all(p3[k]==recipe['postprocess'][k]for k in ('H64_sha256','T64_sha256','coefficient_sha256')),'HYBRID_FIT_P3_PROOF')
        if "saved_r0" in sem:
            require(r0["r0_recipe_sha256"]==sem["saved_r0"]["recipe_sha256"]
                    and r0["O_sha256"]==sem["saved_r0"]["O_sha256"]
                    and p3["coefficient_sha256"]==sem["saved_r0"]["coefficient_sha256"],"MIXED_RECOVERY_COMMIT")
        require(cleanup['actual_wait']and cleanup['owned_tree_empty']and cleanup['identity_exited']and cleanup['exit_code']==0 and
            not cleanup['forced']and held.get('actual_handle_wait')is True and held.get('exit_code')==0,'HYBRID_FIT_SOLVER_EXIT_PROOF')
    else:require(recipe['recipe_sha256']==sem['recipe_sha256'],'HYBRID_FIT_REPLAY_RECIPE')
    require(data['compute-result.json']['lease']==lease and data['compute-result.json']['float32_sha256']==m['results']['Y']and
        data['replay-identity.json']['execution_manifest_sha256']==m['manifest_sha256'],'HYBRID_FIT_RESULT_BINDING')


def publish_project(project, source):
    """Publish an already verified mixed result under its immutable intent."""
    import os
    import uuid
    from ..common import reject_reparse
    from ..store import locked

    reject_reparse(project)
    reject_reparse(source)
    target = project / "grade-mixed"
    with locked(project / "mixed-publication.lock"):
        expected, _ = read_manifest(source / "execution-manifest.json")
        marker = strict_load(source / "published.json")
        names = (*ASSETS, "execution-manifest.json", "published.json")

        def verify_destination(directory):
            reject_reparse(directory)
            saved, _ = read_manifest(directory / "execution-manifest.json")
            require(saved == expected and strict_load(directory / "published.json") == marker,
                    "MIXED_PUBLICATION_CONFLICT")
            return saved

        if target.exists():
            return verify_destination(target)
        temp = project / (".mixed-prepared-" + str(uuid.UUID(marker["attempt_id"])))
        reject_reparse(temp)
        temp.mkdir(exist_ok=True)
        reject_reparse(temp / "r0")
        (temp / "r0").mkdir(exist_ok=True)
        for name in names:
            member = source / name
            result = temp / name
            reject_reparse(member)
            reject_reparse(result)
            digest = sha_file(member)
            if result.is_file():
                require(sha_file(result) == digest, "MIXED_PREPARED_MEMBER_CONFLICT")
                continue
            partial = result.with_name(result.name + "." + uuid.uuid4().hex + ".partial")
            try:
                with member.open("rb") as incoming, partial.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                require(sha_file(partial) == digest, "MIXED_PREPARED_MEMBER_CHANGED")
                partial.rename(result)
            finally:
                partial.unlink(missing_ok=True)
        verify_destination(temp)
        try:
            temp.rename(target)
        except FileExistsError:
            return verify_destination(target)
        return verify_destination(target)
