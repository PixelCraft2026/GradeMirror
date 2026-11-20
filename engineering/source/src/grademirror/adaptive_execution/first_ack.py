"""GM057 plan ACK reads: exact immutable path, bounded bytes, explicit sharing."""
import ctypes,json,msvcrt,os,time
from ctypes import wintypes as w
from pathlib import Path
from ..common import atomic_json,reject_reparse
from ..cpu_replay.contract import need,binding,verify
from .first_contract import E

def shared_bytes(path):
 p=Path(path).resolve();reject_reparse(p)
 k=ctypes.WinDLL('kernel32',use_last_error=True)
 k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE];k.CreateFileW.restype=w.HANDLE
 k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
 h=k.CreateFileW(str(p),0x80000000,7,None,3,0x80,None)
 if h==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
 try:fd=msvcrt.open_osfhandle(int(h),os.O_RDONLY|os.O_BINARY)
 except BaseException:k.CloseHandle(h);raise
 with os.fdopen(fd,'rb') as f:raw=f.read(65537)
 need(len(raw)<=65536,'FIRST_ACK_BYTE_LIMIT');return raw

def parse(raw):
 def pairs(items):
  value={}
  for key,v in items:need(key not in value,'FIRST_ACK_DUPLICATE');value[key]=v
  return value
 def constant(v):raise ValueError('FIRST_ACK_NONFINITE:'+v)
 return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=constant)

def approve(plan,deadline,*,scope=None,context=None):
 p=Path(plan).resolve();observer=Path(os.environ['GM052_OBSERVER_ROOT']).resolve()
 scope_E=E if scope is None else Path(scope).resolve()
 if scope is not None:
  from .calibration_contract import E as calibration_E
  from .portable_contract import enabled as portable_enabled,e as portable_e
  need(scope_E==calibration_E or portable_enabled(context) and scope_E==portable_e(context),'GM063_ACK_NAMESPACE')
 need(observer.is_relative_to(scope_E/'runs') and observer.name=='managed' and p.is_relative_to(observer) and p.parent.name=='plans' and p.suffix=='.json','FIRST_ACK_EXACT_SCOPE')
 ack=Path(str(p)+'.ack');log=observer/'ack-reads'/(p.name+'.json');attempts=[]
 for attempt in range(1,4):
  try:raw=shared_bytes(ack)
  except PermissionError as e:
   attempts.append(dict(attempt=attempt,path=str(ack),errno=e.errno,winerror=getattr(e,'winerror',None),error=str(e)));atomic_json(log,dict(attempts=attempts,status='READ_DENIED',child_created=False))
   if attempt==3 or time.monotonic()+.05>=deadline:raise
   time.sleep(.05);continue
  break
 saved=parse(raw)
 need(type(saved)is dict and set(saved)=={'plan','before_CreateProcess','prepaid_root'} and saved['before_CreateProcess'] is True and saved['prepaid_root']==observer.parent.name,'PRE_CREATE_PLAN_ACK_FIELDS')
 actual=binding(p);row=saved['plan'];need(type(row)is dict and set(row)=={'path','bytes','sha256'} and Path(row['path']).resolve()==p and row['bytes']==actual['bytes'] and row['sha256']==actual['sha256'],'PRE_CREATE_PLAN_ACK_EXACT_IDENTITY');verify(row)
 atomic_json(log,dict(attempts=attempts,status='EXACT_ACK_READ',ack=dict(path=ack.as_posix(),bytes=len(raw),sha256=__import__('hashlib').sha256(raw).hexdigest()),plan=saved['plan'],child_created=False,sharing=7));return saved
