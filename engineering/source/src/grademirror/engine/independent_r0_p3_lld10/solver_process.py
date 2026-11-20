"""One CPU solver child, nested in the numeric operation's owned Job."""
import os
from pathlib import Path

from ...fine_resources import solver_job_bytes, solver_seconds
from ...solver_lifecycle import solve_owned
from ...winproc import OwnedJob
from .profile import LIMITS

P3_PY=Path('__historical_runtime_not_shipped__')
ROOT=Path(__file__).resolve().parents[4]

def spawn_solver(job,run,*,probe=False):
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',NUMEXPR_NUM_THREADS='4',PYTHONIOENCODING='utf-8')
    env.pop('PYTHONPATH',None)
    argv=[str(P3_PY),'-B',str(ROOT/'tools/gm008_solver_worker.py')]
    if probe:argv.append('ownership-probe')
    argv.append(str(run))
    from ...light.runtime import operation, worker
    if operation() is not None:
        argv,env=worker("solver",(("ownership-probe",run) if probe else (run,)),run)
    return job.spawn(argv,str(ROOT),env)

def solve(run,h,target,native_wh,*,identity_control=False,cancelled=lambda:False,deadline=None):
    from ...light.resources import active_plan
    active=active_plan()
    if active is None:
        child_limit=solver_job_bytes(native_wh,(h.shape[1],h.shape[0]))
    else:
        source,reference=active["source"],active["reference"]
        child_limit=active["selection"]["jobs"]["solver"]
        if (tuple(native_wh)!=(source["width"],source["height"])
            or (h.shape[1],h.shape[0])!=(reference["width"],reference["height"]) or child_limit<=0):
            raise ValueError("LIGHT_SOLVER_JOB_BINDING")
    seconds=solver_seconds(h.shape[0]*h.shape[1])

    def launch(job, spec, retain_job):
        return job, spawn_solver(job, run)

    return solve_owned(run, h, target, native_wh, seconds=seconds, cancelled=cancelled,
                       deadline=deadline, make_job=lambda: OwnedJob(child_limit), launch=launch,
                       cpu_entry=False, identity_control=identity_control, child_limit=child_limit,
                       archive_bytes=LIMITS["archive_bytes"])
