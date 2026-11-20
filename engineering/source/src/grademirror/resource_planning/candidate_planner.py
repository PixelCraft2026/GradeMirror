"""Finite advisory comparison. No task, image, model, lease or reservation is opened."""
import copy
from .contract import normalize_snapshot,intent,digest
from .candidate_contract import normalize_request,normalize_request_v3,legacy_request,RESPONSE,RESPONSE_V3,HYBRID
from .capabilities import binding,registry_v2,registry_v3
from .qualification import match,match_v3

def hybrid_stages(request,scope):
    from ..hybrid_fit.plan import make_plan
    def identity(image):
        return dict(shape=[image['height'],image['width'],3],bit_depth=image['bits'],dtype='uint'+str(image['bits']),
            file_sha256=image['sha256'],file_size_bytes=image['bytes'],pixel_sha256=None)
    operation=request['operation'];ranges=scope['ranges']if operation=='replay'else None
    plan=make_plan(identity(request['source']),identity(request['reference']),copy.deepcopy(scope['device']),operation,ranges)
    table=plan['capacities'];jobs=plan['resource_budget'];rows=[]
    for stage in ('decode','R0','P3','CPU','output'):
        row=table[stage]
        rows.append(dict(stage=stage,incremental_physical_upper_bytes=row['additional_bytes']+table['physical_margin'],
            incremental_commit_upper_bytes=row['minimum_commit_headroom'],vram_reserved_bytes=plan['R0_gpu']['reserved_bytes']if stage=='R0'else 0,
            vram_required_free_bytes=plan['R0_gpu']['required_free_bytes']if stage=='R0'else 0,
            estimate_source='unchanged hybrid capacities',incremental=True))
    rows += [dict(stage=stage,incremental_physical_upper_bytes=jobs['host']+jobs['control_ipc'],
        incremental_commit_upper_bytes=jobs['host']+jobs['control_ipc'],vram_reserved_bytes=0,vram_required_free_bytes=0,
        estimate_source='existing host encode/readback envelope',incremental=False)for stage in ('resident','preview_export')]
    envelope=dict(ram_bytes=jobs['pool'],commit_bytes=max(jobs['pool'],max(row['incremental_commit_upper_bytes']for row in rows)),
        vram_bytes=max(row['vram_reserved_bytes']for row in rows))
    applicability={row['stage']:not(operation=='replay'and row['stage']=='P3')for row in rows}
    stages=[]
    for row in rows:
        stages.append(dict(row,applicable=applicability[row['stage']],capacity_kind='incremental'if row['incremental']else'total',
            actual_stage_applicability='inherited_planned_only'if not applicability[row['stage']]else'planned_not_independently_observed'if row['stage']in ('resident','preview_export')else'executed_in_evidence',
            capacity_retained_for_conservative_check=True,preallocated=False,
            physical_margin_bytes=table['physical_margin']if row['incremental']else 0,
            allocator_fraction=.8,vram_margin_bytes=512*1024**2 if row['stage']=='R0'else 0))
    formula={key:copy.deepcopy(plan[key])for key in ('capacities','resource_budget','CPU_budget','CPU_extra','workspace_bound','durations')}
    formula.update(legacy_plan_rows=rows,actual_stage_applicability=applicability,
        verified_envelope_bytes=envelope,fit_ranges_are_prior_knowledge=False,
        anchor_count_upper=[257]*3 if operation=='fit'else[r['count']for r in ranges],
        P3_actual_new_CG=3 if operation=='fit'else 0,
        inherited_P3_capacity='Retained conservative upper bound; no claim of a lower replay admission gate',
        numeric_assets_loaded=False,formula_source='hybrid_fit.plan.make_plan/staged/cpu_budget; resource_execution.planning.build row projection')
    return stages,formula

