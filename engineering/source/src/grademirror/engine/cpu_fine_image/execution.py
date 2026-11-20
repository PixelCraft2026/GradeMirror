"""Original CPU executor and framed receiver; one full Y, borrowed proof fields."""
import hashlib,math,secrets,time
from pathlib import Path
from multiprocessing.connection import Listener
from ...common import atomic_json,strict_load
from ...winproc import OwnedJob,current_identity,identity_alive
from ...gm003_ipc import recv_json,send_json
from ...gm004a_operations import artifact
from ..cpu_postprocess import EXECUTOR_ID
from ..cpu_postprocess.contract import CONSTANTS,digest
from ..cpu_postprocess.receiver import ResultReceiver,ReceiverStore,RECEIVER_ID
from ..cpu_postprocess.transport import FIELDS,_bounded
from .contract import need,service_budget,binding
from . import jobs
class ObservedReceiver(ResultReceiver):
    def __init__(self,*a,**kw):
        self.observed=dict(block_attempts=0,field_attempts=0,fields_verified=0,Y_copy_attempts=0,Y_copies_written=0,cores_received=0,ACK_attempts=0,ACK_sent=0,cores_confirmed=0);self.blocks=[]
        super().__init__(*a,**kw)
    def consume(self,key,start,end,value):
        self.observed['field_attempts']+=1
        if key=='Y':self.observed['Y_copy_attempts']+=1
        super().consume(key,start,end,value)
        self.observed['fields_verified']+=1
        if key=='Y':self.observed['Y_copies_written']+=1
    def block(self,conn,deadline,*,cancel=False):
        self.observed['block_attempts']+=1
        # Validate the full header shape before delegating all original framing.
        head=_bounded(lambda:recv_json(conn),deadline,lambda:False,self.path,conn)
        need(type(head)is dict and set(head)=={'event','job_id','attempt_id','start','end','hashes'},'FINE_BLOCK_FIELDS')
        class Prefixed:
            def __init__(self):self.first=True
            def recv_bytes(self,maxlength=None):
                if self.first:
                    import json
                    self.first=False;return json.dumps(head,allow_nan=False).encode()
                return conn.recv_bytes(maxlength)
            def send_bytes(self,data):return conn.send_bytes(data)
            def close(self):return conn.close()
        header,reply=self.receive_block(Prefixed(),deadline=deadline,ack=False)
        self.observed['cores_received']+=1;self.blocks.append(header)
        # Four fields are verified/copied before cancel. Publish the signal before
        # ACK allows the sender to begin any subsequent core.
        if cancel:atomic_json(Path(self.m['output_root'])/'cancel.signal',dict(after_four_fields=True,before_ACK=True))
        self.observed['ACK_attempts']+=1;send_json(conn,reply);self.observed['ACK_sent']+=1;self.observed['cores_confirmed']+=1
        return header
def range_scan(provider,m,gate):
    # Extracted verbatim loop from original executor.execute; AST checked in P0.
    from ..cpu_postprocess.core import p3_region
    b=provider.b;h,w,_=provider.o.shape;ranges=[]
    for c in range(3):
        lo=float('inf');hi=float('-inf')
        for start in range(0,h,128):
            end=min(h,start+128);a=provider.read(start,end,c)
            p=p3_region(a,b,m['reference_wh'],(h,w),start,end,c,gate)
            lo=min(lo,float(a.min()),float(p.min()));hi=max(hi,float(a.max()),float(p.max()));del p
        kmin=math.floor((lo-2/255)*64)-1;kmax=math.ceil((hi+2/255)*64)+1
        ranges.append(dict(kmin=kmin,kmax=kmax,count=kmax-kmin+1,min=lo,max=hi,delta=1/64,sigma=.1,J=8))
    need(all(2<=r['count']<=257 for r in ranges),'FINE_RANGE_CAP');return ranges
