"""New typed host adapter; shares the original receiver, lease and pixel methods."""
import hashlib,importlib.metadata,os,time,uuid
from pathlib import Path
from ..common import atomic_json,strict_load,sha_file
from .. import winproc as win
from ..hybrid_replay.session import SessionAdapter as ReplayAdapter,array_sha
from ..hybrid_replay.contract import require,RUNTIME,digest
from ..hybrid_replay.audit import science
from ..hybrid_replay.resources import admit
from . import FIT_CONTRACT
from .contract import read_recipe
from .plan import cpu_budget

class SessionAdapter(ReplayAdapter):
    def __init__(self,host):
        from multiprocessing import shared_memory
        self.host=host;self.context=host.spec['hybrid_execution'];self.plan=dict(self.context['plan'])
        require(self.context.get('fit_contract')==FIT_CONTRACT,'HYBRID_FIT_SESSION_TYPE')
        self.path=Path(self.context['operation_path']);self.recipe=None;self.maps={};self.receiver=None;self.view=None
        from .tiers import for_spec,consistent
        consistent(for_spec(strict_load(self.path/'spec.json')),self.plan['core_rows'])
        self.listener=None;self.ready=None;self.closed=False;self.io_counts={};np=host.np
        source=host.source_identity;expected=self.plan['source_identity']
        require(all(source[k]==v for k,v in expected.items()if v is not None)and
            host.source_info.get('sha256')==source['file_sha256'],'HYBRID_FIT_SOURCE_PIXELS')
        self.plan['source_identity']=source
        for key,value in (('S',host.source),):
            self.maps[key]=shared_memory.SharedMemory(create=True,size=value.nbytes)
            data=np.ndarray(value.shape,value.dtype,buffer=self.maps[key].buf);data[:]=value;data.flags.writeable=False
            host.source=data;data=None
        shape=host.source.shape;self.maps['O']=shared_memory.SharedMemory(create=True,size=int(np.prod(shape))*4)
        self.O=np.ndarray(shape,np.float32,buffer=self.maps['O'].buf)
        self.descriptors=dict(S=dict(name=self.maps['S'].name,shape=list(shape),dtype=host.source.dtype.str,readonly=True),
            O=dict(name=self.maps['O'].name,shape=list(shape),dtype='<f4',readonly=True),source_identity=source)
        if self.plan['operation']=='fit':
            science(self.path,'reference-decode',reference_decodes=1)
            ref=host._reference();rid=host._identity(ref,host.spec['reference_sha256'],host.spec['reference_bytes'],ref.dtype.itemsize*8)
            require(all(rid[k]==v for k,v in self.plan['reference_identity'].items()if v is not None)and
                host.reference_info.get('sha256')==rid['file_sha256'],'HYBRID_FIT_REFERENCE_PIXELS')
            self.maps['R']=shared_memory.SharedMemory(create=True,size=ref.nbytes)
            data=np.ndarray(ref.shape,ref.dtype,buffer=self.maps['R'].buf);data[:]=ref;data.flags.writeable=False
            host.reference=data;ref=None;data=None
            self.descriptors.update(R=dict(name=self.maps['R'].name,shape=rid['shape'],dtype=host.reference.dtype.str,readonly=True),
                reference_identity=rid,color_policy=dict(color_policy_id='untagged-assume-srgb-user-20261017',
                    source=dict(interpretation=host.source_info['color']['interpretation'],icc_sha256=host.source_info['color']['icc_sha256'],file_sha256=source['file_sha256']),
                    reference=dict(interpretation=host.reference_info['color']['interpretation'],marker='present'if host.reference_info['color']['icc_present']or host.reference_info['color']['srgb_present']else'absent',file_sha256=rid['file_sha256'])),
                io_runtime={**{n:importlib.metadata.version(n)for n in ('numpy','pillow','tifffile','imagecodecs')},'python':host.spec['io_python']})
            self.plan['reference_identity']=rid
        else:
            self.recipe,_=read_recipe(self.context['recipe_dir'])
            self.descriptors['reference_identity']=self.recipe['reference_identity']
        atomic_json(self.path/'shared-inputs.json',dict(self.descriptors,owner=win.current_identity(),single_source=True))

    def _prepare_cpu(self):
        from multiprocessing.connection import Listener
        from ..engine.cpu_postprocess.contract import CONSTANTS,digest as cpu_digest
        from ..engine.cpu_postprocess.receiver import ResultReceiver
        from ..engine.cpu_postprocess import EXECUTOR_ID
        require(self.receiver is None,'HYBRID_CPU_ALREADY_PREPARED')
        for stage in ('R0','P3')if self.plan['operation']=='fit' and 'saved_post' not in self.plan else('R0',):
            wait=strict_load(self.path/stage/'wait.json')
            require(wait['actual_wait']and wait['actual_worker_handle_wait']and wait['tree_empty']and wait['exit_code']==0 and
                not wait['forced']and win.identity_alive(wait['worker_identity'])is False,'HYBRID_FIT_PREVIOUS_STAGE_MUST_EXIT')
        self.recipe,_=read_recipe(self.context['recipe_dir']);post=self.recipe['postprocess'];shape=self.plan['source_identity']['shape']
        require(array_sha(self.O)==post['O_sha256']and self.recipe['source_identity']==self.plan['source_identity']and
            self.recipe['reference_identity']==self.plan['reference_identity'],'HYBRID_FIT_PREPARED_INPUT_BINDING')
        budget,extra,workspace=cpu_budget(shape,post['ranges'],self.plan['core_rows'])
        require(all(v<=self.plan['CPU_budget'][k]for k,v in budget.items())and workspace<=self.plan['workspace_bound'],'HYBRID_FIT_DATA_BUDGET_CANNOT_EXPAND')
        self.plan.update(parent_recipe_sha256=self.recipe['recipe_sha256'],ranges=post['ranges'],CPU_budget=budget,CPU_extra=extra)
        if 'R'in self.maps:
            self.host.reference=None;self.maps['R'].close();self.maps['R'].unlink();del self.maps['R']
        atomic_json(self.path/'reference-released.json',dict(actual_P3_exit=self.plan['operation']=='fit' and 'saved_post' not in self.plan,reference_mapping_closed=True,
            no_reference_science_decode_on_replay=self.plan['operation']=='replay'))
        binding=dict(fit_contract=FIT_CONTRACT,recipe_sha256=self.recipe['recipe_sha256'],source_identity=self.recipe['source_identity'],
            reference_identity=self.recipe['reference_identity'],O_sha256=post['O_sha256'],coefficient_sha256=post['coefficient_sha256'],
            ranges=post['ranges'],CPU_budget=budget,CPU_extra=extra,workspace_bound=workspace,core_rows=self.plan['core_rows'])
        binding['data_binding_sha256']=digest(binding);atomic_json(self.path/'data-binding.json',binding)
        self.O.flags.writeable=False
        from ..light.resources import active_plan, check_host, shared_object
        active=active_plan("mixed")
        if active is None:
            admit(self.path,self.plan,'CPU',cancelled=self._cancelled)
        else:
            require(not self._cancelled(), "CANCELLED")
            check_host(self.path,active,"CPU",point="mixed-cpu-handoff",
                       buffers=(shared_object("source_rgb",self.maps["S"],self.host.source),
                                shared_object("O",self.maps["O"],self.O)))
        ref=self.recipe['reference_identity']['shape']
        fields=dict(source={'sha256':self.plan['source_identity']['file_sha256']},recipe={'sha256':self.recipe['recipe_sha256']},
            O=dict(shape=shape,dtype='float32',sha256=post['O_sha256']),
            b=dict(shape=post['coefficient_shape'],dtype='float64',sha256=post['coefficient_sha256']),reference_wh=[ref[1],ref[0]],core_rows=self.plan['core_rows'],ranges=post['ranges'])
        self.manifest=dict(executor_id=EXECUTOR_ID,job_id=self.context['job_id'],attempt_id=self.context['attempt_id'],owner=win.current_identity(),
            **{k:fields[k]for k in ('source','recipe','O','b','reference_wh')},constants=CONSTANTS,runtime=RUNTIME,backend='cpu',core_rows=self.plan['core_rows'],
            budget=budget,deadline_monotonic=time.monotonic()+3600,output_root=str(self.path/'CPU'))
        self.receiver=ResultReceiver(self.manifest,None,self.path,dict(job_id=self.context['job_id'],attempt_id=self.context['attempt_id'],
            semantics=dict(binding=fields,manifest_sha256=cpu_digest(fields))))
        auth=os.urandom(24);self.listener=Listener(('127.0.0.1',0),family='AF_INET',authkey=auth)
        self.cpu_payload=dict(manifest=self.manifest,O_shared_memory=self.maps['O'].name,b_path=str(Path(self.context['recipe_dir'])/'p3.npz'),
            address=list(self.listener.address),auth_hex=auth.hex(),ranges=post['ranges'])
        atomic_json(self.path/'CPU-payload.json',self.cpu_payload);return self.cpu_payload

    def _ready(self):
        from ..export_pixels import mix_quantize
        result=self.receiver.prepare(strict_load(self.path/'CPU/wait.json'),self.worker_identity)
        from .tiers import consistent
        consistent(self.plan['core_rows'],self.manifest['core_rows'],result['core_rows'])
        self.O=None;self.maps['O'].close();self.maps['O'].unlink();del self.maps['O'];quant={}
        with self.receiver.prepared_view()as y:
            self.host._consumer_boundary("ready_quantization", point="mixed-ready-quantization")
            for bits in (8,16):
                for alpha in (0.,.5,1.):
                    key=str(bits)+':'+str(alpha);science(self.path,'quant-'+key,qualification_quantization_passes=1)
                    h=hashlib.sha256();blocks=0
                    for a in range(0,len(y),64):
                        require(not self._cancelled(),'CANCELLED')
                        value=mix_quantize(self.host.source[a:a+64],y[a:a+64],alpha,bits=bits)
                        h.update(memoryview(value).cast('B'));blocks+=1;del value
                    quant[key]=h.hexdigest()
                    from ..hybrid_replay.audit import append
                    append(self.path/'quantization.jsonl',dict(bits=bits,alpha=alpha,sha256=quant[key],blocks=blocks))
        hashes=dict(O=self.recipe['postprocess']['O_sha256'],**result['hashes']);prior=self.context.get('prior_manifest')
        if prior is not None:require(hashes==prior['results']and quant==prior['quantization'],'HYBRID_COLD_REPLAY_HASH')
        self.ready=dict(hashes=hashes,quantization=quant,handle_id=str(uuid.uuid4()),session_id=self.host.spec['session_id'],
            resident_bytes=self.host.source.nbytes+result['memory']['Y_bytes'],core_rows=self.plan['core_rows'])
        atomic_json(self.path/'hybrid-ready.json',self.ready);return self.ready

    def _activate(self):
        value=super()._activate();self.host.fit_count=int(self.plan['operation']=='fit' and "saved_r0" not in self.plan);return value

    def io(self,kind,**counts):
        if kind=='export-request':
            self.io_counts[kind]=self.io_counts.get(kind,0)+1
            # Public requests are reserved by the calling qualification driver;
            # count the host command and its necessary full quantization here.
            science(self.path,kind+'-'+str(self.io_counts[kind]),host_export_commands=1,extra_internal_quantization=1)
            return
        return super().io(kind,**counts)
