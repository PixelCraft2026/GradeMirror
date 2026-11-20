"""Explicit first-fit orchestration on the original Store, reservation and recovery."""
import os,time,traceback
from pathlib import Path
from ..common import GMError,atomic_json,strict_load,writable_output_root
from ..gm004a_operations import OperationStore,artifact
from ..store import locked
from .. import winproc as win
from ..hybrid_replay import BACKEND_ID
from ..hybrid_replay.contract import require
from ..hybrid_replay.audit import science,progress
from ..hybrid_replay.resources import admit
from ..hybrid_replay.jobs import Monitor,spawn,retain_actual,finish,settle_failed_worker
from ..hybrid_replay.api import gpu_admission,response
from . import FIT_CONTRACT,is_fit_type
from . import plan as planning
from .audit import bridge
from .publication import make_package

def fit(project,store_root,output_root,*,request_key,client_identity,backend,experimental,profile):
    require(experimental is True and backend==BACKEND_ID,'HYBRID_EXPERIMENTAL_OPT_IN_REQUIRED')
    output=writable_output_root(output_root);project=Path(project).resolve();ops=OperationStore(store_root)
    prior=ops.lookup_request(request_key)
    if prior is not None:
        path,spec=prior;s=spec['semantics']
        require(is_fit_type(spec)and spec['kind']=='fit'and Path(s['project_path'])==project and
            Path(s['output_root'])==output and profile in (None,s['profile_id']),'REQUEST_KEY_CONFLICT')
        from ..project import _read_project_manifest
        m,forward=_read_project_manifest(project)
        require(not forward and m['source']['byte_sha256']==s['source_sha256']and m['reference']['byte_sha256']==s['reference_sha256'],'REQUEST_KEY_CONFLICT')
        return response(ops,path,spec,idempotent=True)
    plan,sem,locators,recipe,previous=planning.fit(project,output,backend,experimental,profile)
    return execute(ops,plan,sem,locators,None,None,None,request_key,client_identity,project)

def replay(project,store_root,output_root,*,request_key,client_identity,backend,experimental,manifest,source,reference):
    require(project is None and manifest is not None,'HYBRID_SOURCE_EXCLUSIVE')
    output=writable_output_root(output_root)
    plan,sem,locators,recipe,prior=planning.replay(manifest,source,reference,output,backend,experimental)
    return execute(OperationStore(store_root),plan,sem,locators,Path(manifest).resolve().parent,recipe,prior,request_key,client_identity,None)

