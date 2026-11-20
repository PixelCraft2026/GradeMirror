"""Explicit portable replay and durable/resident API; no numerical imports."""
import os
import time
import uuid
from pathlib import Path

from ..common import atomic_json, strict_load, GMError, writable_destination
from ..store import locked
from ..winproc import current_identity, identity_alive, identity_key
from ..gm004a_operations import OperationStore, artifact
from ..gm003_ipc import bounded_call, send_json, recv_json
from .contract import BACKEND, authority, check_bundle, output, digest, binding as file_binding, need, resource_admission
from . import runtime
from .pool import pool
from .contract import cpu_error_boundary


def call(path, command, binding=None, *, deadline_monotonic=None, trace=None, **kwargs):
    session = Path(path) / 'session'
    ss = strict_load(session / 'session-spec.json')
    need(ss['backend_id'] == BACKEND, 'CPU_SESSION_BACKEND')
    if command not in {'ACTIVATE', 'RELEASE'}:
        need(not (Path(path) / 'cancel.signal').exists(), 'CANCELLED', exit_code=8)
    application_v2=(ss.get('execution') or {}).get('schema')=='cpu-application-execution-v2'
    deadline = time.monotonic() + (1200 if command == 'EXPORT' and not application_v2 else 120)
    if deadline_monotonic is not None:
        deadline = min(deadline, deadline_monotonic)
        need(time.monotonic() < deadline, 'CPU_QUALIFY_BATCH_DEADLINE')
    if trace is not None:
        trace.update(deadline=deadline,call_started=time.monotonic())
    def exchange(conn):
        if trace is not None:trace['send_started']=time.monotonic()
        send_json(conn, dict(command=command, binding=binding, **kwargs))
        if trace is not None:trace['send_completed']=time.monotonic()
        reply = recv_json(conn)
        if trace is not None:trace['received']=time.monotonic()
        if reply.get('error'):
            raise GMError(reply['error'], reply.get('message', reply['error']), reply.get('exit_code', 7))
        return reply
    return bounded_call(ss['pipe_address'], ss['auth_hex'], exchange, deadline=deadline)


@cpu_error_boundary
def replay_cpu_recipe(bundle_directory, source, store_root, output_root, *, request_key,
                      execution_backend=None, allow_experimental=False, execution_manifest=None,
                      cpu_core_rows=None):
    from ..cpu_recipe import verify_cpu_recipe
    info = verify_cpu_recipe(bundle_directory)
    if info['version'] == 3 and info['mode'] == 'fine':
        from ..cpu_refine.api import replay_fine
        return replay_fine(bundle_directory,source,store_root,output_root,request_key=request_key,
            execution_backend=execution_backend,allow_experimental=allow_experimental,
            execution_manifest=execution_manifest,cpu_core_rows=cpu_core_rows)
    if info['mode'] == 'fast' and info['version'] in (2,3):
        from ..cpu_first_fit.api import replay_generated
        return replay_generated(bundle_directory, source, store_root, output_root, request_key=request_key,
            execution_backend=execution_backend, allow_experimental=allow_experimental,
            execution_manifest=execution_manifest, cpu_core_rows=cpu_core_rows)
    from .lifecycle import selected_context
    work = Path((selected_context() or authority())['evidence_root']) / 'clients'
    runtime.enter('client', work)
    bundle, manifest = check_bundle(bundle_directory, source, execution_manifest,
                                    execution_backend, allow_experimental, cpu_core_rows)
    sem = dict(backend_id=BACKEND, mode=bundle['mode'], bundle_id=bundle['bundle_id'],
               math_id=bundle['math_id'], executor_build=manifest['executor_build']['sha256'],
               bundle_directory=str(Path(bundle_directory).resolve()), source_path=str(Path(source).resolve()),
               source_sha256=manifest['source']['sha256'], output_root=str(output(output_root)),
               execution_manifest=file_binding(execution_manifest), core_rows=manifest['core_rows'])
    return _start(store_root, sem, request_key, manifest)


