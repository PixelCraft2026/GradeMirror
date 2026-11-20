"""Durable monitor obligation, immutable completion and public projection.

Lock order is the existing recovery lock -> record/admission locks. Only the
original release coordinator authorizes cleanup; saved completion is not a lease.
"""
import hashlib,json,sys,time
from pathlib import Path
from .. import winproc as win
from ..common import GMError,strict_load,sha_file
from ..store import locked
from . import BACKEND_ID
from .contract import require
from .resource_journal import create_record,shared_bytes,read_journal,error_details,canonical
VERSION='hybrid-monitor-completion-1.0'

def root(path):return Path(path)/'monitor-completion'

def required(spec):
    if spec.get('semantics',{}).get('backend_id')!=BACKEND_ID:return False
    from .contract import execution_contract
    return spec['semantics'].get('execution_contract_sha256')==execution_contract()[1]

def binding(spec):
    return dict(protocol=VERSION,job_id=spec['job_id'],attempt_id=spec['attempt_id'],
        intent_sha256=spec['intent_sha256'],execution_contract_sha256=spec['semantics']['execution_contract_sha256'])

def _read(path):
    raw=shared_bytes(path)
    if not raw.endswith(b'\n'):return None
    return json.loads(raw.decode('utf-8'))

def _once(path,value):
    # Caller holds the recovery lock once publication may exist. No rewriting
    # or repairing a partially created fact, including a previous failed write.
    create_record(path,value)

def _best_effort(path,value):
    try:_once(path,value)
    except BaseException as exc:
        sys.stderr.write(json.dumps(dict(protocol=VERSION,unsaved_fact=value,
            diagnostic_error=error_details(exc)),ensure_ascii=False,default=str)+'\n');sys.stderr.flush()

def begin(path,spec):
    p=root(path);p.mkdir(exist_ok=False)
    _once(p/'obligation.json',dict(binding(spec),controller=win.current_identity(),
        created_at=time.time(),session_path=str((Path(path)/'session').resolve())))

def bind_session(path,spec,token,session_id):
    obligation=_obligation(path,spec)
    _once(root(path)/'session.json',dict(binding(spec),token=token,session_id=session_id,
        obligation_sha256=sha_file(root(path)/'obligation.json'),bound_at=time.time()))

def _obligation(path,spec):
    value=_read(root(path)/'obligation.json')
    require(isinstance(value,dict)and all(value.get(k)==v for k,v in binding(spec).items())and
        value.get('session_path')==str((Path(path)/'session').resolve())and
        win.identity_key(value.get('controller'))is not None,'HYBRID_MONITOR_OBLIGATION_INVALID')
    return value

def _session(path,spec):
    value=_read(root(path)/'session.json');pin=strict_load(Path(path)/'hybrid-reservation.json');ss=strict_load(Path(path)/'session/session-spec.json')
    require(isinstance(value,dict)and all(value.get(k)==v for k,v in binding(spec).items())and
        value.get('obligation_sha256')==sha_file(root(path)/'obligation.json')and
        value.get('token')==pin['token']and value.get('session_id')==ss['session_id']and
        all(pin.get(k)==spec[k]for k in('job_id','attempt_id')),'HYBRID_MONITOR_SESSION_INVALID')
    return value

def _file(path,base):
    return dict(path=Path(path).relative_to(base).as_posix(),bytes=Path(path).stat().st_size,sha256=sha_file(path))

def _proof(path,spec):
    path=Path(path);obligation=_obligation(path,spec);session=_session(path,spec);files=[]
    for name in('resource-journal','hybrid-progress-journal'):
        stream=path/name;value=read_journal(stream,records=False);b=value['binding']or{}
        require(value['state']=='COMPLETE'and b.get('job_id')==spec['job_id']and b.get('attempt_id')==spec['attempt_id']and
            b.get('operation_path')==str(path.resolve())and win.identity_key(b.get('writer'))==win.identity_key(obligation['controller']),
            'HYBRID_MONITOR_LOG_INCOMPLETE')
        files.extend(_file(p,path)for p in sorted(stream.iterdir())if p.is_file())
    summary=strict_load(path/'resource-summary.json')
    require(summary.get('protocol')=='resource-journal-1.0'and isinstance(summary.get('peaks'),dict)and
        isinstance(summary.get('counts'),dict),'HYBRID_MONITOR_SUMMARY_INVALID')
    files.append(_file(path/'resource-summary.json',path))
    return dict(binding(spec),obligation_sha256=sha_file(root(path)/'obligation.json'),
        session_sha256=sha_file(root(path)/'session.json'),token=session['token'],files=files)