def assess_hybrid(request,snapshot,rows,fresh,envelope):
    reasons=[];unknown=[]
    def add(code,indeterminate=False):
        target=unknown if indeterminate else reasons
        if code not in target:target.append(code)
    p=snapshot['physical'];c=snapshot['commit'];complete=snapshot['complete']
    ph_known=complete['physical']and all(v is not None for v in p.values())and p['available_bytes']<=p['total_bytes']
    co_known=complete['commit']and all(v is not None for v in c.values())and c['headroom_bytes']==c['limit_bytes']-c['used_bytes']and c['used_bytes']<=c['limit_bytes']
    if not fresh['valid']:ph_known=co_known=False;add('SNAPSHOT_STALE_OR_INCONSISTENT',True)
    existing=request.get('existing',snapshot['product'])
    known=existing['coverage']=='full_tree'and ('existing'in request or complete['product'])and existing['rss_bytes']is not None and existing['private_bytes']is not None
    rss=existing['rss_bytes']if known else None;private=existing['private_bytes']if known else None
    if not known:add('EXISTING_PRODUCT_USAGE_UNKNOWN',True)
    state=existing.get('state')
    if state in ('busy','unproven_exit'):add('RESIDENT_BUSY')
    if state=='same_operation':add('EXISTING_OPERATION_REUSE_REQUIRED')
    for key,minimum in envelope.items():
        if key in request['limits']and request['limits'][key]<minimum:add('RESOURCE_LIMIT_BELOW_VERIFIED_ENVELOPE:'+key)
    for row in rows:
        ram=row['incremental_physical_upper_bytes'];commit=row['incremental_commit_upper_bytes']
        if not row['incremental']:
            ram=None if rss is None else max(0,ram-rss);commit=None if private is None else max(0,commit-private)
        total_ram=None if rss is None or ram is None else rss+ram
        total_commit=None if private is None or commit is None else private+commit
        ph=None if not ph_known or ram is None else p['available_bytes']>=ram
        co=None if not co_known or commit is None else c['headroom_bytes']>=commit
        for value,code in ((ph,'PHYSICAL_RAM'),(co,'SYSTEM_COMMIT')):
            if value is False:add(code+'_INSUFFICIENT:'+row['stage'])
            if value is None:add(code+'_UNKNOWN:'+row['stage'],True)
        for key,total in (('ram_bytes',total_ram),('commit_bytes',total_commit)):
            if key in request['limits']:
                if total is None:add('USER_TREE_USAGE_UNKNOWN',True)
                elif total>request['limits'][key]:add('USER_'+key.upper()+':'+row['stage'])
        row.update(required_incremental_physical_bytes=ram,required_incremental_commit_bytes=commit,
            existing_rss_bytes=rss,existing_private_bytes=private,user_tree_physical_upper_bytes=total_ram,
            user_tree_commit_upper_bytes=total_commit,physical_free_check=ph,commit_headroom_check=co)
    required=max(row['vram_required_free_bytes']for row in rows);reserved=max(row['vram_reserved_bytes']for row in rows)
    wanted=request.get('device_uuid',request.get('recipe',{}).get('device',{}).get('uuid'))
    matches=[g for g in snapshot['gpus']if wanted is None or g['uuid']==wanted]
    if snapshot['cuda_available']is False:add('CUDA_UNAVAILABLE')
    elif snapshot['cuda_available']is None:add('CUDA_AVAILABILITY_UNKNOWN',True)
    contradictory=len({g['uuid']for g in snapshot['gpus']})!=len(snapshot['gpus'])
    if contradictory:add('GPU_IDENTITIES_CONTRADICTORY',True)
    effective=None
    if len(matches)!=1:add('GPU_SELECTION_REQUIRED',True)
    else:
        g=matches[0];total=g['total_bytes'];nv=g['nvml_free_bytes'];raw=g['raw_cuda_free_bytes']
        valid=complete['gpu']and fresh['valid']and not contradictory and total is not None and nv is not None and nv<=total
        if raw is not None:valid=valid and raw<=total and g['raw_cuda_uuid']==g['uuid']
        if not valid:add('GPU_SNAPSHOT_UNKNOWN',True)
        else:
            if reserved*5>total*4:add('ALLOCATOR_FRACTION_LIMIT')
            if nv<required:add('NVML_FREE_INSUFFICIENT')
            if raw is None:add('RAW_CUDA_WORKER_CONFIRMATION_REQUIRED',True)
            else:
                effective=min(raw,nv)
                if effective<required:add('EFFECTIVE_VRAM_INSUFFICIENT')
            allocated=g['allocator_reserved_bytes']
            if allocated is not None and (allocated>total or allocated*5>total*4):add('ALLOCATOR_STATE_INCONSISTENT',True)
            if 'vram_bytes'in request['limits']:
                if allocated is None:add('EXISTING_GPU_USAGE_UNKNOWN',True)
                elif allocated+reserved>request['limits']['vram_bytes']:add('USER_VRAM_LIMIT')
    return (False if reasons else None if unknown else True),dict(blockers=reasons,unknowns=unknown,
        effective_vram_bytes=effective,gpu_required_free_bytes=required,gpu_reserved_bytes=reserved,
        free_does_not_subtract_existing_RSS_again=True,total_rows_subtract_existing_once=True,
        legacy_P3_capacity_checked_conservatively=request['operation']=='replay')

