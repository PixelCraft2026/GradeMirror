"""Versioned CPU facts: pure Win32, no GPU discovery or process creation."""
import ctypes as c
import os,time
from pathlib import Path
from ..common import strict_load

def ram_facts():
    value=dict(physical=dict(total_bytes=None,available_bytes=None),commit=dict(limit_bytes=None,used_bytes=None,headroom_bytes=None),
        legacy_process_commit_available=None,product=dict(rss_bytes=None,private_bytes=None,coverage='caller only',pid=os.getpid()),
        sources=dict(physical='GlobalMemoryStatusEx',commit='GetPerformanceInfo',legacy_process_commit='GlobalMemoryStatusEx.availablePageFile',product='GetProcessMemoryInfo/current process'),diagnostics=[])
    try:
        class M(c.Structure):
            _fields_=[('length',c.c_ulong),('load',c.c_ulong)]+[(n,c.c_ulonglong) for n in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
        m=M();m.length=c.sizeof(m);k=c.WinDLL('kernel32',use_last_error=True)
        if not k.GlobalMemoryStatusEx(c.byref(m)):raise OSError(c.get_last_error(),'GlobalMemoryStatusEx')
        value['physical']=dict(total_bytes=int(m.total),available_bytes=int(m.available));value['legacy_process_commit_available']=int(m.page_available)
    except (OSError,AttributeError) as e:value['diagnostics'].append(dict(code='PHYSICAL_UNKNOWN',detail=str(e)))
    try:
        class P(c.Structure):
            _fields_=[('cb',c.c_ulong)]+[(n,c.c_size_t) for n in ('CommitTotal','CommitLimit','CommitPeak','PhysicalTotal','PhysicalAvailable','SystemCache','KernelTotal','KernelPaged','KernelNonpaged','PageSize')]+[(n,c.c_ulong) for n in ('HandleCount','ProcessCount','ThreadCount')]
        p=P();p.cb=c.sizeof(p);d=c.WinDLL('psapi',use_last_error=True)
        if not d.GetPerformanceInfo(c.byref(p),p.cb):raise OSError(c.get_last_error(),'GetPerformanceInfo')
        value['commit']=dict(limit_bytes=int(p.CommitLimit*p.PageSize),used_bytes=int(p.CommitTotal*p.PageSize),headroom_bytes=int((p.CommitLimit-p.CommitTotal)*p.PageSize))
    except (OSError,AttributeError) as e:value['diagnostics'].append(dict(code='SYSTEM_COMMIT_UNKNOWN',detail=str(e)))
    try:
        class C(c.Structure):
            _fields_=[('cb',c.c_ulong),('PageFaultCount',c.c_ulong)]+[(n,c.c_size_t) for n in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage')]
        m=C();m.cb=c.sizeof(m);k=c.WinDLL('kernel32');k.GetCurrentProcess.restype=c.c_void_p
        d=c.WinDLL('psapi');f=d.GetProcessMemoryInfo;f.argtypes=[c.c_void_p,c.POINTER(C),c.c_ulong]
        if not f(k.GetCurrentProcess(),c.byref(m),m.cb):raise OSError(c.get_last_error(),'GetProcessMemoryInfo')
        value['product'].update(rss_bytes=int(m.WorkingSetSize),private_bytes=int(m.PrivateUsage))
    except (OSError,AttributeError) as e:value['diagnostics'].append(dict(code='CALLER_MEMORY_UNKNOWN',detail=str(e)))
    return value

def pool_facts(workspace,pending_context=None,key=None):
    value=dict(state='UNKNOWN',exited=None,resident_rss_bytes=None,resident_commit_bytes=None,proof=None)
    if workspace is None:return value
    w=Path(workspace).resolve();reservation=w/'pool/reservation.json'
    if reservation.is_file():
        row=strict_load(reservation);value['state']=row.get('state','UNKNOWN')
        if value['state']=='FREE':
            path=Path(row['session_path']).parent/'reclaim-receipt.json'
            if path.is_file():
                proof=strict_load(path)
                if (proof.get('tree_empty') is True and proof.get('actual_wait') is True and
                    all(proof.get(k)==row.get(k) for k in ('job_id','attempt_id','token','session_path')) and
                    (proof.get('no_child_started') is True or bool(proof.get('identities'))) and
                    all(x.get('actual_handle_wait') is True and x.get('exited') is True for x in proof.get('identities',[])) and
                    all(x.get('cannot_reopen') is True for x in proof.get('mappings',[]))):
                    from ..cpu_replay.contract import binding
                    value.update(exited=True,resident_rss_bytes=0,resident_commit_bytes=0,proof=binding(path))
        return value
    # Empty isolated application workspace is a positive absence observation.
    store_files=[p for p in (w/'store').rglob('*') if p.is_file() and not (p==w/'store/gm004a-operations/index.lock' and p.read_bytes()==b'0')]
    pending_proof=None
    if pending_context is not None and not reservation.exists():
        from ..gm004a_operations import OperationStore
        from ..cpu_replay.contract import binding
        from ..winproc import current_identity,identity_key
        found=OperationStore(w/'store').lookup_request(key)
        if found is not None:
            path,spec=found;startup=path/'startup.json'
            specs=list((w/'store/gm004a-operations').glob('*/*/spec.json'))
            if specs==[path/'spec.json'] and startup.is_file() and not (path/'session').exists() and spec['semantics'].get('instance_context')==binding(pending_context['manifest_path']):
                row=strict_load(startup)
                if row.get('phase')=='NOT_STARTED' and identity_key(row['claimant'])==identity_key(current_identity()):
                    pending_proof=dict(kind='EXACT_OPERATION_NOT_STARTED',spec=binding(path/'spec.json'),startup=binding(startup))
    if not any(p.is_file() for p in (w/'operations').rglob('*')) and (not store_files or pending_proof is not None):
        value.update(state='FREE',exited=True,resident_rss_bytes=0,resident_commit_bytes=0,proof=pending_proof or dict(kind='NO_OPERATION_OR_RESIDENT_FILES',workspace=str(w)))
    return value

def collect(workspace=None,*,pending_context=None,key=None):
    v=ram_facts();v.update(schema='adaptive-resource-snapshot-v2',captured_at=time.time(),captured_monotonic=time.monotonic(),pool=pool_facts(workspace,pending_context,key),
        gpu=dict(status='NOT_QUERIED',available_bytes=None),complete={})
    v['complete']=dict(physical=v['physical']['available_bytes'] is not None,commit=v['commit']['headroom_bytes'] is not None,
        legacy_process_commit=v['legacy_process_commit_available'] is not None,product=all(v['product'][k] is not None for k in ('rss_bytes','private_bytes')),pool=v['pool']['exited'] is True,gpu=False)
    return v
