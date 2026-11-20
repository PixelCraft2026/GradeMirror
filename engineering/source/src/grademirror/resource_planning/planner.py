"""Pure, finite advisory plans using unchanged resource formulas."""
from ..common import GMError
from ..engine import profiles
from ..engine.independent_r0 import profile as fast
from ..engine.independent_r0_p3_lld10 import profile as fine
from ..gpu_resources import fast_plan, resource_geometry
from ..fine_resources import memory_plan, budget, solver_job_bytes, solver_memory_bytes
from ..fine_contract import validate_limits
from ..image_limits import photo_budget, GIB, MIB
from ..resource_policy import ALLOCATOR_FRACTION
from .contract import normalize_request, normalize_snapshot, intent, digest, training_hw, RESPONSE
from .capabilities import binding, registry

def _stages(r):
    s=r['source'];ref=r.get('reference',s);op=r['operation'];is_fine=r['mode']=='fine'
    names=('import','R0_fit','R0_replay','recipe_load','P3_solve','LLF','handoff','resident','preview_export')
    ram=memory_plan(s,ref,operation=op) if is_fine and op!='export' else None
    jobs=budget(s,ref,admit=False,operation=op) if ram else photo_budget(s,ref)
    gpu=resource_geometry(s,operation=op,fine=is_fine)['plan'] if op!='export' else None
    r0=fast_plan(s['width'],s['height'],operation=op) if op!='export' else None
    rows=[]
    for name in names:
        applies=(name in ('resident','preview_export') or op!='export' and name in ('import','handoff') or
            name=='R0_fit' and op=='fit' or name in ('R0_replay','recipe_load') and op=='replay' or
            name=='P3_solve' and is_fine and op=='fit' or name=='LLF' and is_fine and op!='export')
        row=dict(stage=name,applicable=applies,state='applicable'if applies else'not_applicable')
        if not applies:rows.append(row);continue
        numeric={'R0_fit':'R0_fit_render','R0_replay':'R0_replay_render','P3_solve':'P3_solve_parent_and_child','LLF':'LLF_composition','recipe_load':'recipe_load'}
        calibrated=bool(ram and name in numeric)
        if calibrated:
            working=ram['numeric_stages_bytes'][numeric[name]]+ram['host_active_estimate_bytes']+ram['control_active_estimate_bytes']
            margin=ram['physical_margin_bytes']+ram.get('physical_rounding_bytes',0)
            physical=working+margin
        else:
            working=jobs['host']+jobs['control_ipc'] if name in ('import','handoff','resident','preview_export') else jobs['pool']
            margin=GIB if op!='export' and max(s['width']*s['height'],ref['width']*ref['height'])>64000000 else 0
            physical=working+margin
        commit=jobs['host']+jobs['control_ipc'] if name in ('resident','preview_export') else jobs['pool']
        child_job=solver_job_bytes((s['width'],s['height']),(ref['width'],ref['height'])) if name=='P3_solve' else 0
        commit+=child_job
        device=gpu if name=='LLF' else r0 if name in ('R0_fit','R0_replay') else None
        objects=['host source/Y/blend/quantization/encode/readback envelope','control IPC']
        if name in numeric:objects.append('numeric worker and still-live host')
        if name=='P3_solve':objects.append('P3 parent AND solver child simultaneously')
        row.update(working_physical_estimate_bytes=working,incremental_physical_upper_bytes=physical,physical_margin_bytes=margin,
            commit_upper_bytes=commit,incremental_commit_upper_bytes=commit,commit_bound='CONSERVATIVE_JOB_BOUND_NOT_CALIBRATED',
            estimate_source=ram['ram_policy_version'] if calibrated else 'CONSERVATIVE_JOB_BOUND_NOT_CALIBRATED',
            conservative=True,preallocated=False,simultaneously_live=objects,job_pool_limits=jobs,solver_child_job_bytes=child_job,
            vram_reserved_bytes=device['reserved_bytes'] if device else 0,vram_required_free_bytes=device['required_free_bytes'] if device else 0,
            vram_margin_bytes=512*MIB if device else 0,allocator_fraction=ALLOCATOR_FRACTION,
            additional_R0_or_LLF=False if op=='export' else None,
            export_encode_and_full_readback_included=name=='preview_export')
        rows.append(row)
    return rows,dict(jobs=jobs,fine_memory_plan=ram,gpu=gpu,r0_gpu=r0,
        solver_child_working_bytes=solver_memory_bytes((s['width'],s['height']),(ref['width'],ref['height'])) if is_fine and op=='fit' else 0,
        existing_physical_admission_bytes=ram['required_physical_bytes'] if ram else jobs['pool']+GIB if op!='export' and max(s['width']*s['height'],ref['width']*ref['height'])>64000000 else None,
        fast_small_physical_gate='NO_UNIFORM_6_OR_7_GIB_PHYSICAL_GATE'if not is_fine and s['width']*s['height']<=64000000 else 'not_applicable',
        budgets_are='upper envelopes, not preallocation or new production gates')

