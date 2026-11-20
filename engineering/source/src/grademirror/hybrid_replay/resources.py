"""OS metadata and fixed staged admission, never array/object enumeration."""
import ctypes, time
from ctypes import wintypes as wt
from pathlib import Path
from ..common import GMError, atomic_json, strict_load
from .audit import append
GIB=1024**3


def memory():
    class Info(ctypes.Structure):
        _fields_=[('cb',wt.DWORD)]+[(n,ctypes.c_size_t)for n in('total','limit','peak','physical','available','cache','kernel','paged','nonpaged','page_size')]+[(n,wt.DWORD)for n in('handles','processes','threads')]
    info=Info();info.cb=ctypes.sizeof(info)
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info),ctypes.sizeof(info)):
        raise GMError('RAM_STATUS_UNKNOWN','Cannot read OS physical/commit headroom',5)
    return dict(available=info.available*info.page_size,commit_headroom=(info.limit-info.total)*info.page_size)


def admit(path,plan,stage,*,qualification=False,cancelled=lambda:False):
    if plan.get('fit_contract')=='gm023-hybrid-fit-v1':
        from ..hybrid_fit.audit import admit as new_admit
        return new_admit(path,plan,stage,qualification=qualification,cancelled=cancelled)
    from ..resource_execution.admission import recheck,remaining
    recheck(path,stage)
    path=Path(path);state=strict_load(path/'resource-wait.json')if(path/'resource-wait.json').exists()else dict(wait_seconds=0.)
    before=time.monotonic();table=plan['capacities']
    required=table[stage]
    physical=required['additional_bytes']+table['physical_margin'];commit=required['minimum_commit_headroom']
    from ..light.resources import mixed_stage
    physical,commit=mixed_stage(plan,stage,physical,commit)
    if qualification:physical=max(physical,12*GIB);commit=max(commit,16*GIB)
    while True:
        if cancelled():raise GMError('CANCELLED','Cancelled during resource wait',8)
        now=memory();elapsed=time.monotonic()-before;total=state['wait_seconds']+elapsed
        append(path/'resource-admission.jsonl',dict(stage=stage,monotonic=time.monotonic(),required_physical=physical,required_commit=commit,**now))
        if now['available']>=physical and now['commit_headroom']>=commit:
            atomic_json(path/'resource-wait.json',dict(wait_seconds=total));return now
        if total>=remaining(path,600):
            atomic_json(path/'resource-wait.json',dict(wait_seconds=total))
            raise GMError('RESOURCE_WAIT_CAP','Fixed staged resource admission expired',5)
        time.sleep(min(.5,max(0,remaining(path,600)-total)))
