"""Private prepare / public activate adapter for the existing I/O host."""
import hashlib, os, time, uuid
from pathlib import Path
from ..common import atomic_json, strict_load, sha_file
from .. import winproc as win
from ..gm003_ipc import send_json, recv_json
from . import BACKEND_ID, VERSION_FIELDS
from .contract import require, digest, RUNTIME
from .audit import append, science
from .resources import admit


def array_sha(value):return hashlib.sha256(memoryview(value).cast('B')).hexdigest()


class SessionAdapter:
    def __init__(self,host):
        from multiprocessing import shared_memory
        self.host=host;self.context=host.spec['hybrid_execution'];self.plan=self.context['plan']
        self.path=Path(self.context['operation_path']);self.recipe=strict_load(Path(self.context['recipe_dir'])/'recipe.json')
        self.maps={};self.receiver=None;self.view=None;self.listener=None;self.ready=None;self.closed=False;self.io_counts={}
        require(host.source_identity==self.plan['source_identity'] and
            host.source_info.get('sha256')==self.plan['source_identity']['file_sha256'], 'HYBRID_SOURCE_PIXELS')
        np=host.np;shape=host.source.shape
        self.maps['S']=shared_memory.SharedMemory(create=True,size=host.source.nbytes)
        data=np.ndarray(shape,host.source.dtype,buffer=self.maps['S'].buf);data[:]=host.source
        data.flags.writeable=False;host.source=data;data=None
        self.maps['O']=shared_memory.SharedMemory(create=True,size=int(np.prod(shape))*4)
        self.O=np.ndarray(shape,np.float32,buffer=self.maps['O'].buf)
        self.descriptors=dict(S=dict(name=self.maps['S'].name,shape=list(shape),dtype=host.source.dtype.str,readonly=True),
            O=dict(name=self.maps['O'].name,shape=list(shape),dtype='<f4',readonly=True))
        atomic_json(self.path/'shared-inputs.json',dict(self.descriptors,owner=win.current_identity(),single_source=True))

    def internal(self,request):
        require(request.get('backend_id')==BACKEND_ID and
            request.get('execution_contract_sha256')==self.plan['execution_contract_sha256'] and
            request.get('job_id')==self.context['job_id'] and request.get('attempt_id')==self.context['attempt_id'],
            'HYBRID_INTERNAL_BINDING')

    def _cancelled(self):return(self.path/'cancel.signal').exists()

    def _prepare_cpu(self):
        from multiprocessing.connection import Listener
        from ..engine.cpu_postprocess.contract import CONSTANTS,digest as cpu_digest
        from ..engine.cpu_postprocess.receiver import ResultReceiver
        from ..engine.cpu_postprocess import EXECUTOR_ID
        require(self.receiver is None,'HYBRID_CPU_ALREADY_PREPARED')
        rw=strict_load(self.path/'R0/wait.json');result=strict_load(self.path/'R0/result.json')
        require(rw['actual_wait'] and rw['actual_worker_handle_wait'] and rw['tree_empty'] and rw['exit_code']==0 and
            not rw['forced'] and win.identity_alive(rw['worker_identity'])is False,'HYBRID_CUDA_MUST_EXIT')
        require(result['O_sha256']==array_sha(self.O)==self.recipe['postprocess']['hashes']['O'], 'HYBRID_O_BRIDGE')
        self.O.flags.writeable=False
        admit(self.path,self.plan,'CPU',cancelled=self._cancelled)
        shape=self.plan['source_identity']['shape'];ref=self.plan['reference_identity']['shape']
        binding=dict(source={'sha256':self.plan['source_identity']['file_sha256']},
            recipe={'sha256':self.plan['parent_recipe_sha256']},O=dict(shape=shape,dtype='float32',sha256=result['O_sha256']),
            b=dict(shape=self.recipe['postprocess']['coefficient_shape'],dtype='float64',sha256=self.recipe['postprocess']['coefficient_sha256']),
            reference_wh=[ref[1],ref[0]],core_rows=512,ranges=self.plan['ranges'])
        self.manifest=dict(executor_id=EXECUTOR_ID,job_id=self.context['job_id'],attempt_id=self.context['attempt_id'],
            owner=win.current_identity(),**{k:binding[k] for k in('source','recipe','O','b','reference_wh')},
            constants=CONSTANTS,runtime=RUNTIME,backend='cpu',core_rows=512,budget=self.plan['CPU_budget'],
            deadline_monotonic=time.monotonic()+3600,output_root=str(self.path/'CPU'))
        receiver_spec=dict(job_id=self.context['job_id'],attempt_id=self.context['attempt_id'],
            semantics=dict(binding=binding,manifest_sha256=cpu_digest(binding)))
        self.receiver=ResultReceiver(self.manifest,None,self.path,receiver_spec)
        auth=os.urandom(24);self.listener=Listener(('127.0.0.1',0),family='AF_INET',authkey=auth)
        self.cpu_payload=dict(manifest=self.manifest,O_shared_memory=self.maps['O'].name,
            b_path=str(Path(self.context['recipe_dir'])/'p3.npz'),address=list(self.listener.address),auth_hex=auth.hex(),ranges=self.plan['ranges'])
        atomic_json(self.path/'CPU-payload.json',self.cpu_payload)
        return self.cpu_payload

    def _receive(self):
        from ..engine.cpu_postprocess.transport import _bounded
        deadline=self.manifest['deadline_monotonic'];cancelled=self._cancelled
        conn=_bounded(self.listener.accept,deadline,cancelled,self.path,self.listener)
        try:
            ready=_bounded(lambda:recv_json(conn),deadline,cancelled,self.path,conn)
            require(ready.get('event')=='ready' and ready.get('cuda_initialized')is False,'HYBRID_CPU_READY')
            self.worker_identity=ready['identity']
            def proof(key,a,z,value):
                append(self.path/'field-receipts.jsonl',dict(field=key,start=a,end=z,sha256=array_sha(value)))
            while not self.receiver.coverage.complete:
                self.receiver.receive_block(conn,deadline=deadline,cancelled=cancelled,proof=proof)
            hashes=self.receiver.receive_terminal(conn,deadline=deadline,cancelled=cancelled)
            return dict(hashes=hashes,worker_identity=self.worker_identity)
        finally:conn.close();self.listener.close();self.listener=None

    def _ready(self):
        from ..export_pixels import mix_quantize
        wait=strict_load(self.path/'CPU/wait.json');result=self.receiver.prepare(wait,self.worker_identity)
        require(result['hashes']['OP']==self.recipe['postprocess']['hashes']['OP'],'HYBRID_OP_BRIDGE')
        quant={};self.O=None;self.maps['O'].close();self.maps['O'].unlink();del self.maps['O']
        with self.receiver.prepared_view() as y:
            self.host._consumer_boundary("ready_quantization", point="mixed-ready-quantization")
            for bits in(8,16):
                for alpha in(0.,.5,1.):
                    key=str(bits)+':'+str(alpha);science(self.path,'quant-'+key,qualification_quantization_passes=1)
                    h=hashlib.sha256();blocks=0
                    for a in range(0,len(y),64):
                        if self._cancelled():
                            from ..common import GMError
                            raise GMError('CANCELLED','Cancelled during result validation',8)
                        q=mix_quantize(self.host.source[a:a+64],y[a:a+64],alpha,bits=bits)
                        h.update(memoryview(q).cast('B'));blocks+=1;del q
                    quant[key]=h.hexdigest()
                    append(self.path/'quantization.jsonl',dict(bits=bits,alpha=alpha,sha256=quant[key],blocks=blocks))
        prior=self.context.get('prior_manifest')
        hashes=dict(O=self.recipe['postprocess']['hashes']['O'],**result['hashes'])
        if prior is not None:require(hashes==prior['results'] and quant==prior['quantization'],'HYBRID_COLD_REPLAY_HASH')
        self.ready=dict(hashes=hashes,quantization=quant,handle_id=str(uuid.uuid4()),
            session_id=self.host.spec['session_id'],resident_bytes=self.host.source.nbytes+result['memory']['Y_bytes'])
        atomic_json(self.path/'hybrid-ready.json',self.ready)
        return self.ready

    def _activate(self):
        from .publication import verify
        from ..gm004a_operations import OperationStore
        spec=strict_load(self.path/'spec.json');commit=strict_load(self.path/'commit.json')
        OperationStore._verify_manifest(commit,spec,self.path)
        lease=commit['result_lease']
        require(not self.closed and not(self.path/'hybrid-release.json').exists(),'HYBRID_RESULT_CLOSED')
        require(self.ready is not None and lease['handle_id']==self.ready['handle_id'] and
            lease['owner_instance_id']==self.host.spec['session_id'],'HYBRID_ACTIVATION_BINDING')
        if self.host.lease is not None:
            from ..lease import validate_lease_handle
            from ..gm004a_api import _binding
            validate_lease_handle(self.host.lease,binding=_binding(lease))
            require(self.host.y is not None,'HYBRID_Y_NOT_RESIDENT')
            return dict(lease=self.host.lease,shared_Y=self.receiver.descriptor(),copied_Y=False)
        require(not(self.host.session_dir/'lease.json').exists(),'HYBRID_LEASE_STATE_CONFLICT')
        if self.host.y is None:
            self.receiver.activate();self.view=self.receiver.view();self.host.y=self.view.__enter__()
        self.host.lease=dict(lease,expires_at=time.time()+300);self.host.fit_count=0;self.host.render_count=1
        atomic_json(self.host.session_dir/'lease.json',self.host.lease)
        atomic_json(self.host.session_dir/'hybrid-first-activation.json',dict(
            lease=self.host.lease,shared_Y=self.receiver.descriptor(),copied_Y=False))
        return dict(lease=self.host.lease,shared_Y=self.receiver.descriptor(),copied_Y=False)

    def handle(self,conn,request):
        self.internal(request);command=request['command']
        if command=='HYBRID_INPUTS':reply=self.descriptors
        elif command=='HYBRID_CPU_PREPARE':reply=self._prepare_cpu()
        elif command=='HYBRID_RECEIVE':reply=self._receive()
        elif command=='HYBRID_READY':reply=self._ready()
        elif command=='HYBRID_ACTIVATE':reply=self._activate()
        else:raise ValueError('HYBRID_COMMAND_UNKNOWN')
        send_json(conn,dict(ok=True,result=reply));return True

    def output_objects(self):
        from ..light.resources import shared_object
        objects = []
        if "S" in self.maps and self.host.source is not None:
            objects.append(shared_object("source_rgb", self.maps["S"], self.host.source))
        if self.receiver is not None:
            output = self.receiver.output_object()
            if output is not None:
                objects.append(output)
        return objects

    def output_admission(self):
        from ..light.resources import active_plan
        if active_plan("mixed") is None:
            admit(self.path,self.plan,'output',cancelled=lambda:False)

    def io(self,kind,**counts):
        self.io_counts[kind]=self.io_counts.get(kind,0)+1
        science(self.path,kind+'-'+str(self.io_counts[kind]),**counts)

    def preview(self,*args,**kwargs):
        from .. import gm004a_preview as preview
        original=preview._q8;calls=0
        def counted(value):
            nonlocal calls
            calls+=1;return original(value)
        self.io('preview',preview_generations=1);preview._q8=counted
        try:return preview.get_preview(*args,**kwargs)
        finally:
            preview._q8=original
            append(self.path/'preview-quantization.jsonl',dict(blocks=calls,request_id=kwargs['request_id']))

    def close(self):
        if self.closed:return
        self.host.y=None
        if self.view is not None:self.view.__exit__(None,None,None);self.view=None
        if self.receiver is not None:self.receiver.release()
        if self.listener is not None:self.listener.close()
        self.host.source=None;self.O=None
        for mapping in self.maps.values():mapping.close();mapping.unlink()
        self.maps.clear();self.closed=True
        atomic_json(self.host.session_dir/'hybrid-mapping-cleanup.json',dict(source_and_Y_released=True,creator=True))
