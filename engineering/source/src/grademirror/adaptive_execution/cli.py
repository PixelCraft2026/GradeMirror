"""CLI delegates to the same side-effect-free application facade as Qt."""
from pathlib import Path
from ..common import strict_load,GMError
from . import planner,service

def dispatch(a):
    action='keep_alive' if a.adaptive_command=='keep-alive' else a.adaptive_command
    if action=='plan':return planner.plan(strict_load(Path(a.request)),strict_load(Path(a.snapshot)) if a.snapshot else None)
    if action=='open':return service.open_recipe(a.path)
    if action=='submit':return service.submit(strict_load(Path(a.plan)),a.request_key,Path(a.workspace),experimental=a.experimental,qualification=a.qualification)
    values={}
    if action=='cancel':
        if bool(a.child_job_id)!=bool(a.child_attempt):
            raise GMError('ADAPTIVE_CANCEL_ADDRESS_REQUIRED','Both --child-job-id and --child-attempt are required together.',2)
        if a.child_job_id:values['expected_child']=dict(job_id=a.child_job_id,attempt_id=a.child_attempt)
    if action in ('preview','export','release','keep_alive'):values['binding']=strict_load(Path(a.binding))
    if action in ('preview','export'):
        values.update(destination=str(Path(a.out).resolve()),alpha=a.alpha)
        values['request_id' if action=='preview' else 'request_key']=a.request_key
    return service.invoke(a.workspace,action,a.job_id,a.attempt,**values)
