"""Private transport shared by the fixed production executor and test entry.

No callable selection is read from a payload. One absolute monotonic deadline
bounds authentication, all frames and ACKs. Process ownership remains with the
caller; closing a connection never constitutes a process-exit receipt.
"""
import hashlib,os,threading,time,traceback,weakref
from pathlib import Path
from ...common import atomic_json
from ...gm003_ipc import connect,send_json,recv_json,send_array,recv_array
from ...winproc import current_identity,identity_alive
from .contract import validate,Coverage
FIELDS=('OP','LO','LP','Y')
_runtime_ready=False


class _Progress:
    """One latest state; only ordinary progress persistence is rate limited."""
    FORCED=frozenset(('scalar_complete','core_complete','complete','failed','cancelled','deadline'))
    def __init__(self,root,started,*,clock=None,writer=None):
        self.root=Path(root);self.started=started;self.clock=clock or time.monotonic
        self.writer=writer or atomic_json;self.latest={};self.last_write=None
        self.callbacks=0;self.ordinary_writes=0;self.forced_writes=0

    def __call__(self,**event):
        self.callbacks+=1;now=self.clock()
        event.pop('elapsed',None)
        if event:
            if event.get('stage')is not None and event['stage']!=self.latest.get('stage'):self.latest=dict(event)
            else:self.latest.update(event)
        if not self.latest:return
        forced=event.get('stage')in self.FORCED
        if forced or self.last_write is None or now-self.last_write>=1.:
            self.writer(self.root/'progress.json',dict(elapsed=now-self.started,**self.latest))
            self.last_write=self.clock()
            if forced:self.forced_writes+=1
            else:self.ordinary_writes+=1

    def snapshot(self):
        return dict(latest=dict(self.latest),callbacks=self.callbacks,ordinary_writes=self.ordinary_writes,forced_writes=self.forced_writes)

def _runtime():
    global _runtime_ready
    import numpy as np,torch
    from .contract import runtime_torch
    if os.environ.get('CUDA_VISIBLE_DEVICES')!='-1'or np.__version__!='1.26.4'or torch.__version__!=runtime_torch() or torch.cuda.is_initialized():raise RuntimeError('CPU_RUNTIME')
    if not _runtime_ready:
        torch.set_num_threads(4);torch.set_num_interop_threads(1)
        def forbidden(*a,**kw):raise RuntimeError('FORBIDDEN_CUDA_CALL')
        for name in ('init','_lazy_init','is_available','synchronize','mem_get_info','memory_allocated','memory_reserved','device_count','set_device','set_per_process_memory_fraction'):setattr(torch.cuda,name,forbidden)
        torch.Tensor.cuda=forbidden;_runtime_ready=True
    return np,torch

def _bounded(action,deadline,cancelled,root,connection=None):
    done=threading.Event();box={}
    def call():
        try:box['value']=action()
        except BaseException as error:box['error']=error
        finally:done.set()
    thread=threading.Thread(target=call,daemon=True,name='cpu-postprocess-wire');thread.start()
    while not done.wait(.02):
        cancel=cancelled()
        if cancel or time.monotonic()>=deadline:
            try:wire_snapshot=connection.snapshot()if hasattr(connection,'snapshot')else None
            except BaseException as observation_error:wire_snapshot=dict(observation_error=repr(observation_error)[:512])
            atomic_json(root/('cancel-ack.json'if cancel else'deadline-ack.json'),dict(monotonic=time.monotonic(),identity=current_identity(),wire_snapshot=wire_snapshot))
            if connection is not None:connection.close()
            # A blocked transport thread is not declared complete. The owner
            # must prove worker exit or terminate/wait its OwnedJob.
            raise InterruptedError('TRANSPORT_CANCELLED')if cancel else TimeoutError('TRANSPORT_DEADLINE')
    thread.join(.1)
    if cancelled():raise InterruptedError('TRANSPORT_CANCELLED')
    if time.monotonic()>=deadline:raise TimeoutError('TRANSPORT_DEADLINE')
    if 'error'in box:raise box['error']
    return box.get('value')

