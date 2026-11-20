"""Public recovery of a committed hybrid result. No pixel or compute entry.

Lock order: recovery.lock -> short record/admission locks. Host IPC and actual
waits hold only recovery.lock; host validation never calls public status().
"""
import os
import time
from pathlib import Path

from ..common import GMError, atomic_json, strict_load, reject_reparse
from ..store import locked
from .. import winproc as win
from . import BACKEND_ID
from .contract import require, versions


def persistent(store, path, spec):
    """Recover durable facts under record.lock, without IPC or residency."""
    from ..gm004a_operations import _journal, _append, TERMINAL
    from .publication import recover_locked
    with locked(path/'record.lock', timeout=5):
        state = dict(_journal(path)[-1]['snapshot'])
        recovered = recover_locked(store, path, spec, state)
        if recovered is not None:
            return recovered
        require(not(path/'commit.json').exists(), 'HYBRID_PUBLICATION_MISSING')
        owner = state.get('owner') or spec.get('startup_owner')
        if state['state'] not in TERMINAL and win.identity_alive(owner) is False:
            cancelled = state['state']=='CANCEL_REQUESTED'
            state.update(state='CANCELLED' if cancelled else 'FAILED', stage='recovered',
                         exit_code=8 if cancelled else 9,
                         error_code='CANCELLED' if cancelled else 'INTERRUPTED')
            _append(path, state, 'HYBRID_UNPUBLISHED_OWNER_EXIT')
        return state


def process_state(identity):
    """PID reuse is a conflict, never evidence authorizing cleanup."""
    require(isinstance(identity, dict) and type(identity.get('pid')) is int and
            type(identity.get('creation_time')) is int and
            isinstance(identity.get('image_path'), str) and not identity.get('unknown'),
            'HYBRID_PROCESS_IDENTITY_UNKNOWN')
    actual = win.process_identity(identity['pid'])
    if actual is None:
        return False
    require(not actual.get('unknown'), 'HYBRID_PROCESS_IDENTITY_UNKNOWN')
    require(win.identity_key(actual)==win.identity_key(identity), 'HYBRID_PROCESS_IDENTITY_CONFLICT')
    return True


def _projection(path, spec, state, reason, *, activation=None, retry=False):
    from .publication import residency
    result = residency(path, spec, state)
    if activation is None:
        result.update(resident=False, binding=None)
    else:
        from ..gm004a_api import _binding
        result.update(resident=True, binding=_binding(activation['lease']),
                      result_lease=activation['lease'])
    result['recovery'] = dict(reason=reason, retryable=retry,
                              resident=result['resident'], persistent_complete=result['persistent_complete'])
    return result


def _context(store, path, spec):
    from .. import gm004a_api as api
    session = path/'session'
    for name in ('hybrid-reservation.json','session/session-spec.json',
                 'session/owner.json','session/host-process.json','session/host-entry.json'):
        reject_reparse(path/name)
    pin = strict_load(path/'hybrid-reservation.json')
    require(all(pin.get(k)==spec[k] for k in ('job_id','attempt_id')), 'HYBRID_RESERVATION_BINDING')
    pool = api._resident_pool()
    row = pool.for_operation(spec['job_id'], spec['attempt_id'], session)
    require(row['token']==pin['token'] and Path(row['store_root'])==store.root.parent,
            'HYBRID_RESERVATION_TOKEN_CONFLICT')
    ss = strict_load(session/'session-spec.json')
    context = ss.get('hybrid_execution', {})
    require(ss.get('backend_id')==BACKEND_ID and
            all(context.get(k)==spec[k] for k in ('job_id','attempt_id')) and
            Path(context.get('operation_path',''))==path and
            context.get('plan',{}).get('execution_contract_sha256')==spec['semantics']['execution_contract_sha256'],
            'HYBRID_SESSION_BINDING')
    who = [strict_load(session/n) for n in ('owner.json','host-process.json','host-entry.json')]
    require(row.get('session_id')==ss['session_id'] and
            all(win.identity_key(row[k])==win.identity_key(v) for k,v in zip(('owner','host'),who)),
            'HYBRID_RESERVATION_PROCESS_CONFLICT')
    live = [process_state(v) for v in who]
    return pool, row, ss, who, live


