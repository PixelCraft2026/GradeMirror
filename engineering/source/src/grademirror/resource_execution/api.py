"""The CLI, desktop and Python entry points share exactly this explicit scope."""
from pathlib import Path
from ..common import strict_load,sha_file,writable_output_root
from .contract import CURRENT,normalize,require,check_key

def prior_intent(kind,project,output,backend,manifest,kwargs,spec):
    """Reuse the original read-only intent builders, never resource collection."""
    from .contract import attach
    output=writable_output_root(output);sem=spec['semantics']
    require(spec['kind']==kind and sem.get('backend_id')==backend,'REQUEST_KEY_CONFLICT')
    require(kwargs.get('profile') in (None,sem['profile_id']),'REQUEST_KEY_CONFLICT')
    if kind=='fit':
        require(project is not None and manifest is None,'REQUEST_KEY_CONFLICT')
        from ..fit_publication import intent
        current=intent(project,output)
        from ..project import _read_project_manifest
        meta,forward=_read_project_manifest(Path(project))
        require(not forward,'REQUEST_KEY_CONFLICT')
        locators=dict(source=Path(meta['source']['canonical_path']),reference=Path(project)/meta['reference']['relative_path'])
        require(all(sha_file(p)==current[name+'_sha256'] for name,p in locators.items()),'REQUEST_KEY_CONFLICT')
        if backend is not None:
            from ..hybrid_fit import FIT_CONTRACT,REVISION
            from ..hybrid_replay import CONTRACT_VERSION
            from ..hybrid_replay.contract import execution_contract
            from ..hybrid_replay.plan import BUDGET
            current.update(fit_contract=FIT_CONTRACT,backend_id=backend,revision=REVISION,
                execution_contract_version=CONTRACT_VERSION,execution_contract_sha256=execution_contract()[1],
                resource_budget=BUDGET,device=sem['device'])
    elif backend is not None:
        require((project is not None)!=(manifest is not None),'HYBRID_SOURCE_EXCLUSIVE')
        source=kwargs.get('source_path');reference=kwargs.get('reference_path')
        if manifest is not None:
            from ..hybrid_fit import MANIFEST_SCHEMA
            if strict_load(manifest).get('schema_version')==MANIFEST_SCHEMA:
                from ..hybrid_fit.plan import replay
                _,current,_,_,_=replay(manifest,source,reference,output,backend,True)
            else:
                from ..hybrid_replay.plan import resolve
                _,current,_,_,_,_=resolve(project,manifest,source,reference,output,backend,True)
        else:
            from ..hybrid_replay.plan import resolve
            _,current,_,_,_,_=resolve(project,manifest,source,reference,output,backend,True)
    else:
        require(project is not None and manifest is None and kwargs.get('source_path') is None and kwargs.get('reference_path') is None,'REPLAY_ARGS_INVALID')
        from ..gm004a_api import _replay_intent
        _,_,_,current,_,_=_replay_intent(project,output)
    require(attach(current)==sem,'REQUEST_KEY_CONFLICT','Complete operation intent differs for this request key')


def invoke(function,project,store,output,*,resource_request,kwargs):
    value=normalize(resource_request,experimental=kwargs.get('allow_experimental',False))
    backend=kwargs.get('execution_backend');manifest=kwargs.get('execution_manifest')
    require(backend in (None,'cuda-r0-cpu-lld10-v1'),'RESOURCE_BACKEND_CONFLICT')
    if manifest is not None:require(backend=='cuda-r0-cpu-lld10-v1','RESOURCE_BACKEND_CONFLICT')
    from ..hybrid_fit import MANIFEST_SCHEMA
    from ..hybrid_fit.tiers import effective,consistent
    supplied='cpu_core_rows'in resource_request
    require(not supplied or function.__name__=='replay'and backend=='cuda-r0-cpu-lld10-v1'and
            manifest is not None,'CPU_CORE_ROWS_SCOPE')
    if manifest is not None:
        metadata=strict_load(manifest)
        require(not supplied or metadata.get('schema_version')==MANIFEST_SCHEMA,'CPU_CORE_ROWS_SCOPE')
        if metadata.get('schema_version')==MANIFEST_SCHEMA:consistent(effective(value),metadata['core_rows'])
    token=CURRENT.set(value)
    try:
        from ..gm004a_operations import OperationStore
        ops=OperationStore(store);prior=check_key(ops,kwargs.get('request_key'))
        if prior is not None:
            path,spec=prior;sem=spec['semantics'];kind='fit' if function.__name__=='fit_new_pair' else 'replay'
            prior_intent(kind,project,output,backend,manifest,kwargs,spec)
            from ..hybrid_replay.api import response
            if sem.get('backend_id') is not None:result=response(ops,path,spec,idempotent=True)
            else:
                from ..gm004a_api import _operation_response
                result=_operation_response(ops,path,spec,idempotent=True)
            if (path/'resource-plan-bound.json').is_file():result=dict(result,resource_plan=strict_load(path/'resource-plan-bound.json'),resource_policy='guarded-v1')
            return result
        if backend is None:kwargs=dict(kwargs,allow_experimental=False)
        result=function(project,store,output,**kwargs)
        if 'job_id' in result:
            path,spec=OperationStore(store).path(result['job_id'],result['attempt_id'])
            if (path/'resource-plan-bound.json').is_file():
                result=dict(result,resource_plan=strict_load(path/'resource-plan-bound.json'),resource_policy='guarded-v1')
        return result
    finally:CURRENT.reset(token)

def cli_request(policy,ram,commit,vram,cpu_core_rows=None):
    if policy is None and all(x is None for x in (ram,commit,vram,cpu_core_rows)):return None
    # argparse parses exact decimal integers; API normalization rejects booleans.
    value=dict(policy=policy,ram_limit_mib=ram,commit_limit_mib=commit,vram_limit_mib=vram)
    if cpu_core_rows is not None:value['cpu_core_rows']=cpu_core_rows
    return value
