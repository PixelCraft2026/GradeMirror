"""Shared CLI/Qt/API facade. Process audit hooks never enter the caller."""
import os
import time
import uuid
from pathlib import Path
from ..common import atomic_json,strict_load,GMError
from ..store import locked
from ..cpu_replay.contract import ROOT,binding,verify
from .intent import need,digest
from .planner import plan_v1 as plan,validate,snapshot


def recover_saved_fit(directory, parameter_file, source, *, session_root, request_key, p0=None):
    """Explicit adopted saved-fit capability; does not run a fit or grant a lease."""
    from .saved_fit_recovery import recover_saved_fit as recover
    return recover(directory,parameter_file,source,session_root=session_root,request_key=request_key,p0=p0)

def _control_launch_profile(c):
    """Only an explicitly manifested GM064 fine-replay package opts in."""
    from . import portable_contract as portable
    if not portable.segmented_fine_qualification(c):
        return 'default'
    manifest=strict_load(ROOT/'portable-manifest.json')
    if 'r4_s2_control_launch' not in manifest:
        return 'default'
    expected=dict(schema='gm064-r4-s2-control-launch-v1',profile='detached',
                  role='application_control',phase='replay_fine',mode='fine',domain='24mp',
                  parent_manifest='c69e4447403e4c7f928b35a0af6a2c1af82cd8618343ab3e1adbebfcb9b2e0bb')
    need(manifest['r4_s2_control_launch']==expected,'GM064_CONTROL_LAUNCH_POLICY')
    need(strict_load(portable.package_member('validation/detached-launch.json'))==expected,
         'GM064_CONTROL_LAUNCH_MEMBER')
    q=strict_load(Path(c['qualification']['path']))
    need(q['domain']=='24mp' and q['phase'] in ('dry','native') and c['request']['operation']=='replay',
         'GM064_CONTROL_LAUNCH_SCOPE')
    return 'detached'

def _handoff(job,owner):
    import ctypes as ct
    from ctypes import wintypes as wt
    from .. import winproc as win
    need(win.identity_key(win.process_identity(owner['pid']))==win.identity_key(owner) and job.member(owner['pid']),
         'APPLICATION_OWNER_HANDOFF_IDENTITY')
    h=win.k.OpenProcess(0x40|win.PROCESS_QUERY_LIMITED_INFORMATION|win.SYNCHRONIZE,False,owner['pid'])
    need(bool(h),'APPLICATION_OWNER_HANDOFF_OPEN')
    kernel=ct.WinDLL('kernel32',use_last_error=True);kernel.GetCurrentProcess.restype=wt.HANDLE
    duplicate=kernel.DuplicateHandle;duplicate.argtypes=[wt.HANDLE,wt.HANDLE,wt.HANDLE,ct.POINTER(wt.HANDLE),wt.DWORD,wt.BOOL,wt.DWORD];duplicate.restype=wt.BOOL
    handle=wt.HANDLE()
    try:need(bool(duplicate(kernel.GetCurrentProcess(),job.handle,h,ct.byref(handle),0,False,2)), 'APPLICATION_OWNER_HANDOFF_DUPLICATE')
    finally:win.close(h)
    return dict(owner=owner,duplicate_handle_in_owner=int(handle.value),same_existing_OwnedJob=True,
        released_by_owner_process_exit=True,control_exit_is_not_tree_exit=True)

