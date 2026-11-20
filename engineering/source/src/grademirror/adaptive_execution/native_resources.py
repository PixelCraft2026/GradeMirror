"""Exact native OS admission; unavailable readings never become zero."""
import ctypes
from pathlib import Path
from ..common import atomic_json
from .native_contract import strict_load
from ..cpu_replay.contract import Memory,need

def admission(c):
    from .portable_contract import enabled as portable_enabled
    if portable_enabled(c):
        from .portable_resources import admit
        return admit(c,'native_start',cold=True)
    from .calibration_contract import measurement
    if measurement(c):
        from .calibration_resources import admission as calibration_admission
        return calibration_admission(c)
    if c.get('schema')=='cpu-application-execution-v6':
        from .low_memory_contract import admit
        return admit(c,'native_start')
    if 'resource_selection' in c:
        from .resource_policy_v2 import admit_context
        admit_context(c,'frame_start')
    m=Memory();m.length=ctypes.sizeof(m);ok=bool(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)))
    row=dict(stage='native_start',physical=int(m.availablePhysical) if ok else None,commit=int(m.availablePageFile) if ok else None,physical_floor=6*1024**3,commit_floor=16*1024**3,native=True,unknown=not ok)
    atomic_json(Path(c['output_root'])/'current-admission.json',row)
    need(ok,'RESOURCE_UNKNOWN',str(row));need(row['physical']>=row['physical_floor'] and row['commit']>=row['commit_floor'],'RESOURCE_NOT_READY',str(row))
    return row