def _start(store_root, sem, request_key, manifest):
    """All resources created after begin share the same recovery coordinator."""
    from . import lifecycle as life
    test = life.context(sem)
    from ..adaptive_execution.c2_cpu_tail import enabled as c2_enabled
    new_replay = c2_enabled(test)
    prepared = None
    if test and test.get('schema') == 'cpu-application-execution-v4':
        from ..adaptive_execution.t34r3_c1_route import RESUME_QUALIFICATIONS
        if Path(test['qualification']['path']).name in RESUME_QUALIFICATIONS:
            from ..adaptive_execution.portable_science import prepare_resumed_start
            prepared = prepare_resumed_start(test)
    ops = OperationStore(output(store_root))
    if new_replay and ops.lookup_request(request_key,saved_only=True) is None:
        from ..adaptive_execution.portable_science import prepare_c2_start
        prepared = prepare_c2_start(test,request_key)
    path, spec, state = ops.begin('fit' if sem.get('first_fit') else 'replay', sem, request_key)
    if state['idempotent_hit']:
        return dict(ops.status(spec['job_id'], spec['attempt_id']), idempotent_hit=True)
    process = None
    session = path / 'session'
    try:
        if sem.get('adaptive_application') and sem.get('adaptive_parent_operation'):
            parent=sem['adaptive_parent_operation'];parent_path,parent_spec=ops.path(parent['job_id'],parent['attempt_id'])
            need(parent_spec['semantics'].get('adaptive_parent') is True,'APPLICATION_CHILD_PARENT')
            with locked(parent_path/'record.lock'):
                if (parent_path/'cancel.signal').is_file():
                    atomic_json(path/'cancel.signal',dict(job_id=spec['job_id'],attempt_id=spec['attempt_id'],reason='PARENT_CANCELLED_BEFORE_CHILD_ACCEPTANCE'))
        if not (test and test.get('schema')=='cpu-application-execution-v4'):
            life.checkpoint(path, 'before_startup')
        else:need(test['fault_point']=='' and test['pause_at']=='','FIRST_NATIVE_NO_STARTUP_FAULT')
        atomic_json(path / 'startup.json', dict(phase='NOT_STARTED', claimant=current_identity()))
        if 'preview_version' in sem:
            from ..preview.contract import admission
            resource = admission(test)
            from ..light.runtime import operation,science_boundary
            handoff=science_boundary()
            if handoff is not None and sem.get("first_fit"):
                need(operation().get("validation_evidence") is True,"LIGHT_FIRST_HANDOFF_SCOPE")
                ops.transition(spec['job_id'],spec['attempt_id'],'RUNNING','pre_science_resource_wait',
                               'CPU_PRE_SCIENCE_READY')
                resource=handoff(test,path,admission)
        elif sem.get('instance_context'):
            from ..cpu_first_fit.api import admission
            resource = admission(test)
        else:
            resource = resource_admission(not sem.get('TEST_ONLY'))
        atomic_json(path / 'admission.json', resource)
        from .science import reserve
        if prepared is not None:
            from ..adaptive_execution.portable_science import commit_c2_start,commit_resumed_start
            running = ops.transition(spec['job_id'], spec['attempt_id'], 'RUNNING', 'validate_recipe', 'CPU_PORTABLE_STARTED')
            (commit_c2_start if new_replay else commit_resumed_start)(path,spec,test,prepared,running)
        else:
            if test and test.get('schema')=='cpu-application-execution-v4':
                from ..adaptive_execution.first_science import reserve_validated
                reserve_validated(path,spec,test)
            else:reserve(path,spec)
            ops.transition(spec['job_id'], spec['attempt_id'], 'RUNNING', 'validate_recipe', 'CPU_PORTABLE_STARTED')
        with locked(path / 'record.lock', timeout=120):
            if 'preview_version' in sem and (Path(test['workspace']) / 'cancel.signal').exists():
                atomic_json(path / 'cancel.signal', dict(reason='PREVIEW_CANCEL_BEFORE_OWNER'))
            need(not (path / 'cancel.signal').exists(), 'CANCELLED', exit_code=8)
            p = pool(spec)
            budget = resource.get('envelope') if 'preview_version' in sem else None
            reservation = p.claim(spec['job_id'], spec['attempt_id'], Path(store_root), session, budget_bytes=budget)
            atomic_json(path / 'startup.json', dict(phase='NOT_STARTED', pool_token=reservation['token'], claimant=current_identity()))
            life.checkpoint(path, 'after_claim')
            session.mkdir()
            life.checkpoint(path, 'after_mkdir')
            ss = dict(backend_id=BACKEND, operation_path=str(path), session_id=str(uuid.uuid4()),
                      job_id=spec['job_id'], attempt_id=spec['attempt_id'], semantics=sem,
                      resource_budget=reservation['budget_bytes'], created_at=time.time(), operation_seconds=6000,
                      pool_token=reservation['token'], pool_root=str(p.root), auth_hex=os.urandom(32).hex(),
                      pipe_address=None, execution=manifest)
            atomic_json(session / 'session-spec.json', ss)
            life.checkpoint(path, 'after_spec')
            life.checkpoint(path, 'before_owner')
            atomic_json(path / 'startup.json', dict(phase='CREATING', pool_token=reservation['token'], claimant=current_identity()))
            process = runtime.start_owner(session / 'session-spec.json', session)
            atomic_json(path / 'startup.json', dict(phase='CREATED', pool_token=reservation['token'], claimant=current_identity()))
            deadline = time.monotonic() + (7200 if sem.get('first_fit') or sem.get('refine_prepare') else 1800 if sem['mode'] == 'fast' else 6000)
        if test and test.get('schema')=='cpu-application-execution-v4':
            from ..adaptive_execution.first_contract import read
            limits=read(test['qualification']['path'])['limits']
            deadline=time.monotonic()+limits['fast_compute' if sem['mode']=='fast' else 'fine_compute']
        bound = False
        while time.monotonic() < deadline:
            need(not (path / 'cancel.signal').exists(), 'CANCELLED', exit_code=8)
            for root in (session, session / 'host'):
                if (root / 'error.json').is_file():
                    raise GMError('CPU_SESSION_FAILED', str(strict_load(root / 'error.json')), 9)
            if not bound and (session / 'host-ready.json').is_file():
                p.bind(reservation['token'], ss['session_id'], strict_load(session / 'owner.json'), strict_load(session / 'host-process.json'))
                atomic_json(session / 'compute-go.json', dict(bound=True))
                bound = True
            if (session / 'prepared.json').is_file():
                prepared = strict_load(session / 'prepared.json')
                p.bind_lease(reservation['token'], dict(prepared['lease'], state='RESIDENT'))
                life.pause(session, ss, 'prepared')
                life.checkpoint(path, 'prepared')
                publish_started = time.perf_counter()
                ops.commit(spec['job_id'], spec['attempt_id'], [artifact(path / n) for n in
                           ('compute-result.json', 'compute-cleanup.json', 'replay-identity.json')], prepared['binding'])
                if 'preview_version' in sem:
                    from ..light.eta import owner_timing
                    owner_timing(path,publish_started)
                return dict(ops.status(spec['job_id'], spec['attempt_id']), idempotent_hit=False)
            need(process.poll() is None, 'CPU_OWNER_EXITED_BEFORE_READY')
            time.sleep(.02)
        raise GMError('CPU_REPLAY_DEADLINE', 'Portable replay deadline', 9)
    except BaseException as exc:
        # Diagnostics and cleanup are independent: failed logging cannot skip it.
        life.diagnostic(path, exc)
        try:
            state=life.reconcile(ops, path, spec, failure=exc)
            if 'preview_version' in sem:
                from ..light.resource_retry import cpu_stopped
                cpu_stopped(exc,path,spec,process,state)
        except BaseException as cleanup:
            exc.add_note('Session cleanup: ' + repr(cleanup))
            runtime.stderr_error(exc)
        raise
    finally:
        if process is not None and process.poll() is not None:
            process.wait(timeout=30)