def _dispatch(c,action,arguments,timeout=300):
    if c.get('schema')=='cpu-application-execution-v6':
        from .low_memory_contract import admit
        return admit(c,'before_control_worker')
    from ..winproc import OwnedJob,current_identity,creation_flags
    from ..cpu_replay.runtime import approve_plan
    from .portable_contract import enabled as portable_enabled
    if action=='submit' and c.get('resource_selection') is not None and not portable_enabled(c):

        from ..gm004a_operations import OperationStore
        existing=OperationStore(Path(c['workspace'])/'store').lookup_request(arguments['request_key'])
        # A committed operation reuses its exact pair; no new computation.
        if existing is None or not (existing[0]/'commit.json').is_file():
            from .resource_policy_v2 import admit_context
            admit_context(c,'before_control_worker',arguments['request_key'])
    work=Path(c['workspace'])/'.adaptive/control'/uuid.uuid4().hex;work.mkdir(parents=True)
    atomic_json(work/'payload.json',dict(action=action,arguments=arguments))
    exe=c['interpreters']['control']['path'];argv=[exe,'-B','-S','-X','utf8',str(ROOT/('tools/gm059_worker.py' if c['schema']=='cpu-application-execution-v5' else 'tools/gm055_worker.py')),'application_control',str(work/'payload.json')]
    if portable_enabled(c):
        from .portable_resources import admit
        admit(c,'before_launch_application_control',role='application_control');argv=[exe,'-I',*argv[1:]]
        # The portable v4 capability uses its packaged v4 entry directly.
        argv[-3]=str(ROOT/'tools/gm057_worker.py')
        need(Path(argv[-3]).is_file(),'PORTABLE_CONTROL_ENTRY_MISSING')
    observer=os.environ.get('GM052_OBSERVER_ROOT');need(observer,'APPLICATION_LAUNCH_ACCOUNTING_REQUIRED')
    q=strict_load(Path(c['qualification']['path']))
    launch_profile=_control_launch_profile(c)
    dry_detached=launch_profile=='detached' and q['phase']=='dry'
    if dry_detached:
        argv[-2]='entry_application_control'
    native=c['schema'] in ('cpu-application-execution-v3','cpu-application-execution-v4') or c['schema']=='cpu-application-execution-v5' and c['TEST_ONLY'] is False
    roots=[str(Path(q['paths']['evidence'])/'runs')] if native or c['schema']=='cpu-application-execution-v5' else q['observer_roots']
    need(any(Path(observer).resolve().is_relative_to(Path(root)) for root in roots), 'APPLICATION_OBSERVER_SCOPE')
    if native:timeout=q['limits']['fast_control' if c['mode']=='fast' else 'fine_control'] if action=='submit' else q['limits']['export'] if action=='export' else q['limits']['resident_control'] if action=='keep_alive' else timeout
    launch=Path(observer)/'plans'/(uuid.uuid4().hex+'.json')
    launch_data=dict(argv=argv,cwd=str(ROOT),before_CreateProcess=True,role='application_control')
    if launch_profile=='detached':
        launch_data.update(launch_profile=launch_profile,creation_flags=creation_flags(launch_profile,unicode_environment=True),
                           inherit_handles=False,assign_job_before_resume=True)
    atomic_json(launch,launch_data);approve_plan(launch,context=c)
    env=dict(os.environ,PYTHONPATH=str(ROOT/'src'),PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',GM055_CONTEXT=c['manifest_path'],CUDA_VISIBLE_DEVICES='-1',TEMP=str(work),TMP=str(work))
    for key in ('GM053_CONTEXT','GM054_CONTEXT','GM052_R1_CONTEXT'):env.pop(key,None)
    if c['schema']=='cpu-application-execution-v5':
        from .independent_layout import environment
        env=environment('control',work,context=c)
    if portable_enabled(c):
        from .portable_runtime import environment
        env=environment('control',work,context=c)
    job=OwnedJob((6 if native else 3)*1024**3);code=None;handoff=None;created=None
    try:
        created=job.spawn(argv,str(ROOT),env,launch_profile=launch_profile)
        if dry_detached:
            from ..cpu_replay.runtime import wait
            code=wait(job,work,timeout=timeout)['exit_code']
        else:
            code=job.wait(timeout*1000)
        need((work/'result.json').is_file(),'APPLICATION_CONTROL_RESULT_MISSING')
        envelope=strict_load(work/'result.json')
        if not envelope['ok']:
            error=GMError(envelope['error_code'],envelope['message'],envelope['exit_code'])
            if c['schema']=='cpu-application-execution-v4':
                detail=envelope.get('failure',{});error.adaptive_first=True;error.execution_stage=detail.get('stage');error.preserved_recipes=detail.get('preserved_recipes',[]);error.next_step=detail.get('next_step')
            raise error
        need(code==0,'APPLICATION_CONTROL_EXIT')
        result=envelope['result'];lease=result.get('binding')
        if result.get('resident') is True and lease and job.member(lease['owner']['pid']):handoff=_handoff(job,lease['owner'])
        if handoff is None:need(job.wait_empty(30000),'APPLICATION_CONTROL_TREE_NOT_EMPTY')
        return result
    finally:
        if job.child:
            if job.poll() is None:job.terminate();code=job.wait(30000)
            if handoff is None and action=='submit':
                # A control crash after publication must not kill a healthy
                # owner. Preserve the failure and transfer only this exact
                # registered operation's Job to its verified live owner.
                from ..gm004a_operations import OperationStore
                from ..winproc import identity_alive
                found=OperationStore(Path(c['workspace'])/'store').lookup_request(arguments['request_key'])
                if found:
                    path,spec=found;owner_file=path/'session/owner.json'
                    if spec['semantics'].get('instance_context')==binding(c['manifest_path']) and owner_file.is_file():
                        owner=strict_load(owner_file)
                        if identity_alive(owner) is True and job.member(owner['pid']):handoff=_handoff(job,owner)
            atomic_json(work/'actual-wait.json',dict(identity=created,actual_control_handle_wait=True,exit_code=code,
                tree_empty=None if handoff else job.wait_empty(30000),owner_handoff=handoff,
                peak_job_memory_bytes=job.memory_peak(),caller=current_identity(),
                **(dict(launch_profile=launch_profile,creation_flags=launch_data['creation_flags'],
                        inherit_handles=False,launch_plan=binding(launch)) if launch_profile=='detached' else {})))
        job.close()

def _submit_v1(value,request_key,workspace,*,experimental=False,qualification=None,_selection=None):
    value=validate(value);r=value['request']
    need(experimental is True,'EXECUTION_NOT_QUALIFIED')
    from .qualification import authorize
    q=authorize(qualification,r)
    need(r['execution_preference']=='cpu','EXECUTION_NOT_QUALIFIED','该执行偏好尚未获得当前应用上下文资格。')
    need(r['operation'] in ('fit','refine','replay'),'APPLICATION_RESIDENT_REQUIRES_BINDING','驻留预览或导出必须使用现有 job/attempt 和绑定。')
    workspace=Path(workspace).resolve();need(workspace.is_relative_to(Path(q['paths']['workspace'] if q.get('schema') in ('adaptive-native-qualification-v1','adaptive-first-qualification-v1','adaptive-first-recovery-qualification-v1','adaptive-first-actual-b-recovery-v1','adaptive-independent-qualification-v3') else q['workspace_root'])),'QUALIFICATION_WORKSPACE_SCOPE')
    need(type(request_key) is str and 0<len(request_key)<=128,'APPLICATION_REQUEST_KEY')
    index=workspace/'.adaptive/requests';record=index/(digest(request_key)+'.json')
    from .t34r3_c1_route import selected
    from . import c2_fine_replay
    c2_replay=Path(q['manifest_path']).name in c2_fine_replay.QUALIFICATIONS if q.get('task')=='GM-064' else False
    if c2_replay:
        from .portable_contract import session
        c2_fine_replay.dispatch_scope(q,session(qualification),workspace,request_key)
    route=selected(q,workspace,request_key)
    if route is not None:
        need(_selection==binding(route.selection),'C1_ROUTE_SELECTION_REQUIRED')
        record=route.request
    with locked(workspace/'.adaptive/submit.lock'):
        if record.exists():
            prior=strict_load(record);need(prior['intent_id']==value['intent_id'],'REQUEST_KEY_CONFLICT')
            from .context import load
            c=load(prior['context'])
            if _selection is not None:need(c.get('resource_selection')==_selection,'RESOURCE_SELECTION_CONTEXT_CHANGED')
            elif c.get('resource_selection') is not None:
                from .selection import validate_binding
                verify(c['resource_selection']);selected=strict_load(Path(c['resource_selection']['path']));validate_binding(selected,r)
                need(selected['original_request']==r,'REQUEST_KEY_CONFLICT')
        else:
            need(-1<=time.time()-value['snapshot']['captured_at']<=60,'SNAPSHOT_STALE')
            current=snapshot();pool=workspace/'pool/reservation.json'
            if pool.is_file():
                row=strict_load(pool)
                current['existing']=dict(rss=0,commit=0,vram=0,state='FREE' if row['state']=='FREE' else 'BUSY')
                if row['state']=='FREE':
                    session=Path(row['session_path']);proof=session.parent/'reclaim-receipt.json'
                    current['own_tree_exited']=proof.is_file() and strict_load(proof).get('tree_empty') is True
            elif not any(index.glob('*.json')):
                current['own_tree_exited']=True;current['existing']=dict(rss=0,commit=0,vram=0,state='FREE')
            fresh=plan(r,current)
            if q.get('task')=='GM-064':
                from .portable_plan import legacy_fresh
                fresh=legacy_fresh(r,current,dict(q,paths=dict(q['paths'],workspace=str(workspace))))
            if q.get('task')=='GM-063' and q.get('admission_addendum') is not None:fresh=plan(r,current,_qualification=q)
            cpu=fresh['candidates'][0]
            atomic_json(workspace/'.adaptive/admissions'/(uuid.uuid4().hex+'.json'),dict(snapshot=current,
                stage='before_operation_creation',minimum_available_ram=cpu['minimum_available_ram'],
                minimum_available_commit=cpu['minimum_available_commit'],additional_peak_bytes=cpu['additional_peak_bytes'],
                resource_feasible=cpu['resource_feasible'],reasons=cpu['resource_reasons'],source='GlobalMemoryStatusEx',no_worker_created_yet=True))
            need(cpu['resource_feasible'] is True,'RESOURCE_NOT_READY',str(cpu['resource_reasons']))
            if q.get('schema') in ('adaptive-native-qualification-v1','adaptive-first-qualification-v1','adaptive-first-recovery-qualification-v1','adaptive-first-actual-b-recovery-v1'):
                need(q['phase']=='native' or q.get('schema')=='adaptive-independent-qualification-v3' and q['TEST_ONLY'] is True,'NATIVE_DRY_ENTRY_NO_COMPUTE')
                from .native_resources import admission as native_admission
                if q.get('TEST_ONLY') is not True:
                    admission_context=dict(output_root=str(workspace/'.adaptive/admissions'))
                    if q.get('task')in('GM-063','GM-064'):admission_context['qualification']=binding(qualification)
                    native_admission(admission_context)
            from .context import register
            case=q.get('cases',{}).get(request_key,{})
            identifier=route.context_id if route is not None else str(uuid.uuid4())
            c=register(workspace,r,qualification,identifier,r['quality'],fault=case.get('fault',''),pause=case.get('pause',''),resource_selection=_selection)
            atomic_json(record,dict(intent_id=value['intent_id'],context=c['manifest_path'],request_key=request_key))
        if route is not None:
            saved=route.before_dispatch(c)
            if saved is not None:return saved
        if c2_replay:
            saved=c2_fine_replay.before_dispatch(c)
            if saved is not None:return saved
    if q.get('task')=='GM-064':
        from .portable_renewal import prepare_client
        prepare_client(c)
    if r['quality']=='fine' and r['operation']=='fit':return _composite_submit(c,request_key)
    result=_dispatch(c,'submit',dict(request_key=request_key))
    if route is not None:route.remember(c,result)
    if c2_replay:c2_fine_replay.remember(c,result)
    return result

def _composite_submit(c,key):
    import copy
    from .context import register
    parent=_dispatch(c,'parent_begin',dict(request_key=key))
    if parent['idempotent_hit']:return parent
    pair=dict(job_id=parent['job_id'],attempt_id=parent['attempt_id']);fast=None
    try:
        for stage in ('fast','fine'):
            request=copy.deepcopy(c['request'])
            request.update(quality=stage,operation='fit' if stage=='fast' else 'refine',recipe=None if stage=='fast' else fast['recipe'])
            q=strict_load(Path(c['qualification']['path']));case=q.get('cases',{}).get(key,{})
            child=register(c['workspace'],request,c['qualification']['path'],str(uuid.uuid4()),stage,
                fault=case.get(stage+'_fault',''),pause=case.get(stage+'_pause',''),parent=pair)
            childkey='child-'+digest(dict(parent=pair,stage=stage))
            _dispatch(c,'parent_attach',dict(pair,child_context=child['manifest_path'],child_key=childkey,stage=stage))
            value=_dispatch(child,'submit',dict(request_key=childkey))
            state=_dispatch(c,'parent_status',pair)
            if stage=='fast':
                fast=value
                invoke(c['workspace'],'release',value['job_id'],value['attempt_id'],binding=value['binding'])
        if c['schema']=='cpu-application-execution-v2':state=dict(state,lease_policy=c['lease_policy'])
        return state
    except BaseException as e:
        try:
            state=_dispatch(c,'parent_status',pair);e.add_note('Retained recipes: '+str(state.get('preserved_recipes')))
            e.preserved_recipes=state.get('preserved_recipes',[])
            e.execution_stage=state.get('children',[])[-1].get('stage') if state.get('children') else state.get('stage')
        except BaseException as secondary:e.add_note('Parent recovery: '+str(secondary))
        raise

def invoke(workspace,action,job_id,attempt_id,**arguments):
    need(action in ('status','cancel','preview','export','release','keep_alive'),'APPLICATION_ACTION_INVALID')
    fields={'keep_alive':{'binding'},'status':set(),'cancel':set(),'release':{'binding'},'preview':{'binding','destination','alpha','request_id'},'export':{'binding','destination','alpha','request_key'}}
    need(set(arguments)==fields[action] or action=='cancel' and set(arguments)=={'expected_child'},'APPLICATION_ACTION_FIELDS')
    if action=='cancel' and arguments.get('expected_child') is not None:
        need(type(arguments['expected_child']) is dict and set(arguments['expected_child'])=={'job_id','attempt_id'},'APPLICATION_CANCEL_ADDRESS')
    from ..gm004a_operations import OperationStore
    from .context import context
    workspace=Path(workspace).resolve();_,spec=OperationStore(workspace/'store').path(job_id,attempt_id)
    if spec['semantics'].get('adaptive_parent'):
        from .context import load
        c=load(spec['semantics']['instance_context']['path'],cleanup=action in ('status','cancel','release'))
        need(c['workspace']==str(workspace),'APPLICATION_WORKSPACE_IDENTITY')
        args=dict(job_id=job_id,attempt_id=attempt_id)
        if action in ('status','cancel'):
            if action=='cancel':args['expected_child']=arguments.get('expected_child')
            return _dispatch(c,'parent_'+action,args)
        # Address the registered current child without starting a second
        # control merely to rediscover it before every resident command.
        from ..cpu_replay.contract import verify
        parentpath,_=OperationStore(workspace/'store').path(job_id,attempt_id)
        with locked(parentpath/'record.lock'):
            rows=strict_load(parentpath/'children.json')['children'];need(bool(rows),'APPLICATION_NO_ACTIVE_CHILD')
            row=rows[-1];verify(row['context']);cc=strict_load(Path(row['context']['path']))
            need(cc['workspace']==str(workspace) and cc['parent_operation']==args,'APPLICATION_CHILD_PARENT')
            found=OperationStore(workspace/'store').lookup_request(row['request_key']);need(found is not None,'APPLICATION_NO_ACTIVE_CHILD')
            childpath,childspec=found
            need(childspec['semantics']['instance_context']==row['context'] and
                childspec['semantics']['adaptive_parent_operation']==args,'APPLICATION_CHILD_IDENTITY')
            child=dict(job_id=childspec['job_id'],attempt_id=childspec['attempt_id'])
        if 'binding' in arguments:
            childpath,_=OperationStore(workspace/'store').path(child['job_id'],child['attempt_id'])
            expected=strict_load(childpath/'commit.json')['result_lease']
            need(arguments['binding']==expected,'CPU_RESULT_BINDING_STALE')
        value=invoke(workspace,action,child['job_id'],child['attempt_id'],**arguments)
        if action=='release':value=dict(value,parent_operation=args)
        return value
    c=context(spec,cleanup=action in ('status','cancel','release'));need(Path(c['workspace'])==workspace,'APPLICATION_WORKSPACE_IDENTITY')
    return _dispatch(c,action,dict(job_id=job_id,attempt_id=attempt_id,**arguments),timeout=30 if action=='keep_alive' else 300)

def locate(workspace,request_key):
    if not request_key:return None
    from ..gm004a_operations import OperationStore
    row=OperationStore(Path(workspace)/'store').lookup_request(request_key)
    return (row[1]['job_id'],row[1]['attempt_id']) if row else None

def _progress_record(path,*,with_children=False):
    """A progress observation never waits behind a live operation action."""
    from ..gm004a_operations import _journal
    lock=path/'record.lock'
    try:
        with locked(lock,nonblocking=True):
            event=_journal(path)[-1]
            if not with_children:return event
            try:children=strict_load(path/'children.json')['children']
            except FileNotFoundError:children=None
            return dict(event=event,children=children)
    except GMError as error:
        if error.code!='LOCK_BUSY' or str(error)!=str(lock):raise
        return None

def _progress_wait(pair,child,*,observation='RECORD_BUSY_NO_STATE_OBSERVED'):
    return dict(job_id=pair[0],attempt_id=pair[1],state='UNKNOWN',stage='waiting_for_status',
        done=None,total=None,active_child=child,event_id=digest(dict(progress_wait=pair,child=child)),
        progress_only=True,worker_heartbeat=False,observation_status=observation)

def progress(workspace,request_key):
    """Read existing journal/progress facts; no recovery, activation or guard."""
    from ..gm004a_operations import OperationStore,_journal
    from .context import load,context
    workspace=Path(workspace).resolve();pair=locate(workspace,request_key)
    if pair is None:return dict(state='STARTING',stage='starting',total=None,active_child=None,event_id='not-accepted',progress_only=True,worker_heartbeat=False)
    ops=OperationStore(workspace/'store');path,spec=ops.path(*pair);child=None;parent=path
    if spec['semantics'].get('adaptive_parent'):
        c=load(spec['semantics']['instance_context']['path'],cleanup=True)
        need(c['workspace']==str(workspace),'APPLICATION_WORKSPACE_IDENTITY')
        observed=_progress_record(path,with_children=True)
        if observed is None:return _progress_wait(pair,None)
        if observed['children'] is None:return _progress_wait(pair,None,observation='CHILDREN_NOT_OBSERVED')
        parent_event=observed['event'];rows=observed['children']
        if rows:
            row=rows[-1];verify(row['context']);found=ops.lookup_request(row['request_key'])
            if found:
                path,cs=found
                need(cs['semantics']['instance_context']==row['context'] and cs['semantics']['adaptive_parent_operation']==dict(job_id=pair[0],attempt_id=pair[1]),'APPLICATION_CHILD_PARENT')
                context(cs,cleanup=True);child=dict(job_id=cs['job_id'],attempt_id=cs['attempt_id'])
    else:
        c=context(spec,cleanup=True);need(c['workspace']==str(workspace),'APPLICATION_WORKSPACE_IDENTITY');parent_event=None
    event=_progress_record(path)
    if event is None:return _progress_wait(pair,child)
    facts=dict(event['snapshot']);detail=strict_load(path/'progress.json') if (path/'progress.json').is_file() else {}
    return dict(job_id=pair[0],attempt_id=pair[1],state=facts['state'],stage=detail.get('stage',facts['stage']),
        done=detail.get('done'),total=detail.get('total'),detail=detail,active_child=child,
        event_id=digest(dict(parent=parent_event,event=event,detail=detail)),progress_only=True,worker_heartbeat=False)

def open_recipe(path):
    from ..cpu_recipe import inspect_cpu_recipe
    value=inspect_cpu_recipe(path)
    need(value.get('version') in (2,3),'APPLICATION_RECIPE_VERSION')
    return dict(value,availability='REPLAY_REQUIRED',resident=False,application_execution_qualified=False)

def relink_recipe(path,source):
    from ..cpu_recipe import relink_cpu_recipe_source
    return relink_cpu_recipe_source(path,source)


def qualify_native(workspace,job_id,attempt_id,binding):
    """Validation-only quantization, behind the same exact native context."""
    from ..gm004a_operations import OperationStore
    from .context import context
    _,spec=OperationStore(Path(workspace)/'store').path(job_id,attempt_id)
    c=context(spec);need(c['schema'] in ('cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5') and Path(c['workspace'])==Path(workspace).resolve(),'NATIVE_QUALIFY_SCOPE')
    return _dispatch(c,'qualify',dict(job_id=job_id,attempt_id=attempt_id,binding=binding),timeout=1200)


def submit(value,request_key,workspace,*,experimental=False,qualification=None):
    if isinstance(value,dict) and value.get('schema')=='adaptive-plan-v5':
        from .portable_plan import submit as portable_submit
        return portable_submit(value,request_key,workspace,experimental=experimental,qualification=qualification)
    if isinstance(value,dict) and value.get('schema')=='adaptive-plan-v4':
        from .low_memory_plan import submit as low_memory_submit
        return low_memory_submit(value,request_key,workspace,experimental=experimental,qualification=qualification)
    if isinstance(value,dict) and value.get('schema')=='adaptive-plan-v3':
        from .independent_selection import submit as independent_submit
        return independent_submit(value,request_key,workspace,experimental=experimental,qualification=qualification)
    if isinstance(value,dict) and value.get('schema')=='adaptive-plan-v2':
        from .selection import submit as selected_submit
        return selected_submit(value,request_key,workspace,experimental=experimental,qualification=qualification)
    return _submit_v1(value,request_key,workspace,experimental=experimental,qualification=qualification)
