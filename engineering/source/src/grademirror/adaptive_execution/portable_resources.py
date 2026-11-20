"""Stage-bound empirical admission; current allocations are never counted twice."""
import json,os,time
from pathlib import Path
from ..common import atomic_json,canonical_json
from ..cpu_replay.contract import ROOT,binding,need
from . import portable_contract as k
from .portable_policy import evaluate,margin,POLICY
_last={}
_pressure={}

def model(c):
    q=k.read(c['qualification']['path']);v=k.read(k.package_member('validation/resource-model.json'))
    need(v['policy']==POLICY and q['resource_model']==binding(ROOT/'validation/resource-model.json'),'PORTABLE_RESOURCE_MODEL_CHANGED')
    return q,v

def admit(c,name,*,role=None,cold=False,facts=None):
    from .memory_snapshot import ram_facts
    q,v=model(c);facts=ram_facts() if facts is None else facts
    if cold:
        target=v['cold'][q['request']['quality']];occupied=facts['product']
        ram,commit=target['rss_bytes'],target['private_bytes']
        oram,ocommit=occupied['rss_bytes'],occupied['private_bytes']
    else:
        target=v['roles'][role or 'host'];ram,commit=target['rss_bytes'],target['private_bytes']
        oram=ocommit=0
        if not name.startswith('before_launch_'):
            oram=facts['product']['rss_bytes'];ocommit=facts['product']['private_bytes']
    need(all(type(x)is int and x>=0 for x in(oram,ocommit)),'PORTABLE_CURRENT_OCCUPANCY_UNKNOWN')
    row=evaluate(facts,ram,commit,occupied_ram=oram,occupied_commit=ocommit,cold=cold)
    row.update(stage=name,at=time.time(),input_sha256=q['source']['sha256'],build=q['executor_build']['sha256'],
        prediction=target,scope='fixed empirical prediction with stated uncertainty; not allocator upper-bound certification')
    out=Path(c['output_root']);out.mkdir(parents=True,exist_ok=True)
    atomic_json(out/('admission-'+name+'.json'),row)
    need(not row['unknown'],'RESOURCE_UNKNOWN',str(row));need(row['resource_feasible'],'RESOURCE_NOT_READY',str(row))
    return row

def stage(c,name,**detail):
    from .memory_snapshot import ram_facts
    from ..winproc import current_identity,process_peak_memory
    who=current_identity();facts=ram_facts();root=Path(c['evidence_root']);root.mkdir(parents=True,exist_ok=True)
    row=dict(stage=name,at=time.time(),monotonic=time.monotonic(),identity=who,system=facts,OS_lifetime_peaks=process_peak_memory(who),
        detail=detail,PageFaultCount=page_fault_count(),IO=io_counters(),hard_page_faults=None)
    with (root/'measurement-stages.jsonl').open('ab') as stream:stream.write(canonical_json(row)+b'\n')
    if name.startswith('before_'):
        component=detail.get('component');role={'normalize_source':'fit','normalize_reference':'fit','fit':'fit','R0':'r0','H':'prepare','T':'prepare','CG':'solver','range':'prepare','CPU':'cpu'}.get(component,'host')
        admit(c,name,role=role)

def pressure(host):
    c=host.context
    if not k.enabled(c):return
    now=time.monotonic();key=c['id']
    if now-_last.get(key,0)<.5:return
    _last[key]=now
    from .memory_snapshot import ram_facts
    hard_limits(c,'host')
    f=ram_facts();a=f['physical']['available_bytes'];h=f['commit']['headroom_bytes']
    need(type(a)is int and type(h)is int,'RESOURCE_UNKNOWN')
    low=a<margin(0) or h<1024**3
    _pressure[key]=_pressure.get(key,0)+1 if low else 0
    if _pressure[key]>=3:
        atomic_json(host.path/'cancel.signal',dict(reason='SUSTAINED_PORTABLE_RESOURCE_PRESSURE',facts=f,consecutive_samples=_pressure[key]))
        need(False,'CANCELLED','资源余量持续不足，已请求合作取消。',exit_code=8)


def page_fault_count():
    """Read-only total faults; deliberately not labeled hard paging."""
    import ctypes as ct
    try:
        class C(ct.Structure):
            _fields_=[('cb',ct.c_ulong),('PageFaultCount',ct.c_ulong)]+[(n,ct.c_size_t) for n in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage')]
        value=C();value.cb=ct.sizeof(value);kernel=ct.WinDLL('kernel32');kernel.GetCurrentProcess.restype=ct.c_void_p
        fn=ct.WinDLL('psapi').GetProcessMemoryInfo;fn.argtypes=[ct.c_void_p,ct.POINTER(C),ct.c_ulong]
        if not fn(kernel.GetCurrentProcess(),ct.byref(value),value.cb):raise ct.WinError()
        return dict(known=True,total=int(value.PageFaultCount),hard_faults=None,source='GetProcessMemoryInfo.PageFaultCount',hard_paging_inferred=False)
    except OSError as error:return dict(known=False,total=None,hard_faults=None,error=str(error),hard_paging_inferred=False)

def hard_limits(c,role):
    from .memory_snapshot import ram_facts
    from ..winproc import current_identity,process_peak_memory
    f=ram_facts();who=current_identity();p=process_peak_memory(who)
    rss=f['product']['rss_bytes'];private=f['product']['private_bytes']
    need(type(rss)is int and type(private)is int,'RESOURCE_CURRENT_PROCESS_UNKNOWN')
    io=role.removeprefix('entry_')=='host';rss_cap=(3 if io else 6)*1024**3;private_cap=9*1024**3//2 if io else 6*1024**3
    need(rss<=rss_cap and private<=private_cap,'PORTABLE_ROLE_RESOURCE_HARD_CAP')
    if p.get('known'):
        need(p['peak_working_set_bytes']<=rss_cap and p['peak_pagefile_usage_bytes']<=private_cap,'PORTABLE_ROLE_PEAK_HARD_CAP')
    return dict(current=f['product'],OS_lifetime_peaks=p,PageFaultCount=page_fault_count(),IO=io_counters(),rss_cap_bytes=rss_cap,private_cap_bytes=private_cap,hard_cap_unchanged=True)


def io_counters():
    """OS process IO totals, not an inferred hard-page-fault measure."""
    import ctypes as ct
    try:
        class I(ct.Structure):
            _fields_=[(n,ct.c_ulonglong)for n in ('read_operations','write_operations','other_operations','read_bytes','write_bytes','other_bytes')]
        value=I();kernel=ct.WinDLL('kernel32',use_last_error=True);kernel.GetCurrentProcess.restype=ct.c_void_p
        fn=kernel.GetProcessIoCounters;fn.argtypes=[ct.c_void_p,ct.POINTER(I)]
        if not fn(kernel.GetCurrentProcess(),ct.byref(value)):raise ct.WinError(ct.get_last_error())
        return dict(known=True,source='GetProcessIoCounters',counters={name:int(getattr(value,name))for name,_ in I._fields_},hard_paging_inferred=False)
    except OSError as error:return dict(known=False,counters=None,error=str(error),hard_paging_inferred=False)
