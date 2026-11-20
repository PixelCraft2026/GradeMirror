"""Bounded product Jobs and external OS-only phase observation."""
import ctypes, os, sys, threading, time, traceback
from collections import deque
from pathlib import Path
from .. import winproc as win
from ..common import GMError, atomic_json, strict_load
from ..fine_observation import sample_processes
from .audit import append, start, progress
from .resources import memory
from .ownership import classify
from .contract import require
from .resource_journal import Journal, VERSION as JOURNAL_VERSION, create_record, preserve_error, error_details, close_progress

ROOT=Path(__file__).resolve().parents[3]
CONTROL=Path('__historical_runtime_not_shipped__')
NUMERIC=Path('__historical_runtime_not_shipped__')
IO=Path('__historical_runtime_not_shipped__')
ENTRY=ROOT/'tools/gm021_compute_worker.py'
GIB=1024**3


class Nvml:
    """GM020 S7 process/memory query with driver/name metadata added."""
    def __init__(self,uid):
        from ..light.runtime import operation
        if operation() is not None:
            from ..light.system import signed,system_scope,windows_root
            windows=windows_root();library=windows/"System32/nvml.dll"
            require(system_scope(library,windows)=="nvidia" and signed(library,"nvidia"),"LIGHT_NVML_TRUST")
        else:
            library=Path(os.environ['SystemRoot'])/'System32/nvml.dll'
        self.d=ctypes.CDLL(str(library));self.h=ctypes.c_void_p();self.uid=uid
        self.require(self.d.nvmlInit_v2());self.d.nvmlDeviceGetHandleByUUID.argtypes=[ctypes.c_char_p,ctypes.POINTER(ctypes.c_void_p)]
        self.require(self.d.nvmlDeviceGetHandleByUUID(uid.encode(),ctypes.byref(self.h)))
    @staticmethod
    def require(code):
        if code:raise GMError('NVML_UNKNOWN','NVML status '+str(code),5)
    def sample(self):
        from ..resource_policy import Memory
        class Proc(ctypes.Structure):_fields_=[('pid',ctypes.c_uint),('used',ctypes.c_ulonglong),('gpuInstanceId',ctypes.c_uint),('computeInstanceId',ctypes.c_uint)]
        mem=Memory();self.require(self.d.nvmlDeviceGetMemoryInfo(self.h,ctypes.byref(mem)));groups={}
        for name in('Compute','Graphics'):
            fn=getattr(self.d,'nvmlDeviceGet'+name+'RunningProcesses_v2');n=ctypes.c_uint(0);code=fn(self.h,ctypes.byref(n),None)
            require(code in(0,7) and n.value<=1024,'NVML_PROCESS_UNKNOWN')
            capacity=min(1024,max(16,n.value+16));values=(Proc*capacity)();n.value=capacity
            self.require(fn(self.h,ctypes.byref(n),values));groups[name]=[int(values[i].pid)for i in range(n.value)]
        driver=ctypes.create_string_buffer(256);name=ctypes.create_string_buffer(256)
        self.require(self.d.nvmlSystemGetDriverVersion(driver,256));self.require(self.d.nvmlDeviceGetName(self.h,name,256))
        return dict(uuid=self.uid,name=name.value.decode(),driver_version=driver.value.decode(),
                    total=mem.total,free=mem.free,used=mem.used,**groups)
    def close(self):self.require(self.d.nvmlShutdown())


