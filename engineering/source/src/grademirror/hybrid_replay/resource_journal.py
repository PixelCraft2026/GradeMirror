"""resource-journal-1.0: single-writer durable JSONL and immutable closure.
No numeric imports. Raw records are preserved; missing closure is INCOMPLETE.
"""
import ctypes,errno,hashlib,json,os,sys,threading,time,uuid
from pathlib import Path
VERSION='resource-journal-1.0'
CAP=1024**2

def error_details(exc,*,operation=None,path=None,stage=None,identity=None):
    return dict(operation=operation or getattr(exc,'journal_operation',None)or'unspecified-operation',
        target=str(path or getattr(exc,'journal_path',None)or getattr(exc,'filename','')or'<unknown>'),
        exception_type=type(exc).__name__,errno=getattr(exc,'errno',None),
        winerror=getattr(exc,'winerror',None),HRESULT=getattr(exc,'hresult',None),
        message=str(exc),stage=stage,identity=identity)

def _tag(exc,operation,path):
    try:exc.journal_operation=operation;exc.journal_path=str(path)
    except BaseException:pass
    return exc

def canonical(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')+b'\n'

def shared_bytes(path,limit=CAP+65536,attempts=None):
    path=Path(path)
    for n in range(3):
        try:
            if os.name=='nt':
                import msvcrt
                k=ctypes.WinDLL('kernel32',use_last_error=True)
                k.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p]
                k.CreateFileW.restype=ctypes.c_void_p
                h=k.CreateFileW(str(path),0x80000000,7,None,3,0x80,None)
                if h==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
                try:fd=msvcrt.open_osfhandle(h,os.O_RDONLY|os.O_BINARY)
                except BaseException:k.CloseHandle(ctypes.c_void_p(h));raise
                stream=os.fdopen(fd,'rb')
            else:stream=path.open('rb')
            with stream:data=stream.read(limit+1)
            if len(data)>limit:raise ValueError('JOURNAL_READ_LIMIT')
            return data
        except OSError as exc:
            detail=error_details(exc,operation='shared-read',path=path)
            if attempts is not None:attempts.append(dict(attempt=n,**detail))
            sys.stderr.write(json.dumps(dict(read_attempt=n,**detail),ensure_ascii=False)+'\n');sys.stderr.flush()
            if n==2:raise _tag(exc,'shared-read',path)
            time.sleep(.02)

def _json(path,*,active=False):
    raw=shared_bytes(path)
    if not raw.endswith(b'\n'):
        if active:return None
        raise ValueError('JOURNAL_METADATA_INCOMPLETE')
    return json.loads(raw.decode('utf-8'))

def create_record(path,value):
    path=Path(path)
    try:
        raw=canonical(value)
        if len(raw)>CAP:raise ValueError('JOURNAL_RECORD_LIMIT')
        with path.open('xb',buffering=0)as f:f.write(raw);f.flush();os.fsync(f.fileno())
    except BaseException as exc:raise _tag(exc,'create-record',path)

def preserve_error(path,exc,*,stage=None,identity=None,secondary=None):
    value=dict(protocol=VERSION,primary=error_details(exc,stage=stage,identity=identity),secondary=secondary or[])
    try:create_record(path,value)
    except BaseException as diagnostic:
        value['diagnostic_write_error']=error_details(diagnostic)
        sys.stderr.write(json.dumps(value,ensure_ascii=False,default=str)+'\n');sys.stderr.flush()
    return value

