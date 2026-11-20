"""One record-lock coordinator for portable startup, recovery and reclamation.

Lock order: operation record.lock -> admission.lock. Host/owner never acquire
either lock. No release.lock is used, and cleanup never calls public status.
"""
import os
import time
from pathlib import Path
from ..common import GMError, atomic_json, strict_load
from ..store import locked
from .. import winproc as win
from .contract import ROOT, need, verify, binding, executor_closure

E = ROOT / 'docs/evidence/gm-052-r1/20261030-v1'
W = ROOT / 'tests/workspaces/g52r1/20261030-v1'
CONTEXT_FIELDS = {'schema', 'TEST_ONLY', 'id', 'mode', 'executor_build', 'output_root',
                  'evidence_root', 'pool_root', 'frame_shape', 'lease_seconds',
                  'request_seconds', 'prepared_clock_shift', 'fault_point',
                  'fault_action', 'pause_at', 'force_grace_seconds'}


def test_context(item, *, allow_stale=False):
    """A frozen file and index, not an environment switch or client payload."""
    verify(item)
    path = Path(item['path']).resolve()
    need(path.parent == E / 'contexts', 'TEST_CONTEXT_POINTER')
    index = strict_load(E / 'contexts/index.json')
    c = strict_load(path)
    need(set(c) == CONTEXT_FIELDS and c['schema'] == 'gm052-r1-instance-v1'
         and c['TEST_ONLY'] is True and index.get(c['id']) == item, 'TEST_CONTEXT_FIELDS')
    need(c['mode'] in ('fast', 'fine') and c['frame_shape'] == [33, 47, 3], 'TEST_FRAME_SCOPE')
    need(Path(c['output_root']).resolve() == W / c['id'] and
         Path(c['evidence_root']).resolve() == E and
         Path(c['pool_root']).resolve().is_relative_to(W), 'TEST_CONTEXT_SCOPE')
    need(type(c['lease_seconds']) in (int, float) and 0 < c['lease_seconds'] <= 120
         and 0 < c['request_seconds'] <= 120 and c['prepared_clock_shift'] in (0, 7000)
         and c['force_grace_seconds'] in (1, 30), 'TEST_CLOCK_SCOPE')
    need(c['fault_action'] in ('raise', 'exit') and c['fault_point'] in
         ('', 'before_startup', 'after_claim', 'after_claim_diagnostic', 'after_mkdir', 'after_spec', 'before_owner', 'owner_after_ready',
          'owner_created', 'prepared', 'before_commit', 'after_commit',
          'before_success', 'release_pending', 'cleanup_once'), 'TEST_FAULT_SCOPE')
    need(c['pause_at'] in ('', 'before_ready', 'before_compute', 'prepared'), 'TEST_PAUSE_SCOPE')
    need(allow_stale or c['executor_build'] == executor_closure()['sha256'], 'TEST_CONTEXT_BUILD')
    return c


def context(spec, *, cleanup=False):
    sem = spec.get('semantics', spec)
    if 'preview_version' in sem:
        from ..preview.contract import context as preview_context
        return preview_context(spec, cleanup=cleanup)
    if sem.get('instance_context'):
        from ..cpu_first_fit.contract import context as instance_context
        return instance_context(spec, cleanup=cleanup)
    if not sem.get('TEST_ONLY'):
        return None
    c = test_context(sem['test_context'], allow_stale=cleanup)
    need(sem['mode'] == c['mode'] and (cleanup or sem['executor_build'] == c['executor_build']) and
         Path(sem['output_root']).resolve() == Path(c['output_root']) and
         Path(sem['test_pool']).resolve() == Path(c['pool_root']), 'TEST_INSTANCE_MISMATCH')
    return c


def selected_context():
    from ..preview.contract import selected as preview_selected
    preview = preview_selected()
    if preview is not None:
        return preview
    from ..cpu_first_fit.contract import selected
    current = selected()
    if current is not None:
        return current
    value = os.environ.get('GM052_R1_CONTEXT')
    if not value:
        return None
    return test_context(binding(value))


def checkpoint(path, name):
    path = Path(path)
    c = context(strict_load(path / 'spec.json'), cleanup=True)
    if c and (c['fault_point'] == name or (name == 'after_claim' and c['fault_point'] == 'after_claim_diagnostic')):
        if (path / ('checkpoint-' + name + '.json')).is_file():
            return
        atomic_json(path / ('checkpoint-' + name + '.json'), dict(point=name, identity=win.current_identity()))
        if c['fault_point'] == 'after_claim_diagnostic':
            (path / 'error.json').mkdir()
        if c['fault_action'] == 'exit':
            os._exit(87)
        raise OSError('TEST_ONLY_' + name)