def _handles(who, live):
    held = []
    try:
        for identity, alive in zip(who, live):
            if not alive:
                continue
            h = win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION|win.SYNCHRONIZE,
                                  False, identity['pid'])
            require(h, 'HYBRID_PROCESS_HANDLE_UNKNOWN')
            held.append((h, identity))
            require(process_state(identity) is True, 'HYBRID_PROCESS_EXIT_DURING_OPEN')
        return held
    except BaseException:
        for h,_ in held: win.close(h)
        raise


def _wait(path, held, deadline, name):
    receipts = []
    for handle, identity in held:
        milliseconds = max(0, int((deadline-time.monotonic())*1000))
        waited = win.k.WaitForSingleObject(handle, milliseconds)==win.WAIT_OBJECT_0
        receipts.append(dict(identity=identity, actual_handle_wait=waited))
        require(waited, 'HYBRID_OWNED_EXIT_UNCONFIRMED')
    atomic_json(path/name, receipts)
    return receipts


def _cleanup(path, pool, row, who, live):
    """Only exact owned identities; no PID scans or unknown-process kill."""
    held = _handles(who, live)
    deadline = time.monotonic()+15
    try:
        atomic_json(path/'hybrid-release.json', dict(state='LOST', token=row['token'], reason='RECOVERY_IPC_FAILED'))
        if live[0]: win.terminate_verified(who[0])
        _wait(path, held, deadline, 'hybrid-recovery-cleanup-waits.json')
        require(all(process_state(v) is False for v in who), 'HYBRID_OWNED_EXIT_UNCONFIRMED')
        pool.close(row['token'], outcome='LOST')
    finally:
        for h,_ in held: win.close(h)