class Journal:
    def __init__(self,path,binding):
        self.path=Path(path);self.lock=threading.RLock();self.binding=dict(binding)
        self.file=None;self.seq=0;self.rows=0;self.total=0;self.chunk_rows=0;self.size=0
        self.digest=None;self.chunks=[];self.closed=False;self.failed=None
        self.path.mkdir(parents=True,exist_ok=True)
        try:
            if any(self.path.iterdir()):raise FileExistsError(errno.EEXIST,'JOURNAL_STREAM_REUSED',str(self.path))
            self.claim=os.open(self.path/'writer.lock',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            raw=canonical(dict(protocol=VERSION,binding=self.binding));os.write(self.claim,raw);os.fsync(self.claim)
            create_record(self.path/'stream.json',dict(protocol=VERSION,binding=self.binding,expanded_cap=CAP))
        except BaseException as exc:
            if hasattr(self,'claim'):os.close(self.claim)
            raise _tag(exc,'claim-stream',self.path/'writer.lock')
    def _open(self):
        self.seq+=1;self.size=self.chunk_rows=0;self.digest=hashlib.sha256()
        target=self.path/(str(self.seq).zfill(6)+'.jsonl')
        try:self.file=target.open('xb',buffering=0)
        except BaseException as exc:raise _tag(exc,'create-chunk',target)
    def _seal(self):
        if self.file is None:return
        try:self.file.flush();os.fsync(self.file.fileno());self.file.close();self.file=None
        except BaseException as exc:raise _tag(exc,'close-chunk',self.path/(str(self.seq).zfill(6)+'.jsonl'))
        row=dict(protocol=VERSION,binding=self.binding,sequence=self.seq,
            file=str(self.seq).zfill(6)+'.jsonl',bytes=self.size,rows=self.chunk_rows,sha256=self.digest.hexdigest())
        create_record(self.path/(str(self.seq).zfill(6)+'.closed.json'),row)
        self.chunks.append(row)
    def append_bytes(self,raw):
        with self.lock:
            if self.closed or self.failed is not None:raise RuntimeError('JOURNAL_NOT_WRITABLE')
            target=self.path
            try:
                if not isinstance(raw,bytes)or not raw or len(raw)>CAP or not raw.endswith(b'\n')or raw.count(b'\n')!=1:raise ValueError('JOURNAL_ROW_BOUNDS')
                json.loads(raw.decode('utf-8'))
                if self.file is None:self._open()
                if self.size+len(raw)>CAP:self._seal();self._open()
                target=self.path/(str(self.seq).zfill(6)+'.jsonl')
                n=self.file.write(raw)
                if n!=len(raw):raise OSError(errno.EIO,'JOURNAL_SHORT_WRITE',str(target))
                self.file.flush();os.fsync(self.file.fileno())
                self.size+=len(raw);self.chunk_rows+=1;self.rows+=1;self.total+=len(raw);self.digest.update(raw)
            except BaseException as exc:
                self.failed=_tag(exc,getattr(exc,'journal_operation','append-row'),getattr(exc,'journal_path',target))
                raise self.failed
    def append(self,value):
        try:raw=canonical(value)
        except BaseException as exc:raise _tag(exc,'serialize-row',self.path)
        self.append_bytes(raw)
    def close(self):
        with self.lock:
            if self.closed:return
            primary=self.failed
            try:
                if primary is not None:raise primary
                self._seal()
                create_record(self.path/'closed.json',dict(protocol=VERSION,binding=self.binding,
                    chunks=len(self.chunks),rows=self.rows,bytes=self.total))
                self.closed=True
            except BaseException as exc:primary=exc;self.failed=exc
            finally:
                for action in ((lambda:self.file.close())if self.file is not None else(lambda:None),
                               (lambda:os.close(self.claim))if self.claim is not None else(lambda:None)):
                    try:action()
                    except BaseException as exc:
                        if primary is None:primary=exc
                        else:primary.journal_secondary=getattr(primary,'journal_secondary',[])+[error_details(exc)]
                self.file=None;self.claim=None
            if primary is not None:raise primary

def read_journal(path,*,records=True):
    path=Path(path)
    closing=_json(path/'closed.json',active=True)if(path/'closed.json').exists()else None
    closed=closing is not None
    meta=_json(path/'stream.json',active=not closed)if(path/'stream.json').exists()else None
    if meta is None:
        if closed:raise ValueError('JOURNAL_HEADER_MISSING')
        return dict(protocol=VERSION,binding=None,state='INCOMPLETE',rows=0,bytes=0,committed_chunks=0,tail_incomplete=True,records=[],latest=None)
    if meta.get('protocol')!=VERSION or meta.get('expanded_cap')!=CAP:raise ValueError('JOURNAL_PROTOCOL')
    binding=meta['binding']
    # Freeze metadata before consuming data. An active chunk never becomes a
    # sealed chunk halfway through this read, even if its writer closes it.
    manifests=sorted(path.glob('*.closed.json'))
    snapshot={p.name:_json(p,active=not closed)for p in manifests}
    files=sorted(path.glob('*.jsonl'))
    if [p.name for p in files]!=[str(i).zfill(6)+'.jsonl'for i in range(1,len(files)+1)]:raise ValueError('JOURNAL_SEQUENCE')
    if [p.name for p in manifests]!=[str(i).zfill(6)+'.closed.json'for i in range(1,len(manifests)+1)]:raise ValueError('JOURNAL_SEQUENCE')
    if len(manifests)>len(files):raise ValueError('JOURNAL_MISSING_DATA')
    if any(snapshot[p.name]is None for p in manifests[:-1]):raise ValueError('JOURNAL_MISSING_MANIFEST')
    if not closed:
        # A rollover after the manifest snapshot may expose two data chunks.
        # Keep only the first unsealed tail in this fixed view; later chunks
        # belong to a later read. A gap in the visible data still fails above.
        end=next((i for i in range(1,len(files)+1)if snapshot.get(str(i).zfill(6)+'.closed.json')is None),len(files))
        files=files[:end]
    result=[];latest=None;rows=total=committed=0;tail_incomplete=False
    for i,p in enumerate(files,1):
        m=snapshot.get(str(i).zfill(6)+'.closed.json')
        raw=shared_bytes(p,CAP)
        complete_raw=raw if raw.endswith(b'\n')else raw[:raw.rfind(b'\n')+1]
        parsed=[]
        for line in complete_raw.splitlines():
            if not line:raise ValueError('JOURNAL_EMPTY_ROW')
            parsed.append(json.loads(line.decode('utf-8')))
        if m is not None:
            expected=dict(protocol=VERSION,binding=binding,sequence=i,file=p.name,bytes=len(raw),rows=len(parsed),sha256=hashlib.sha256(raw).hexdigest())
            if m!=expected or complete_raw!=raw:raise ValueError('JOURNAL_CHUNK_IDENTITY')
            committed+=1
        elif i!=len(files)or closed:raise ValueError('JOURNAL_MISSING_MANIFEST')
        if parsed:latest=parsed[-1]
        if records:result.extend(parsed)
        rows+=len(parsed);total+=len(raw);tail_incomplete|=complete_raw!=raw
    if closed:
        expected=dict(protocol=VERSION,binding=binding,chunks=len(files),rows=rows,bytes=total)
        if closing!=expected or committed!=len(files)or tail_incomplete:raise ValueError('JOURNAL_CLOSE_IDENTITY')
    return dict(protocol=VERSION,binding=binding,state='COMPLETE'if closed else'INCOMPLETE',
        rows=rows,bytes=total,committed_chunks=committed,tail_incomplete=tail_incomplete,
        records=result,latest=latest)

_progress_lock=threading.RLock()
_progress={}
def publish_progress(path,value):
    from ..winproc import current_identity
    path=Path(path).resolve()
    with _progress_lock:
        journal=_progress.get(path)
        if journal is None:
            base=path if(path/'spec.json').exists()else path.parent
            spec=_json(base/'spec.json')if(base/'spec.json').exists()else{}
            journal=Journal(path/'hybrid-progress-journal',dict(role='progress',operation_path=str(base),
                job_id=spec.get('job_id',base.parent.name),attempt_id=spec.get('attempt_id',base.name),writer=current_identity()))
            _progress[path]=journal
        journal.append(value)

def close_progress(path):
    with _progress_lock:
        journal=_progress.pop(Path(path).resolve(),None)
        if journal is not None:journal.close()

def latest_progress(path):
    path=Path(path);stream=path/'hybrid-progress-journal'
    if stream.exists():
        value=read_journal(stream,records=False)['latest']or{}
    else:
        from ..live_json import read_live_json
        value=read_live_json(path/'hybrid-progress.json')
    forbidden={'state','stage','job_id','attempt_id','owner','host','exit_code','error_code','result_lease','resident','resident_state','progress_only','persistent_solve'}
    return {k:v for k,v in value.items()if k not in forbidden}
