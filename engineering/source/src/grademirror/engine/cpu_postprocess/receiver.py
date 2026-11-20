"""Experimental single-Y receiver. Not a production lease or CPU qualification.

Only the owner writes private Y. Proof fields have synchronous, borrowed
lifetimes. Persistent commit and the availability of resident RAM are distinct.
"""
import hashlib,time,traceback,weakref
from contextlib import contextmanager
from pathlib import Path
from ...common import atomic_json,strict_load,GMError
from ...store import locked
from ...winproc import current_identity,identity_alive
from ...gm003_ipc import recv_json
from ...gm004a_operations import artifact
from .service import ExperimentalStore
from .contract import Coverage,digest,validate
from .transport import FIELDS,_receive,_bounded
from . import EXECUTOR_ID

RECEIVER_ID='gm020-single-y-v1'


class ReceiverStore(ExperimentalStore):
    @staticmethod
    def _verify_manifest(manifest,spec,path):
        ExperimentalStore._verify_manifest(manifest,spec,path)
        sem=spec['semantics'];r=strict_load(path/'executor-result.json')
        c=strict_load(path/'executor-cleanup.json')
        if (sem.get('receiver_id')!=RECEIVER_ID or r.get('receiver_id')!=RECEIVER_ID or
            (r.get('job_id'),r.get('attempt_id'))!=(spec['job_id'],spec['attempt_id']) or
            r.get('binding')!=sem.get('binding') or r.get('hashes')!=r.get('sender_hashes') or
            set(r.get('hashes',{}))!=set(FIELDS) or r.get('shape')!=sem['binding']['O']['shape'] or
            r.get('sender_complete')is not True or r.get('readonly')is not True or
            c.get('worker_identity_exited')is not True or
            c.get('worker_identity')!=r.get('worker_identity') or
            not isinstance(r.get('owner'),dict)):
            raise GMError('COMMIT_INVALID','Receiver provenance or full completion differs',7)

    @staticmethod
    def _fit_residency(path,spec,state):
        persistent=state['state']=='SUCCEEDED';resident=False
        if persistent:
            r=strict_load(path/'executor-result.json')
            live=strict_load(path/'receiver-residency.json') if (path/'receiver-residency.json').is_file() else {}
            resident=(live.get('state')=='RESIDENT' and live.get('owner')==r['owner'] and
                      live.get('name')==r.get('shared_memory') and identity_alive(r['owner'])is True)
        return {**state,'persistent_complete':persistent,'resident':resident,
                'receiver_id':RECEIVER_ID,'experimental_executor':EXECUTOR_ID,
                'production_activation':False}