def _recover_coordinated(store, path, spec, deadline=None,*,_controller=False):
    from .. import gm004a_api as api
    state = persistent(store, path, spec)
    from . import monitor_completion as completion
    if _controller:completion.controller_authorized(path,spec)
    elif completion.intercept(path,spec,state):
        return completion.coordinate_locked(store,path,spec,state,deadline or time.monotonic()+45)
    if state['state']!='SUCCEEDED':
        return _projection(path, spec, state, 'NOT_PUBLISHED')
    from ..resource_execution import binding as resource_binding
    plan_error=resource_binding.diagnostic(path,spec)
    if plan_error is not None:
        closing=_continue_release(store,path,spec,state,deadline or time.monotonic()+45,create=True)
        return resource_binding.project(_release_projection(path,spec,state,closing),plan_error)
    lease = state['result_lease']; versions(lease)
    if (path/'hybrid-release.json').exists():
        return _release_projection(path,spec,state,_continue_release(store,path,spec,state,deadline or time.monotonic()+45))
    # An older completed operation must remain queryable after another Store
    # has acquired the pool. Confirm all of its identities before looking up
    # its old token; a stale close never touches the new reservation.
    identities=[strict_load(path/'session'/n) for n in ('owner.json','host-process.json','host-entry.json')]
    if not any(process_state(v) for v in identities):
        pool=api._resident_pool()
        if not pool._exit_proven(path/'session'):
            return _projection(path,spec,state,'OWNED_EXIT_UNPROVEN',retry=True)
        try:
            old=pool.for_operation(spec['job_id'],spec['attempt_id'],path/'session')
            pin=strict_load(path/'hybrid-reservation.json')
            require(old['token']==pin['token'],'HYBRID_RESERVATION_TOKEN_CONFLICT')
            pool.close(old['token'],outcome='LOST')
        except GMError as error:
            if error.code!='RESIDENT_ADMISSION_STALE':raise
        atomic_json(path/'hybrid-release.json',dict(state='LOST',reason='OWNER_HOST_EXITED'))
        return _projection(path,spec,state,'OWNER_HOST_EXITED')
    pool, row, ss, who, live = _context(store, path, spec)
    if not all(live):
        if not any(live):
            pool.close(row['token'], outcome='LOST')
            return _projection(path, spec, state, 'OWNER_HOST_EXITED')
        # The owning process may still be finishing its exact host Job. Keep
        # the reservation occupied until all identities have confirmed exit.
        return _projection(path, spec, state, 'OWNED_EXIT_PENDING', retry=True)
    require(row['state']=='BOUND', 'HYBRID_RESERVATION_NOT_BOUND')
    actual = path/'session/lease.json'
    if actual.exists():
        from ..lease import validate_lease_handle
        current = strict_load(actual)
        try: validate_lease_handle(current, binding=api._binding(lease))
        except GMError as error:
            if error.code=='RESULT_NOT_RESIDENT':
                return _projection(path, spec, state, 'LEASE_CLOSED_OR_EXPIRED')
            raise
    internal = dict(command='HYBRID_ACTIVATE', backend_id=BACKEND_ID,
                    execution_contract_sha256=spec['semantics']['execution_contract_sha256'],
                    job_id=spec['job_id'], attempt_id=spec['attempt_id'])
    try:
        activation = api._call(ss, internal, deadline=time.monotonic()+10)['result']
    except (GMError, OSError, EOFError, ValueError) as error:
        # Recheck identities before cleanup; unknown or reused PID propagates
        # its explicit conflict and does not authorize termination or FREE.
        live = [process_state(v) for v in who]
        if all(live): _cleanup(path, pool, row, who, live)
        elif not any(live): pool.close(row['token'], outcome='LOST')
        return _projection(path, spec, state, 'HOST_UNREACHABLE:'+getattr(error,'code',type(error).__name__))
    from ..lease import validate_lease_handle
    validate_lease_handle(activation['lease'], binding=api._binding(lease))
    ready=strict_load(path/'executor-result.json')
    descriptor=dict(name=ready['shared_memory'],shape=ready['shape'],dtype='<f4',readonly=True,
                    job_id=spec['job_id'],attempt_id=spec['attempt_id'],owner=ready['owner'])
    require(activation.get('copied_Y') is False and activation['shared_Y']==descriptor,
            'HYBRID_RECOVERY_Y_CONFLICT')
    # Check immutable record again after IPC, before the admission CAS.
    require(store.path(spec['job_id'],spec['attempt_id'])==(path,spec), 'HYBRID_RECOVERY_RECORD_CHANGED')
    pool.recover_lease(row['token'], spec['job_id'], spec['attempt_id'], store.root.parent,
                       path/'session', ss['session_id'], who[0], who[1], activation['lease'])
    atomic_json(path/'shared-adoption.json', activation)
    atomic_json(path/'hybrid-recovery-receipt.json', dict(job_id=spec['job_id'], attempt_id=spec['attempt_id'],
        token=row['token'], lease=activation['lease'], shared_Y=activation['shared_Y'], copied_Y=False))
    return _projection(path, spec, state, 'RESIDENT', activation=activation)


