"""Explicit hybrid replay through the existing operation/session APIs."""
import os,time,traceback
from pathlib import Path
from ..common import GMError, atomic_json, strict_load, writable_output_root
from ..gm004a_operations import OperationStore, artifact
from ..store import locked
from .. import winproc as win
from . import BACKEND_ID
from .contract import require
from .plan import resolve
from .audit import science, progress
from .resources import admit
from .jobs import Nvml,Monitor,spawn,retain_actual,finish
from .publication import make_package,residency


def response(ops,path,spec,*,idempotent):
    state=ops.status(spec['job_id'],spec['attempt_id'])
    from .monitor_completion import envelope
    return envelope(state,path,spec,idempotent=idempotent)


def gpu_admission(path,plan):
    from ..resource_execution.admission import wait_budget
    with wait_budget(path,600) as limit:
        return _gpu_admission(path,plan,limit)


def _gpu_admission(path,plan,limit):
    nv=Nvml(plan['device']['uuid']);started=time.monotonic()
    try:
        while True:
            sample=nv.sample();expected=plan['device']
            require((sample['name'],sample['uuid'],sample['driver_version'])==
                (expected['name'],expected['uuid'],expected['driver_version']),'REPLAY_DEVICE_CHANGED')
            require(not(set(sample['Compute'])-set(sample['Graphics'])),'FOREIGN_PURE_COMPUTE')
            atomic_json(path/'gpu-admission.json',sample)
            if sample['free']>=plan['R0_gpu']['required_free_bytes']:return
            state=strict_load(path/'resource-wait.json')
            if state['wait_seconds']+time.monotonic()-started>=limit:raise GMError('RESOURCE_WAIT_CAP','GPU admission expired',5)
            if(path/'cancel.signal').exists():raise GMError('CANCELLED','Cancelled during GPU wait',8)
            time.sleep(.5)
    finally:
        nv.close()
        state=strict_load(path/'resource-wait.json')
        atomic_json(path/'resource-wait.json',dict(wait_seconds=state['wait_seconds']+time.monotonic()-started))