class ResultReceiver:
    """One full FP32 HWC target, no reference outputs and no background queue.

    The caller owns and waits the sender Job. `complete` accepts its actual wait
    receipt only after the transport terminal record has been read. Local views
    and registered external readers must be gone before release can succeed.
    """
    def __init__(self,manifest,store,path,spec):
        import numpy as np
        from multiprocessing import shared_memory
        self.m=validate(manifest);self.store=store;self.path=Path(path);self.spec=spec
        if (self.m['job_id'],self.m['attempt_id'])!=(spec['job_id'],spec['attempt_id']):raise ValueError('RECEIVER_IDENTITY')
        self.owner=current_identity()
        if self.owner!=self.m['owner']:raise ValueError('RECEIVER_OWNER')
        self._lock=locked(self.path/'receiver-owner.lock',timeout=.25);self._lock.__enter__()
        self._shm=None;self._y=None;self._views=[];self._consumers=[];self._closed=False
        self._complete=False;self._prepared=False;self._failed=False;self._sender=None;self._next=0;self._field=0
        self.coverage=Coverage(self.m);self.hashes={k:hashlib.sha256() for k in FIELDS}
        self.peak_field_bytes=0;self.fields=0
        try:
            shape=tuple(self.m['O']['shape']);self._shm=shared_memory.SharedMemory(create=True,size=int(np.prod(shape))*4)
            self._y=np.ndarray(shape,np.float32,buffer=self._shm.buf)
            self._write_residency('PRIVATE')
        except BaseException:
            self.release();raise

    def _write_residency(self,state):
        atomic_json(self.path/'receiver-residency.json',dict(state=state,owner=self.owner,
                    name=None if self._shm is None else self._shm.name,receiver_id=RECEIVER_ID))

    def consume(self,key,start,end,value):
        import numpy as np
        if self._failed or self._complete or self._closed:raise ValueError('RECEIVER_TERMINAL')
        expected=FIELDS[self._field]
        if (key!=expected or start!=self._next or end!=min(self.m['O']['shape'][0],start+self.m['core_rows']) or
            value.dtype!=np.float32 or value.shape!=(end-start,self.m['O']['shape'][1],3) or
            not value.flags.c_contiguous or np.shares_memory(value,self._y)):
            raise ValueError('RECEIVER_FIELD')
        # Finite and block hash are checked by _receive before this call.
        if key=='Y':self._y[start:end]=value
        self.peak_field_bytes=max(self.peak_field_bytes,value.nbytes);self.fields+=1
        self._field+=1
        if self._field==4:self._field=0;self._next=end

    def receive_block(self,conn,*,deadline,cancelled=lambda:False,expected_hash=None,proof=None,ack=True):
        def field(key,start,end,value):
            self.consume(key,start,end,value)
            if proof is not None:proof(key,start,end,value)
        try:
            return _bounded(lambda:_receive(conn,self.m,self.coverage,self.hashes,ack=ack,
                            expected_hash=expected_hash,consumer=field),deadline,cancelled,self.path,conn)
        except BaseException as error:
            self._failed=True
            traceback.clear_frames(error.__traceback__)
            raise

    def receive_terminal(self,conn,*,deadline,cancelled=lambda:False):
        self.coverage.finish()
        sender=_bounded(lambda:recv_json(conn),deadline,cancelled,self.path,conn)
        hashes={k:v.hexdigest() for k,v in self.hashes.items()}
        if (self._failed or self._field or self._next!=self.m['O']['shape'][0] or
            sender.get('event')!='complete' or sender.get('hashes')!=hashes or
            sender.get('cuda_initialized')is not False):raise ValueError('RECEIVER_COMPLETE')
        self._sender=sender;return hashes

    def prepare(self,wait,worker_identity):
        """Validate and persist private readiness, without a business commit."""
        self.coverage.finish()
        if (self._failed or self._closed or self._sender is None or self._field or
            wait.get('actual_wait')is not True or wait.get('tree_empty')is not True or
            wait.get('exit_code')!=0 or wait.get('forced')is True or
            identity_alive(worker_identity)is not False):raise ValueError('RECEIVER_WAIT_REQUIRED')
        hashes={k:v.hexdigest() for k,v in self.hashes.items()}
        if self._sender.get('identity')!=worker_identity:raise ValueError('RECEIVER_SENDER_IDENTITY')
        self._y.flags.writeable=False
        result=dict(executor_id=EXECUTOR_ID,receiver_id=RECEIVER_ID,status='COMPLETE',
                    manifest_sha256=self.spec['semantics']['manifest_sha256'],
                    binding=self.spec['semantics']['binding'],job_id=self.m['job_id'],attempt_id=self.m['attempt_id'],
                    hashes=hashes,sender_hashes=self._sender['hashes'],sender_complete=True,
                    coverage_complete=True,shape=self.m['O']['shape'],readonly=True,
                    shared_memory=self._shm.name,owner=self.owner,worker_identity=worker_identity,
                    core_rows=self.m['core_rows'],fields=self.fields,
                    memory=dict(Y_bytes=self._y.nbytes,peak_field_bytes=self.peak_field_bytes,
                                frame_bytes=4*1024**2,validation_metadata_limit=16*1024**2))
        cleanup={**wait,'worker_identity':worker_identity,'worker_identity_exited':True}
        atomic_json(self.path/'executor-result.json',result);atomic_json(self.path/'executor-cleanup.json',cleanup)
        self._prepared=True;self._write_residency('PREPARED');return result

    def activate(self):
        """Caller must verify its durable business publication before activation."""
        if not self._prepared or self._failed or self._closed:raise ValueError('RESULT_NOT_PREPARED')
        self._complete=True;self._write_residency('RESIDENT')
        return self.descriptor()

    def complete(self,wait,worker_identity):
        result=self.prepare(wait,worker_identity)
        self.store.commit(self.m['job_id'],self.m['attempt_id'],
                          [artifact(self.path/n) for n in ('executor-result.json','executor-cleanup.json')])
        self.activate();return result

    @contextmanager
    def prepared_view(self):
        if not self._prepared or self._closed:raise ValueError('RESULT_NOT_PREPARED')
        v=self._y.view();v.flags.writeable=False;self._views.append(weakref.ref(v))
        try:yield v
        finally:del v

    @contextmanager
    def view(self):
        if not self._complete or self._closed:raise ValueError('RESULT_NOT_RESIDENT')
        v=self._y.view();v.flags.writeable=False;self._views.append(weakref.ref(v))
        try:yield v
        finally:del v

    def output_object(self):
        if self._closed or not (self._prepared or self._complete):
            return None
        from ...light.resources import shared_object
        return shared_object("Y", self._shm, self._y)

    def descriptor(self):
        if not self._complete or self._closed:raise ValueError('RESULT_NOT_RESIDENT')
        return dict(name=self._shm.name,shape=self.m['O']['shape'],dtype='<f4',readonly=True,
                    job_id=self.m['job_id'],attempt_id=self.m['attempt_id'],owner=self.owner)

    def register_consumer(self,identity):
        if not self._complete or self._closed or identity_alive(identity)is not True:raise ValueError('CONSUMER_NOT_LIVE')
        if identity not in self._consumers:self._consumers.append(identity)

    def release(self):
        if self._closed:return
        if any(r()is not None for r in self._views):raise RuntimeError('LOCAL_SHARED_VIEW_LIVE')
        if any(identity_alive(i)is not False for i in self._consumers):raise RuntimeError('MAPPED_CONSUMER_NOT_EXITED')
        self._y=None
        if self._shm is not None:
            self._shm.close();self._shm.unlink()
        self._closed=True
        try:self._write_residency('RELEASED')
        finally:self._lock.__exit__(None,None,None)
