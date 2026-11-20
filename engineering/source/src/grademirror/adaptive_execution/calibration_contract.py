"""GM063 fixed24MP measurement capability; no expansion of old tickets."""
from pathlib import Path
from ..common import strict_load as _strict_load,reject_reparse

def strict_load(path):
    return _strict_load(Path(path))
from ..cpu_replay.contract import ROOT,FLAGS,RESOURCE_ENVELOPE,binding,verify,need

E=ROOT/'docs/evidence/gm-063/20261104-v1'
W=ROOT/'tests/workspaces/g63/20261104-v1'
I=ROOT/'docs/evidence/gm-063/20261104-plan-v1'
TASK_SHA='3f8e2b7a70eeeb30ddc69f2dcf88ca33ac01bfc555614ffab6a86a13556c7860'


def pointer(path):
    return path is not None and Path(path).resolve().parent==E/'qualifications'


def measurement(c):
    return bool(c and isinstance(c,dict) and pointer(c.get('qualification',{}).get('path')))


def authorize(path,request,*,cleanup=False):
    from .intent import normalize
    from .build import current_closure
    p=Path(path).resolve();need(pointer(p),'GM063_QUALIFICATION_POINTER');reject_reparse(p)
    index=strict_load(E/'qualification-index.json');need(str(p)in index,'GM063_QUALIFICATION_INDEX');verify(index[str(p)])
    q=strict_load(p)
    need(q['task']=='GM-063' and q['measurement_schema']=='gm063-fixed24-measurement-v1' and
         q['schema']=='adaptive-first-qualification-v1' and q['manifest_path']==str(p) and
         q['phase']in('dry','native') and q['TEST_ONLY'] is False,'GM063_QUALIFICATION_FIELDS')
    need(q['taskbook']==binding(ROOT/'docs/tasks/GM-063-existing-chain-resource-calibration-v1-sol.md') and
         q['taskbook']['sha256']==TASK_SHA and q['authorization']==binding(E/'authorization.json'),'GM063_TASK_AUTHORITY')
    need(q['paths']==dict(evidence=str(E),workspace=str(W)) and q['resource_envelope']==RESOURCE_ENVELOPE,'GM063_RESOURCE_SCOPE')
    from .first_contract import LIMITS
    need(q['limits']==LIMITS,'GM063_ORIGINAL_INTERNAL_LIMITS')
    fixed=strict_load(E/'fixed-inputs.json');verify(q['fixed_inputs']);need(q['fixed_inputs']==binding(E/'fixed-inputs.json'),'GM063_FIXED_INPUTS')
    for key in ('source','reference','source_identity','reference_identity','settings','profile','color_policy'):
        need(q[key]==fixed[key],'GM063_INPUT_BINDING:'+key)
    need(q['source_identity']['shape']==[6000,4000,3] and q['reference_identity']['shape']==[1536,1024,3] and
         q['settings']['tile_side']==512 and q['settings']['updates']==1000,'GM063_GEOMETRY_AND_MATH')
    r=normalize(request);need(r==q['request'] and r['execution_preference']=='cpu' and
         r['operation']in('fit','refine','replay'),'GM063_EXACT_REQUEST')
    need(r['source']==dict(file=q['source'],metadata=dict(width=4000,height=6000,bits=8,format='JPEG')),'GM063_REQUEST_SOURCE')
    if r['operation']in('fit','refine'):
        need(r['reference']==dict(file=q['reference'],metadata=dict(width=1024,height=1536,bits=8,format='PNG')),'GM063_REQUEST_REFERENCE')
    else:need(r['reference'] is None,'GM063_REPLAY_NO_REFERENCE_DECODE')
    need((r['operation']=='fit')==(q['parent_package'] is None),'GM063_PARENT_PHASE')
    if q['parent_package'] is not None:
        pkg=q['parent_package'];need(r['recipe']=={k:pkg[k]for k in('directory','bundle_id','math_id','version')},'GM063_PARENT_REQUEST')
        need(Path(pkg['directory']).resolve().is_relative_to(W),'GM063_NO_OLD_WEIGHTS')
        for item in pkg['files']:verify(item)
    verify(q['runtime']['inventory']);need(q['runtime']['status']=='KNOWN_EXACT_RUNTIME','GM063_RUNTIME_UNKNOWN')
    need(Path(q['runtime']['inventory']['path']).resolve().is_relative_to(E/'runtime'),'GM063_RUNTIME_POINTER')
    for row in q['interpreters'].values():verify(row)
    need(cleanup or q['executor_build']==current_closure(native=True),'GM063_BUILD_CHANGED')
    if q['phase']=='native' and not cleanup:
        verify(q['p0']);proof=strict_load(q['p0']['path'])
        need(proof['status']=='GM063_CPU_ENTRY_PASS' and proof['executor_build']==q['executor_build']['sha256'],'GM063_P0_REQUIRED')
    if not cleanup:verify(q['source']);verify(q['reference'])
    return q


def expected_tiles(c,legacy=140):
    from .portable_contract import enabled as portable_enabled
    if portable_enabled(c):
        from .portable_contract import expected_tiles as portable_tiles
        return portable_tiles(c)
    if not measurement(c):return legacy
    need(c['source_identity']['shape']==[6000,4000,3],'GM063_TILE_INPUT')
    return 96


def validate_parent(pkg,q,m):
    need(Path(pkg['directory']).resolve().is_relative_to(W) and
         (m['bundle_id'],m['math_id'],m['version'])==tuple(pkg[k]for k in('bundle_id','math_id','version')),
         'GM063_PARENT_IDENTITY')
    need(m['provenance']['TEST_ONLY'] is False and
         m['mathematical']['inputs']==dict(source_identity=q['source_identity'],reference_identity=q['reference_identity']) and
         m['mathematical']['settings']==q['settings'],'GM063_PARENT_INPUT_AND_MATH')
    for item in pkg['files']:verify(item)
    proof=pkg['producer']
    for item in proof.values():verify(item)
    wait=strict_load(proof['root_wait']['path'])
    need(all(wait[k] is True for k in('actual_root_wait','actual_all_wait','tree_empty','exact')) and wait['root_exit']==0,
         'GM063_PARENT_ACTUAL_EXIT')
    result=strict_load(proof['compute']['path'])
    need(result['status']=='COMPLETE' and result['source_pixel_sha256']==q['source_identity']['pixel_sha256'],'GM063_PARENT_REAL_COMPUTE')
    reclaim=strict_load(proof['reclaim']['path'])
    need(reclaim['actual_wait'] is True and reclaim['tree_empty'] is True and reclaim['identities'] and
         all(x['exited'] is True for x in reclaim['identities']),'GM063_PARENT_RECLAIM')
    return True


def recipe_scope(c,spec,directory,m):
    q=authorize(c['qualification']['path'],c['request'],cleanup=True)
    need(Path(directory).resolve()==Path(c['bundle']).resolve()==Path(spec['semantics']['bundle_directory']).resolve(),
         'GM063_RESULT_DIRECTORY')
    need(m['mathematical']['inputs']==dict(source_identity=c['source_identity'],reference_identity=c['reference_identity']) and
         m['mathematical']['settings']==c['settings'] and m['provenance']['TEST_ONLY'] is False,'GM063_RESULT_INPUTS')
    if c['request']['operation']=='replay':return validate_parent(q['parent_package'],q,m)
    need(m['producer_build']==c['executor_build'] and all(m['provenance'][k]==spec[k]for k in('job_id','attempt_id')) and
         m['version']==(2 if c['phase']=='fit' else 3),'GM063_RESULT_PRODUCER')
    return True