class Monitor:
    def __init__(self,path,plan,owner,host,host_launcher=None):
        self.path=Path(path);self.plan=plan;self.roles=[('control',win.current_identity()),('session_owner',owner),('host',host)]
        if host_launcher is not None:self.roles.append(('host_launcher',host_launcher))
        self.lock=threading.RLock();self.job=None;self.phase='R0';self.handles={};self.history={};self.times=deque()
        self.error=None;self.stop=threading.Event();self.nv=Nvml(plan['device']['uuid']);self.peaks={};self.counts=dict(raw_unknown=0,final_unknown=0,samples=0);self.cancel_seen=None
        self.thread=threading.Thread(target=self._loop,daemon=True)
        base=self.path if(self.path/'spec.json').exists()else self.path.parent
        spec=strict_load(base/'spec.json')if(base/'spec.json').exists()else{}
        try:
            self.journal=Journal(self.path/'resource-journal',dict(role='resource-attempts',operation_path=str(base.resolve()),
                job_id=spec.get('job_id',base.parent.name),attempt_id=spec.get('attempt_id',base.name),writer=win.current_identity()))
        except BaseException as exc:
            secondary=[]
            try:self.nv.close()
            except BaseException as cleanup:secondary.append(error_details(cleanup,operation='nvml-close',path=self.path))
            preserve_error(self.path/'resource-open-error.json',exc,stage=self.phase,identity=win.current_identity(),secondary=secondary)
            raise
        from ..light.resources import active_plan
        dynamic=active_plan("mixed")
        self.dynamic_limits=dynamic["selection"]["jobs"] if dynamic is not None else None
        if self.dynamic_limits is not None:
            require(self.plan["resource_budget"]==self.dynamic_limits["pool"],"LIGHT_MIXED_JOB_BUDGET_CHANGED")
        self.latest=None;self.diagnostics=[]
        self._logs_attempted=False;self._logs_error=None;self._observers_released=False
    def begin(self):self.thread.start();return self
    def set_job(self,job,phase):
        with self.lock:self.job=job;self.phase=phase
    def _sample(self):
        while True:
            now=time.monotonic()
            while self.times and now-self.times[0]>=1.:self.times.popleft()
            delay=max(0.,1.001-(now-self.times[0]))if len(self.times)>=5 else 0.
            if self.times:delay=max(delay,.025-(now-self.times[-1]))
            if delay<=0:break
            time.sleep(delay)
        self.times.append(time.monotonic())
        snapshots=[] if self.job is None else[self.job.member_snapshot()]
        tree=sample_processes(self.roles,snapshots=snapshots)
        for row in tree['processes']:
            ident=row['identity'];key=win.identity_key(ident);self.history[key]=ident
            if key not in self.handles:
                h=win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION|win.SYNCHRONIZE,False,ident['pid'])
                if h and win.identity_key(win.process_identity(ident['pid']))==key:self.handles[key]=(h,ident)
                elif h:win.close(h)
        gpu=self.nv.sample();ownership=classify(gpu['Compute'],gpu['Graphics'],self.history,self.handles,
                                             win.process_identity,lambda h:win.k.WaitForSingleObject(h,0))
        row=dict(clock=self.times[-1],wall=time.time(),stage=self.phase,complete=tree['complete']and ownership['complete'],
                 tree=tree,gpu=gpu,ownership=ownership,system=memory(),job_highwater=None if self.job is None else self.job.memory_peak())
        self.journal.append(row)
        if ownership['foreign']:raise GMError('FOREIGN_PURE_COMPUTE','Unowned GPU compute identity',5)
        return row
    def check(self):
        if self.error is not None:raise self.error
    def boundary(self,phase):
        with self.lock:
            self.phase=phase
            for _ in range(3):
                row=self._sample()
                if row['complete']:break
                self.counts['raw_unknown']+=1
            if not row['complete']:
                self.counts['final_unknown']+=1;raise GMError('CRITICAL_UNKNOWN','Incomplete phase observation',5)
            rss=row['tree']['tree_rss_bytes'];private=row['tree']['tree_private_commit_bytes']
            if self.dynamic_limits is not None:
                require(rss<=self.dynamic_limits["tree_rss"] and private<=self.dynamic_limits["tree_private"],
                        'HYBRID_TREE_RESOURCE')
            else:
                require(rss<=6*GIB and private<=6*GIB,'HYBRID_TREE_RESOURCE')
                if phase in('CPU','ready','output'):require(rss<=3*GIB and private<=int(4.5*GIB),'HYBRID_CPU_RESOURCE')
            peak=self.peaks.setdefault(phase,dict(rss=0,private=0));peak['rss']=max(peak['rss'],rss);peak['private']=max(peak['private'],private)
            self.counts['samples']+=1;self.latest=row
            if phase in('R0','CPU')or phase=='P3'and self.plan.get('fit_contract')=='gm023-hybrid-fit-v1':
                from ..live_json import read_live_json
                value=read_live_json(self.path/phase/'progress.json')
                if value:progress(self.path,phase,phase_progress=value)
            return row
    def _loop(self):
        while not self.stop.is_set():
            if(self.path/'cancel.signal').exists():
                if self.cancel_seen is None:self.cancel_seen=time.monotonic()
                if(self.path/'CPU').is_dir():(self.path/'CPU/cancel.signal').touch()
                with self.lock:
                    if self.job is not None and self.job.poll()is None and time.monotonic()-self.cancel_seen>=10:
                        self.job._hybrid_forced=True;self.job.terminate()
            try:self.boundary(self.phase)
            except BaseException as exc:
                self.error=exc
                preserve_error(self.path/'resource-STOP.json',exc,stage=self.phase,identity=win.current_identity())
                try:(self.path/'cancel.signal').touch()
                except BaseException as secondary:self.diagnostics.append(error_details(secondary,operation='cancel-signal',path=self.path/'cancel.signal'))
                try:
                    with self.lock:
                        if self.job is not None and self.job.poll()is None:
                            self.job._hybrid_forced=True;self.job.terminate()
                except BaseException as secondary:
                    self.diagnostics.append(error_details(secondary,operation='terminate-owned-job',path=self.path))
                    preserve_error(self.path/'resource-cleanup-error.json',exc,stage=self.phase,identity=win.current_identity(),secondary=self.diagnostics)
                return
            self.stop.wait(1.)
    def stop_sampling(self):
        self.stop.set();self.thread.join(timeout=10)
        require(not self.thread.is_alive(),'MONITOR_EXIT')

    def finish_logs(self):
        if self._logs_attempted:
            if self._logs_error is not None:raise self._logs_error
            return
        self._logs_attempted=True;primary=self.error
        def finish(action):
            nonlocal primary
            try:action()
            except BaseException as exc:
                if primary is None:primary=exc
                else:self.diagnostics.append(error_details(exc,stage=self.phase,identity=win.current_identity()))
        finish(self.stop_sampling)
        finish(self.nv.close);finish(self.journal.close);finish(lambda:close_progress(self.path))
        finish(lambda:create_record(self.path/'resource-summary.json',dict(protocol=JOURNAL_VERSION,peaks=self.peaks,counts=self.counts,
            sampled_not_continuous=True,no_RSS_deduplication=True,Job_highwater_not_a_stage_peak=True)))
        self._logs_error=primary
        if primary is not None:
            preserve_error(self.path/'resource-close-error.json',primary,stage=self.phase,identity=win.current_identity(),secondary=self.diagnostics)
            raise primary

    def release_observers(self):
        if self._observers_released:return
        primary=None
        for h,identity in self.handles.values():
            try:
                try:
                    state=win.k.WaitForSingleObject(h,0)
                    if state==win.WAIT_OBJECT_0:
                        append(self.path/'actual-exits.jsonl',dict(identity=identity,actual_handle_wait=win.k.WaitForSingleObject(h,10000)==win.WAIT_OBJECT_0))
                finally:win.close(h)
            except BaseException as exc:
                if primary is None:primary=exc
                else:self.diagnostics.append(error_details(exc,stage=self.phase,identity=win.current_identity()))
        self.handles.clear();self._observers_released=True
        if primary is not None:raise primary

    def close(self):
        primary=sys.exc_info()[1]
        for action in(self.finish_logs,self.release_observers):
            try:action()
            except BaseException as exc:
                if primary is None:primary=exc
                elif exc is not primary:self.diagnostics.append(error_details(exc,stage=self.phase,identity=win.current_identity()))
        if primary is not None:
            preserve_error(self.path/'resource-close-error.json',primary,stage=self.phase,identity=win.current_identity(),secondary=self.diagnostics)
            raise primary

    def wait_session_exits(self):
        with self.lock:
            for role,identity in self.roles:
                if role=='control':continue
                held=self.handles.get(win.identity_key(identity))
                require(held is not None,'SESSION_EXIT_HANDLE_MISSING')
                waited=win.k.WaitForSingleObject(held[0],10000)==win.WAIT_OBJECT_0
                append(self.path/'actual-exits.jsonl',dict(identity=identity,actual_handle_wait=waited,cleanup_role=role))
                require(waited,'SESSION_EXIT_WAIT')