def complete(path,spec):
    require(not(root(path)/'failed.json').exists(),'HYBRID_MONITOR_ALREADY_FAILED')
    value=_proof(path,spec);_once(root(path)/'complete.json',dict(value,completed_at=time.time()))

def failure(path,spec,exc,*,interrupted=False):
    target=root(path)/'failed.json'
    if target.exists():return
    value=dict(binding(spec),failed_at=time.time(),error_code='HYBRID_MONITOR_CLOSE_INTERRUPTED'if interrupted else'HYBRID_MONITOR_CLOSE_FAILED',
        original_error=error_details(exc,stage='monitor-finalization',identity=win.current_identity()))
    _best_effort(target,value)

def inspect(path,spec):
    p=root(path);value=dict(state='PENDING',cleanup=dict(state='PENDING'),controller_alive=None)
    try:
        obligation=_obligation(path,spec);value['controller']=obligation['controller'];value['controller_alive']=win.identity_alive(obligation['controller'])
        value['obligation_valid']=True
        failed=_read(p/'failed.json')if(p/'failed.json').exists()else None
        if (p/'failed.json').exists():
            require(isinstance(failed,dict)and all(failed.get(k)==v for k,v in binding(spec).items()),'HYBRID_MONITOR_FAILURE_INVALID')
            require(failed.get('error_code')in('HYBRID_MONITOR_CLOSE_FAILED','HYBRID_MONITOR_CLOSE_INTERRUPTED'),'HYBRID_MONITOR_FAILURE_INVALID')
            value.update(state='FAILED',error_code=failed['error_code'],original_error=failed['original_error'],failed_at=failed['failed_at'])
        elif(p/'complete.json').exists():
            fact=_read(p/'complete.json')
            if fact is not None:
                expected=_proof(path,spec)
                require({k:v for k,v in fact.items()if k!='completed_at'}==expected and type(fact.get('completed_at'))in(int,float),
                    'HYBRID_MONITOR_COMPLETION_INVALID')
                value.update(state='COMPLETE',completed_at=fact['completed_at'])
        if value['state']=='PENDING'and value['controller_alive']is False:
            value.update(state='FAILED',error_code='HYBRID_MONITOR_CLOSE_INTERRUPTED',original_error=dict(message='Controller exited without valid monitor completion'))
        attempts=sorted((p/'cleanup').glob('*.json'))if(p/'cleanup').exists()else[]
        if attempts:
            fact=_read(attempts[-1]);require(isinstance(fact,dict)and all(fact.get(k)==v for k,v in binding(spec).items()),'HYBRID_MONITOR_CLEANUP_INVALID')
            session=_session(path,spec);require(fact.get('token')==session['token'],'HYBRID_MONITOR_CLEANUP_INVALID')
            value['cleanup']=fact['cleanup']
    except BaseException as exc:
        value.update(state='FAILED',error_code='HYBRID_MONITOR_CLOSE_FAILED',original_error=error_details(exc,stage='completion-proof'))
        value['proof_error']=True
    return value

def project(path,spec,state,*,validated=False,light=False,observation=None):
    if not required(spec):return state
    observed=inspect(path,spec)if observation is None else observation
    result=dict(state,monitor_completion=observed)
    if light:result.update(progress_only=True,resident=False,binding=None)
    committed=state.get('state')=='SUCCEEDED'or state.get('persistent_complete')is True
    if not committed:return result
    if observed['state']=='COMPLETE':return result
    failed=observed['state']=='FAILED'
    result.update(state='FAILED'if failed else'RUNNING',stage='monitor_finalization',
        detail_stage='MONITOR_CLOSE_FAILED'if failed else'MONITOR_FINALIZING',exit_code=9 if failed else None,
        error_code=observed.get('error_code')if failed else None,resident=False,binding=None,
        result_lease_usable=False,persistent_solve=True)
    if validated:result['persistent_complete']=True
    elif light:result.pop('persistent_complete',None)
    return result

def envelope(state,path,spec,*,idempotent):
    from ..resource_execution import binding as resource_binding
    state=resource_binding.project(state,resource_binding.diagnostic(path,spec))
    return dict(state,idempotent_hit=idempotent,output_root=spec['semantics']['output_root'],
        project_revision=spec['semantics']['revision'],receipt_path=str(Path(path)/'replay-identity.json'))

def intercept(path,spec,state):
    return required(spec)and state['state']=='SUCCEEDED'and inspect(path,spec)['state']!='COMPLETE'

