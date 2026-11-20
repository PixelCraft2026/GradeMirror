"""Current admission readings, preserved even when the stage cannot start."""
import ctypes
from pathlib import Path
from ..common import atomic_json
from ..cpu_replay.contract import Memory
from .intent import need

def admission(c):
    if c.get('schema')=='cpu-application-execution-v6':
        from .low_memory_contract import admit
        return admit(c,'frame_start')
    from .portable_contract import enabled as portable_enabled
    if portable_enabled(c):
        from .native_resources import admission as native_admission
        return native_admission(c)
    if 'resource_selection' in c:
        from .resource_policy_v2 import admit_context
        admit_context(c,'frame_start')
    if c.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v4') or c.get('schema')=='cpu-application-execution-v5' and c.get('TEST_ONLY') is False:
        from .native_resources import admission as native_admission
        return native_admission(c)
    m=Memory();m.length=ctypes.sizeof(m);ok=bool(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)))
    value=dict(stage='frame_start',physical=int(m.availablePhysical) if ok else None,commit=int(m.availablePageFile) if ok else None,
        physical_floor=4*1024**3,commit_floor=6*1024**3,native=False,unknown=not ok)
    atomic_json(Path(c['output_root'])/'current-admission.json',value)
    need(ok,'RESOURCE_UNKNOWN',str(value));need(value['physical']>=value['physical_floor'] and value['commit']>=value['commit_floor'],
        'RESOURCE_NOT_READY',str(value));return value