def stopping(session, spec):
    from ..adaptive_execution.residency import context as idle_context,stopped
    session = Path(session)
    if 'preview_version' in spec['semantics']:
        from ..preview.contract import context as preview_context, selected
        c = selected()
        if c is None or c['client'] != spec['semantics']['preview_client']:
            c = preview_context(spec, cleanup=True)
        if win.identity_alive(c['client']) is not True:
            return 'PREVIEW_CLIENT_EXITED'
        if c.get('light_plan'):
            return stopped(session,spec)
    elif idle_context(spec) is not None:
        return stopped(session,spec)
    if (session / 'stop.signal').exists() or (Path(spec['operation_path']) / 'cancel.signal').exists():
        return 'STOP_REQUESTED'
    p = session / 'lease.json'
    if p.is_file():
        lease = strict_load(p)
        if lease['state'] in ('PREPARED', 'RESIDENT', 'RELEASE_PENDING') and time.time() >= lease['expires_at']:
            return 'LEASE_EXPIRED'
    return None


def pause(session, spec, point):
    c = context(spec)
    if c and c['pause_at'] == point:
        session = Path(session)
        atomic_json(session / ('barrier-' + point + '.json'), dict(identity=win.current_identity()))
        until = time.monotonic() + 120
        while not (session / ('continue-' + point + '.signal')).exists():
            need(not stopping(session, spec), 'CANCELLED', exit_code=8)
            need(time.monotonic() < until, 'TEST_BARRIER_TIMEOUT')
            time.sleep(.02)


def diagnostic(path, error):
    from .runtime import error as record
    try:
        record(path, error)
    except BaseException as secondary:
        from .runtime import stderr_error
        error.add_note('Diagnostic: ' + repr(secondary))
        stderr_error(error)


def _read(path):
    return strict_load(path) if Path(path).is_file() else None


def _retain(who):
    need(win.identity_key(who) is not None, 'CLEANUP_IDENTITY_UNKNOWN')
    live = win.identity_alive(who)
    need(live is not None, 'CLEANUP_IDENTITY_UNKNOWN')
    if not live:
        return None
    h = win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION | win.SYNCHRONIZE, False, who['pid'])
    need(h and win.identity_key(win.process_identity(who['pid'])) == win.identity_key(who), 'CLEANUP_IDENTITY_CHANGED')
    return h