def controller_authorized(path,spec):
    obligation=_obligation(path,spec)
    require(win.identity_key(obligation['controller'])==win.identity_key(win.current_identity()),'HYBRID_MONITOR_CONTROLLER_CONFLICT')

def finish_controller(store,path,spec,monitor):
    """Only the owning controller can activate while its obligation is pending."""
    from . import recovery
    from .audit import progress
    from .publication import residency
    with locked(Path(path)/'hybrid-recovery.lock',timeout=5):
        controller_authorized(path,spec)
        state=recovery.persistent(store,path,spec);require(state['state']=='SUCCEEDED','RESULT_UNAVAILABLE')
        active=recovery._recover_coordinated(store,path,spec,deadline=time.monotonic()+45,_controller=True)
        monitor.boundary('output');progress(path,'RESIDENT_READY'if active['resident']else'NONRESIDENT')
        monitor.finish_logs();monitor.release_observers();complete(path,spec)
        return residency(path,spec,state)

def _cleanup_record(path,spec,cleanup):
    target=root(path)/'cleanup';target.mkdir(exist_ok=True)
    seq=len(list(target.glob('*.json')))+1;session=_session(path,spec)
    _best_effort(target/(str(seq).zfill(6)+'.json'),dict(binding(spec),token=session['token'],observed_at=time.time(),cleanup=cleanup))

def coordinate_locked(store,path,spec,state,deadline):
    """Called with the original recovery lock held; never calls public release."""
    from .recovery import _continue_release
    observed=inspect(path,spec)
    if observed['state']=='PENDING':return project(path,spec,state,validated=True,observation=observed)
    if observed['state']=='COMPLETE':return project(path,spec,state,validated=True,observation=observed)
    if not(root(path)/'failed.json').exists():
        failure(path,spec,GMError(observed['error_code'],str(observed['original_error']),9),interrupted=observed['error_code']=='HYBRID_MONITOR_CLOSE_INTERRUPTED')
    if observed['cleanup']['state']!='COMPLETE':
        try:
            _obligation(path,spec);_session(path,spec)
            closing=_continue_release(store,path,spec,state,deadline,create=True)
            cleanup=dict(state='COMPLETE'if closing.get('released')is True else'PENDING',release=closing)
        except BaseException as exc:
            cleanup=dict(state='PENDING',error=error_details(exc,stage='monitor-cleanup'))
        try:_cleanup_record(path,spec,cleanup)
        except BaseException as exc:
            sys.stderr.write(json.dumps(dict(cleanup_unsaved=cleanup,error=error_details(exc)),ensure_ascii=False,default=str)+'\n');sys.stderr.flush()
        observed['cleanup']=cleanup
    return project(path,spec,state,validated=True,observation=observed)

def fail_controller(store,path,spec,monitor,exc):
    from .recovery import persistent
    # Persist the failure before any observer handles can be discarded. The
    # strict release coordinator then opens/retains its own verified handles.
    failure(path,spec,exc)
    try:monitor.finish_logs()
    except BaseException as secondary:
        if secondary is not exc:monitor.diagnostics.append(error_details(secondary,stage='monitor-close-secondary'))
    try:
        with locked(Path(path)/'hybrid-recovery.lock',timeout=5):
            state=persistent(store,path,spec)
            result=coordinate_locked(store,path,spec,state,time.monotonic()+45)
    except BaseException as secondary:
        state=persistent(store,path,spec);observed=inspect(path,spec)
        observed.update(state='FAILED',error_code='HYBRID_MONITOR_CLOSE_FAILED',original_error=error_details(exc),
            cleanup=dict(state='PENDING',error=error_details(secondary,stage='cleanup-coordination')))
        result=project(path,spec,state,validated=True,observation=observed)
    if result['state']!='FAILED':
        observed=inspect(path,spec)
        observed.update(state='FAILED',error_code='HYBRID_MONITOR_CLOSE_FAILED',original_error=error_details(exc))
        result=project(path,spec,persistent(store,path,spec),validated=True,observation=observed)
    return envelope(result,path,spec,idempotent=False)

def dispose_monitor(monitor):
    # Normal closure was processed inside the ordinary try/except. Unpublished
    # failures keep their primary exception; cleanup diagnostics go to stderr.
    if monitor is None:return
    try:monitor.close()
    except BaseException as exc:
        sys.stderr.write(json.dumps(dict(protocol=VERSION,finalizer_error=error_details(exc),secondary=monitor.diagnostics),ensure_ascii=False,default=str)+'\n');sys.stderr.flush()
