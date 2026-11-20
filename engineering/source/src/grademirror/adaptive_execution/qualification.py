"""Only the frozen engineering factory can authorize small-frame controls."""
from pathlib import Path
from ..common import strict_load
from ..cpu_replay.contract import ROOT,binding,verify
from .build import current_closure as executor_closure
from .intent import need,normalize
FACTORY=ROOT/'docs/evidence/gm-055/20261031-v1/factory.json'
R1_FACTORY=ROOT/'docs/evidence/gm-055-r1/20261031-v1/factory.json'
R1_QT_FACTORY=ROOT/'docs/evidence/gm-055-r1/20261031-v1/qt-completion-1/factory.json'
GM056_FIXTURE_FACTORY=ROOT/'docs/evidence/gm-056/20261101-v1/fixtures/factory.json'
GM057_FIXTURE_FACTORY=ROOT/'docs/evidence/gm-057/20261101-v1/fixtures/factory.json'
GM058_FIXTURE_FACTORY=ROOT/'docs/evidence/gm-058/20261102-v1/fixtures/factory.json'
R1_BASES=(R1_FACTORY,R1_QT_FACTORY,GM056_FIXTURE_FACTORY,GM057_FIXTURE_FACTORY,GM058_FIXTURE_FACTORY)

def factory_for(manifest):
    path=Path(manifest).resolve()
    for base in (FACTORY,)+R1_BASES:
        if path==base or path.parent==base.parent/'factories':return base
    need(False,'EXECUTION_NOT_QUALIFIED')

def used_assets(q,r):
    paths={Path(r['source']['file']['path']).resolve()}
    if r['reference'] is not None:paths.add(Path(r['reference']['file']['path']).resolve())
    if r['operation']=='fit':paths.add(Path(q['parameter_file']).resolve())
    if r['operation'] in ('fit','refine') and r['quality']=='fine':paths.add(Path(q['b_file']).resolve())
    table={Path(row['path']).resolve():row for row in q['assets']}
    need(len(table)==len(q['assets']) and paths<=set(table),'QUALIFICATION_ASSETS')
    return [table[p] for p in sorted(paths)]

def authorize(manifest,request,*,cleanup=False):
    from .portable_contract import pointer as portable_pointer
    if portable_pointer(manifest):
        from .portable_contract import authorize as portable_authorize
        return portable_authorize(manifest,request,cleanup=cleanup)
    from .calibration_contract import pointer as calibration_pointer,authorize as calibration_authorize
    if calibration_pointer(manifest):return calibration_authorize(manifest,request,cleanup=cleanup)
    from .independent_contract import pointer as independent_pointer,authorize as independent_authorize
    if independent_pointer(manifest):return independent_authorize(manifest,request,cleanup=cleanup)
    from .first_contract import pointer as first_pointer,authorize as first_authorize
    if first_pointer(manifest):return first_authorize(manifest,request,cleanup=cleanup)
    from .native_contract import pointer,authorize as native_authorize
    if manifest is not None and pointer(manifest):return native_authorize(manifest,request,cleanup=cleanup)
    need(manifest is not None,'EXECUTION_NOT_QUALIFIED','当前应用执行版本尚未通过真实计算资格。')
    path=Path(manifest).resolve();base=factory_for(path);registry=base.with_name('factory-index.json')
    if registry.exists():
        index=strict_load(registry);need(str(path) in index,'EXECUTION_NOT_QUALIFIED');verify(index[str(path)])
    else:
        need(path==base,'EXECUTION_NOT_QUALIFIED');verify(strict_load(base.with_name('factory.binding.json')))
    q=strict_load(path);r=normalize(request)
    fields={'schema','TEST_ONLY','science_all_zero','manifest_path','executor_build','workspace_root','observer_roots',
        'source_identity','reference_identity','request_source','request_reference','parameter_file','b_file','assets','frame_context','fault_points'}
    need(fields<=set(q)<=fields|({'cases','context_version','lease_policy'} if base in R1_BASES else {'cases'}) and set(q['frame_context'])=={'runtime','interpreters','settings','profile','color_policy'},'QUALIFICATION_FIELDS')
    need(q['schema']==('adaptive-engineering-factory-v2' if base in R1_BASES else 'adaptive-engineering-factory-v1') and q['TEST_ONLY'] is True and
        q['science_all_zero'] is True and q['manifest_path']==str(path),'QUALIFICATION_INVALID')
    if base in R1_BASES:
        from .residency import POLICY,SCHEMA
        need(q.get('context_version')==SCHEMA and q.get('lease_policy')==POLICY,'QUALIFICATION_LEASE_POLICY')
    need(cleanup or q['executor_build']==executor_closure(),'QUALIFICATION_BUILD_MISMATCH')
    need(r['source']==q['request_source'] and (r['reference'] is None or r['reference']==q['request_reference']) and
        max(r['source']['metadata'][k] for k in ('width','height'))<=64,'QUALIFICATION_INPUT_MISMATCH')
    for role in ('control','io','desktop'):verify(q['frame_context']['interpreters'][role])
    verify(q['frame_context']['runtime']['inventory'])
    if not cleanup:
        for row in used_assets(q,r):verify(row)
    return q