def reclaim(path, spec):
    """Called only with record.lock held; observed failures remain pending."""
    from .pool import pool
    path = Path(path)
    session = path / 'session'
    p = pool(spec)
    startup = _read(path / 'startup.json') or {}
    row = p.for_exact_operation(spec, session, startup.get('pool_token'))
    if row is None:
        need(startup.get('phase') == 'NOT_STARTED' or not session.exists(), 'STARTUP_OWNERSHIP_UNKNOWN')
        return dict(complete=True, no_reservation_created=True)
    token = row['token']
    proof_path = path / 'reclaim-receipt.json'
    prior = _read(proof_path)
    if prior:
        free = p.finalize(spec, session, token, prior)
        _lease_released(path, session, token)
        return dict(complete=True, reservation=free, proof=binding(proof_path))
    if 'preview_version' not in spec['semantics'] and spec['semantics'].get('instance_context') and not spec['semantics'].get('cpu_refine') and not spec['semantics'].get('adaptive_application') and not (session/'owner-cleanup.json').exists():
        from ..cpu_first_fit.outer_exit import adopted_proof
        adopted=adopted_proof(path,spec,token)
        if adopted is not None:
            atomic_json(proof_path,adopted)
            free=p.finalize(spec,session,token,adopted)
            _lease_released(path,session,token)
            return dict(complete=True,reservation=free,proof=binding(proof_path),proof_source=adopted['proof_source'])
    try:
        checkpoint(path, 'cleanup_once') if not (path / 'cleanup-fault-observed.json').exists() else None
    except BaseException as e:
        atomic_json(path / 'cleanup-fault-observed.json', dict(error=repr(e)))
        raise
    session.mkdir(exist_ok=True)
    atomic_json(session / 'stop.signal', dict(reason='PUBLIC_RECONCILE', token=token))
    launched = _read(session / 'owner-launch.json')
    no_child = startup.get('phase') == 'NOT_STARTED' and not launched and not (session / 'owner.json').exists()
    if no_child:
        receipt = dict(no_child_created=True, actual_wait=False, host_exited=True,
                       claimant=spec['startup_owner'], operation=[spec['job_id'], spec['attempt_id']], token=token)
        atomic_json(session / 'startup-cleanup.json', receipt)
        proof = dict(token=token, job_id=spec['job_id'], attempt_id=spec['attempt_id'],
                     session_path=str(session), no_child_started=True, actual_wait=False,
                     identities=[], tree_empty=True, mappings=[])
    else:
        need(launched and launched['session_id'] == strict_load(session / 'session-spec.json')['session_id']
             and launched['pool_token'] == token, 'OWNER_LAUNCH_UNKNOWN')
        owner = launched['identity']
        held = _retain(owner)
        try:
            if held:
                # Owner still holds the host Job and implements the 30s grace.
                if win.k.WaitForSingleObject(held, 35000) != win.WAIT_OBJECT_0:
                    return dict(complete=False, coordinator='NEXT_PUBLIC_STATUS_OR_RELEASE', reason='OWNER_EXIT_PENDING')
            cleanup = _read(session / 'owner-cleanup.json')
            need(cleanup and cleanup['owned_tree_empty'] is True and (cleanup['actual_wait'] is True or cleanup.get('host_never_started') is True)
                 and win.identity_key(cleanup['owner']) == win.identity_key(owner), 'OWNER_CLEANUP_UNKNOWN')
            rows = [dict(identity=owner, actual_handle_wait=True if held else None,
                         exited=win.identity_alive(owner) is False, proof='retained_handle' if held else 'owner_job_cleanup_and_exact_exited_identity')]
            if not cleanup.get('host_never_started'):
                need(cleanup['actual_host_handle_wait'] is True, 'HOST_ACTUAL_WAIT_UNKNOWN')
                for key in ('host', 'actual_host_identity'):
                    who = cleanup[key]
                    need(win.identity_key(who) is not None and win.identity_alive(who) is False, 'HOST_EXIT_UNKNOWN')
                    rows.append(dict(identity=who, exited=True, actual_handle_wait=True, proof='owner_retained_Job_and_actual_handle'))
            mappings = []
            m = _read(session / 'shared-mappings.json') or {}
            names = {v['name'] for v in m.values() if isinstance(v, dict) and 'name' in v}
            from ..adaptive_execution.portable_contract import receiver_directory
            for result in receiver_directory(session).rglob('executor-result.json'):
                names.add(strict_load(result)['shared_memory'])
            from multiprocessing.shared_memory import SharedMemory
            for name in sorted(names):
                try:
                    sm = SharedMemory(name=name, create=False)
                except FileNotFoundError:
                    mappings.append(dict(name=name, cannot_reopen=True))
                else:
                    sm.close()
                    raise ValueError('CLEANUP_MAPPING_STILL_OPEN')
            proof = dict(token=token, job_id=spec['job_id'], attempt_id=spec['attempt_id'],
                         session_path=str(session), no_child_started=False, actual_wait=True,
                         identities=rows, tree_empty=True, mappings=mappings,
                         owner_cleanup=binding(session / 'owner-cleanup.json'), forced=cleanup['forced'],
                         host_normal_tail=cleanup.get('host_normal_tail', False))
        finally:
            if held:
                win.close(held)
    atomic_json(proof_path, proof)
    checkpoint(path, 'proof_written')
    free = p.finalize(spec, session, token, proof)
    _lease_released(path, session, token)
    return dict(complete=True, reservation=free, proof=binding(proof_path))


def _lease_released(path, session, token):
    # Both first and resumed proof paths converge after exact pool.finalize.
    checkpoint(path, 'pool_freed')
    lease = _read(session / 'lease.json')
    if lease:
        need(lease['binding']['pool_token'] == token, 'LEASE_TOKEN_MISMATCH')
        from ..adaptive_execution.residency import context as idle_context,lock
        spec=strict_load(path/'spec.json')
        if idle_context(spec) is not None:
            with lock(session):
                lease=strict_load(session/'lease.json')
                need(lease['binding']['pool_token']==token,'LEASE_TOKEN_MISMATCH')
                atomic_json(session/'lease.json',dict(lease,state='RELEASED'))
        else:
            atomic_json(session / 'lease.json', dict(lease, state='RELEASED'))


