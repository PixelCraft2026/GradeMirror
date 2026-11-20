"""CPU solver transport with the packaged entry acknowledgement."""
from pathlib import Path

from ..cpu_replay import runtime
from ..engine.independent_r0_p3_lld10.profile import LIMITS
from ..solver_lifecycle import solve_owned


def solve(run, h, target, native_wh, *, cancelled=lambda: False, deadline=None):
    from ..light.runtime import operation
    from ..fine_resources import solver_seconds

    run = Path(run)
    packaged = operation() is not None
    seconds = solver_seconds(h.shape[0] * h.shape[1]) if packaged else 1000

    def launch(job, spec, retain_job):
        if packaged:
            return runtime.launch("solver", spec, run, retain_job=retain_job)
        return runtime.launch("solver", spec, run, memory=3 * 1024**3)

    return solve_owned(run, h, target, native_wh, seconds=seconds, cancelled=cancelled,
                       deadline=deadline, make_job=None, launch=launch, cpu_entry=True,
                       identity_control=False, child_limit=None, archive_bytes=LIMITS["archive_bytes"])