def spawn(path,mode,payload,*,cpu,monitor):
    from ..resource_execution.binding import pointer
    resource=pointer(path)
    if resource is not None:
        data=strict_load(payload);data['resource_execution']=resource;atomic_json(payload,data)
    new=monitor.plan.get('fit_contract')=='gm023-hybrid-fit-v1'
    out=Path(path)/mode;out.mkdir(exist_ok=False);start(mode,4 if new else 2)
    env=dict(os.environ,PYTHONPATH=str(ROOT/'src'),PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',
        CUDA_VISIBLE_DEVICES='-1'if cpu else'0',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4')
    entry=ROOT/'tools/gm023_compute_worker.py'if new else ENTRY
    argv=[str(NUMERIC),'-B','-X','utf8',str(entry),mode,str(payload)]
    if new:
        from ..hybrid_fit.audit import record_launch
        record_launch(mode,argv,ROOT)
    from ..light.runtime import operation,worker
    if operation() is not None:
        require(new,"LIGHT_MIXED_LEGACY_CONTRACT_UNSUPPORTED")
        argv,env=worker("mixed",(mode,payload),out)
    jobs=monitor.dynamic_limits
    limit=(jobs["cpu_worker"] if cpu else jobs["pool"]["compute"]) if jobs is not None else 6*GIB
    job=win.OwnedJob(limit);who=job.spawn(argv,str(ROOT),env)
    atomic_json(out/'spawn.json',dict(identity=who,job_limit=limit));monitor.set_job(job,mode)
    return job,who,out


def finish(job,who,out,*,monitor,seconds=10,expected=(0,),accept_forced=False):
    end=time.monotonic()+seconds
    held=getattr(job,'_hybrid_actual',None)
    h,actual=held if held is not None else (None,None)
    forced=getattr(job,'_hybrid_forced',False)
    observation_error=None
    try:
        while job.poll()is None and time.monotonic()<end:
            if h is None and observation_error is None and (out/'entry.json').is_file():
                try:
                    candidate=strict_load(out/'entry.json')['identity']
                    handle=win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION|win.SYNCHRONIZE,False,candidate['pid'])
                    try:
                        require(handle and job.member(candidate['pid']) and
                            win.identity_key(win.process_identity(candidate['pid']))==win.identity_key(candidate),'HYBRID_WORKER_HANDLE')
                        h,actual=handle,candidate
                        handle=None
                    finally:
                        if handle:win.close(handle)
                except Exception as exc:
                    observation_error=error_details(exc,operation='retain-actual-worker',path=out/'entry.json')
            time.sleep(.02)
        if job.poll()is None:
            job.terminate()
            forced=True
        code=job.wait(10000)
        empty=job.wait_empty(10000)
        waited=h is not None and win.k.WaitForSingleObject(h,10000)==win.WAIT_OBJECT_0
        forced=forced or getattr(job,'_hybrid_forced',False)
        receipt=dict(actual_wait=True,actual_worker_handle_wait=waited,exit_code=code,tree_empty=empty,
            forced=forced,launcher=who,worker_identity=actual,job_peak=job.memory_peak())
        if observation_error is not None:
            receipt['worker_observation_error']=observation_error
        atomic_json(out/'wait.json',receipt)
        require(waited and empty and code in expected and (accept_forced or not forced),'HYBRID_WORKER_EXIT')
        return receipt
    finally:
        if h:win.close(h)
        job._hybrid_finished=True
        try:
            monitor.set_job(None,monitor.phase)
        finally:
            job.close()


def settle_failed_worker(active,monitor):
    job,who,out=active
    if getattr(job,'_hybrid_finished',False):
        return
    # Give the failed worker no grace interval. The previously retained
    # handle stays authoritative across forced exit and is waited in finish.
    return finish(job,who,out,monitor=monitor,seconds=0,expected=(0,1,8,9),accept_forced=True)


def retain_actual(job,out,monitor):
    end=time.monotonic()+30
    while not(out/'entry.json').exists():
        monitor.check()
        if job.poll()is not None or time.monotonic()>end:raise GMError('WORKER_ENTRY_MISSING','Actual worker did not enter',9)
        time.sleep(.01)
    ident=strict_load(out/'entry.json')['identity'];h=win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION|win.SYNCHRONIZE,False,ident['pid'])
    require(h and win.identity_key(win.process_identity(ident['pid']))==win.identity_key(ident),'ACTUAL_WORKER_IDENTITY')
    job._hybrid_actual=(h,ident)
    atomic_json(out/'actual-handle-retained.json',dict(identity=ident,retained=True))
