"""First-fit intent is immutable; generated ranges enter a separate data binding."""
import os
from pathlib import Path
from ..common import strict_load,sha_file,reject_reparse
from ..hybrid_replay.contract import require,execution_contract,digest,RUNTIME
from ..hybrid_replay import BACKEND_ID,CONTRACT_VERSION
from ..hybrid_replay.plan import BUDGET,capacity,execution_device
from ..gpu_resources import fast_plan
from ..light.resources import active_plan, pool_budget, scoped_gpu_plan, workspace_limit
from . import FIT_CONTRACT,REVISION
GIB=1024**3;MIB=1024**2

def cpu_budget(shape,ranges,core_rows=512):
    from ..engine.cpu_postprocess.geometry import plan,workspace_bound
    from .tiers import rows as checked_rows
    h,w,_=shape;geometry=plan(h,w,checked_rows(core_rows));n=len(geometry);anchors=sum(r['count']for r in ranges);rows=sum(s.base_rows for s in geometry)
    footprints=n*6;remaps=n*2*anchors
    return (dict(scalar_LLF_footprints=footprints,remap_pyramids=remaps,D_calls=(remaps+footprints)*7,
        U_calls=remaps*14+footprints*7,P3_scalar_evaluations=3*w*(h+rows),scalar_compositions=3*n),
        dict(CPU_global_scans=1,base_remap_pixels=2*anchors*w*rows),max(workspace_bound(s)['total_bytes']for s in geometry))

