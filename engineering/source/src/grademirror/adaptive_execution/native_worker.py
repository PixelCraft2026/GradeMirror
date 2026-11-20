"""Exact session and role authority before any native preload or computation."""
from pathlib import Path
from .native_contract import strict_load
from ..cpu_replay.contract import need,binding
from .native_contract import authorize as qualification

def authorize(role,path,payload,c):
    if c.get('schema')=='cpu-application-execution-v6':
        from .low_memory_contract import admit
        return admit(c,'worker_start')
    import sys
    effective=role.removeprefix('entry_');category='numeric' if effective in ('r0','cpu') else 'io' if effective=='host' else 'control'
    need(Path(sys.executable).resolve()==Path(c['interpreters'][category]['path']).resolve(),'NATIVE_ROLE_INTERPRETER')
    path=Path(path).resolve();ws=Path(c['workspace']);q=qualification(c['qualification']['path'],c['request'],cleanup=role in ('owner','application_control'))
    if role.startswith('entry_'):
        need(q['phase']=='dry' and set(payload)=={'work','role','entry_only'} and payload['entry_only'] is True and payload['role']==role and path.is_relative_to(Path(c['output_root'])) and Path(payload['work'])==path.parent,'NATIVE_ENTRY_PAYLOAD')
        return
    if role=='application_control':
        fields={'submit':{'request_key'},'status':{'job_id','attempt_id'},'cancel':{'job_id','attempt_id'},'keep_alive':{'job_id','attempt_id','binding'},'release':{'job_id','attempt_id','binding'},'preview':{'job_id','attempt_id','binding','destination','alpha','request_id'},'export':{'job_id','attempt_id','binding','destination','alpha','request_key'},'qualify':{'job_id','attempt_id','binding'}}
        need(path.is_relative_to(ws/'.adaptive/control') and path.name=='payload.json' and set(payload)=={'action','arguments'} and payload['action'] in fields and type(payload['arguments'])is dict and set(payload['arguments'])==fields[payload['action']],'NATIVE_CONTROL_PAYLOAD')
        need(q['phase']=='native','NATIVE_DRY_ENTRY_NO_COMPUTE');return
    need(q['phase']=='native' and role in ('owner','host','r0','cpu'),'NATIVE_WORKER_PHASE_ROLE')
    session=path.parent.parent if role in ('r0','cpu') else path.parent
    ss=strict_load(session/'session-spec.json');spec=strict_load(session.parent/'spec.json')
    from .native_context import load,operation
    if c['schema']=='cpu-application-execution-v5':
        from .independent_context import operation
    need(operation(load(c['manifest_path']),spec['semantics'])==c and ss['execution']==c and ss['semantics']==spec['semantics'] and all(ss[k]==spec[k] for k in ('job_id','attempt_id')) and Path(ss['operation_path'])==session.parent,'NATIVE_SESSION_OPERATION')
    if role=='owner':need(path==session/'session-spec.json','NATIVE_OWNER_PAYLOAD');return
    if role=='host':need(path==session/'host-payload.json' and payload==dict(session=str(session)),'NATIVE_HOST_PAYLOAD');return
    from .native_science import authorize_worker
    authorize_worker(c,role,session.parent)
    need(strict_load(session/'compute-go.json')=={'bound':True},'NATIVE_COMPUTE_GO')
    maps=strict_load(session/'shared-mappings.json');shape=c['source_identity']['shape']
    if role=='r0':
        need(path==session/'R0/payload.json' and set(payload)=={'S','O','bundle','cancel','operation','work','reservation'} and payload['S']==dict(name=maps['S']['name'],shape=shape,dtype='uint8',readonly=True) and payload['O']==dict(name=maps['O']['name'],shape=shape,dtype='float32') and payload['bundle']==c['bundle']['directory'] and payload['operation']==str(session.parent) and payload['work']==str(path.parent) and payload['cancel']==str(session.parent/'cancel.signal') and payload['reservation']==dict(R0_render=1,forward=140,model=1,state_load=1),'NATIVE_R0_PAYLOAD')
    else:
        need(c['mode']=='fine' and path==session/'CPU/payload.json' and set(payload)=={'manifest','O_shared_memory','b_path','address','auth_hex','ranges'} and payload['O_shared_memory']==maps['O']['name'] and payload['b_path']==str(Path(c['bundle']['directory'])/'p3/b.npz'),'NATIVE_CPU_PAYLOAD')
        p3=strict_load(Path(c['bundle']['directory'])/'manifest.json')['mathematical']['p3']
        need(payload['ranges']==p3['ranges'] and payload['manifest']['O']['shape']==shape and payload['manifest']['core_rows']==256 and payload['manifest']['reference_wh']==p3['reference_wh'],'NATIVE_CPU_FIXED_MATH')
