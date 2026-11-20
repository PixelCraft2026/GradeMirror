"""Independent fine recipe and image-result commit validation."""
from pathlib import Path
from ..common import strict_load
from ..cpu_replay.contract import need,verify
from ..cpu_recipe.generated_fine import complete
from ..cpu_first_fit.publication import validate_worker_waits


def validate_prepare_waits(cleanup):
    for key in ('P3_wait','solver_wait'):
        row=cleanup.get(key)
        need(type(row) is dict and all(row.get(k) is True for k in ('actual_wait','actual_worker_handle_wait','tree_empty')) and
             type(row.get('actual_worker_identity')) is dict and type(row.get('exit_code')) is int and
             row['exit_code']==0 and row.get('forced') is False,'REFINE_PREPARE_ACTUAL_WAIT')


def recipe_status(spec):
    sem=spec['semantics'];need(type(sem.get('refine_prepare')) is bool,'REFINE_RECIPE_MODE')
    directory=Path(sem['bundle_directory'])
    # Only the producer coordinator owns the recipe transaction. A cold
    # consumer verifies the committed input without taking a writable lock.
    if sem['refine_prepare'] and directory.is_dir():
        from ..cpu_recipe.generated_fine import recover
        return recover(directory)
    if not (directory/'COMPLETE.json').is_file():
        return dict(recipe_available=False,recipe_state='INTERRUPTED_OR_UNCOMMITTED')
    try:m,_=complete(directory)
    except BaseException as error:
        return dict(recipe_available=False,recipe_state='INVALID',recipe_error=getattr(error,'code',repr(error)))
    return dict(recipe_available=True,recipe_state='PERSISTENT_COMPLETE',recipe_bundle_id=m['bundle_id'],
                recipe_math_id=m['math_id'],recipe_directory=str(directory))


def verify_result_commit(mark,spec,path):
    need(set(mark)=={'state','job_id','attempt_id','intent_sha256','kind','artifacts','result_lease'} and
         mark['state']=='COMPLETE' and all(mark[k]==spec[k] for k in ('job_id','attempt_id','intent_sha256','kind')),
         'REFINE_RESULT_COMMIT_FIELDS')
    names=set()
    for item in mark['artifacts']:
        verify(item);p=Path(item['path']);need(p.parent==path and p.name not in names,'REFINE_COMMIT_SCOPE');names.add(p.name)
    need(names=={'compute-result.json','compute-cleanup.json','replay-identity.json'},'REFINE_RESULT_ARTIFACT_SET')
    sem=spec['semantics'];identity=strict_load(path/'replay-identity.json');result=strict_load(path/'compute-result.json')
    directory=Path(sem['bundle_directory'])
    if directory.exists():
        m,_=complete(directory)
        need((m['bundle_id'],m['math_id'])==(identity['bundle_id'],identity['math_id']),'REFINE_RESULT_BUNDLE')
    else:need((path/'commit.json').is_file() and strict_load(path/'commit.json')==mark,'REFINE_INITIAL_BUNDLE_MISSING')
    need(identity==dict(backend_id=sem['backend_id'],bundle_id=identity['bundle_id'],math_id=identity['math_id'],
         executor_build=sem['executor_build'],source_sha256=sem['source_sha256']) and
         result['status']=='COMPLETE' and result['mode']=='fine','REFINE_RESULT_IDENTITY')
    cleanup=strict_load(path/'compute-cleanup.json');validate_worker_waits(cleanup)
    if sem['refine_prepare'] and not sem['TEST_ONLY']:validate_prepare_waits(cleanup)
    lease=mark['result_lease']
    need(all(lease[k]==identity[k] for k in ('backend_id','bundle_id','math_id','executor_build')) and
         all(lease[k]==spec[k] for k in ('job_id','attempt_id')),'REFINE_RESULT_LEASE')