def execute(o_mapping,o,b_path,binding_request,ranges,work,*,core_rows=512,cancel=False):
    import numpy as np
    from ..cpu_postprocess.executor import array_hash
    work=Path(work);work.mkdir(parents=True,exist_ok=True);b=np.load(b_path,allow_pickle=False)
    try:bmeta=dict(shape=list(b['b'].shape),dtype='float64',sha256=array_hash(b['b']))
    finally:b.close()
    sem=dict(executor_id=EXECUTOR_ID,receiver_id=RECEIVER_ID,profile_id='independent-r0-p3-lld10-v1',binding=binding_request,manifest_sha256=digest(dict(request=binding_request,ranges=ranges,core_rows=core_rows,cancel=cancel)))
    store=ReceiverStore(work);path,spec,state=store.begin('replay',sem,binding_request['key']);need(not state['idempotent_hit'],'FINE_EXECUTION_ALREADY_STARTED')
    store.transition(spec['job_id'],spec['attempt_id'],'RUNNING','CPU','CPU_FINE_STARTED')
    cpu=path/'CPU';cpu.mkdir();deadline=time.monotonic()+(900 if cancel else 600 if o.shape[0]<1000 else 3600)
    m=dict(executor_id=EXECUTOR_ID,job_id=spec['job_id'],attempt_id=spec['attempt_id'],owner=current_identity(),source=binding_request['source'],recipe=dict(sha256=digest(binding_request)),O=binding_request['O'],b=bmeta,reference_wh=binding_request['reference_wh'],constants=CONSTANTS,runtime=dict(torch='2.5.1+cu124',numpy='1.26.4',threads=4,interop=1),backend='cpu',core_rows=core_rows,budget=service_budget(list(o.shape),ranges,core_rows,cancel=cancel),deadline_monotonic=deadline,output_root=cpu.as_posix())
    listener=Listener(('127.0.0.1',0),authkey=(auth:=secrets.token_bytes(32))) 
    from ...light.resources import active_plan
    active=active_plan("cpu")
    limit=active["selection"]["jobs"]["cpu_worker"] if active else (6*1024**3 if o.shape[0]>1000 else 3*1024**3)
    job=OwnedJob(limit);receiver=None;conn=None;identity=None;forced=False;wait=None
    payload=cpu/'payload.json';atomic_json(payload,dict(manifest=m,O_shared_memory=o_mapping.name,b_path=str(b_path),address=list(listener.address),auth_hex=auth.hex(),ranges=ranges))
    atomic_json(cpu/'exact-prepayment.json',dict(before_LLF=True,budget=m['budget'],scalar_compositions=(3 if cancel else 3*math.ceil(o.shape[0]/core_rows)),request=binding_request))
    try:
        receiver=ObservedReceiver(m,store,path,spec)
        if cancel:receiver._y.fill(np.float32(-123.5))
        identity=jobs.launch(job,'cpu',payload)
        conn=_bounded(listener.accept,deadline,lambda:False,cpu);hello=_bounded(lambda:recv_json(conn),deadline,lambda:False,cpu,conn)
        need(set(hello)=={'event','identity','cuda_initialized'} and hello['event']=='ready' and hello['cuda_initialized']is False and job.member(hello['identity']['pid']),'FINE_SENDER_IDENTITY');worker=hello['identity']
        for _ in range(1 if cancel else math.ceil(o.shape[0]/core_rows)):receiver.block(conn,deadline,cancel=cancel)
        hashes=None if cancel else receiver.receive_terminal(conn,deadline=deadline)
        if cancel:conn.close();conn=None
        code=job.wait(30000);empty=job.wait_empty(30000);wait=dict(actual_wait=True,tree_empty=empty,exit_code=code,forced=False,launcher=identity,worker=worker,peak_job_memory_bytes=job.memory_peak(),outer_actual_member_wait='LATE_OUTER_JOB_ATTACHMENT')
        atomic_json(cpu/'owned-wait.json',wait);need(empty and identity_alive(worker)is False,'FINE_CPU_EXIT')
        atomic_json(cpu/'copy-ACK-counters.json',dict(actual=True,counts=receiver.observed,blocks=receiver.blocks))
        if cancel:
            actual=strict_load(cpu/'actual.json');need(actual['actual'].get('scalar_LLF_footprints')==6 and not(cpu/'worker-result.json').exists() and code==8 and strict_load(cpu/'result.json')['status']=='EXPECTED_CPU_CANCEL','FINE_CANCEL_BOUNDARY')
            need(np.all(receiver._y[core_rows:]==np.float32(-123.5)),'FINE_CANCEL_SENTINEL')
            atomic_json(work/'cancel-result.json',dict(status='CANCELLED_NO_BINDING',first_core=receiver.blocks[0],rest_sentinel=True,actual=actual,wait=wait,COMPLETE=False))
            store.transition(spec['job_id'],spec['attempt_id'],'CANCELLED','terminal','CPU_FINE_CANCELLED');receiver.release();receiver=None;return None
        need(code==0,'FINE_CPU_FAILED');receiver.prepare(wait,worker);return receiver
    except BaseException:
        if identity and job.poll()is None:forced=True;job.terminate()
        if identity:job.wait(30000);job.wait_empty(30000)
        if receiver is not None:receiver.release()
        raise
    finally:
        if conn is not None:conn.close()
        listener.close();job.close()
def activate(receiver):
    receiver.store.commit(receiver.m['job_id'],receiver.m['attempt_id'],[artifact(receiver.path/n)for n in ('executor-result.json','executor-cleanup.json')]);receiver.activate();return receiver
