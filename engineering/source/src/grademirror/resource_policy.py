"""Versioned resource admission policy; separate from recipes and algorithm IDs."""
VERSION = 'gm014-dynamic-vram-v1'
MIB = 1024**2
FAST_WORKING_BYTES = 1536 * MIB
GPU_MARGIN_BYTES = 512 * MIB
FAST_REQUIRED_FREE_MIB = (FAST_WORKING_BYTES + GPU_MARGIN_BYTES) // MIB
ALLOCATOR_FRACTION = .8


def operation_name(operation):
    if operation not in ('fit','replay'):
        raise ValueError('Resource operation must be fit or replay')
    return operation


import ctypes as c,os,time
class Memory(c.Structure):_fields_=[('total',c.c_ulonglong),('free',c.c_ulonglong),('used',c.c_ulonglong)]
def device_memory_snapshot(uuid):
    path=os.path.join(os.environ.get('SystemRoot','C:\\Windows'),'System32','nvml.dll')
    dll=c.CDLL(path);dll.nvmlInit_v2.restype=c.c_int;dll.nvmlShutdown.restype=c.c_int
    dll.nvmlDeviceGetHandleByUUID.argtypes=[c.c_char_p,c.POINTER(c.c_void_p)];dll.nvmlDeviceGetHandleByUUID.restype=c.c_int
    dll.nvmlDeviceGetMemoryInfo.argtypes=[c.c_void_p,c.POINTER(Memory)];dll.nvmlDeviceGetMemoryInfo.restype=c.c_int
    dll.nvmlDeviceGetUUID.argtypes=[c.c_void_p,c.c_char_p,c.c_uint];dll.nvmlDeviceGetUUID.restype=c.c_int
    dll.nvmlDeviceGetDriverModel.argtypes=[c.c_void_p,c.POINTER(c.c_uint),c.POINTER(c.c_uint)];dll.nvmlDeviceGetDriverModel.restype=c.c_int
    def require(code):
        if code:raise RuntimeError('NVML_QUERY_FAILED '+str(code))
    require(dll.nvmlInit_v2())
    try:
        handle=c.c_void_p();require(dll.nvmlDeviceGetHandleByUUID(uuid.encode('ascii'),c.byref(handle)))
        actual=c.create_string_buffer(96);require(dll.nvmlDeviceGetUUID(handle,actual,len(actual)));assert actual.value.decode()==uuid,'NVML_UUID_CHANGED'
        m=Memory();require(dll.nvmlDeviceGetMemoryInfo(handle,c.byref(m)))
        current=c.c_uint();pending=c.c_uint();model_code=dll.nvmlDeviceGetDriverModel(handle,c.byref(current),c.byref(pending))
        return dict(at=time.time(),uuid=uuid,total_bytes=m.total,free_bytes=m.free,used_bytes=m.used,driver_model='WDDM'if model_code==0 and current.value==0 else'TCC'if model_code==0 and current.value==1 else'UNKNOWN',driver_model_query_code=model_code,api='nvmlDeviceGetMemoryInfo',dll_path=path)
    finally:require(dll.nvmlShutdown())


def available_gpu_bytes(cuda_free, snapshot):
    if (type(cuda_free)is not int or cuda_free<0 or not isinstance(snapshot,dict)
            or type(snapshot.get('free_bytes'))is not int or type(snapshot.get('total_bytes'))is not int
            or not 0<=snapshot['free_bytes']<=snapshot['total_bytes']):
        raise ValueError('GPU_RESOURCE_QUERY_INVALID')
    return min(cuda_free,snapshot['free_bytes'])