def _release_context(store,path,spec,state,deadline):
    from .. import gm004a_api as api
    from ..gm004a_resident import _same_identity
    session=path/'session'
    for name in('hybrid-reservation.json','session/session-spec.json','session/owner.json','session/host-process.json','session/host-entry.json'):
        reject_reparse(path/name)
    pin=strict_load(path/'hybrid-reservation.json');ss=strict_load(session/'session-spec.json')
    require(all(pin.get(k)==spec[k] for k in('job_id','attempt_id')),'HYBRID_RELEASE_PIN_CONFLICT')
    pool=api._resident_pool();row=pool.release_record(pin['token'],deadline=deadline)
    context=ss.get('hybrid_execution',{})
    require(row['token']==pin['token'] and all(row.get(k)==spec[k] for k in('job_id','attempt_id')) and
        Path(row['store_root'])==store.root.parent and Path(row['session_path'])==session and
        row.get('session_id')==ss.get('session_id'),'HYBRID_RELEASE_RESERVATION_CONFLICT')
    require(ss.get('backend_id')==BACKEND_ID and all(context.get(k)==spec[k] for k in('job_id','attempt_id')) and
        Path(context.get('operation_path',''))==path and
        context.get('plan',{}).get('execution_contract_sha256')==spec['semantics']['execution_contract_sha256'],
        'HYBRID_RELEASE_SESSION_CONFLICT')
    who=[strict_load(session/name) for name in('owner.json','host-process.json','host-entry.json')]
    require(all(_same_identity(row[k],v) for k,v in zip(('owner','host'),who)) and
        _same_identity(who[2],strict_load(path/'executor-result.json')['owner']),'HYBRID_RELEASE_PROCESS_CONFLICT')
    lease=state['result_lease'];versions(lease)
    require(row.get('lease_handle_id') in(None,lease['handle_id']),'HYBRID_RELEASE_LEASE_CONFLICT')
    expected=dict(schema='gm021-release-intent-v1',job_id=spec['job_id'],attempt_id=spec['attempt_id'],
        intent_sha256=spec['intent_sha256'],backend_id=BACKEND_ID,
        execution_contract_sha256=spec['semantics']['execution_contract_sha256'],
        session_id=ss['session_id'],session_path=str(session),token=pin['token'],
        handle_id=lease['handle_id'],generation=lease['generation'],binding=api._binding(lease))
    return pool,row,ss,who,expected


def _release_projection(path,spec,state,closing):
    result=_projection(path,spec,state,closing['reason'],retry=closing.get('retryable',False))
    result['release']=closing
    return result


def _release_incomplete(reason):
    return dict(released=False,resident=False,release_state='RELEASE_PENDING',reason=reason,
        retryable=reason in('LOCK_BUSY','HYBRID_PROCESS_IDENTITY_UNKNOWN','HYBRID_RELEASE_IN_PROGRESS','HYBRID_RELEASE_EXIT_UNPROVEN'))


def _release_created(previous):
    """Validate saved times without substituting the observing request time."""
    import math
    created=previous.get('created_at')
    require(created is None or type(created) in(int,float) and math.isfinite(created) and created>0,
        'HYBRID_RELEASE_TIME_CONFLICT')
    if previous.get('schema')=='gm021-release-intent-v1' and previous.get('state') in('RELEASED','LOST'):
        completed=previous.get('completed_at')
        require(type(completed) in(int,float) and math.isfinite(completed) and completed>0 and
            (created is None or completed>=created),'HYBRID_RELEASE_TIME_CONFLICT')
    return created


def _release_terminal(previous,closed,expected):
    """Derive a terminal record solely from this original token's receipt."""
    import math
    created=_release_created(previous);outcome=closed.get('free_reason');freed=closed.get('freed_at')
    require(closed.get('state')=='FREE' and outcome in('RELEASED','LOST'),'HYBRID_RELEASE_RECEIPT_CONFLICT')
    require(type(freed) in(int,float) and math.isfinite(freed) and freed>0 and
        (created is None or freed>=created),'HYBRID_RELEASE_TIME_CONFLICT')
    terminal=previous.get('state') in('RELEASED','LOST')
    if terminal:require(previous['state']==outcome,'HYBRID_RELEASE_TERMINAL_CONFLICT')
    if terminal and previous.get('schema')==expected['schema']:return previous,False
    return dict(previous,**expected,state=outcome,reason=previous.get('reason',outcome),
        created_at=created,completed_at=freed),True