def _fresh(snapshot):
    wall=snapshot['clock']['wall']-snapshot['collected_wall'];mono=snapshot['clock']['monotonic']-snapshot['collected_monotonic']
    ok=snapshot['machine_id']==snapshot['clock']['machine_id'] and -1<=wall<=5 and 0<=mono<=5
    return dict(valid=ok,wall_age_seconds=wall,monotonic_age_seconds=mono,window_seconds=5,source='snapshot explicit observation clock',reservation=False)

def _assess(r,s,rows,fresh):
    reasons=[];unknown=[];p=s['physical'];c=s['commit'];checks=[]
    def add(code,kind='false'):
        target=reasons if kind=='false' else unknown
        if code not in target:target.append(code)
    physical_ok=s['complete']['physical'] and all(x is not None for x in p.values()) and p['available_bytes']<=p['total_bytes']
    commit_ok=s['complete']['commit'] and all(x is not None for x in c.values()) and c['used_bytes']<=c['limit_bytes'] and c['headroom_bytes']==c['limit_bytes']-c['used_bytes']
    if not fresh['valid']:physical_ok=commit_ok=False;add('SNAPSHOT_STALE_OR_INCONSISTENT','unknown')
    existing=r.get('existing',s['product']);known=existing.get('coverage')=='full_tree' and ('existing'in r or s['complete']['product'])
    rss=existing['rss_bytes'] if known else None;private=existing['private_bytes'] if known else None
    if r.get('existing',{}).get('state') in ('busy','unproven_exit'):add('RESIDENT_BUSY')
    if r.get('existing',{}).get('state')=='same_operation':add('EXISTING_OPERATION_REUSE_REQUIRED')
    for row in rows:
        if not row['applicable']:continue
        ph=row['incremental_physical_upper_bytes'];co=row['incremental_commit_upper_bytes']
        row['existing_rss_bytes']=rss;row['existing_private_bytes']=private
        row['user_tree_physical_upper_bytes']=None if rss is None else rss+ph
        row['user_tree_commit_upper_bytes']=None if private is None else private+co
        row['physical_free_check']=p['available_bytes']>=ph if physical_ok else None
        row['commit_headroom_check']=c['headroom_bytes']>=co if commit_ok else None
        if row['physical_free_check'] is False:add('PHYSICAL_RAM_INSUFFICIENT')
        if row['physical_free_check'] is None:add('PHYSICAL_RAM_UNKNOWN','unknown')
        if row['commit_headroom_check'] is False:add('SYSTEM_COMMIT_INSUFFICIENT')
        if row['commit_headroom_check'] is None:add('SYSTEM_COMMIT_UNKNOWN','unknown')
        for limit,total,code in (('ram_bytes',row['user_tree_physical_upper_bytes'],'USER_RAM_LIMIT'),('commit_bytes',row['user_tree_commit_upper_bytes'],'USER_COMMIT_LIMIT')):
            if limit in r['limits']:
                if total is None:add('EXISTING_PRODUCT_USAGE_UNKNOWN','unknown')
                elif total>r['limits'][limit]:add(code)
        checks.append(dict(stage=row['stage'],physical=row['physical_free_check'],commit=row['commit_headroom_check']))
    selected=None;effective=None;gpu_check=None
    required=max((row.get('vram_required_free_bytes',0) for row in rows),default=0)
    reserved=max((row.get('vram_reserved_bytes',0) for row in rows),default=0)
    if required:
        wanted=r.get('device_uuid',r.get('recipe',{}).get('device',{}).get('uuid'))
        matches=[g for g in s['gpus'] if wanted is None or g['uuid']==wanted]
        if len({g['uuid'] for g in s['gpus']})!=len(s['gpus']):add('GPU_IDENTITIES_CONTRADICTORY','unknown')
        if s['cuda_available'] is False:add('CUDA_UNAVAILABLE')
        if len(matches)!=1:add('REPLAY_DEVICE_CHANGED' if r['operation']=='replay' else 'GPU_SELECTION_REQUIRED','unknown')
        else:
            selected=matches[0];total=selected['total_bytes'];nvml=selected['nvml_free_bytes'];raw=selected['raw_cuda_free_bytes']
            valid=s['complete']['gpu'] and fresh['valid'] and total is not None and nvml is not None and nvml<=total
            if raw is not None:valid=valid and raw<=total and selected['raw_cuda_uuid']==selected['uuid']
            if not valid:add('GPU_SNAPSHOT_UNKNOWN','unknown')
            else:
                if nvml<required:add('NVML_FREE_INSUFFICIENT')
                if reserved*5>total*4:add('ALLOCATOR_FRACTION_LIMIT')
                if selected['allocator_reserved_bytes'] is not None and (selected['allocator_reserved_bytes']>total or selected['allocator_reserved_bytes']*5>total*4):add('ALLOCATOR_STATE_INCONSISTENT','unknown')
                if raw is None or s['cuda_available'] is None:add('RAW_CUDA_WORKER_CONFIRMATION_REQUIRED','unknown')
                else:
                    effective=min(raw,nvml);gpu_check=effective>=required
                    if not gpu_check:add('EFFECTIVE_VRAM_INSUFFICIENT')
                if 'vram_bytes'in r['limits']:
                    used=selected['allocator_reserved_bytes']
                    if used is None:add('EXISTING_GPU_USAGE_UNKNOWN','unknown')
                    elif used+reserved>r['limits']['vram_bytes']:add('USER_VRAM_LIMIT')
            if r['operation']=='replay':
                device=r['recipe']['device']
                if any(selected[k]!=device[k] for k in ('uuid','driver')):add('REPLAY_DEVICE_CHANGED')
                if selected['cuda_runtime'] is None:add('CUDA_RUNTIME_UNKNOWN','unknown')
                elif selected['cuda_runtime']!=device['cuda_runtime']:add('REPLAY_DEVICE_CHANGED')
    result=False if reasons else None if unknown else True
    return result,dict(blockers=reasons,unknowns=unknown,stages=checks,gpu_uuid=selected['uuid']if selected else None,
        effective_vram_bytes=effective,nvml_only_necessary_condition=required>0 and effective is None,
        gpu_required_free_bytes=required,gpu_reserved_bytes=reserved,gpu_check=gpu_check,free_does_not_subtract_existing_RSS_again=True)