def reconcile(ops, path, spec, *, release_binding=None, cancel=False, failure=None):
    from ..gm004a_operations import _append, _journal
    from .publication import verify_commit
    from .api import call
    path = Path(path)
    context(spec, cleanup=True)  # Stale credentials can never activate, but proven ownership can be reclaimed.
    with locked(path / 'record.lock', timeout=120):
        current = dict(_journal(path)[-1]['snapshot'])
        parse_error = None
        try:
            mark = _read(path / 'commit.json')
        except BaseException as e:
            mark = None
            parse_error = getattr(e, 'code', repr(e))
        session = path / 'session'
        if spec['semantics'].get('preview_version') and spec['semantics'].get('cpu_refine'):
            from ..solver_lifecycle import recover_saved_settlement
            try:
                recover_saved_settlement(session/'P3/solver')
            except (OSError, ValueError, KeyError, GMError) as error:
                diagnostic(path,error)
        valid, invalid = False, parse_error
        if mark:
            try:
                verify_commit(mark, spec, path)
                valid = True
            except BaseException as e:
                invalid = getattr(e, 'code', str(e))
        if release_binding is not None:
            need(valid and mark['result_lease'] == release_binding, 'CPU_RESULT_BINDING_STALE', exit_code=4)
        if cancel and valid:
            return dict(current, cancel_disposition='ALREADY_COMMITTED')
        if cancel:
            atomic_json(path / 'cancel.signal', dict(job_id=spec['job_id'], attempt_id=spec['attempt_id']))
        cancelled = (path / 'cancel.signal').exists()
        caller = win.identity_alive(spec['startup_owner'])
        lease = _read(session / 'lease.json')
        if 'preview_version' in spec['semantics']:
            from ..preview.contract import package
            credential = spec['semantics']['executor_build'] == package()['build']['sha256']
        else:
            credential = spec['semantics']['executor_build'] == executor_closure()['sha256']
        live = []
        for name in ('owner.json', 'host-entry.json'):
            who = _read(session / name)
            live.append(win.identity_alive(who) if win.identity_key(who) else None)
        expired = bool(lease and time.time() >= lease['expires_at'])
        release_requested = release_binding is not None or (lease and lease['state'] in ('RELEASE_PENDING', 'RELEASED'))
        cleanup_needed = (failure is not None or cancelled or invalid or release_requested or
                          (not valid and (caller is False or current['state'] in ('FAILED', 'CANCELLED'))) or
                          (valid and (not credential or expired or any(v is not True for v in live))))
        from ..adaptive_execution.residency import context as idle_context,decision
        versioned_idle=idle_context(spec) is not None
        if versioned_idle:
            forced=(failure is not None or cancelled or invalid or
                    (not valid and (caller is False or current['state'] in ('FAILED','CANCELLED'))) or
                    (valid and (not credential or any(v is not True for v in live))))
            if spec['semantics'].get('preview_version'):
                forced=forced or win.identity_alive(spec['semantics']['preview_client']) is not True
            lease,cleanup_needed=decision(session,forced,release_binding is not None)
        cleanup = None
        if cleanup_needed:
            if lease and lease['state'] != 'RELEASED' and not versioned_idle:
                atomic_json(session / 'lease.json', dict(lease, state='RELEASE_PENDING'))
            if release_binding is not None:
                checkpoint(path, 'release_pending')
            try:
                cleanup = reclaim(path, spec)
            except BaseException as e:
                diagnostic(path, e)
                cleanup = dict(complete=False, reason=getattr(e, 'code', repr(e)), coordinator='NEXT_PUBLIC_STATUS_OR_RELEASE')
            lease = _read(session / 'lease.json')
        resident = False
        if valid and not cleanup_needed and credential and lease and lease['state'] in ('PREPARED', 'RESIDENT') and all(v is True for v in live):
            reply = call(path, 'ACTIVATE', mark['result_lease'])
            need(reply.get('binding') == mark['result_lease'], 'CPU_RECOVERY_ACTIVATION_BINDING')
            resident = True
            lease = strict_load(session / 'lease.json')
        target = 'SUCCEEDED' if valid else 'CANCELLED' if cancelled else 'FAILED' if cleanup_needed else current['state']
        if target != current['state'] or (cleanup and not current.get('cleanup_requested')):
            current.update(state=target, stage='cleanup' if cleanup_needed else 'recovered',
                           exit_code=0 if valid else 8 if cancelled else 9 if cleanup_needed else None,
                           error_code=None if valid else invalid or ('INTERRUPTED' if cleanup_needed else None),
                           cleanup_requested=bool(cleanup_needed))
            _append(path, current, 'CPU_PORTABLE_RECONCILED')
        pending = bool(cleanup and not cleanup['complete'])
        if spec['semantics'].get('instance_context'):
            if spec['semantics'].get('adaptive_application') is True:
                from ..adaptive_execution.publication import recipe_status
            elif spec['semantics'].get('cpu_refine'):
                from ..cpu_refine.publication import recipe_status
            else:
                from ..cpu_first_fit.publication import recipe_status
            current.update(recipe_status(spec))
        return dict(current, resident=resident, binding=mark['result_lease'] if resident else None,
                    persistent_complete=valid, availability='CLEANUP_PENDING' if pending else 'RESIDENT' if resident else
                    'REPLAY_REQUIRED' if valid else 'UNKNOWN' if caller is None else 'NOT_COMMITTED',
                    result_lease=lease, cleanup=cleanup, released=bool(cleanup and cleanup['complete']),
                    execution_credential='CURRENT' if credential else 'STALE_BUILD',
                    cancel_disposition='REQUESTED' if cancel else None, recovered_without_science=True)