def _continue_release(store,path,spec,state,deadline,*,create=False):
    """One operation lock, one original token, one absolute public deadline."""
    from .. import gm004a_api as api
    require(time.monotonic()<deadline,'HYBRID_RELEASE_DEADLINE')
    pool,row,ss,who,expected=_release_context(store,path,spec,state,deadline)
    marker=path/'hybrid-release.json';reject_reparse(marker)
    previous=strict_load(marker) if marker.is_file() else None
    live=[process_state(identity) for identity in who]
    if previous is not None:
        if previous.get('schema')==expected['schema']:
            require(all(previous.get(k)==v for k,v in expected.items()) and
                previous.get('state') in('RELEASE_PENDING','RELEASED','LOST'),'HYBRID_RELEASE_INTENT_CONFLICT')
        else:
            # A legacy terminal record made by the unchanged healthy recovery
            # branch is only meaningful with full independent exit proof.
            require(previous.get('state')=='LOST' and not any(live) and pool._exit_proven(path/'session') and
                previous.get('token',row['token'])==row['token'],'HYBRID_RELEASE_INTENT_CONFLICT')
    else:
        require(create,'HYBRID_RELEASE_INTENT_MISSING')
        previous=dict(expected,state='RELEASE_PENDING',created_at=time.time())
        atomic_json(marker,previous)
    require(store.path(spec['job_id'],spec['attempt_id'])==(path,spec),'HYBRID_RELEASE_RECORD_CHANGED')
    _release_created(previous)
    if previous.get('state') in('RELEASED','LOST'):
        require(row['state']=='FREE','HYBRID_RELEASE_TERMINAL_CONFLICT')
    held=_handles(who,live);outcome='RELEASED'
    try:
        if any(live):
            require(row['state']=='BOUND','HYBRID_RELEASE_HISTORY_LIVE')
            # Reserve the last 15 seconds for exact owned cleanup. All nested
            # waits/IPC/admission locks use the remaining original deadline.
            normal_end=deadline-15
            try:
                if all(live):
                    api._call(ss,dict(command='RELEASE',**expected['binding']),deadline=min(normal_end,time.monotonic()+10))
                for handle,_ in held:
                    left=max(0,int((normal_end-time.monotonic())*1000))
                    if win.k.WaitForSingleObject(handle,left)!=win.WAIT_OBJECT_0:raise TimeoutError('release exit')
            except (GMError,OSError,EOFError,ValueError,TimeoutError):
                outcome='LOST'
                # Terminate only exact identities from the original reservation.
                # A retained query handle does not itself grant termination.
                for identity in who:
                    if process_state(identity):
                        require(time.monotonic()<deadline,'HYBRID_RELEASE_DEADLINE')
                        h=win.k.OpenProcess(win.PROCESS_TERMINATE|win.PROCESS_QUERY_LIMITED_INFORMATION|win.SYNCHRONIZE,False,identity['pid'])
                        require(h and process_state(identity),'HYBRID_RELEASE_TERMINATE_IDENTITY')
                        try:require(win.k.TerminateProcess(h,9),'HYBRID_RELEASE_TERMINATE_FAILED')
                        finally:win.close(h)
            _wait(path,held,deadline,'session/release-actual-waits.json')
        require(not any(process_state(identity) for identity in who),'HYBRID_RELEASE_EXIT_PENDING')
        if not pool._exit_proven(path/'session'):
            return _release_incomplete('HYBRID_RELEASE_EXIT_UNPROVEN')
        require(time.monotonic()<deadline,'HYBRID_RELEASE_DEADLINE')
        closed=row if row['state']=='FREE' else pool.close(row['token'],outcome=outcome,deadline=deadline)
        require(closed['state']=='FREE','HYBRID_RELEASE_NOT_FREE')
        completed,needs_write=_release_terminal(previous,closed,expected)
        if needs_write:atomic_json(marker,completed)
        return dict(released=True,resident=False,release_state=completed['state'],reason=completed.get('reason',completed['state']),retryable=False,
            token=row['token'],already_released=previous.get('state') in('RELEASED','LOST'))
    finally:
        for handle,_ in held:win.close(handle)