def execute(ops,plan,sem,locators,directory,recipe,prior,key,client_identity,project):
    from .. import gm004a_api as api
    from ..gm003_supervisor import start_session
    fit=plan['operation']=='fit';path,spec,accepted=ops.begin('fit'if fit else'replay',sem,key)
    if accepted['idempotent_hit']:return response(ops,path,spec,idempotent=True)
    atomic_json(path/'execution-plan.json',plan)
    atomic_json(path/'execution-locators.json',dict(inputs=locators,recipe_dir=None if directory is None else str(directory),project=None if project is None else str(project)))
    fitted=int(fit and "saved_r0" not in plan)
    if fit:
        directory=path/'new-recipe'
        directory.mkdir(exist_ok=False)
        if "saved_r0" in plan:
            from ..light.recovery import mixed_seed
            import shutil
            require(mixed_seed(project)==plan["saved_r0"],"MIXED_RECOVERY_SEED_CHANGED")
            (directory/"r0").mkdir()
            for name in ("recipe.json","parameters.npz"):
                shutil.copyfile(project/"mixed-recovery"/name,directory/"r0"/name)
                from ..common import sha_file
                require(sha_file(directory/"r0"/name)==plan["saved_r0"]["files"][name],"MIXED_RECOVERY_SEED_CHANGED")
        if "saved_post" in plan:
            from ..saved_post import mixed
            require(mixed(project,plan["saved_r0"])==plan["saved_post"],"MIXED_SAVED_POST_CHANGED")
            for name in ("recipe.json","p3.npz"):
                shutil.copyfile(project/"mixed-recovery"/"saved-post"/name,directory/name)
            from .contract import read_recipe
            _,assets=read_recipe(directory)
            require(assets==plan["saved_post"]["assets"],"MIXED_SAVED_POST_CHANGED")
            atomic_json(path/"saved-post-consumption.json",dict(binding=plan["saved_post"],scalar_CG=0,range_discovery=0))
    output=Path(sem['output_root']);job,attempt=spec['job_id'],spec['attempt_id'];pool=api._resident_pool();reservation=None
    session=owner=host=monitor=active=None;started=time.monotonic()
    try:
        from ..hybrid_replay import monitor_completion as completion
        completion.begin(path,spec)
        ops.transition(job,attempt,'RUNNING','preflight','HYBRID_FIT_PREFLIGHT',owner=win.current_identity())
        from ..resource_execution.admission import reservation_lock
        with reservation_lock(path,api._pool_lock()):
            from ..resource_execution.admission import prepare
            def header(identity):
                return dict(width=identity['shape'][1],height=identity['shape'][0],bits=identity['bit_depth'])
            prepare(path,locators,header(plan['source_identity']),header(plan['reference_identity']),pool=pool,legacy_plan=plan)
            cancelled=lambda:(path/'cancel.signal').exists()
            admit(path,plan,'decode',qualification=bool(os.environ.get('GM021_QUALIFICATION_ROOT')),cancelled=cancelled)
            atomic_json(path/'resource-wait.json',dict(wait_seconds=0.))
            gpu_admission(path,plan)
            reservation=pool.claim(job,attempt,ops.root.parent,path/'session',budget_bytes=plan['resource_budget'],cancelled=cancelled)
            atomic_json(path/'hybrid-reservation.json',dict(token=reservation['token'],job_id=job,attempt_id=attempt))
            output.mkdir(parents=True,exist_ok=False)
            bound=dict(locators,_resource_budget=plan['resource_budget'],_operation_seconds=plan['durations']['operation'],
                _profile_id=sem['profile_id'],_profile_contract_sha256=sem['profile_contract_sha256'],
                _hybrid_execution=dict(fit_contract=FIT_CONTRACT,backend_id=BACKEND_ID,job_id=job,attempt_id=attempt,
                    operation_path=str(path),recipe_dir=str(directory),plan=plan,prior_manifest=prior))
            science(path,'source-decode',source_decodes=1);session=path/'session'
            ss,owner,host=start_session(path,session,'development_pair',output,sem['revision'],inputs=bound,client_identity=client_identity)
            pool.bind(reservation['token'],ss['session_id'],owner,host)
            completion.bind_session(path,spec,reservation['token'],ss['session_id'])
            monitor=Monitor(path,plan,owner,strict_load(session/'host-entry.json'),host).begin()
            internal=dict(backend_id=BACKEND_ID,execution_contract_sha256=plan['execution_contract_sha256'],job_id=job,attempt_id=attempt)
            def call(command,seconds):
                monitor.check();reply=api._call(ss,dict(command=command,**internal),deadline=time.monotonic()+seconds,cancelled=lambda:cancelled()or monitor.error is not None)
                monitor.check();return reply['result']
            shared=call('HYBRID_INPUTS',30);admit(path,plan,'R0',cancelled=cancelled)
            progress(path,'CUDA_R0_FIT'if fit else'CUDA_R0_REPLAY',tiles=0,total=plan['tiles'])
            payload=path/'R0-payload.json';atomic_json(payload,dict(operation_path=str(path),recipe_dir=str(directory),plan=plan,shared=shared))
            active=spawn(path,'R0',payload,cpu=False,monitor=monitor);retain_actual(active[0],active[2],monitor)
            rw=finish(*active,monitor=monitor,seconds=plan['durations']['R0']);active=None
            progress(path,'CUDA_EXIT_AND_HANDOFF',actual_exit=True);bridge(path,'R0')
            pw=None
            if fit and 'saved_post' not in plan:
                admit(path,plan,'P3',cancelled=cancelled);progress(path,'P3_CONSTRUCTION_AND_SOLVE')
                payload=path/'P3-payload.json';atomic_json(payload,dict(operation_path=str(path),recipe_dir=str(directory),plan=plan,shared=shared))
                active=spawn(path,'P3',payload,cpu=True,monitor=monitor);retain_actual(active[0],active[2],monitor)
                pw=finish(*active,monitor=monitor,seconds=600);active=None;bridge(path,'P3')
            payload=call('HYBRID_CPU_PREPARE',60);monitor.boundary('CPU')
            bound_data=strict_load(path/'data-binding.json')
            science(path,'CPU-service',**bound_data['CPU_budget'],**bound_data['CPU_extra'])
            progress(path,'CPU',core=0,total=(plan['source_identity']['shape'][0]+511)//512)
            active=spawn(path,'CPU',path/'CPU-payload.json',cpu=True,monitor=monitor);retain_actual(active[0],active[2],monitor)
            received=call('HYBRID_RECEIVE',3600);cw=finish(*active,monitor=monitor,seconds=10);active=None
            allocations=strict_load(path/'CPU/allocations.json');cleanup=strict_load(path/'CPU/mapping-cleanup.json')
            from ..light.resources import workspace_limit
            h,w,_=plan['source_identity']['shape']
            require(allocations['tracked_storage_peak_bytes']<=workspace_limit(h,w,plan['core_rows']) and allocations['base_remap_pixels']==bound_data['CPU_extra']['base_remap_pixels']and
                cleanup['views_remaining']==0 and cleanup['handle_closed'],'HYBRID_CPU_CLEANUP')
            monitor.boundary('ready');ready=call('HYBRID_READY',120);bridge(path,'CPU');lease=make_package(path,spec,directory,ready)
            recipe=strict_load(directory/'recipe.json')
            compute=dict(status='PASS',mode='hybrid-first-fit'if fit else'hybrid-fit-replay',backend_id=BACKEND_ID,
                fit_count=fitted,render_count=1,recipe_sha256=recipe['recipe_sha256'],source_identity=recipe['source_identity'],
                reference_identity=recipe['reference_identity'],float32_sha256=ready['hashes']['Y'],lease=lease,hashes=ready['hashes'],quantization=ready['quantization'])
            atomic_json(path/'compute-result.json',compute)
            atomic_json(path/'cleanup.json',dict(actual_wait=True,owned_processes_exited=True,R0=rw,P3=pw,CPU=cw,mode=compute['mode']))
            atomic_json(path/'replay-identity.json',dict(fit_count=fitted,render_count=1,tiles=plan['tiles'],source_sha256=sem['source_sha256'],
                reference_sha256=sem['reference_sha256'],recipe_sha256=recipe['recipe_sha256'],execution_manifest_sha256=lease['execution_manifest_sha256'],
                execution_contract_sha256=plan['execution_contract_sha256'],hashes=ready['hashes'],quantization=ready['quantization'],elapsed_seconds=time.monotonic()-started))
            names=['compute-result.json','cleanup.json','replay-identity.json','hybrid-ready.json','executor-result.json','executor-cleanup.json',
                'execution-plan.json','data-binding.json','reference-released.json','R0/result.json','R0/wait.json','R0/gpu-admission.json','R0/gpu-final.json',
                'CPU/wait.json','CPU/actual.json','CPU/allocations.json','session/source-decode.json','quantization.jsonl','field-receipts.jsonl']
            if fitted:names+=['R0/training-record.json']
            if 'saved_post' in plan:names+=['saved-post-consumption.json']
            elif fit:names+=['P3/result.json','P3/wait.json','P3/solver-cleanup.json','P3/solver-individual-wait.json']
            ops.commit(job,attempt,[artifact(path/n)for n in names],lease)
        state=completion.finish_controller(ops,path,spec,monitor)
        return completion.envelope(state,path,spec,idempotent=False)
    except BaseException as exc:
        if monitor is not None and(path/'commit.json').is_file():
            return completion.fail_controller(ops,path,spec,monitor,exc)
        primary=monitor.error if (monitor is not None and monitor.error is not None
            and getattr(exc,'code',None)=='CANCELLED') else exc
        secondary=[]
        def cleanup(label,action):
            try:return action()
            except Exception as error:
                secondary.append(dict(step=label,type=type(error).__name__,message=str(error)))
        if active is not None:
            cleanup('compute-wait',lambda:settle_failed_worker(active,monitor))
        if monitor is not None:cleanup('stop-sampling',monitor.stop_sampling)
        if owner is not None:
            def session_exit():
                try:
                    api._lose_session(session,owner,host)
                except GMError as error:
                    if error.code!='OWNED_TREE_WAIT_TIMEOUT'or monitor is None:raise
                    cleanup('legacy-session-exit',lambda:atomic_json(path/'legacy-session-cleanup-observation-error.json',
                        dict(error_code=error.code,error=str(error),resolution='use exact retained-handle waits')))
                if monitor is not None:
                    monitor.wait_session_exits()
                    publish_session_exit_proof(path,monitor)
            cleanup('session-exit',session_exit)
        if reservation is not None:
            cleanup('pool-reconcile',lambda:pool.close(reservation['token'],outcome='LOST' if owner is not None else 'STARTUP_FAILED'))
        cancelled_before=(path/'cancel.signal').exists() and not(path/'resource-STOP.json').exists() and (monitor is None or monitor.error is None)
        code='CANCELLED' if cancelled_before else getattr(primary,'code','HYBRID_FIRST_FIT_ERROR')
        exit_code=8 if cancelled_before else getattr(primary,'exit_code',9)
        state=cleanup('operation-terminal',lambda:ops.transition(job,attempt,'CANCELLED' if cancelled_before else 'FAILED',
            'terminal',code,error_code=code,exit_code=exit_code))
        cleanup('failure-diagnostic',lambda:atomic_json(path/'hybrid-failure.json',dict(error=str(primary),
            code=code,traceback=traceback.format_exc(),secondary=secondary)))
        for error in secondary:
            primary.add_note(f"{error['step']}: {error['type']}: {error['message']}")
        if state is not None and state['state']=='SUCCEEDED':return response(ops,path,spec,idempotent=False)
        if primary is not exc:raise primary from exc
        raise
    finally:
        from ..hybrid_replay.monitor_completion import dispose_monitor
        dispose_monitor(monitor)


def abort_pending(ops,spec):
    """Acknowledge new-type cancellation; the controller owns compute cleanup."""
    require(is_fit_type(spec)and spec['kind']in('fit','replay'),'HYBRID_CANCEL_TYPE')
    state=ops.cancel(spec['job_id'],spec['attempt_id'])
    return dict(state,cleanup_state='NOT_REQUIRED_COMPLETED'if state['state']=='SUCCEEDED'else'CANCEL_REQUESTED',
        cleanup_owner='hybrid-fit-controller',cleanup_complete=False)


def publish_session_exit_proof(path,monitor):
    """Publish the existing strict receipt from exact retained, waited handles."""
    for phase in('R0','P3','CPU'):
        if(Path(path)/phase/'spawn.json').is_file():
            wait=strict_load(Path(path)/phase/'wait.json')
            require(wait.get('actual_wait')is True and wait.get('actual_worker_handle_wait')is True and
                wait.get('tree_empty')is True,'HYBRID_COMPUTE_EXIT_UNCONFIRMED')
    identities=[strict_load(Path(path)/'session'/name)for name in('owner.json','host-process.json','host-entry.json')]
    rows=[]
    for identity in identities:
        key=win.identity_key(identity);held=monitor.handles.get(key)
        require(held is not None and win.identity_key(held[1])==key,'HYBRID_SESSION_EXIT_HANDLE_MISSING')
        waited=win.k.WaitForSingleObject(held[0],0)==win.WAIT_OBJECT_0
        require(waited,'HYBRID_SESSION_EXIT_UNCONFIRMED')
        rows.append(dict(identity=identity,actual_handle_wait=True))
    atomic_json(Path(path)/'hybrid-recovery-cleanup-waits.json',rows)
