"""Application identity adapter over the original commit/reclaim coordinator."""
from pathlib import Path
from ..common import strict_load
from ..cpu_replay.contract import verify
from ..cpu_recipe.validation import complete
from ..cpu_first_fit.publication import validate_worker_waits
from .intent import need

def recipe_status(spec):
    directory=Path(spec['semantics']['bundle_directory'])
    if not (directory/'COMPLETE.json').exists():
        from .context import selected
        c=selected(cleanup=True)
        if c is not None:
            from .first_publication import recover
            recover(spec,c)
        if not (directory/'COMPLETE.json').exists():return dict(recipe_available=False,recipe_state='INTERRUPTED_OR_UNCOMMITTED')
    pending=False
    try:
        m,_=complete(directory)
        sem=spec['semantics'];request=sem['application_intent']
        if request['operation']!='replay':
            need(directory.resolve().is_relative_to(Path(sem['output_root']).resolve()),'APPLICATION_PRODUCER_RECIPE_SCOPE')
            from ..store import locked
            from ..common import atomic_json
            record=directory/'.pack/record.json'
            value=strict_load(record) if record.is_file() else None
            if value and value['state'] in ('RUNNING','INTERRUPTED','FAILED'):
                from .context import selected
                current=selected()
                if current is None or current['output_root']!=sem['output_root']:
                    # COMPLETE is authoritative saved data. A parent/cold
                    # reader has no package writer authority for this child.
                    pending=True
                else:
                    with locked(directory/'.pack/target.lock'):
                        complete(directory);value=strict_load(record)
                        atomic_json(record,dict(value,state='SUCCEEDED',recovered_from_complete=True))
    except Exception as e:return dict(recipe_available=False,recipe_state='INVALID',recipe_error=str(e))
    return dict(recipe_available=True,recipe_state='PERSISTENT_COMPLETE',recipe_directory=str(directory),
        recipe_bundle_id=m['bundle_id'],recipe_math_id=m['math_id'],producer_record_recovery_pending=pending)

def verify_commit(mark,spec,path):
    need(set(mark)=={'state','job_id','attempt_id','intent_sha256','kind','artifacts','result_lease'} and
        mark['state']=='COMPLETE' and all(mark[k]==spec[k] for k in ('job_id','attempt_id','intent_sha256','kind')),'APPLICATION_COMMIT_IDENTITY')
    names=set()
    for item in mark['artifacts']:
        verify(item);p=Path(item['path']);need(p.parent==path and p.name not in names,'APPLICATION_COMMIT_ARTIFACT');names.add(p.name)
    need(names=={'compute-result.json','compute-cleanup.json','replay-identity.json'},'APPLICATION_ARTIFACT_SET')
    sem=spec['semantics'];identity=strict_load(path/'replay-identity.json');result=strict_load(path/'compute-result.json')
    directory=Path(sem['bundle_directory'])
    if directory.exists():
        m,_=complete(directory);need((m['bundle_id'],m['math_id'])==(identity['bundle_id'],identity['math_id']),'APPLICATION_RECIPE_CHANGED')
    else:need((path/'commit.json').is_file() and strict_load(path/'commit.json')==mark,'APPLICATION_RECIPE_MISSING')
    expected={k:sem[k] for k in ('backend_id','executor_build','source_sha256')}
    expected.update(bundle_id=identity['bundle_id'],math_id=identity['math_id'])
    need(identity==expected and
        result['status']=='COMPLETE' and result['mode']==sem['mode'],'APPLICATION_RESULT_IDENTITY')
    validate_worker_waits(strict_load(path/'compute-cleanup.json'))
    lease=mark['result_lease']
    need(set(lease)=={'backend_id','handle_id','generation','owner_instance_id','project_revision','recipe_sha256',
        'job_id','attempt_id','bundle_id','math_id','executor_build','pool_token','owner','host'} and
        all(lease[k]==identity[k] for k in ('backend_id','bundle_id','math_id','executor_build')) and
        all(lease[k]==spec[k] for k in ('job_id','attempt_id')) and
        lease['project_revision']==lease['recipe_sha256']==identity['math_id'],'APPLICATION_RESULT_LEASE')