@cpu_error_boundary
def result_status(store, job_id, attempt_id):
    ops = OperationStore(store)
    state = ops.status(job_id, attempt_id)
    return dict(operation=state, resident=state.get('resident', False), binding=state.get('binding'),
                result_lease=state.get('result_lease'), availability=state.get('availability'))


def check_binding(path, spec, supplied, *, portable_native=False):
    need(type(supplied) is dict, 'CPU_RESULT_BINDING_REQUIRED')
    mark = strict_load(path / 'commit.json')
    from .publication import verify_commit
    verify_commit(mark, spec, path)
    need(supplied == mark['result_lease'], 'CPU_RESULT_BINDING_STALE', exit_code=4)
    from .contract import require_current_executor
    executor = spec['semantics']['executor_build']
    if 'preview_version' in spec['semantics']:
        from ..preview.contract import context
        context(spec)
    elif portable_native:
        from ..adaptive_execution.build import current_closure
        need(executor == current_closure(native=True)['sha256'], 'CPU_EXECUTION_CREDENTIAL_STALE')
    else:
        require_current_executor(executor)
    if not spec['semantics'].get('TEST_ONLY'):
        actual = file_binding(spec['semantics']['source_path'])
        expected = (strict_load(Path(spec['semantics']['instance_context']['path']))['source']
                    if spec['semantics'].get('instance_context') else authority()['source'])
        need(actual['sha256'] == spec['semantics']['source_sha256'] and
             actual['bytes'] == expected['bytes'], 'CPU_REPLAY_SOURCE_CHANGED')
    session = path / 'session'
    for name in ('owner.json', 'host-entry.json'):
        need(identity_alive(strict_load(session / name)) is True, 'RESULT_NOT_RESIDENT', exit_code=4)
    current = strict_load(session / 'lease.json')
    need(current['state'] == 'RESIDENT' and current['binding'] == supplied, 'RESULT_NOT_RESIDENT', exit_code=4)
    return current


