"""Read-only Win32/NVML metadata; never initializes CUDA or shells out."""
import ctypes as c
import os
import socket
import time
from .contract import SNAPSHOT

def collect_snapshot():
    started=time.monotonic();machine=socket.gethostname();diagnostics=[]
    value=dict(schema_version=SNAPSHOT,machine_id=machine,collected_wall=time.time(),collected_monotonic=started,
        clock={},sources=dict(physical='GlobalMemoryStatusEx',commit='GetPerformanceInfo',gpu='NVML DLL',product='not collected'),
        complete=dict(physical=False,commit=False,gpu=False,product=False),
        physical=dict(total_bytes=None,available_bytes=None),commit=dict(limit_bytes=None,used_bytes=None,headroom_bytes=None),
        product=dict(rss_bytes=None,private_bytes=None,coverage='unknown'),gpus=[],cuda_available=None,diagnostics=diagnostics)
    def problem(code,error):diagnostics.append(dict(code=code,detail=str(error)[:2048]))
    from ..adaptive_execution.memory_snapshot import ram_facts
    ram=ram_facts();value['physical']=ram['physical'];value['commit']=ram['commit']
    value['complete']['physical']=ram['physical']['available_bytes'] is not None
    value['complete']['commit']=ram['commit']['headroom_bytes'] is not None
    diagnostics.extend(ram['diagnostics'])
    nvml=None;initialized=False
    try:
        path=os.path.join(os.environ.get('SystemRoot','C:\\Windows'),'System32','nvml.dll');nvml=c.CDLL(path)
        def fn(name,args):
            f=getattr(nvml,name);f.argtypes=args;f.restype=c.c_int;return f
        def check(code):
            if code:raise OSError(code,'NVML metadata query')
        check(fn('nvmlInit_v2',[])());initialized=True
        count=c.c_uint();check(fn('nvmlDeviceGetCount_v2',[c.POINTER(c.c_uint)])(c.byref(count)))
        if count.value>16:raise OSError('GPU count exceeds metadata quota')
        driver=c.create_string_buffer(128);check(fn('nvmlSystemGetDriverVersion',[c.c_char_p,c.c_uint])(driver,len(driver)))
        class Memory(c.Structure):_fields_=[(n,c.c_ulonglong) for n in ('total','free','used')]
        for index in range(count.value):
            h=c.c_void_p();check(fn('nvmlDeviceGetHandleByIndex_v2',[c.c_uint,c.POINTER(c.c_void_p)])(index,c.byref(h)))
            uuid=c.create_string_buffer(128);name=c.create_string_buffer(128);m=Memory()
            check(fn('nvmlDeviceGetUUID',[c.c_void_p,c.c_char_p,c.c_uint])(h,uuid,len(uuid)))
            check(fn('nvmlDeviceGetName',[c.c_void_p,c.c_char_p,c.c_uint])(h,name,len(name)))
            check(fn('nvmlDeviceGetMemoryInfo',[c.c_void_p,c.POINTER(Memory)])(h,c.byref(m)))
            value['gpus'].append(dict(uuid=uuid.value.decode('ascii'),name=name.value.decode('utf-8',errors='replace'),driver=driver.value.decode('ascii'),
                cuda_runtime=None,total_bytes=m.total,nvml_free_bytes=m.free,raw_cuda_free_bytes=None,raw_cuda_uuid=None,allocator_reserved_bytes=None))
        value['complete']['gpu']=True
        problem('RAW_CUDA_NOT_QUERIED','NVML necessary condition only; CUDA architecture/runtime/context not probed')
    except (OSError,AttributeError) as e:problem('NVML_UNAVAILABLE',e)
    finally:
        if initialized:
            try:
                code=nvml.nvmlShutdown()
                if code:problem('NVML_SHUTDOWN_FAILED',code);value['complete']['gpu']=False
            except (OSError,AttributeError) as e:problem('NVML_SHUTDOWN_FAILED',e);value['complete']['gpu']=False
    value['clock']=dict(wall=time.time(),monotonic=time.monotonic(),machine_id=machine)
    if time.monotonic()-started>5:problem('SNAPSHOT_TIMEOUT','Read-only DLL calls exceeded 5 seconds; no cancellable DLL guarantee');value['complete']={k:False for k in value['complete']}
    return value