def plan_resources(request, snapshot=None):
    if isinstance(request, dict) and request.get('schema_version') == 'gm042-plan-request-v3':
        from .candidate_planner import plan_candidates_v3
        return plan_candidates_v3(request, snapshot)
    if isinstance(request, dict) and request.get('schema_version') == 'gm029-plan-request-v2':
        from .candidate_planner import plan_candidates
        return plan_candidates(request, snapshot)
    r=normalize_request(request)
    from .snapshot import collect_snapshot
    s=normalize_snapshot(collect_snapshot() if snapshot is None else snapshot)
    versions=[];profile=None
    try:
        profile=profiles.get_profile(r['profile']['id'],r['profile']['contract_sha256'])
        if profile['id']!={'fast':profiles.R0,'fine':profiles.FINE}[r['mode']]:versions.append('MODE_PROFILE_MISMATCH')
    except GMError as e:versions.append(e.code)
    from ..hybrid_replay.contract import execution_contract
    hybrid=execution_contract()[1]
    if r['mode']=='fine' and profile is not None:
        limits=fine.COMPATIBLE_CONTRACTS.get(profile['contract_sha256'],{}).get('limits',fine.LIMITS)
        validate_limits(r['source'],r.get('reference',r['source']),limits=limits)
    if r['operation']=='replay' and profile is not None:
        recipe=r['recipe'];runtime=fast.RUNTIME if r['mode']=='fast' else fine.RUNTIME
        if any(recipe[k]!=v for k,v in (('profile_id',profile['id']),('profile_contract_sha256',profile['contract_sha256']),('core_sha256',profile['core_sha256']),('runtime',runtime))):versions.append('RECIPE_VERSION_CONFLICT')
    for meta in (r.get('recipe',{}),r.get('result',{})):
        if 'execution_contract_sha256'in meta and meta['execution_contract_sha256']!=hybrid:versions.append('HYBRID_IMPLEMENTATION_CHANGED')
    rows,formula=_stages(r);fresh=_fresh(s);feasible,checks=_assess(r,s,rows,fresh)
    if r['operation']=='export':
        result=r['result']
        if result['lease_state']!='active' or result['owner_alive']is not True or result['lease_expires_wall']<=s['clock']['wall']:
            checks['blockers'].append('RESIDENT_REQUIRED');feasible=False
        if result['backend'] not in ('cuda-'+r['mode'],'cuda-r0-cpu-lld10-v1'):versions.append('RESULT_BACKEND_UNAVAILABLE')
        if result['backend']=='cuda-r0-cpu-lld10-v1' and 'execution_contract_sha256'not in result:versions.append('HYBRID_VERSION_REQUIRED')
    if versions:feasible=False
    device_binding=[code for code in checks['blockers']+checks['unknowns'] if code in ('REPLAY_DEVICE_CHANGED','CUDA_RUNTIME_UNKNOWN','GPU_SELECTION_REQUIRED','GPU_IDENTITIES_CONTRADICTORY')]
    current='resident-export'if r['operation']=='export'else'cuda-'+r['mode']
    capabilities=registry()
    for candidate in capabilities:
        candidate['considered_for_request']=candidate['id']==current
        candidate['rejection_reasons']=list(versions)+checks['blockers']+checks['unknowns'] if candidate['id']==current else ['MODE_OR_OPERATION_NOT_REQUESTED'] if candidate['id']in ('cuda-fast','cuda-fine','resident-export') else ['CPU_NOT_QUALIFIED' if candidate['id']=='cuda-r0-cpu-lld10-v1' else 'IMPLEMENTATION_UNQUALIFIED']
    policy=binding();out=dict(schema_version=RESPONSE,**policy,intent_digest=digest(intent(r)),snapshot_digest=digest(s),request_identity=intent(r),
        stages=rows,formula= formula,candidates=capabilities,recommended_candidate=current if feasible is True else None,
        binding_checks=dict(valid=not versions and not device_binding,reasons=versions+device_binding,current_hybrid_execution_digest=hybrid,new_digest_numerical_qualification=False,
            requested_profile_contract_sha256=r['profile']['contract_sha256'],requested_core_sha256=profile['core_sha256']if profile else None),
        resource_checks=checks,estimated_resource_feasible=feasible,freshness=fresh,
        summary='资源元数据满足保守预算，执行时仍须原入口复核。'if feasible is True else'当前资源或版本不满足；查看阻塞阶段，保持原尺寸和效果。'if feasible is False else'资源信息不足或已过期，需取得新快照并由执行worker确认。',
        input_validation='METADATA_ONLY_NOT_REDECODED',runtime_validation='WORKER_CONFIRMATION_REQUIRED',
        advisory_only=True,execution_authorized=False,automatic_selection_enabled=False,CPU_QUALIFIED=False,PRODUCTION_ACTIVATION=False,
        estimated_seconds=None,reservation=False,no_quality_changes=True,output_contract=dict(source=r['source'],export=r.get('export'),
            training_hw=training_hw(r['source']),tile_side=512,threads=4,settings=fast.SETTINGS,P3=fine.P3 if r['mode']=='fine'else None,LLF=fine.LLF if r['mode']=='fine'else None),
        snapshot_diagnostics=s['diagnostics'])
    out['plan_digest']=digest(out)
    return out