class _Connection:
    """Bounded observation, with an optional private test-only trace callback."""
    def __init__(self,raw,root,*,observer=None):
        self.raw=raw;self.root=root;self.field='metadata';self.observer=observer
        self._lock=threading.Lock();self._last=None;self._frames=0;self._bytes=0
        self._returned_frames=0;self._returned_bytes=0;self._closed=False;self._observer_error=None

    def snapshot(self):
        with self._lock:
            return dict(schema='gm018-wire-summary-v1',frames=self._frames,bytes=self._bytes,returned_frames=self._returned_frames,returned_bytes=self._returned_bytes,closed=self._closed,observer_error=self._observer_error,last_frame=None if self._last is None else dict(self._last))

    def _event(self,state,length,error=None):
        now=time.monotonic()
        with self._lock:
            if state=='send_enter':self._frames+=1;self._bytes+=length
            elif state=='send_return':self._returned_frames+=1;self._returned_bytes+=length
            self._last=dict(state=state,field=self.field,frame_bytes=length,frame=self._frames,monotonic=now)
            if error is not None:self._last.update(error_type=type(error).__name__,error=str(error)[:512])
        if self.observer is not None:
            try:self.observer(self.snapshot())
            except BaseException as error:
                with self._lock:self._observer_error=repr(error)[:512]
                self.observer=None

    def send_bytes(self,data):
        self._event('send_enter',len(data))
        try:result=self.raw.send_bytes(data)
        except BaseException as error:
            self._event('send_error',len(data),error);raise
        self._event('send_return',len(data));return result
    def recv_bytes(self,maxlength=None):return self.raw.recv_bytes(maxlength)
    def close(self):
        self.raw.close()
        with self._lock:self._closed=True
    def save_summary(self):atomic_json(self.root/'wire.json',self.snapshot())

def _receive(conn,m,coverage,hashes,*,ack=True,expected_hash=None,consumer=None):
    """Consume one real production-framed block, before acknowledging it."""
    header=recv_json(conn)
    if not isinstance(header,dict)or header.get('event')!='block':raise ValueError('BLOCK_HEADER')
    start,end=header['start'],header['end'];h,w,_=m['O']['shape'];shape=(end-start,w,3)
    coverage.accept(header['job_id'],header['attempt_id'],start,end,{k:shape for k in FIELDS})
    if set(header['hashes'])!=set(FIELDS):raise ValueError('BLOCK_FIELDS')
    actual={}
    for key in FIELDS:
        value=recv_array(conn,expected_dtype='<f4',expected_shape=shape)
        digest=hashlib.sha256(memoryview(value).cast('B')).hexdigest()
        if digest!=header['hashes'][key]or(expected_hash is not None and digest!=expected_hash(key,start,end)):raise ValueError('BLOCK_HASH')
        try:
            if consumer is not None:
                import numpy as np
                if not np.isfinite(value).all():raise ValueError('BLOCK_NONFINITE')
                value.flags.writeable=False
                consumer(key,start,end,value)
            hashes[key].update(memoryview(value).cast('B'));actual[key]=digest
        except BaseException as error:
            traceback.clear_frames(error.__traceback__)
            raise
        finally:del value
    reply=dict(accepted=True,job_id=m['job_id'],attempt_id=m['attempt_id'],start=start,end=end,hashes=actual)
    if ack:send_json(conn,reply)
    return header,reply

