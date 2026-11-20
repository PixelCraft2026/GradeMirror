"""Validate the operation, private registry, actual session and owned mappings."""
from pathlib import Path
from ..common import strict_load
from ..cpu_replay.contract import binding
from .context import context
from .intent import need

def authorize(role,path,payload,c):
    if c.get('schema')=='cpu-application-execution-v6':
        from .low_memory_contract import admit
        return admit(c,'worker_start')
    if c.get('schema')=='cpu-application-execution-v4':
        from .first_worker import authorize as first_authorize
        return first_authorize(role,path,payload,c)
    if c.get('schema')=='cpu-application-execution-v3' or c.get('schema')=='cpu-application-execution-v5' and not c['TEST_ONLY']:
        from .native_worker import authorize as native_authorize
        return native_authorize(role,path,payload,c)
    path=Path(path).resolve();workspace=Path(c['workspace'])
    if role=='application_control':
        actions=('submit','status','cancel','preview','export','release','parent_begin','parent_attach','parent_status','parent_cancel','probe')
        from .residency import SCHEMA,POLICY
        if c.get('schema') in (SCHEMA,'cpu-application-execution-v5') and c.get('lease_policy')==POLICY and c.get('TEST_ONLY') is True and c.get('metadata_only') is True:
            actions=(*actions,'keep_alive')
        need(path.is_relative_to(workspace/'.adaptive/control') and path.name=='payload.json' and
            set(payload)=={'action','arguments'} and payload['action'] in actions,
            'APPLICATION_CONTROL_PAYLOAD')
        fields={'submit':{'request_key'},'parent_begin':{'request_key'},'status':{'job_id','attempt_id'},'cancel':{'job_id','attempt_id'},
            'keep_alive':{'job_id','attempt_id','binding'},'release':{'job_id','attempt_id','binding'},'preview':{'job_id','attempt_id','binding','destination','alpha','request_id'},
            'export':{'job_id','attempt_id','binding','destination','alpha','request_key'},'parent_status':{'job_id','attempt_id'},
            'parent_cancel':{'job_id','attempt_id','expected_child'},'parent_attach':{'job_id','attempt_id','child_context','child_key','stage'},'probe':{'kind'}}
        keys=set(payload['arguments']) if type(payload['arguments']) is dict else None
        need(keys==fields[payload['action']] or payload['action']=='cancel' and keys==fields['cancel']|{'expected_child'},'APPLICATION_CONTROL_ARGUMENT_FIELDS')
        return
    session=path.parent.parent if role=='frame_sender' else path.parent
    ss=strict_load(session/'session-spec.json');spec=strict_load(session.parent/'spec.json')
    actual=context(spec)
    need(actual==c and ss['execution']==c and ss['semantics']==spec['semantics'] and
        all(ss[k]==spec[k] for k in ('job_id','attempt_id')) and Path(ss['operation_path'])==session.parent,
        'APPLICATION_WORKER_SESSION')
    if role=='owner':need(path==session/'session-spec.json','APPLICATION_OWNER_PAYLOAD')
    elif role=='host':need(path==session/'host-payload.json' and payload==dict(session=str(session)), 'APPLICATION_HOST_PAYLOAD')
    else:
        need(role=='frame_sender' and path==session/'CPU/payload.json' and payload.get('TEST_ONLY') is True and
            payload['manifest']['O']['shape']==c['source_identity']['shape'] and max(payload['manifest']['O']['shape'][:2])<=64 and
            strict_load(session/'compute-go.json')['bound'] is True,'APPLICATION_FRAME_PAYLOAD')
        mappings=strict_load(session/'shared-mappings.json')
        need(payload['O_shared_memory']==mappings['O']['name'] and
            Path(payload['b_path'])==Path(spec['semantics']['bundle_directory'])/'p3/b.npz','APPLICATION_FRAME_MAPPING')
