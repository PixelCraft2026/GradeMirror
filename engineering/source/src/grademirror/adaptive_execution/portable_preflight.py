"""Real role payload checks stop before all image/model/solver work."""
from pathlib import Path
from ..common import strict_load
from ..cpu_replay.contract import need
from . import portable_contract as k

def entry_checks(role,path,c):
    need(role.startswith('entry_'),'PORTABLE_ENTRY_ONLY')
    if role=='entry_fit':
        from ..cpu_first_fit.worker import validate_payload
        validate_payload(strict_load(Path(path)),'fit')
    elif role=='entry_cpu':
        from ..engine.cpu_postprocess.service import worker
        worker(Path(path),entry_only=True)
    need(k.enabled(c) and c['TEST_ONLY'] is False,'PORTABLE_REAL_DOMAIN_NO_TEST_BYPASS')
    from ..common import atomic_json,GMError
    checks=[]
    def refuses(name,call):
        try:call()
        except GMError as error:
            need(error.code.startswith('FIRST_ROLE_OR_DRY_BOUNDARY:'),'PORTABLE_REFUSAL_WRONG_CAUSE:'+str(error))
            checks.append(dict(name=name,before_body=True,code=error.code,science=0));return
        need(False,'PORTABLE_DRY_GUARD_DID_NOT_REFUSE:'+name)
    effective=role.removeprefix('entry_')
    if effective in ('fit','r0'):
        from ..engine.independent_r0.core import Field
        refuses('real_model_constructor_boundary',lambda:Field({},43))
    elif effective=='prepare':
        from ..engine.cpu_fine_image.execution import range_scan
        refuses('real_range_boundary',lambda:range_scan(None,None,None))
    elif effective=='solver':
        from ..engine.independent_r0_p3_lld10.p3_solver import solve
        refuses('real_solver_boundary',lambda:solve(None,None,None,None))
    elif effective=='cpu':
        from ..engine.cpu_postprocess.service import worker
        refuses('real_CPU_execute_boundary',lambda:worker(Path(path),entry_only=False))
    elif effective=='host':
        from ..engine.group15_codec import read_source
        refuses('real_photo_decode_boundary',lambda:read_source(c['source']['path'],strict_group15=False,development_pair=True))
    from ..cpu_replay.contract import ROOT
    try:
        with (ROOT/'validation/goldens/expected.json').open('rb') as stream:stream.read(1)
    except GMError as error:
        need(error.code=='PORTABLE_GOLDENS_FORBIDDEN_TO_CALCULATOR','PORTABLE_GOLDEN_REFUSAL_WRONG_CAUSE')
        checks.append(dict(name='validator_goldens_not_readable',before_first_byte=True,code=error.code))
    else:need(False,'PORTABLE_CALCULATOR_READ_GOLDENS')
    receipt_dir=Path(path).parent
    if effective=='host':
        receipt_dir=receipt_dir/'host'
    atomic_json(receipt_dir/'portable-dry-refusals.json',dict(checks=checks,science=0,image_decode=0,model_construct=0))
    if effective in ('owner','prepare'):
        from ..cpu_replay import runtime
        parent=Path(path).parent
        child='host' if effective=='owner' else 'solver'
        work=parent/child
        payload=parent/'host-payload.json' if child=='host' else work/'solver-spec.json'
        job,_=runtime.launch('entry_'+child,payload,work,memory=(3 if child=='solver' else 6)*1024**3)
        try:
            wait=runtime.wait(job,work,timeout=120)
            need(wait['exit_code']==0 and wait['actual_worker_handle_wait'] is True and wait['tree_empty'] is True,
                 'GM064_NESTED_DRY_WAIT',child)
            need(strict_load(work/'entry-contract-result.json')['status']=='GM064_ENTRY_PASS','GM064_NESTED_DRY_ENTRY',child)
        finally:
            job.close()