def _existing_usage_known(request,snapshot):
    explicit='existing'in request
    usage=request['existing']if explicit else snapshot['product']
    return (explicit or snapshot['complete']['product'])and usage['coverage']=='full_tree'and usage['rss_bytes']is not None and usage['private_bytes']is not None

def plan_candidates(value,snapshot=None):
    return _plan_candidates(value,snapshot)

def plan_candidates_v3(value,snapshot=None):
    return _plan_candidates(value,snapshot,v3=True)

def _plan_candidates(value,snapshot=None,*,v3=False):
    from ..common import GMError
    from ..hybrid_replay.contract import execution_contract
    from ..resource_execution.contract import policy_binding,digest as policy_digest
    from .planner import plan_resources,_fresh
    request=normalize_request_v3(value)if v3 else normalize_request(value)
    if snapshot is None:
        from .snapshot import collect_snapshot
        snapshot=collect_snapshot()
    snapshot=normalize_snapshot(snapshot);fresh=_fresh(snapshot);execution=execution_contract()[1]
    backend=request.get('recipe',{}).get('backend_id','cuda-'+request['mode'])
    operation=request['operation'];current='resident-export'if operation=='export'else'cuda-'+request['mode']
    candidates=registry_v3()if v3 else registry_v2();legacy=None
    if operation!='replay'or backend!=HYBRID:legacy=plan_resources(legacy_request(request),snapshot)
    for row in candidates:
        row.update(qualification_match=False,resource_feasible=None,advisory_eligible=False,scope_id=None,
            stages=[],blockers=[],unknowns=[],qualification_reasons=[],estimated_seconds=None,
            execution_contract_sha256=execution,execution_authorized=False,new_policy_numerical_qualification=False)
        if row['id']==current and legacy is not None:
            checks=copy.deepcopy(legacy['resource_checks'])
            feasible=False if checks['blockers']else None if checks['unknowns']else True
            if not _existing_usage_known(request,snapshot):
                checks['unknowns'].append('EXISTING_PRODUCT_USAGE_UNKNOWN');feasible=False if checks['blockers']else None
            match_cuda=legacy['binding_checks']['valid']
            if operation=='replay'and backend!=current:match_cuda=False;row['qualification_reasons'].append('EXPLICIT_MIGRATION_REQUIRED')
            row.update(qualification_match=match_cuda,resource_feasible=feasible,stages=legacy['stages'],formula=legacy['formula'],
                blockers=checks['blockers'],unknowns=checks['unknowns'],resource_checks=checks,
                scope_id='existing-resident-source-format-v1'if operation=='export'else'existing-manual-'+current,
                qualification_reasons=row['qualification_reasons']+legacy['binding_checks']['reasons'],
                deadline_contract='Original operation/preview/export deadline; no ETA')
        elif row['id']==HYBRID and operation in ('fit','replay')and request['mode']=='fine':
            q=match_v3(request,snapshot,execution)if v3 else match(request,snapshot,execution)
            row.update(qualification_match=q['match'],scope_id=q['scope_id'],qualification_reasons=q['reasons'],
                input_validation='CALLER_METADATA_NOT_FILE_VERIFICATION',old_assets_allowed_as_new_fit_input=False)
            if operation=='replay'and backend!=HYBRID:row['qualification_match']=False;row['qualification_reasons'].append('EXPLICIT_MIGRATION_REQUIRED')
            if not request['allow_cpu']:row['qualification_reasons'].append('CPU_NOT_ALLOWED')
            if q['scope']is not None:
                try:
                    stages,formula=hybrid_stages(request,q['scope']);feasible,checks=assess_hybrid(request,snapshot,stages,fresh,formula['verified_envelope_bytes'])
                    row.update(resource_feasible=feasible,stages=stages,formula=formula,resource_checks=checks,
                        blockers=checks['blockers'],unknowns=checks['unknowns'],deadline_contract=formula['durations'])
                except GMError as error:row.update(resource_feasible=False,blockers=[error.code])
            else:row['unknowns'].append('NO_BOUND_RECIPE_RANGES_OR_SCOPE')
        else:row['qualification_reasons'].append('MODE_OR_OPERATION_NOT_REQUESTED'if row['id']in ('cuda-fast','cuda-fine','resident-export')else'IMPLEMENTATION_UNQUALIFIED')
        row['advisory_eligible']=bool(row['qualification_match']and row['resource_feasible']is True and not row['qualification_reasons'])
    by_id={row['id']:row for row in candidates};selected=None;reason='NO_QUALIFIED_RESOURCE_FEASIBLE_CANDIDATE'
    if by_id[current]['advisory_eligible']:selected=current;reason='FIXED_PRIORITY_EXISTING_BACKEND_NO_SPEED_CLAIM'
    elif operation=='replay'and backend==HYBRID and by_id[HYBRID]['advisory_eligible']:
        selected=HYBRID;reason='SAVED_BACKEND_ONLY_NO_MIGRATION'
    elif operation=='fit'and request['mode']=='fine'and by_id[current]['resource_feasible']is False and by_id[HYBRID]['advisory_eligible']:
        selected=HYBRID;reason='CUDA_DEFINITELY_INSUFFICIENT_SCOPED_CPU512_FEASIBLE'
    policy=binding();out=dict(schema_version=RESPONSE_V3 if v3 else RESPONSE,**policy,execution_contract_sha256=execution,
        guarded_policy_sha256=policy_digest(policy_binding()),qualification_table_digest=digest(registry_v3()if v3 else registry_v2()),
        intent_digest=digest(intent(request)),request_identity=intent(request),snapshot_digest=digest(snapshot),
        freshness=fresh,candidates=candidates,recommended_candidate=selected,selection_reason=reason,
        advisory_only=True,execution_authorized=False,automatic_selection_enabled=False,
        CPU_QUALIFIED=False,AUTO_SELECTION=False,PRODUCTION_ACTIVATION=False,estimated_seconds=None,reservation=False,
        input_validation='METADATA_ONLY_NOT_REDECODED',runtime_validation='WORKER_CONFIRMATION_REQUIRED',
        new_policy_numerical_qualification=False,no_quality_changes=True,snapshot_diagnostics=snapshot['diagnostics'])
    if v3:
        from .qualification import current_scope
        scope=current_scope()
        out.update(current_execution_contract_sha256=execution,current_guarded_policy_sha256=out['guarded_policy_sha256'],
            evidence_execution_contract_sha256=scope['execution_contract_sha256'],
            evidence_guarded_policy_sha256=scope['evidence_policy_sha256'],
            evidence_status='FIXED_PAIR_NATIVE512_REPLAY_ACCEPTED',evidence_scope_id=scope['scope_id'],
            qualification_evidence=scope['evidence'],evidence_qualification_conditions=scope['qualification_conditions'])
        for row in candidates:
            if row['id']==HYBRID:
                row.update(evidence_status='FIXED_PAIR_NATIVE512_REPLAY_ACCEPTED',
                    evidence_scope_id=scope['scope_id'],evidence=scope['evidence'],
                    evidence_execution_contract_sha256=scope['execution_contract_sha256'],
                    evidence_guarded_policy_sha256=scope['evidence_policy_sha256'],
                    current_guarded_policy_sha256=out['guarded_policy_sha256'],
                    qualification_table_digest=out['qualification_table_digest'],
                    resource_explanation=_resource_explanation(request,snapshot,row,scope))
    out['plan_digest']=digest(out);return out

def _resource_explanation(request,snapshot,row,scope):
    """Expose original staged checks; historical peaks never alter a formula."""
    existing=request.get('existing',snapshot['product'])
    return dict(required_envelope=copy.deepcopy(row.get('formula',{}).get('verified_envelope_bytes')),
        available=dict(physical=copy.deepcopy(snapshot['physical']),commit=copy.deepcopy(snapshot['commit']),
            gpu_effective_free_bytes=row.get('resource_checks',{}).get('effective_vram_bytes')),
        existing=copy.deepcopy(existing),existing_source='explicit_request'if 'existing'in request else'snapshot',
        blocking_stages=[r['stage']for r in row['stages']if r.get('physical_free_check')is False or r.get('commit_headroom_check')is False],
        blockers=list(row['blockers']),unknown_reasons=list(row['unknowns']),
        qualification_conditions=copy.deepcopy(scope['qualification_conditions']),
        qualification_floor_is_production_rule=False,historical_peaks_used_as_requirements=False,
        estimates_preallocate_nothing=True,execution_authorized=False)
