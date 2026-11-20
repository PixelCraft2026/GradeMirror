"""The complete recipe and resident result are two independent transactions."""
from pathlib import Path
from ..common import strict_load
from ..cpu_replay.contract import need, verify, binding
from ..cpu_recipe.generated import complete


def validate_worker_waits(cleanup):
    need(type(cleanup) is dict and cleanup.get('workers_actual_wait') is True and
         cleanup.get('worker_trees_empty') is True,'FIRST_FIT_COMPUTE_WAIT_SUMMARY')
    for name in ('R0_wait','CPU_wait','fit_wait'):
        row=cleanup.get(name)
        need(row is None or (type(row) is dict and row.get('actual_wait') is True and
             row.get('tree_empty') is True and type(row.get('exit_code')) is int and row['exit_code']==0 and
             row.get('actual_worker_handle_wait') is True and type(row.get('actual_worker_identity')) is dict),
             'FIRST_FIT_COMPUTE_ACTUAL_WAIT')


def recipe_status(spec):
    directory = Path(spec['semantics']['bundle_directory'])
    if not (directory / 'COMPLETE.json').is_file():
        return dict(recipe_available=False, recipe_state='INTERRUPTED_OR_UNCOMMITTED')
    try:
        m, _ = complete(directory)
    except BaseException as error:
        return dict(recipe_available=False, recipe_state='INVALID', recipe_error=getattr(error,'code',repr(error)))
    return dict(recipe_available=True, recipe_state='PERSISTENT_COMPLETE', recipe_bundle_id=m['bundle_id'],
                recipe_math_id=m['math_id'], recipe_directory=str(directory))


def verify_result_commit(mark, spec, path):
    fields = {'state','job_id','attempt_id','intent_sha256','kind','artifacts','result_lease'}
    need(set(mark) == fields and mark['state'] == 'COMPLETE' and mark['kind'] == spec['kind'] and
         all(mark[k] == spec[k] for k in ('job_id','attempt_id','intent_sha256')), 'FIRST_FIT_COMMIT_FIELDS')
    names=set()
    for a in mark['artifacts']:
        verify(a)
        p=Path(a['path'])
        need(p.parent == path and p.name not in names, 'FIRST_FIT_COMMIT_SCOPE')
        names.add(p.name)
    need(names == {'compute-result.json','compute-cleanup.json','replay-identity.json'}, 'FIRST_FIT_COMMIT_ARTIFACTS')
    result = strict_load(path / 'compute-result.json')
    cleanup = strict_load(path / 'compute-cleanup.json')
    ident = strict_load(path / 'replay-identity.json')
    sem=spec['semantics']
    directory=Path(sem['bundle_directory'])
    if directory.exists():
        m,_=complete(directory)
        need((m['bundle_id'],m['math_id'])==(ident['bundle_id'],ident['math_id']),'FIRST_FIT_COMMIT_RECIPE_CHANGED')
    else:
        # Moving a previously committed bundle cannot erase immutable result
        # history. Initial publication still requires the actual COMPLETE.
        need((path/'commit.json').is_file() and strict_load(path/'commit.json')==mark,'FIRST_FIT_INITIAL_RECIPE_MISSING')
    expected=dict(backend_id=sem['backend_id'], bundle_id=ident['bundle_id'], math_id=ident['math_id'],
                  executor_build=sem['executor_build'], source_sha256=sem['source_sha256'])
    need(ident == expected and result['status'] == 'COMPLETE' and result['mode'] == 'fast' and
         cleanup['workers_actual_wait'] is True and cleanup['worker_trees_empty'] is True, 'FIRST_FIT_RESULT_IDENTITY')
    lease=mark['result_lease']
    need(all(lease[k] == expected[k] for k in ('backend_id','bundle_id','math_id','executor_build')) and
         all(lease[k] == spec[k] for k in ('job_id','attempt_id')), 'FIRST_FIT_RESULT_LEASE')
