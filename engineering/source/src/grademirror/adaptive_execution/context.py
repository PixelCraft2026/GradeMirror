"""Private application workspace registration, not historical task identities."""
import os
from pathlib import Path
from ..common import strict_load,atomic_json,reject_reparse
from ..cpu_replay.contract import binding,verify,FLAGS
from .build import current_closure as executor_closure
from .intent import need,digest
SCHEMA='cpu-application-execution-v2'
VERSIONS=('cpu-application-execution-v1',SCHEMA)

def selected(*,cleanup=False):
    from ..cpu_first_fit.contract import _active
    current=_active.get()
    if current is not None and current.get('schema') in VERSIONS+('cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5'):return load(current['manifest_path'],cleanup=cleanup)
    value=os.environ.get('GM055_CONTEXT');return load(value,cleanup=cleanup) if value else None

def load(path,*,cleanup=False):
    from .portable_contract import in_session_base
    if in_session_base(path):
        from .portable_context import load as portable_load
        return portable_load(path,cleanup=cleanup)
    path=Path(path).resolve();reject_reparse(path)
    need(path.parent.name=='contexts' and path.parent.parent.name=='.adaptive','APPLICATION_CONTEXT_POINTER')
    from .calibration_contract import W as CALIBRATION_W
    if path.is_relative_to(CALIBRATION_W):
        from .calibration_context import load as calibration_load
        return calibration_load(path,cleanup=cleanup)
    from .first_contract import W as FIRST_W,SCHEMA as FIRST_SCHEMA
    from .independent_contract import W as INDEPENDENT_W,SCHEMA as INDEPENDENT_SCHEMA
    if path.is_relative_to(INDEPENDENT_W) and strict_load(path).get('schema')==INDEPENDENT_SCHEMA:
        from .independent_context import load as independent_load
        return independent_load(path,cleanup=cleanup)
    if path.is_relative_to(FIRST_W) and strict_load(path).get('schema')==FIRST_SCHEMA:
        from .first_context import load as first_load
        return first_load(path,cleanup=cleanup)
    from .native_contract import W
    if path.is_relative_to(W) and strict_load(path).get('schema')=='cpu-application-execution-v3':
        from .native_context import load as native_load
        return native_load(path,cleanup=cleanup)
    from ..cpu_first_fit.contract import _active
    current=_active.get()
    if current is not None and current.get('schema') in VERSIONS and Path(current['manifest_path'])==path:
        from ..common import canonical_json,sha_bytes
        actual=binding(path);index=strict_load(path.parent.parent/'context-index.json')
        raw=canonical_json(current)+b'\n'
        need(actual['sha256']==sha_bytes(raw) and actual['bytes']==len(raw) and index.get(current['id'])==actual,'APPLICATION_ACTIVE_CONTEXT_CHANGED')
        verify(current['qualification'])
        from .qualification import factory_for
        registered=strict_load(factory_for(current['qualification']['path']).with_name('factory-index.json'))
        need(registered.get(current['qualification']['path'])==current['qualification'],'APPLICATION_ACTIVE_QUALIFICATION_CHANGED')
        if 'resource_selection' in current:
            verify(current['resource_selection'])
            from .selection import validate_binding
            validate_binding(strict_load(Path(current['resource_selection']['path'])),current['request'])
        return current
    c=strict_load(path);index=strict_load(path.parent.parent/'context-index.json')
    need(type(c) is dict and c.get('schema') in VERSIONS and index.get(c['id'])==binding(path) and
        c['manifest_path']==str(path) and c['flags']==FLAGS,'APPLICATION_CONTEXT_REGISTRATION')
    from .qualification import authorize
    q=authorize(c['qualification']['path'],c['request'],cleanup=cleanup)
    expected=set(q['frame_context'])|{'schema','id','phase','mode','TEST_ONLY','metadata_only','workspace','manifest_path','request',
        'source','reference','source_identity','reference_identity','executor_build','output_root','evidence_root','pool_root',
        'qualification','flags','fault_point','fault_action','pause_at','prepared_clock_shift','lease_seconds','request_seconds',
        'force_grace_seconds','bundle','paths','parent_operation'}
    if c['schema']==SCHEMA:expected.add('lease_policy')
    if 'resource_selection' in c:
        expected.add('resource_selection');verify(c['resource_selection'])
        from .selection import validate_binding
        row=validate_binding(strict_load(Path(c['resource_selection']['path'])),c['request'])
        need(row['qualification']==c['qualification'],'RESOURCE_SELECTION_QUALIFICATION_CHANGED')
    need(c['schema']==q.get('context_version','cpu-application-execution-v1'),'APPLICATION_CONTEXT_VERSION')
    if c['schema']==SCHEMA:
        from .residency import POLICY
        need(c['lease_policy']==q['lease_policy']==POLICY,'APPLICATION_LEASE_POLICY')
    need(set(c)==expected and c['source_identity']==q['source_identity'] and c['reference_identity']==q['reference_identity'] and
        all(c[k]==v for k,v in q['frame_context'].items()),'APPLICATION_CONTEXT_FIELDS')
    need(c['qualification']==binding(q['manifest_path']) and c['TEST_ONLY'] is True and c['metadata_only'] is True and
        c['phase']=='frame' and c['mode'] in ('fast','fine') and c['source']==c['request']['source']['file'],
        'APPLICATION_CONTEXT_AUTHORITY')
    workspace=path.parent.parent.parent
    expected_paths=dict(store=str(workspace/'store'),operations=c['output_root'],exports=str(workspace/'exports'),
        pool=str(workspace/'pool'),logs=c['evidence_root'],temporary=str(Path(c['output_root'])/'tmp'),recipes=c['output_root'])
    need(c['paths']==expected_paths and c['bundle'] is None,'APPLICATION_CONTEXT_PATH_FIELDS')
    need(Path(c['output_root']).resolve().is_relative_to(workspace/'operations') and
        Path(c['evidence_root']).resolve().is_relative_to(workspace/'.adaptive') and
        Path(c['pool_root']).resolve()==workspace/'pool' and c['workspace']==str(workspace),'APPLICATION_CONTEXT_SCOPE')
    need(cleanup or c['executor_build']==executor_closure(),'APPLICATION_EXECUTOR_CHANGED')
    need(type(c['lease_seconds']) is int and 1<=c['lease_seconds']<=120 and c['request_seconds']==120 and
        c['force_grace_seconds']==30 and c['fault_action'] in ('raise','exit') and
        c['fault_point'] in q['fault_points'] and c['pause_at'] in ('','before_ready','before_compute','prepared') and
        c['prepared_clock_shift'] in (0,7000),'APPLICATION_CONTEXT_LIMIT')
    return c