def _run(payload,execute_fixed,*,observer=None):
    """Private injection boundary: production calls this with only execute."""
    np,torch=_runtime();m=validate(payload['manifest']);root=Path(m['output_root']);root.mkdir(parents=True,exist_ok=True)
    if identity_alive(m['owner'])is not True:raise RuntimeError('OWNER_NOT_LIVE')
    from multiprocessing import shared_memory
    from ..independent_r0_p3_lld10.ledger import Ledger
    from .core import Gate,StopRequested
    from .executor import ArrayProvider
    L=Ledger(root/'actual.json',m['budget'],deadline=m['deadline_monotonic']);shm=provider=o=b=conn=None;refs=[];success=False;started=time.monotonic();coverage=Coverage(m);hashes={k:hashlib.sha256()for k in FIELDS};blocks_sent=0;primary=None
    progress=_Progress(root,started)
    cancelled=lambda:(root/'cancel.signal').exists()
    try:
        shm=shared_memory.SharedMemory(name=payload['O_shared_memory'],create=False)
        o=np.ndarray(tuple(m['O']['shape']),dtype=np.float32,buffer=shm.buf);refs.append(weakref.ref(o))
        atomic_json(root/'mapped.json',dict(name=shm.name,identity=current_identity(),creator=False))
        with np.load(payload['b_path'],allow_pickle=False)as archive:b=archive['b'].copy()
        provider=ArrayProvider(o,b);refs.extend((weakref.ref(provider.o),weakref.ref(provider.b)))
        raw=_bounded(lambda:connect(payload['address'],payload['auth_hex'],timeout_s=max(.001,m['deadline_monotonic']-time.monotonic())),m['deadline_monotonic'],cancelled,root)
        conn=_Connection(raw,root,observer=observer)
        _bounded(lambda:send_json(conn,dict(event='ready',identity=current_identity(),cuda_initialized=False)),m['deadline_monotonic'],cancelled,root,conn)
        def sink(start,end,blocks):
            nonlocal blocks_sent
            coverage.accept(m['job_id'],m['attempt_id'],start,end,{k:v.shape for k,v in blocks.items()})
            actual={k:hashlib.sha256(memoryview(blocks[k]).cast('B')).hexdigest()for k in FIELDS}
            def exchange():
                send_json(conn,dict(event='block',job_id=m['job_id'],attempt_id=m['attempt_id'],start=start,end=end,hashes=actual))
                for key in FIELDS:conn.field=key;send_array(conn,blocks[key])
                expected=dict(accepted=True,job_id=m['job_id'],attempt_id=m['attempt_id'],start=start,end=end,hashes=actual)
                if recv_json(conn)!=expected:raise ValueError('SINK_REJECTED')
            _bounded(exchange,m['deadline_monotonic'],cancelled,root,conn)
            for key in FIELDS:hashes[key].update(memoryview(blocks[key]).cast('B'))
            blocks_sent+=1;progress(stage='transport',blocks=blocks_sent,fields=list(FIELDS))
        gate=Gate(L.take,cancelled,progress,m['deadline_monotonic'])
        result=execute_fixed(m,provider,sink,gate,payload['ranges']);coverage.finish()
        actual={k:v.hexdigest()for k,v in hashes.items()}
        if result.get('hashes')!=actual:raise ValueError('TRANSPORT_RESULT_HASH')
        result.update(event='complete',identity=current_identity(),elapsed=time.monotonic()-started,cuda_initialized=False,transport_coverage=True,transport_blocks=blocks_sent)
        progress(stage='complete',blocks=blocks_sent,fields=list(FIELDS))
        _bounded(lambda:send_json(conn,result),m['deadline_monotonic'],cancelled,root,conn)
        atomic_json(root/'worker-result.json',result);L.save('COMPLETE')
        success=True;return result
    except BaseException as error:
        primary=error
        try:atomic_json(root/'worker-failure.json',dict(traceback=traceback.format_exc(),error_type=type(error).__name__,wire_snapshot=None if conn is None else conn.snapshot(),progress=progress.snapshot()))
        except BaseException as receipt_error:error.add_note('Failure receipt write failed: '+repr(receipt_error))
        terminal='cancelled'if isinstance(error,(InterruptedError,StopRequested))else'deadline'if isinstance(error,TimeoutError)else'failed'
        try:progress(stage=terminal,error_type=type(error).__name__,error=str(error))
        except BaseException as observation_error:
            error.add_note('Terminal progress write failed: '+repr(observation_error))
        # Preserve the full diagnostic string before releasing inactive frame
        # locals that might otherwise keep a shared-memory NumPy view alive.
        traceback.clear_frames(error.__traceback__)
        try:L.save('FAILED')
        except BaseException as receipt_error:error.add_note('Failed ledger write: '+repr(receipt_error))
        raise
    finally:
        try:
            if conn is not None:
                try:conn.close();conn.save_summary()
                except BaseException as observation_error:
                    if primary is None:raise
                    primary.add_note('Wire observation cleanup failed: '+repr(observation_error))
        finally:
            if provider is not None:provider.o=None;provider.b=None
            provider=o=b=None
            live=sum(ref()is not None for ref in refs)
            try:
                if shm is not None:shm.close()
            except BaseException as cleanup_error:
                if primary is None:raise
                primary.add_note('Shared memory close failed: '+repr(cleanup_error))
            try:atomic_json(root/'mapping-cleanup.json',dict(shared_memory_name=payload['O_shared_memory'],creator=False,views_remaining=live,handle_closed=shm is None or shm._mmap is None,success=success,identity=current_identity()))
            except BaseException as receipt_error:
                if primary is None:raise
                primary.add_note('Mapping cleanup receipt failed: '+repr(receipt_error))
            if live:
                if primary is None:raise RuntimeError('SHARED_VIEWS_RETAINED')
                primary.add_note('SHARED_VIEWS_RETAINED')
