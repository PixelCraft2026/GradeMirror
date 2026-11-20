"""Fixed argv adapters; retain the original OwnedJob and solver mathematics."""
import os,sys,time
from pathlib import Path
from ...winproc import OwnedJob,current_identity
from ...common import atomic_json
from .contract import ROOT,need
NUMERIC='__historical_runtime_not_shipped__'
SOLVER='__historical_runtime_not_shipped__'
IO='__historical_runtime_not_shipped__'
def argv(role,payload,exe=NUMERIC):return [exe,'-B','-X','utf8',str(ROOT/'tools/gm050_stage_worker.py'),role,str(payload)]
def launch(job,role,payload,exe=NUMERIC):
    p=Path(payload);env=dict(os.environ,GM050_ROLE=role,GM050_OUT=p.parent.as_posix(),PYTHONPATH=str(ROOT/'src')+';'+str(ROOT/'tests/helpers'),TEMP=p.parent.as_posix(),TMP=p.parent.as_posix())
    a=argv(role,p,exe);atomic_json(p.parent/'spawn-plan.json',dict(argv=a,role=role,parent=current_identity(),before_CreateProcess=True,slots=4,environment=env))
    return job.spawn(a,str(ROOT),env)
def solve(run,h,target,native_wh):
    from ..independent_r0_p3_lld10 import solver_process as old
    original=old.spawn_solver
    def spawn(job,where,*,probe=False):
        need(not probe,'NO_SOLVER_PROBE_IN_SCIENCE');return launch(job,'solver',Path(where)/'solver-spec.json',SOLVER)
    old.spawn_solver=spawn
    try:return old.solve(run,h,target,native_wh,identity_control=False)
    finally:old.spawn_solver=original