def context(spec,*,cleanup=False):
    sem=spec.get('semantics',spec);verify(sem['instance_context']);c=load(sem['instance_context']['path'],cleanup=cleanup)
    if c['schema']=='cpu-application-execution-v5':
        from .independent_context import operation
        return operation(c,sem)
    if c['schema']=='cpu-application-execution-v4':
        from .first_context import operation
        return operation(c,sem)
    if c['schema']=='cpu-application-execution-v3':
        from .native_context import operation
        return operation(c,sem)
    need(sem.get('adaptive_application') is True and sem['executor_build']==c['executor_build']['sha256'] and
        sem['source_sha256']==c['source']['sha256'] and sem['output_root']==c['output_root'] and
        sem['mode']==c['mode'] and sem['TEST_ONLY'] is True and
        sem.get('adaptive_parent_operation')==c['parent_operation'],'APPLICATION_OPERATION_CONTEXT')
    return c

def register(workspace,request,qualification,identifier,mode,*,fault='',pause='',parent=None,resource_selection=None):
    from .portable_contract import pointer as portable_pointer
    if portable_pointer(qualification):
        from .portable_context import register as portable_register
        return portable_register(workspace,request,qualification,identifier,mode,fault=fault,pause=pause,parent=parent,resource_selection=resource_selection)
    from .calibration_contract import pointer as calibration_pointer
    if calibration_pointer(qualification):
        from .calibration_context import register as calibration_register
        return calibration_register(workspace,request,qualification,identifier,mode,fault=fault,pause=pause,parent=parent,resource_selection=resource_selection)
    from .independent_contract import pointer as independent_pointer
    if independent_pointer(qualification):
        from .independent_context import register as independent_register
        return independent_register(workspace,request,qualification,identifier,mode,fault=fault,pause=pause,parent=parent,resource_selection=resource_selection)
    from .first_contract import pointer as first_pointer
    if first_pointer(qualification):
        from .first_context import register as first_register
        return first_register(workspace,request,qualification,identifier,mode,fault=fault,pause=pause,parent=parent)
    from .native_contract import pointer
    if pointer(qualification):
        from .native_context import register as native_register
        return native_register(workspace,request,qualification,identifier,mode,fault=fault,pause=pause,parent=parent)
    from .qualification import authorize
    q=authorize(qualification,request);workspace=Path(workspace).resolve();reject_reparse(workspace)
    need(workspace.is_relative_to(Path(q['workspace_root'])),'QUALIFICATION_WORKSPACE_SCOPE')
    registry=workspace/'.adaptive';out=workspace/'operations'/identifier;out.mkdir(parents=True)
    manifest=registry/'contexts'/(identifier+'.json')
    base=q['frame_context'];c=dict(base,schema=q.get('context_version','cpu-application-execution-v1'),id=identifier,phase='frame',mode=mode,TEST_ONLY=True,
        metadata_only=True,workspace=str(workspace),manifest_path=str(manifest),request=request,
        source=request['source']['file'],reference=request['reference']['file'] if request['reference'] else None,
        source_identity=q['source_identity'],reference_identity=q['reference_identity'],
        executor_build=executor_closure(),output_root=str(out),evidence_root=str(registry/'logs'/identifier),
        pool_root=str(workspace/'pool'),qualification=binding(qualification),flags=FLAGS,
        fault_point=fault,fault_action='exit',pause_at=pause,prepared_clock_shift=0,
        lease_seconds=120,request_seconds=120,force_grace_seconds=30,bundle=None,parent_operation=parent)
    if resource_selection is not None:
        verify(resource_selection)
        from .selection import validate_binding
        row=validate_binding(strict_load(Path(resource_selection['path'])),request)
        need(row['qualification']==binding(qualification),'RESOURCE_SELECTION_QUALIFICATION_CHANGED')
        c['resource_selection']=resource_selection
    if c['schema']==SCHEMA:c['lease_policy']=q['lease_policy']
    c['paths']=dict(store=str(workspace/'store'),operations=str(out),exports=str(workspace/'exports'),
        pool=str(workspace/'pool'),logs=str(registry/'logs'/identifier),temporary=str(out/'tmp'),recipes=str(out))
    atomic_json(manifest,c)
    from ..store import locked
    with locked(registry/'registry.lock'):
        index_path=registry/'context-index.json';index=strict_load(index_path) if index_path.exists() else {}
        need(identifier not in index,'APPLICATION_CONTEXT_REUSE');index[identifier]=binding(manifest);atomic_json(index_path,index)
    return c
