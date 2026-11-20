"""Fine-first parent/children over the existing OperationStore journal and lock."""
from pathlib import Path
from ..common import atomic_json,strict_load
from ..store import locked
from ..gm004a_operations import OperationStore,_journal,_append
from ..cpu_replay.contract import binding,verify
from .intent import need,digest

def parent_sem(c):
    return dict(backend_id='adaptive-application-parent-v1',adaptive_parent=True,
        instance_context=binding(c['manifest_path']),executor_build=c['executor_build']['sha256'],
        output_root=c['output_root'],source_sha256=c['source']['sha256'],application_intent=c['request'])

def begin(c,key):
    ops=OperationStore(Path(c['workspace'])/'store');path,spec,prior=ops.begin('fit',parent_sem(c),key)
    if not prior['idempotent_hit']:
        ops.transition(spec['job_id'],spec['attempt_id'],'RUNNING','fast_prepare','ADAPTIVE_PARENT_ACCEPTED')
        atomic_json(path/'children.json',dict(children=[]))
    return dict(recover(ops,path,spec),idempotent_hit=prior['idempotent_hit'])

def attach(c,pair,child_context,child_key,stage):
    from .context import load
    ops=OperationStore(Path(c['workspace'])/'store');path,spec=ops.path(*pair);child=load(child_context)
    need(child['parent_operation']==dict(job_id=pair[0],attempt_id=pair[1]) and
        child['workspace']==c['workspace'] and stage in ('fast','fine'),'ADAPTIVE_CHILD_PARENT')
    with locked(path/'record.lock'):
        value=strict_load(path/'children.json');rows=value['children']
        need(not (path/'cancel.signal').exists(),'CANCELLED')
        need(len(rows)==(0 if stage=='fast' else 1),'ADAPTIVE_STAGE_ORDER')
        if stage=='fine':need((path/'fast-committed.json').is_file(),'ADAPTIVE_FAST_NOT_COMMITTED')
        rows.append(dict(stage=stage,context=binding(child_context),request_key=child_key))
        atomic_json(path/'children.json',dict(children=rows))
    return dict(attached=True,stage=stage)

def child_rows(ops,path,spec):
    rows=[]
    for row in strict_load(path/'children.json')['children']:
        verify(row['context']);c=strict_load(Path(row['context']['path']))
        need(c['parent_operation']==dict(job_id=spec['job_id'],attempt_id=spec['attempt_id']),'ADAPTIVE_CHILD_IDENTITY')
        found=ops.lookup_request(row['request_key'])
        if found is None:rows.append(dict(row,state='NOT_STARTED',job_id=None,attempt_id=None));continue
        p,s=found;need(s['semantics']['instance_context']==row['context'] and
            s['semantics'].get('adaptive_parent_operation')==c['parent_operation'],'ADAPTIVE_CHILD_SPEC')
        state=ops.status(s['job_id'],s['attempt_id'])
        rows.append(dict(row,**{k:state.get(k) for k in ('job_id','attempt_id','state','resident','binding','availability','released','recipe_available','recipe_directory','recipe_bundle_id','recipe_math_id','error_code')}))
    return rows

def recover(ops,path,spec,*,cancel=False,expected_child=None):
    # Child waits/cancellation run outside the parent record lock.
    rows=child_rows(ops,path,spec)
    if cancel:
        with locked(path/'record.lock'):
            current_rows=strict_load(path/'children.json')['children']
            last=current_rows[-1] if current_rows else None
            found=ops.lookup_request(last['request_key']) if last else None
            if found:
                verify(last['context'])
                need(found[1]['semantics']['instance_context']==last['context'] and
                    found[1]['semantics']['adaptive_parent_operation']==dict(job_id=spec['job_id'],attempt_id=spec['attempt_id']),'ADAPTIVE_CHILD_IDENTITY')
            address=dict(job_id=found[1]['job_id'],attempt_id=found[1]['attempt_id']) if found else None
            if address!=expected_child:
                current=dict(_journal(path)[-1]['snapshot'])
                return dict(current,cancel_disposition='STALE_CHILD_NOT_CANCELLED',expected_child=expected_child,current_child=address,no_cancel_signal_written=True)
            atomic_json(path/'cancel.signal',dict(job_id=spec['job_id'],attempt_id=spec['attempt_id']))
        if found:ops.cancel(address['job_id'],address['attempt_id'])
        rows=child_rows(ops,path,spec)
    recipes=[]
    for row in rows:
        if row.get('recipe_available'):
            recipes.append(dict(stage=row['stage'],directory=row['recipe_directory'],bundle_id=row['recipe_bundle_id'],math_id=row['recipe_math_id']))
    with locked(path/'record.lock'):
        current=dict(_journal(path)[-1]['snapshot']);last=rows[-1] if rows else None
        if recipes:
            for recipe in recipes:
                mark=path/(recipe['stage']+'-committed.json')
                if not mark.exists():atomic_json(mark,dict(recipe=recipe,child=last if recipe['stage']=='fine' else rows[0]))
        succeeded=bool(last and last['stage']=='fine' and last['state']=='SUCCEEDED')
        failed=bool(last and last['state']=='FAILED');cancelled=(path/'cancel.signal').exists() or bool(last and last['state']=='CANCELLED')
        state='SUCCEEDED' if succeeded else 'CANCELLED' if cancelled else 'FAILED' if failed else 'RUNNING'
        if state!=current['state']:
            current.update(state=state,stage='terminal' if state!='RUNNING' else (last['stage'] if last else 'fast_prepare'),
                exit_code=0 if succeeded else 8 if cancelled else 9 if failed else None,error_code=last.get('error_code') if failed else None)
            _append(path,current,'ADAPTIVE_PARENT_RECONCILED')
        resident=bool(succeeded and last.get('resident'))
        return dict(current,children=rows,preserved_recipes=recipes,persistent_complete=succeeded,
            released=state in ('SUCCEEDED','FAILED','CANCELLED') and all(row.get('released') is True or row['state']=='NOT_STARTED' for row in rows),
            resident=resident,binding=last.get('binding') if resident else None,
            availability='RESIDENT' if resident else 'REPLAY_REQUIRED' if recipes else 'NOT_COMMITTED',
            active_child=dict(job_id=last['job_id'],attempt_id=last['attempt_id']) if last and last['job_id'] else None,
            no_automatic_fit_or_CG_recovery=True)

def control(c,action,arguments):
    if action=='parent_begin':return begin(c,arguments['request_key'])
    pair=(arguments['job_id'],arguments['attempt_id'])
    if action=='parent_attach':return attach(c,pair,arguments['child_context'],arguments['child_key'],arguments['stage'])
    ops=OperationStore(Path(c['workspace'])/'store');path,spec=ops.path(*pair)
    need(spec['semantics']['instance_context']==binding(c['manifest_path']),'ADAPTIVE_PARENT_CONTEXT')
    return recover(ops,path,spec,cancel=action=='parent_cancel',expected_child=arguments.get('expected_child'))
