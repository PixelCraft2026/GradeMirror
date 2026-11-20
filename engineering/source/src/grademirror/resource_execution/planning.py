"""Build plans only from product-read identities and original resource formulas."""
import uuid
from pathlib import Path
from ..common import strict_load, sha_file
from ..engine import profiles
from ..gpu_resources import resource_geometry
from ..resource_planning.planner import _stages
from .contract import require, digest, policy_binding, MIB
from . import PLAN

def image(info, locator):
    return dict(sha256=locator['sha256'],bytes=locator['bytes'],width=info['width'],height=info['height'],
                bits=info['bits'],format=info.get('format','jpeg'))

def build(spec, inputs, source, reference, snapshot, *, legacy_plan=None):
    from ..hybrid_replay.contract import execution_contract
    from ..gm003_supervisor import CONTROL_PY, NEURAL_PY, IO_PY
    sem=spec['semantics'];kind=spec['kind'];profile=profiles.get_profile(sem['profile_id'],sem['profile_contract_sha256'])
    identities={name:image(info,inputs[name]) for name,info in (('source',source),('reference',reference))}
    for name,identity in identities.items():
        p=Path(inputs[name]['path']);require(p.is_file() and p.stat().st_size==identity['bytes'] and sha_file(p)==identity['sha256'],'RESOURCE_INPUT_CHANGED',name)
        require(identity['sha256']==sem[name+'_sha256'],'RESOURCE_INPUT_CHANGED',name)
    backend=sem.get('backend_id') or ('cuda-fine' if profile['id']==profiles.FINE else 'cuda-fast')
    from ..hybrid_fit.tiers import for_spec,consistent
    core_rows=for_spec(spec)
    if legacy_plan is not None:
        consistent(core_rows,legacy_plan['core_rows'])
        jobs=legacy_plan['resource_budget'];table=legacy_plan['capacities'];rows=[]
        for stage in ('decode','R0','P3','CPU','output'):
            if stage not in table:continue
            row=table[stage]
            rows.append(dict(stage=stage,incremental_physical_upper_bytes=row['additional_bytes']+table['physical_margin'],
                incremental_commit_upper_bytes=row['minimum_commit_headroom'],vram_reserved_bytes=legacy_plan['R0_gpu']['reserved_bytes'] if stage=='R0' else 0,
                vram_required_free_bytes=legacy_plan['R0_gpu']['required_free_bytes'] if stage=='R0' else 0,
                estimate_source='unchanged hybrid capacities',incremental=True))
        rows += [dict(stage=n,incremental_physical_upper_bytes=jobs['host']+jobs['control_ipc'],
                      incremental_commit_upper_bytes=jobs['host']+jobs['control_ipc'],vram_reserved_bytes=0,vram_required_free_bytes=0,
                      estimate_source='existing host encode/readback envelope',incremental=False) for n in ('resident','preview_export')]
        device=legacy_plan['device'];formula=dict(capacities=table,resource_budget=jobs,workspace_bytes=512*MIB)
    else:
        req=dict(operation=kind,mode='fine' if profile['id']==profiles.FINE else 'fast',**identities)
        stages,formula=_stages(req);jobs=formula['jobs']
        rows=[dict(x,incremental=False) for x in stages if x['applicable']]
        candidates=snapshot['gpus'];require(len(candidates)==1,'CUDA_DEVICE_UNAVAILABLE','Exactly one known CUDA device required; CPU R0 is unavailable')
        g=candidates[0];device=dict(uuid=g['uuid'],name=g['name'],driver_version=g['driver'])
        recipe_dir=inputs.get('_recipe_dir')
        if recipe_dir is not None:
            saved=strict_load(Path(recipe_dir)/'recipe.json')['device']
            require(all(device[k]==saved[k] for k in device),'REPLAY_DEVICE_CHANGED')
    envelope=dict(ram_limit_mib=jobs['pool'],commit_limit_mib=max(jobs['pool'],max(x['incremental_commit_upper_bytes'] for x in rows)),
                  vram_limit_mib=max(x['vram_reserved_bytes'] for x in rows))
    user=sem['resource_intent']
    for key,minimum in envelope.items():
        require(user[key] is None or user[key]*MIB>=minimum,'RESOURCE_LIMIT_BELOW_VERIFIED_ENVELOPE',key+': verified envelope '+str(minimum)+' bytes')
    runtime={str(p):dict(bytes=Path(p).stat().st_size,sha256=sha_file(p)) for p in (CONTROL_PY,NEURAL_PY,IO_PY)}
    out=dict(schema_version=PLAN,policy='guarded-v1',plan_id=str(uuid.uuid4()),policy_sha256=digest(policy_binding()),resource_intent=user,
        resource_intent_sha256=digest(user),job_id=spec['job_id'],attempt_id=spec['attempt_id'],intent_sha256=spec['intent_sha256'],
        operation=kind,inputs=identities,input_locators={n:dict(inputs[n]) for n in ('source','reference')},
        recipe_sha256=sem.get('recipe_sha256'),profile_id=profile['id'],profile_contract_sha256=profile['contract_sha256'],
        execution_contract_sha256=execution_contract()[1],runtimes=runtime,backend=backend,device=device,stages=rows,
        formulas=formula,verified_envelope_bytes=envelope,tile_side=512,core_rows=core_rows,threads=4,
        snapshot=snapshot,deadline_at=spec['operation_deadline_at'],output_root=sem['output_root'],
        worker_confirmation_required=True,rawCUDA_confirmation_required=True,fallback=False,
        automatic_eligible=False,CPU_QUALIFIED=False,PRODUCTION_ACTIVATION=False)
    out['plan_sha256']=digest(out)
    return out