def replay(project,store_root,output_root,*,request_key,client_identity,backend,experimental,manifest,source,reference):
    if manifest is not None:
        from ..hybrid_fit import MANIFEST_SCHEMA
        if strict_load(Path(manifest)).get('schema_version')==MANIFEST_SCHEMA:
            from ..hybrid_fit.api import replay as new_replay
            return new_replay(project,store_root,output_root,request_key=request_key,client_identity=client_identity,
                backend=backend,experimental=experimental,manifest=manifest,source=source,reference=reference)
    from .. import gm004a_api as api
    from ..gm003_supervisor import start_session
    output=writable_output_root(output_root)
    plan,sem,locators,directory,recipe,prior=resolve(project,manifest,source,reference,output,backend,experimental)
    ops=OperationStore(store_root);path,spec,accepted=ops.begin('replay',sem,request_key)
    if accepted['idempotent_hit']:return response(ops,path,spec,idempotent=True)
    atomic_json(path/'execution-plan.json',plan)
    atomic_json(path/'execution-locators.json',dict(inputs=locators,recipe_dir=str(directory),
        project=None if project is None else str(Path(project).resolve()),manifest=None if manifest is None else str(Path(manifest).resolve())))
    job_id,attempt=spec['job_id'],spec['attempt_id'];pool=api._resident_pool();reservation=None
    session=owner=host=monitor=active=None;started=time.monotonic()
    try:
        from . import monitor_completion as completion
        completion.begin(path,spec)
        ops.transition(job_id,attempt,'RUNNING','preflight','HYBRID_PREFLIGHT',owner=win.current_identity())
        from ..resource_execution.admission import reservation_lock
        with reservation_lock(path,api._pool_lock()):
            from ..resource_execution.admission import prepare
            def header(identity):
                return dict(width=identity['shape'][1],height=identity['shape'][0],bits=identity['bit_depth'])
            prepare(path,locators,header(plan['source_identity']),header(plan['reference_identity']),pool=pool,legacy_plan=plan)
            cancelled=lambda:(path/'cancel.signal').exists()
            admit(path,plan,'decode',qualification=bool(os.environ.get('GM021_QUALIFICATION_ROOT')),cancelled=cancelled)
            gpu_admission(path,plan)
            reservation=pool.claim(job_id,attempt,store_root,path/'session',budget_bytes=plan['resource_budget'],cancelled=cancelled)
            atomic_json(path/'hybrid-reservation.json',dict(token=reservation['token'],job_id=job_id,attempt_id=attempt))
            output.mkdir(parents=True,exist_ok=False)
            bound=dict(locators,_resource_budget=plan['resource_budget'],_operation_seconds=5400,
                _profile_id=recipe['profile_id'],_profile_contract_sha256=recipe['profile_contract_sha256'],
                _hybrid_execution=dict(backend_id=BACKEND_ID,job_id=job_id,attempt_id=attempt,operation_path=str(path),
                    recipe_dir=str(directory),plan=plan,prior_manifest=prior))
            science(path,'source-decode',source_decodes=1)
            session=path/'session'
            session_spec,owner,host=start_session(path,session,'development_pair',output,sem['revision'],
                inputs=bound,client_identity=client_identity)
            pool.bind(reservation['token'],session_spec['session_id'],owner,host)
            completion.bind_session(path,spec,reservation['token'],session_spec['session_id'])
            actual_host=strict_load(session/'host-entry.json')
            monitor=Monitor(path,plan,owner,actual_host,host).begin()
            internal=dict(backend_id=BACKEND_ID,execution_contract_sha256=plan['execution_contract_sha256'],job_id=job_id,attempt_id=attempt)
            def call(command,seconds):
                monitor.check()
                reply=api._call(session_spec,dict(command=command,**internal),deadline=time.monotonic()+seconds,
                    cancelled=lambda:cancelled()or monitor.error is not None)
                monitor.check();return reply['result']
            shared=call('HYBRID_INPUTS',30)
            admit(path,plan,'R0',cancelled=cancelled);progress(path,'CUDA R0',tiles=0,total=plan['tiles'])
            payload=path/'R0-payload.json';atomic_json(payload,dict(operation_path=str(path),recipe_dir=str(directory),plan=plan,shared=shared))
            active=spawn(path,'R0',payload,cpu=False,monitor=monitor);retain_actual(active[0],active[2],monitor)
            rw=finish(*active,monitor=monitor,seconds=900);active=None
            monitor.check();progress(path,'CUDA_EXIT_AND_HANDOFF',actual_exit=True)
            payload=call('HYBRID_CPU_PREPARE',60);monitor.boundary('CPU')
            science(path,'CPU-service',**plan['CPU_budget'],**plan['CPU_extra'])
            progress(path,'CPU',core=0,total=(plan['source_identity']['shape'][0]+511)//512)
            active=spawn(path,'CPU',path/'CPU-payload.json',cpu=True,monitor=monitor);retain_actual(active[0],active[2],monitor)
            received=call('HYBRID_RECEIVE',3600)
            cw=finish(*active,monitor=monitor,seconds=10);active=None
            allocations=strict_load(path/'CPU/allocations.json');cleanup=strict_load(path/'CPU/mapping-cleanup.json')
            from ..light.resources import workspace_limit
            h,w,_=plan['source_identity']['shape']
            require(allocations['tracked_storage_peak_bytes']<=workspace_limit(h,w,plan['core_rows']) and
                allocations['base_remap_pixels']==plan['CPU_extra']['base_remap_pixels'] and
                cleanup['views_remaining']==0 and cleanup['handle_closed'],'HYBRID_CPU_CLEANUP')
            monitor.boundary('ready');progress(path,'VALIDATING_RESULT')
            ready=call('HYBRID_READY',120)
            lease=make_package(path,spec,directory,ready)
            compute=dict(status='PASS',mode='hybrid-replay',backend_id=BACKEND_ID,fit_count=0,render_count=1,
                recipe_sha256=recipe['recipe_sha256'],source_identity=plan['source_identity'],
                float32_sha256=ready['hashes']['Y'],lease=lease,hashes=ready['hashes'],quantization=ready['quantization'])
            atomic_json(path/'compute-result.json',compute)
            atomic_json(path/'cleanup.json',dict(actual_wait=True,owned_processes_exited=True,R0=rw,CPU=cw,mode='hybrid-replay'))
            atomic_json(path/'replay-identity.json',dict(fit_count=0,render_count=1,tiles=plan['tiles'],
                source_sha256=sem['source_sha256'],reference_sha256=sem['reference_sha256'],
                recipe_sha256=recipe['recipe_sha256'],execution_manifest_sha256=lease['execution_manifest_sha256'],
                execution_contract_sha256=plan['execution_contract_sha256'],hashes=ready['hashes'],
                quantization=ready['quantization'],elapsed_seconds=time.monotonic()-started))
            names=('compute-result.json','cleanup.json','replay-identity.json','hybrid-ready.json',
                'executor-result.json','executor-cleanup.json','execution-plan.json','R0/result.json','R0/wait.json',
                'R0/gpu-admission.json','R0/gpu-final.json','CPU/wait.json','CPU/actual.json','CPU/allocations.json',
                'session/source-decode.json','quantization.jsonl','field-receipts.jsonl')
            ops.commit(job_id,attempt,[artifact(path/n) for n in names],lease)
        # The original token remains occupied. Leave the global compute lock
        # before entering the same coordinator used by public recovery.
        progress(path,'ACTIVATING_RESIDENT_RESULT')
        state=completion.finish_controller(ops,path,spec,monitor)
        return completion.envelope(state,path,spec,idempotent=False)
    except BaseException as exc:
        if monitor is not None and(path/'commit.json').is_file():
            return completion.fail_controller(ops,path,spec,monitor,exc)
        atomic_json(path/'hybrid-failure.json',dict(error=str(exc),traceback=traceback.format_exc()))
        if active is not None:
            job,who,out=active
            if not getattr(job,'_hybrid_finished',False):
                if job.poll()is None:job._hybrid_forced=True;job.terminate()
                try:finish(job,who,out,monitor=monitor,seconds=10,expected=(0,1,8,9))
                except BaseException as cleanup_error:atomic_json(path/'compute-cleanup-error.json',dict(error=str(cleanup_error)))
            active=None
        code=exc.code if isinstance(exc,GMError)else'HYBRID_REPLAY_ERROR'
        cancelled_before=(path/'cancel.signal').exists()and not(path/'resource-STOP.json').exists()
        if cancelled_before:code='CANCELLED'
        state=ops.transition(job_id,attempt,'CANCELLED'if cancelled_before or isinstance(exc,GMError)and exc.exit_code==8 else'FAILED',
            'terminal',code,error_code=code,exit_code=8 if cancelled_before else exc.exit_code if isinstance(exc,GMError)else 9)
        if monitor is not None:monitor.stop_sampling()
        if owner is not None:
            try:
                api._lose_session(session,owner,host)
                if monitor is not None:monitor.wait_session_exits()
            finally:
                if reservation is not None:pool.close(reservation['token'],outcome='LOST')
        elif reservation is not None:pool.close(reservation['token'],outcome='STARTUP_FAILED')
        # Publication wins even if activation or the controller failed. A later
        # request observes success/nonresident and never starts another worker.
        if state['state']=='SUCCEEDED':return response(ops,path,spec,idempotent=False)
        raise
    finally:
        from .monitor_completion import dispose_monitor
        dispose_monitor(monitor)


def release(delegate,store,job,attempt,binding):
    from .recovery import release as coordinated_release
    return coordinated_release(delegate,store,job,attempt,binding)
