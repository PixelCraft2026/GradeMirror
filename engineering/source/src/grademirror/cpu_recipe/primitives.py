"""Bounded standard-library data IO; no algorithm or image imports."""
import ctypes,hashlib,json,math,msvcrt,os,re,sys
from pathlib import Path,PurePosixPath
from ..common import GMError,canonical_json,atomic_json,reject_reparse,writable_path
JSON_LIMIT=1024**2;BUNDLE_LIMIT=8*1024**2;EXPANDED_LIMIT=4*1024**2
class RecipeError(GMError):
    def __init__(self,code,message='',*,stage='validate',exit_code=7):
        super().__init__(code,message or code,exit_code);self.stage=stage
def require(test,code,*,stage='validate'):
    if not test:raise RecipeError(code,stage=stage)
def digest(v):return hashlib.sha256(canonical_json(v)).hexdigest()
def fields(v,keys,code='FIELDS'):
    require(type(v)is dict and set(v)==set(keys),code)
def sha(s):return type(s)is str and re.fullmatch('[a-f0-9]{64}',s)is not None
def relative(name):
    require(type(name)is str and re.fullmatch('[a-zA-Z0-9_./-]+',name)is not None,'PAYLOAD_PATH')
    p=PurePosixPath(name);require(not p.is_absolute() and p.as_posix()==name and all(x not in ('','..','.') and x.split('.')[0].upper()not in {'CON','PRN','AUX','NUL',*(f'COM{i}'for i in range(1,10)),*(f'LPT{i}'for i in range(1,10))}for x in p.parts),'PAYLOAD_PATH');return name
def root(path,*,write=False):
    p=Path(path).expanduser().absolute();reject_reparse(p);return writable_path(p)if write else p.resolve()
def inside(directory,name):
    p=Path(directory)/relative(name);reject_reparse(p);require(p.resolve().is_relative_to(Path(directory).resolve()),'PAYLOAD_ESCAPE');return p
def read_bytes(path,limit=JSON_LIMIT):
    p=Path(path);reject_reparse(p)
    require(p.is_file() and p.stat().st_size<=limit,'FILE_MISSING_OR_SIZE')
    sys.audit('open',str(p),'rb',os.O_RDONLY)
    # Durable atomic renames must not be blocked by a concurrent read handle.
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p];k.CreateFileW.restype=ctypes.c_void_p
    handle=k.CreateFileW(str(p),0x80000000,7,None,3,0x80,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    with os.fdopen(fd,'rb')as f:data=f.read(limit+1)
    require(len(data)<=limit,'FILE_SIZE');return data
def loads(data):
    def pairs(items):
        d={}
        for k,v in items:require(k not in d,'DUPLICATE_JSON_KEY');d[k]=v
        return d
    def nonfinite(x):raise RecipeError('NONFINITE_JSON',x)
    try:v=json.loads(data,object_pairs_hook=pairs,parse_constant=nonfinite);canonical_json(v);return v
    except RecipeError:raise
    except (ValueError,TypeError,UnicodeError)as e:raise RecipeError('JSON_INVALID',str(e))from e
def read(path):return loads(read_bytes(path))
def file_record(path,*,name=None):
    from ..light.portable import source_record
    p=Path(path)
    row=source_record(p)
    if row is not None:
        return dict(path=relative(name) if name is not None else p.as_posix(),
                    bytes=row["source_bytes"], sha256=row["source_sha256"])
    reject_reparse(p);h=hashlib.sha256();n=0
    with p.open('rb')as f:
        while data:=f.read(64*1024):n+=len(data);h.update(data)
    return dict(path=relative(name)if name is not None else p.resolve().as_posix(),bytes=n,sha256=h.hexdigest())
def verify_file(path,item):
    fields(item,('path','bytes','sha256'),'FILE_RECORD_FIELDS');require(type(item['bytes'])is int and item['bytes']>=0 and sha(item['sha256']),'FILE_RECORD_TYPE')
    actual=file_record(path);require((actual['bytes'],actual['sha256'])==(item['bytes'],item['sha256']),'ASSET_IDENTITY');return actual
def write_new(path,data):
    p=Path(path);reject_reparse(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('xb')as f:f.write(data);f.flush();os.fsync(f.fileno())
def new_json(path,value):
    data=canonical_json(value)+b'\n';require(len(data)<=JSON_LIMIT,'JSON_SIZE');write_new(path,data)