@cpu_error_boundary
def preview(store, job_id, attempt_id, binding, destination, *, view='result', alpha=1., max_side=1024, roi=None, request_id=None):
    ops = OperationStore(store)
    path, spec = ops.path(job_id, attempt_id)
    check_binding(path, spec, binding)
    from ..preview_limits import MAX_FIT_SIDE
    from .lifecycle import context
    c=context(spec)
    limit=MAX_FIT_SIDE if c and c.get("light_plan") else 1024
    views=('result','source','reference') if c and c.get('light_plan') else ('result','source')
    need(view in views and type(max_side) is int and 1 <= max_side <= limit, 'CPU_PREVIEW_ARGS', exit_code=2)
    return call(path, 'PREVIEW', binding, destination=str(output(destination)), view=view, alpha=alpha,
                max_side=max_side, roi=roi, request_id=request_id or str(uuid.uuid4()))


@cpu_error_boundary
def export(store, job_id, attempt_id, binding, destination, *, alpha, format, bit_depth,
           icc_policy, request_key=None, export_root=None):
    ops = OperationStore(store)
    path, spec = ops.path(job_id, attempt_id)
    check_binding(path, spec, binding)
    need((format, bit_depth, icc_policy) == ('jpeg', 8, 'preserve_source'), 'CPU_EXPORT_FORMAT', exit_code=2)
    dest = output(destination)
    need(export_root is not None and dest.is_relative_to(output(export_root)), 'CPU_EXPORT_ROOT', exit_code=2)
    return call(path, 'EXPORT', binding, destination=str(dest), alpha=alpha, request_key=request_key)


@cpu_error_boundary
def keep_alive(store, job_id, attempt_id, binding, *, deadline_monotonic=None):
    ops=OperationStore(store);path,spec=ops.path(job_id,attempt_id)
    from ..adaptive_execution.residency import context,POLICY
    c=context(spec)
    need(c is not None,'KEEP_ALIVE_VERSION_NOT_SUPPORTED',exit_code=4)
    check_binding(path,spec,binding)
    from .contract import binding as file_binding
    # No caller record/lease lock is held while waiting for host IPC.
    from ..adaptive_execution.residency import policy
    return call(path,'KEEP_ALIVE',binding,lease_policy=policy(c),context=file_binding(c['manifest_path']),
                deadline_monotonic=deadline_monotonic)


def finish_release(path, spec, binding):
    """Compatibility wrapper; public paths use the one record-lock coordinator."""
    from .lifecycle import reconcile
    return reconcile(OperationStore(Path(path).parents[2]), path, spec, release_binding=binding)


@cpu_error_boundary
def release(store, job_id, attempt_id, binding):
    from .lifecycle import reconcile
    ops = OperationStore(store)
    path, spec = ops.path(job_id, attempt_id)
    return reconcile(ops, path, spec, release_binding=binding)