def recover(store,path,spec):
    deadline=time.monotonic()+45;state=None
    from ..resource_execution import binding as resource_binding
    plan_error=resource_binding.diagnostic(path,spec)
    def result(value):return resource_binding.project(value,plan_error)
    try:
        with locked(path/'hybrid-recovery.lock',timeout=min(5,max(0,deadline-time.monotonic()))):
            plan_error=resource_binding.diagnostic(path,spec)
            from . import monitor_completion as completion
            state=persistent(store,path,spec)
            if state['state']!='SUCCEEDED':
                state=resource_binding.unpublished(path,spec,state,plan_error)
            if completion.intercept(path,spec,state):
                return result(completion.coordinate_locked(store,path,spec,state,deadline))
            if (path/'hybrid-release.json').exists():
                state=persistent(store,path,spec)
                require(state['state']=='SUCCEEDED','RESULT_UNAVAILABLE')
                return result(_release_projection(path,spec,state,_continue_release(store,path,spec,state,deadline)))
            return result(_recover_coordinated(store,path,spec,deadline=deadline))
    except GMError as error:
        from . import monitor_completion as completion
        if state is None:state=persistent(store,path,spec)
        if completion.intercept(path,spec,state):
            projected=completion.project(path,spec,state,validated=True)
            projected['recovery']=dict(reason=error.code,retryable=error.code=='LOCK_BUSY',resident=False)
            return result(projected)
        closing=(path/'hybrid-release.json').exists()
        if closing:
            if state is None:state=persistent(store,path,spec)
            return result(_release_projection(path,spec,state,_release_incomplete(error.code)))
        if error.code in {'HYBRID_PROCESS_IDENTITY_UNKNOWN','HYBRID_PROCESS_IDENTITY_CONFLICT',
                          'HYBRID_RESERVATION_BINDING','HYBRID_RESERVATION_TOKEN_CONFLICT',
                          'HYBRID_SESSION_BINDING','HYBRID_RESERVATION_PROCESS_CONFLICT',
                          'RESIDENT_RECORD_INVALID','RESIDENT_ADMISSION_STALE','RESIDENT_RECOVERY_CONFLICT'}:
            state=persistent(store,path,spec)
            return result(_projection(path,spec,state,error.code,retry=error.code=='HYBRID_PROCESS_IDENTITY_UNKNOWN'))
        if error.code!='LOCK_BUSY':raise
        from ..gm004a_operations import _journal
        with locked(path/'record.lock',timeout=min(5,max(0,deadline-time.monotonic()))):state=dict(_journal(path)[-1]['snapshot'])
        return result(_projection(path,spec,state,'RECOVERY_IN_PROGRESS',retry=True))


def release(delegate,store_root,job,attempt,binding):
    from ..gm004a_operations import OperationStore
    from ..gm004a_api import _binding
    deadline=time.monotonic()+45;store=OperationStore(store_root);path,spec=store.path(job,attempt)
    try:
        with locked(path/'hybrid-recovery.lock',timeout=min(5,max(0,deadline-time.monotonic()))):
            state=persistent(store,path,spec)
            require(state['state']=='SUCCEEDED','RESULT_UNAVAILABLE');versions(binding)
            require(binding==_binding(state['result_lease']),'HYBRID_RELEASE_BINDING')
            return _continue_release(store,path,spec,state,deadline,create=True)
    except GMError as error:
        if error.code in('HYBRID_RELEASE_BINDING','HYBRID_VERSION_REQUIRED','HYBRID_IMPLEMENTATION_CHANGED'):raise
        return _release_incomplete(error.code)
