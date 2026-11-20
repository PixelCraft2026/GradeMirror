"""Short-lived control process; all public operations use the original APIs."""
import time
from pathlib import Path
from ..common import atomic_json,strict_load
from ..cpu_replay import api
from ..cpu_replay.contract import BACKEND,binding
from .context import selected
from .intent import need

def run(payload, *, work=None, context=None):
    # The dry fixture supplies the context already verified by the same worker guard.
    c=context if context is not None else selected();need(c is not None,'APPLICATION_CONTROL_CONTEXT');action=payload['action'];kwargs=payload['arguments']
    native=c['schema'] in ('cpu-application-execution-v3','cpu-application-execution-v4') or c['schema']=='cpu-application-execution-v5' and c['TEST_ONLY'] is False
    if action=='probe':
        from .probe import run
        return run(c,kwargs)
    if action.startswith('parent_'):
        from .composite import control
        return control(c,action,kwargs)
    store=Path(c['workspace'])/'store'
    if action=='submit':
        if c['parent_operation'] is not None:
            from ..gm004a_operations import OperationStore
            parent,_=OperationStore(store).path(c['parent_operation']['job_id'],c['parent_operation']['attempt_id'])
            need(not (parent/'cancel.signal').exists(),'CANCELLED')
        from .intent import digest
        request=c['request'];recipe=request['recipe'] if request['operation']=='replay' else None
        directory=Path(recipe['directory']) if recipe else Path(c['output_root'])/('fast-recipe' if c['mode']=='fast' else 'fine-recipe')
        sem=dict(backend_id=BACKEND,adaptive_application=True,mode=c['mode'],TEST_ONLY=False if native else True,
            instance_context=binding(c['manifest_path']),executor_build=c['executor_build']['sha256'],
            source_path=c['source']['path'],source_sha256=c['source']['sha256'],output_root=c['output_root'],
            bundle_directory=str(directory),bundle_id=recipe['bundle_id'] if recipe else digest(dict(pending_recipe=c['id'])),
            math_id=recipe['math_id'] if recipe else digest(dict(pending_math=c['id'])),core_rows=256,
            application_intent=c['request'],adaptive_parent_operation=c['parent_operation'])
        if c['schema']=='cpu-application-execution-v4':
            fit=request['operation']=='fit';refine=request['operation']=='refine'
            sem.update(first_fit=fit,cpu_refine=refine,refine_prepare=refine and c['phase']!='fine_recovery',reference_path=c['reference']['path'],reference_sha256=c['reference']['sha256'],parent_bundle=None if fit else request['recipe']['directory'])
            from .portable_contract import enabled as portable_enabled
            if portable_enabled(c):
                from .portable_context import semantics as portable_semantics
                portable_semantics(c,sem)
            from .calibration_contract import measurement
            if measurement(c):
                from .calibration_context import semantics
                semantics(c,sem)
        result=api._start(store,sem,kwargs['request_key'],c)
        from ..cpu_recipe.validation import complete
        if c['schema']=='cpu-application-execution-v4' and not (directory/'COMPLETE.json').is_file():return dict(result,workspace=c['workspace'],recipe=None,recipe_state='PENDING_OR_INTERRUPTED',lease_policy=c['lease_policy'])
        m,_=complete(directory)
        result=dict(result,workspace=c['workspace'],recipe=dict(directory=str(directory),bundle_id=m['bundle_id'],math_id=m['math_id'],version=m['version']))
        if c['schema'] in ('cpu-application-execution-v2','cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5'):result['lease_policy']=c['lease_policy']
        return result
    from ..gm004a_operations import OperationStore
    ops=OperationStore(store);path,spec=ops.path(kwargs['job_id'],kwargs['attempt_id'])
    need(spec['semantics']['instance_context']==binding(c['manifest_path']),'APPLICATION_OPERATION_IDENTITY')
    pair=(kwargs['job_id'],kwargs['attempt_id'])
    if native and action=='qualify':
        from .portable_contract import FINE_QUALIFICATION_ROWS, segmented_fine_qualification
        lease = kwargs['binding']
        from .portable_qualification import enabled as reuse_enabled, consume
        if reuse_enabled(c):
            need(work is not None,'GM064_R4_CONSUMER_WORK_REQUIRED')
            return consume(c,path,spec,lease,work)
        api.check_binding(path,spec,lease)
        if not segmented_fine_qualification(c):
            return api.call(path,'QUALIFY',lease)
        rows = []
        timings = []
        deadline = None
        for index, (alpha, bits) in enumerate(FINE_QUALIFICATION_ROWS):
            renewed = api.keep_alive(store,*pair,lease,deadline_monotonic=deadline)
            need(renewed.get('host_confirmed') is True and renewed.get('binding') == lease,
                 'CPU_QUALIFY_RENEWAL_NOT_CONFIRMED')
            if deadline is None:
                deadline = time.monotonic() + c['request_seconds']
            reply = api.call(path,'QUALIFY_PART',lease,index=index,deadline_monotonic=deadline)
            received = time.monotonic()
            need(received < deadline, 'CPU_QUALIFY_BATCH_DEADLINE')
            row = reply['row']
            need(row.get('alpha') == alpha and row.get('bits') == bits, 'CPU_QUALIFY_PART_REPLY')
            rows.append(row)
            timings.append(dict(reply['timing'], received=received, renewed_at=renewed['confirmed_at']))
        return dict(rows=rows, timing=timings, controller_deadline=deadline)
    if action=='status':
        state=ops.status(*pair);progress=path/'progress.json'
        detail=strict_load(progress) if state['state']=='RUNNING' and progress.is_file() else dict(stage=state['stage'],done=None,total=None)
        return dict(state,progress=detail)
    if action=='cancel':return ops.cancel(*pair)
    if action=='keep_alive':return api.keep_alive(store,*pair,kwargs['binding'])
    if action=='release':return api.release(store,*pair,kwargs['binding'])
    if action=='preview':return api.preview(store,*pair,kwargs['binding'],kwargs['destination'],alpha=kwargs['alpha'],request_id=kwargs['request_id'],max_side=1024 if native else 64)
    if action=='export':return api.export(store,*pair,kwargs['binding'],kwargs['destination'],alpha=kwargs['alpha'],format='jpeg',bit_depth=8,icc_policy='preserve_source',request_key=kwargs['request_key'],export_root=Path(kwargs['destination']).parent)
    raise ValueError('APPLICATION_CONTROL_ACTION')
