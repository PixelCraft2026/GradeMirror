"""One operation per short process; existing OwnedJob supplies process control."""
import os
import sys
import time
from pathlib import Path
from ..common import atomic_json,strict_load
from ..cpu_replay.contract import ROOT,binding,verify
from .intent import need
_guard=None

class Guard:
    def __init__(self,c):
        self.c=c;self.reads=set();self.writes=set();self.denied=[];self.timings={};self._clock={}
        self.roots=[Path(c[k]).resolve() for k in ('output_root','evidence_root','pool_root')]
        self.roots.append(Path(c['workspace'])/'.adaptive')
        self.roots.extend(Path(p).resolve() for p in c['paths'].values())
        self.inputs=[Path(c['source']['path']).resolve()]
        if c['reference']:self.inputs.append(Path(c['reference']['path']).resolve())
        if c['request']['recipe']:self.inputs.append(Path(c['request']['recipe']['directory']).resolve())
        q=strict_load(Path(c['qualification']['path']))
        from .qualification import used_assets
        self.assets=used_assets(q,c['request'])
        self.inputs.extend(Path(row['path']).resolve() for row in self.assets)
        inventory=strict_load(Path(c['runtime']['inventory']['path']))['files']
        # These are already canonical frozen file bindings. Actual open/load
        # targets are still resolved and hashed; resolving 12k unused frozen
        # names per control process provides no additional identity proof.
        self.exact={Path(row['path']) for row in inventory}
        self.exact.update((ROOT/row['path']).resolve() for row in c['executor_build']['files'])
        from .qualification import factory_for
        self.authority_reads={Path(c['qualification']['path']).resolve(),factory_for(c['qualification']['path']).with_name('factory-index.json').resolve(),
            Path(c['runtime']['inventory']['path']).resolve()}
        self.authority_reads.update(Path(item['path']).resolve() for item in c['interpreters'].values())
        self.exact.update(self.authority_reads)
        self.known={os.path.normcase(str(Path(x['path']))):x['sha256'] for x in inventory}
        self.known.update({os.path.normcase(str((ROOT/x['path']).resolve())):x['sha256'] for x in c['executor_build']['files']})
        self.read_roots=[]
        if c['request']['operation']=='fit' and c['request']['quality']=='fine':self.read_roots.append(Path(c['workspace'])/'operations')
        self.runtime_roots=[Path(v['path']).resolve().parents[1] for k,v in c['interpreters'].items() if k in ('control','io')]
    def audit(self,event,args):
        if event=='import' and args and args[0].split('.')[0] in ('torch','scipy','pynvml','pycuda'):
            raise ImportError('APPLICATION_ZERO_SCIENCE_IMPORT:'+args[0])
        if event=='ctypes.dlopen' and args and any(x in str(args[0]).lower() for x in ('nvcuda','cudart','nvml')):
            raise RuntimeError('APPLICATION_GPU_DLL_FORBIDDEN')
        targets=[];write=False
        if event=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
            if os.name=='nt' and os.fsdecode(args[0]).casefold()=='nul':return
            mode=args[1];flags=args[2] if len(args)>2 else 0
            write=(isinstance(mode,str) and any(k in mode for k in 'wax+')) or bool(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            targets=args[:1]
        elif event in ('os.mkdir','os.remove','os.rmdir','os.rename'):
            targets=args[:2] if event=='os.rename' else args[:1];write=True
        for target in targets:
            if not isinstance(target,(str,bytes,os.PathLike)):continue
            p=Path(os.fsdecode(target)).absolute().resolve()
            if write:
                need(any(p.is_relative_to(r) for r in self.roots),'APPLICATION_WRITE_SCOPE',str(p));self.writes.add(str(p))
            else:
                allowed=p in self.exact or any(p.is_relative_to(r) for r in self.roots+self.inputs+self.read_roots+[ROOT/'src',ROOT/'tools'])
                allowed=allowed or p.is_relative_to(ROOT/'docs/evidence/gm-055/20261031-plan-v1')
                if not allowed:self.denied.append(str(p));raise PermissionError('APPLICATION_READ_SCOPE:'+str(p))
                self.reads.add(str(p))
        if event in ('os.system','os.spawn','os.posix_spawn'):raise RuntimeError('APPLICATION_UNDECLARED_PROCESS')
    def profile(self,frame,event,arg):
        if event not in ('call','return'):return
        name=frame.f_code.co_name
        if event=='return' and name not in ('load','authorize','verified_closure','require_runtime','loaded_binaries','binding','sha_file'):return
        f=frame.f_code.co_filename.replace('\\','/')
        if name in ('load','authorize','verified_closure','require_runtime','loaded_binaries','binding','sha_file'):
            key=f+':'+name
            if event=='call':self._clock[id(frame)]=(key,time.perf_counter())
            elif event=='return':
                prior=self._clock.pop(id(frame),None)
                if prior:
                    row=self.timings.setdefault(prior[0],dict(calls=0,seconds=0.));row['calls']+=1;row['seconds']+=time.perf_counter()-prior[1]
        if event!='call':return
        forbidden=(('/independent_r0/core.py' in f and name in ('__init__','forward','render_tiles')) or
            ('/cpu_r0_fit/' in f and name in ('_fit_loop','fit')) or
            ('/cpu_postprocess/' in f and name in ('execute','scalar_footprint','p3_region','worker')) or
            ('/independent_r0_p3_lld10/' in f and name in ('area_prefix','solve','evaluate','llf')) or
            (f.endswith('/group15_codec.py') and name in ('read_source','read_reference','inspect_source','inspect_reference')))
        if forbidden:raise RuntimeError('APPLICATION_UNEXPECTED_SCIENCE:'+f+':'+name)

def enter(role,work,c):
    global _guard
    need(role in ('application_control','owner','host','frame_sender','entry_control','entry_host','entry_owner'), 'APPLICATION_ROLE_FORBIDDEN')
    if _guard is None:
        _guard=Guard(c);sys.addaudithook(_guard.audit);sys.setprofile(_guard.profile)
    else:need(_guard.c['id']==c['id'],'APPLICATION_PROCESS_CONTEXT_REUSE')
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    action=strict_load(work/'payload.json')['action'] if role=='application_control' else None
    _guard.require_assets=role!='application_control' or action=='submit'
    from ..winproc import current_identity
    atomic_json(work/'actual-entry.json',dict(identity=current_identity(),role=role))
    observer=os.environ.get('GM052_OBSERVER_ROOT')
    need(observer,'APPLICATION_LAUNCH_ACCOUNTING_REQUIRED')
    q=strict_load(Path(c['qualification']['path']))
    need(any(Path(observer).resolve().is_relative_to(Path(root)) for root in q['observer_roots']), 'APPLICATION_OBSERVER_SCOPE')
    import uuid
    entry=Path(observer)/'entries'/uuid.uuid4().hex
    # Observer receipts are part of the explicitly frozen qualification writer.
    _guard.roots.append(Path(observer).resolve())
    atomic_json(work/'guard-graph.json',dict(role=role,operation=c['id'],qualification_reads=sorted(map(str,_guard.authority_reads)),
        exact_runtime_files=len(_guard.exact),write_roots=sorted(map(str,_guard.roots)),input_roots=sorted(map(str,_guard.inputs)),
        composite_child_read_roots=sorted(map(str,_guard.read_roots)),native_science_allowed=False))
    atomic_json(entry/'entry.json',dict(identity=current_identity(),role=role,work=str(work),entry_only=False))
    until=time.monotonic()+30
    while not (entry/'ack.json').is_file():
        need(time.monotonic()<until,'APPLICATION_ACTUAL_HANDLE_ACK');time.sleep(.01)

def observation(work,role,status):
    capture=require_runtime(work,_guard.c)
    atomic_json(Path(work)/'runtime-observation.json',dict(role=role,status=status,modules=capture['modules'],binaries=capture['binaries'],
        reads=sorted(_guard.reads),writes=sorted(_guard.writes),denied=_guard.denied,
        metadata_profile=_guard.timings,science=0))

def require_runtime(work,c):
    from ..cpu_replay.runtime import loaded_binaries
    from ..cpu_replay.contract import executor_closure
    need(executor_closure()==c['executor_build'],'APPLICATION_EXECUTOR_CHANGED')
    if _guard.require_assets:
        for row in _guard.assets:verify(row)
    verify(c['runtime']['inventory'])
    need(_guard is not None and _guard.c['id']==c['id'],'APPLICATION_RUNTIME_CONTEXT')
    known=_guard.known
    binaries=loaded_binaries();modules=[]
    for name,module in sorted(sys.modules.items()):
        p=getattr(module,'__file__',None)
        if p and Path(p).is_file() and Path(p).suffix.lower() in ('.py','.pyd','.dll'):modules.append(dict(module=name,**binding(p)))
    rows=binaries+modules
    # binding() resolved each actual file immediately before hashing it.
    diff=[dict(actual=x,expected=known.get(os.path.normcase(x['path']))) for x in rows
          if known.get(os.path.normcase(x['path']))!=x['sha256']]
    atomic_json(Path(work)/'before-math-runtime.json',dict(actual=rows,differences=diff));need(not diff,'APPLICATION_RUNTIME_DRIFT')
    return dict(modules=modules,binaries=binaries)

def reserve(path,spec):
    from .first_context import operation
    from .context import context as first_context
    if first_context(spec).get('schema')=='cpu-application-execution-v4':
        from .first_science import reserve as first_reserve
        return first_reserve(path,spec)
    from .context import context
    c=context(spec)
    if c.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v5'):
        from .native_science import reserve as native_reserve
        return native_reserve(path,spec)
    atomic_json(Path(path)/'scientific-reservation.json',dict(TEST_ONLY=True,science=0,photo_decode=0,
        factory=c['qualification'],before_any_frame=True,application_context=spec['semantics']['instance_context']))

def host_count(host,delta):
    if host.context and host.context.get('schema')=='cpu-application-execution-v4':
        from .first_science import host_count as first_count
        return first_count(host,delta)
    if host.context and host.context.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v5'):
        from .native_science import host_count as native_count
        return native_count(host,delta)
    need(all(type(v) is int and v>=0 for v in delta.values()),'APPLICATION_COUNT_UNKNOWN')
    need(not any(delta.get(k,0) for k in ('source_decode','reference_decode','R0_render','forward','CPU256_service')),
         'APPLICATION_SCIENCE_COUNT_NONZERO')
    for k,v in delta.items():host.counts[k]=host.counts.get(k,0)+v
    atomic_json(host.session/'io-counts.json',host.counts)