def staged(source,reference,operation):
    h,w,_=source['shape'];rh,rw,_=reference['shape'];mem=capacity(source['shape'],source['bit_depth'])
    reference_bytes=rh*rw*3*(reference['bit_depth']//8);ht=rh*rw*3*8;f=mem['O_bytes'];s=mem['source_bytes']
    from ..fine_resources import solver_job_bytes
    solver=solver_job_bytes((w,h),(rw,rh))
    # Includes host integer reference, normalized native float temporary, H/T
    # parent and wire copies, original sparse child Job plus runtime allowance.
    mem['decode']['additional_bytes']+=2*reference_bytes
    mem['decode']['minimum_commit_headroom']+=2*reference_bytes
    mem['R0']['additional_bytes']+=reference_bytes+rh*rw*3*4
    mem['R0']['minimum_commit_headroom']+=reference_bytes+rh*rw*3*4
    mem['P3']=dict(additional_bytes=reference_bytes+5*ht+solver+512*MIB,
        minimum_commit_headroom=reference_bytes+5*ht+solver+GIB)
    mem['lifetimes']=dict(reference_integer_bytes=reference_bytes,H64_bytes=ht,T64_bytes=ht,solver_nested_Job_bytes=solver,
        source_normalization_temporary_bytes=f,R0_render_plus_shared_O_bytes=2*f,
        P3_parent_and_wire_copies_bytes=5*ht,source_bytes=s,shared_O_bytes=f,shared_Y_bytes=f,
        P3_buffers_released_before_CPU=True,reference_mapping_closed_before_CPU=True,
        estimates_are='simultaneous incremental capacity, not Job preallocation or observed RSS')
    if active_plan("mixed") is None:
        require(mem['P3']['additional_bytes']+GIB<=6*GIB,'HYBRID_FIT_PREPARE_STATIC_CAPACITY')
    else:
        jobs = pool_budget(BUDGET, "mixed")
        mem.update(host_limit=jobs["host"], compute_limit=jobs["compute"])
    return mem

def fit(project,output,backend,experimental,profile):
    require(experimental is True and backend==BACKEND_ID,'HYBRID_EXPERIMENTAL_OPT_IN_REQUIRED')
    from ..fit_publication import intent
    from ..project import _read_project_manifest
    from ..gm004a_api import _inputs
    from ..engine import profiles
    base=intent(project,output);manifest,forward=_read_project_manifest(Path(project))
    require(not forward and manifest['current_revision']=='r000001'and manifest['revisions']==['r000001']and
        'refinement_seed'not in base,'PROJECT_NOT_NEW_PAIR')
    require(base['profile_id']==profiles.FINE and profile in (None,profiles.FINE),'HYBRID_FINE_PARENT_REQUIRED')
    locators=dict(source=dict(path=manifest['source']['canonical_path'],sha256=manifest['source']['byte_sha256'],bytes=manifest['source']['bytes']),
        reference=dict(path=str(Path(project)/manifest['reference']['relative_path']),sha256=manifest['reference']['byte_sha256'],bytes=manifest['reference']['bytes']))
    from ..image_probe import inspect_pair
    from ..hybrid_replay.audit import science,start
    science(Path(output).parent,'fit-input-headers',header_probe_calls=2);start('fit-headers',4)
    from .audit import record_launch
    from ..image_probe import IO_PY,ROOT
    record_launch('fit-headers',[IO_PY,'-B',ROOT/'tools/gm_photo_probe.py',*(Path(locators[k]['path']).resolve()for k in ('source','reference'))],ROOT)
    a,b=inspect_pair(*(Path(locators[k]['path'])for k in ('source','reference')))
    source=header_identity(a,locators['source']);reference=header_identity(b,locators['reference'])
    from ..fine_contract import validate_limits
    validate_limits(a,b)
    from ..light.runtime import operation
    if operation() is not None:
        device=execution_device(None)
    else:
        from ..resource_planning.snapshot import collect_snapshot
        snap=collect_snapshot();require(snap['complete']['gpu']and len(snap['gpus'])==1,'HYBRID_FIT_DEVICE_UNKNOWN')
        gpu=snap['gpus'][0];device=dict(name=gpu['name'],uuid=gpu['uuid'],driver_version=gpu['driver'])
    sem={**base,'fit_contract':FIT_CONTRACT,'backend_id':BACKEND_ID,'revision':REVISION,
        'execution_contract_version':CONTRACT_VERSION,'execution_contract_sha256':execution_contract()[1],
        'resource_budget':pool_budget(BUDGET, 'mixed'),'device':device}
    plan=make_plan(source,reference,device,'fit')
    from ..light.recovery import mixed_seed
    seed=mixed_seed(project)
    if seed is not None:
        plan["saved_r0"]=sem["saved_r0"]=seed
        from ..saved_post import mixed
        retained=mixed(project,seed)
        if retained is not None:plan["saved_post"]=sem["saved_post"]=retained
    return plan,sem,locators,None,None

def header_identity(header,locator):
    return dict(file_sha256=locator['sha256'],file_size_bytes=locator['bytes'],pixel_sha256=None,
        shape=[header['height'],header['width'],3],dtype='uint'+str(header['bits']),bit_depth=header['bits'])

def make_plan(source,reference,device,operation,ranges=None,core_rows=512):
    from ..engine.independent_r0_p3_lld10 import profile
    from .tiers import rows
    core_rows=rows(core_rows);require(core_rows==512 or operation=='replay','CPU_CORE_ROWS_SCOPE')
    h,w,_=source['shape'];mem=staged(source,reference,operation)
    max_ranges=[dict(count=257)]*3
    budget,extra,workspace=cpu_budget(source['shape'],ranges or max_ranges,core_rows)
    require(workspace<=workspace_limit(h,w,core_rows),'HYBRID_CPU_WORKSPACE')
    return dict(fit_contract=FIT_CONTRACT,operation=operation,backend_id=BACKEND_ID,execution_contract_version=CONTRACT_VERSION,
        execution_contract_sha256=execution_contract()[1],source_identity=source,reference_identity=reference,device=device,
        runtime=RUNTIME,resource_budget=pool_budget(BUDGET, 'mixed'),capacities=mem,
        R0_gpu=scoped_gpu_plan(dict(width=w,height=h),fast_plan(w,h,operation=operation),fine=False,operation=operation),
        durations=dict(R0=1200 if operation=='fit'else 900,P3=600,CPU=3600,operation=6600 if operation=='fit'else 5400,preview=120,export=300),
        tiles=((h+511)//512)*((w+511)//512),tile_side=512,core_rows=core_rows,ranges=ranges,
        CPU_budget=budget,CPU_extra=extra,workspace_bound=workspace,fallback=False,
        profile_contract_sha256=profile.PROFILE_CONTRACT_SHA256,
        data_slots=['R0 parameters','O','H64','T64','b64','ranges','root recipe'],immutable_intent=True)

def replay(manifest,source,reference,output,backend,experimental):
    require(experimental is True and backend in (None,BACKEND_ID),'HYBRID_EXPERIMENTAL_OPT_IN_REQUIRED')
    from .contract import read_manifest
    from .tiers import effective,consistent
    from ..resource_execution.contract import CURRENT
    prior,recipe=read_manifest(manifest);locators={}
    core_rows=consistent(effective(CURRENT.get()),prior['core_rows'])
    for name,path in (('source',source),('reference',reference)):
        require(path is not None,'HYBRID_MANIFEST_LOCATORS_REQUIRED');p=Path(path).resolve();reject_reparse(p);expected=recipe[name+'_identity']
        require(p.is_file()and p.stat().st_size==expected['file_size_bytes']and sha_file(p)==expected['file_sha256'],'HYBRID_INPUT_IDENTITY')
        locators[name]=dict(path=str(p),bytes=p.stat().st_size,sha256=expected['file_sha256'])
    plan=make_plan(recipe['source_identity'],recipe['reference_identity'],execution_device(recipe['device']),'replay',recipe['postprocess']['ranges'],core_rows)
    plan['parent_recipe_sha256']=recipe['recipe_sha256']
    sem=dict(fit_contract=FIT_CONTRACT,backend_id=BACKEND_ID,revision=REVISION,
        execution_contract_version=CONTRACT_VERSION,execution_contract_sha256=plan['execution_contract_sha256'],
        recipe_sha256=recipe['recipe_sha256'],source_sha256=recipe['source_identity']['file_sha256'],reference_sha256=recipe['reference_identity']['file_sha256'],
        parent_manifest_sha256=prior['manifest_sha256'],output_root=str(Path(output).resolve()),profile_id=recipe['profile_id'],
        profile_contract_sha256=recipe['profile_contract_sha256'],resource_budget=pool_budget(BUDGET, 'mixed'))
    return plan,sem,locators,recipe,prior
