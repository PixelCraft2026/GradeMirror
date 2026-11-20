"""One explicitly user-adopted, already exited qualification Job. No math."""
import json,math
from pathlib import Path
from ..common import strict_load
from ..cpu_replay.contract import need, binding, verify, FLAGS, ROOT, executor_closure
from .. import winproc as win
from .contract import E, context

Z=E/'closure-supplement'
AUTH=Z/'authorization.json'
SOURCE='USER_ADOPTED_EXACT_OUTER_JOB'
PROPOSAL_SHA='e97e0e8a5e964109100ec396847edb1728e3dd5022907354558c3fb310640fde'


def check_wait(raw,slots):
    need(type(raw) is dict and set(raw)=={'actual_root_wait','records','resources','actual_members','elapsed','actual_all_wait','error',
         'query_headers','root_exit','exact','tree_empty'},'OUTER_WAIT_FIELDS')
    need(type(raw['records']) is list and all(raw[k] is True for k in ('actual_root_wait','actual_all_wait','exact','tree_empty')) and
         type(raw['actual_members']) is int and raw['actual_members']==len(raw['records']) and 0<raw['actual_members']<=slots,
         'OUTER_WAIT_COMPLETENESS')
    need(type(raw['elapsed']) in (int,float) and math.isfinite(raw['elapsed']) and 0<=raw['elapsed']<=1810 and
         type(raw['resources']) is list and bool(raw['resources']),'OUTER_RESOURCE_UNKNOWN')
    for sample in raw['resources']:
        need(set(sample)=={'elapsed','RSS','private','physical','commit'} and
             all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in sample.values()) and
             max(sample['RSS'],sample['private'])<=6*1024**3 and min(sample['physical'],sample['commit'])>=1024**3,'OUTER_RESOURCE_UNKNOWN_OR_LIMIT')
    known={};pids=set()
    for rec in raw['records']:
        need(type(rec) is dict and set(rec)=={'pid','creation_time','image_path','parent_pid','parent_query_success','command_line',
             'command_query','job_member_verified','actual_handle_wait','exit_code'},'OUTER_MEMBER_FIELDS')
        key=win.identity_key(rec)
        need(key is not None and key not in known and rec['pid'] not in pids and
             all(rec[k] is True for k in ('parent_query_success','job_member_verified','actual_handle_wait')) and
             type(rec['exit_code']) is int and rec['exit_code']>=0 and type(rec['parent_pid']) is int and
             type(rec['command_line']) is str and bool(rec['command_line']) and rec['command_query']=='NTSTATUS=0',
             'OUTER_MEMBER_IDENTITY_OR_WAIT')
        known[key]=rec;pids.add(rec['pid'])
    root=raw['records'][0];parents={r['pid']:r['parent_pid'] for r in raw['records']}
    need(root['parent_pid'] not in pids,'OUTER_ROOT_PARENT')
    for rec in raw['records'][1:]:
        current=rec['pid'];seen=set()
        while current!=root['pid']:
            need(current not in seen and current in parents,'OUTER_PARENT_GRAPH')
            seen.add(current);current=parents[current]
    need(bool(raw['query_headers']),'OUTER_QUERY_MISSING')
    for query in raw['query_headers']:
        need(type(query) is dict and set(query)=={'reported','listed','capacity','query_success','native_error','utc_ticks','returned_pids','decision'},'OUTER_QUERY_FIELDS')
        need(query['query_success'] is True and query['native_error']==0 and
             all(type(query[k]) is int and query[k]>=0 for k in ('reported','listed','capacity','utc_ticks')) and
             query['reported']==query['listed']==len(query['returned_pids'])<=query['capacity'] and
             len(set(query['returned_pids']))==len(query['returned_pids']) and
             all(type(p) is int and p in pids for p in query['returned_pids']) and
             query['decision']=='COMPLETE_LIST_ORIGINAL_GATES_APPLY','OUTER_QUERY_INCOMPLETE')
    return known


def _archive_verify(item,archive_map):
    for row in archive_map['files']:
        if row['original']==item:
            verify(row['archive'])
            need((row['archive']['bytes'],row['archive']['sha256'])==(item['bytes'],item['sha256']),'OUTER_ARCHIVE_BYTES')
            return strict_load(Path(row['archive']['path']))
    need(False,'OUTER_ARCHIVE_MISSING')


def evidence(*,require_gate=True):
    auth=strict_load(AUTH)
    need(type(auth) is dict and set(auth)=={'schema','approved','question_id','user_authorization','proposal','old_failure_seal','record',
         'archive_map','science','native_IO','first_wall','engineering_max','closure_max'},'OUTER_ADOPTION_FIELDS')
    need(auth['schema']=='gm053-exact-outer-cleanup-adoption-v1' and auth['approved'] is True and
         auth['question_id']=='call_c320f4b65bf84a21bd1e6612f53be687' and auth['first_wall']==1793329316 and
         auth['engineering_max']==auth['closure_max']==32 and auth['proposal']['sha256']==PROPOSAL_SHA,'OUTER_ADOPTION_NOT_APPROVED')
    for key in ('proposal','old_failure_seal','record','archive_map','science','native_IO'):
        need(Path(auth[key]['path']).resolve().is_relative_to(E) or key=='native_IO','OUTER_ADOPTION_PATH')
        verify(auth[key])
    proposal=strict_load(Path(auth['proposal']['path']));record=strict_load(Path(auth['record']['path']))
    need(set(record)=={'schema','operation','outer_wait','outer_plan','outer_config','prepayment_prefix','identity_sources','mapping_manifest'} and
         record['schema']=='gm053-exact-outer-exit-input-v1' and record['operation']==proposal['operation'] and
         record['outer_wait']==proposal['original_outer_wait'] and record['outer_plan']==proposal['original_outer_plan'] and
         record['outer_config']==proposal['original_frozen_config'] and record['prepayment_prefix']==proposal['prepayment_prefix'],
         'OUTER_RECORD_SCOPE')
    op=record['operation'];path=Path(op['path']).resolve();session=path/'session'
    need(Path(auth['native_IO']['path']).resolve()==session/'io-counts.json','OUTER_IO_POINTER')
    verify(op['spec']);verify(op['context']);spec=strict_load(Path(op['spec']['path']));ctx=context(spec,cleanup=True)
    need(path==Path(op['spec']['path']).resolve().parent and spec['job_id']==op['job_id'] and spec['attempt_id']==op['attempt_id'] and
         spec['semantics']['instance_context']==op['context'] and ctx['TEST_ONLY'] is False and ctx['phase']=='render' and ctx['flags']==FLAGS and
         all(v is False for v in FLAGS.values()) and spec['semantics']['executor_build']==proposal['native_execution_build'],'OUTER_OPERATION_SCOPE')
    ss=strict_load(session/'session-spec.json')
    need(ss['session_id']==op['session_id'] and ss['pool_token']==op['pool_token'] and ss['semantics']==spec['semantics'],'OUTER_SESSION_TOKEN')
    for key in ('outer_wait','outer_plan','outer_config','prepayment_prefix','mapping_manifest'):verify(record[key])
    plan=strict_load(Path(record['outer_plan']['path']));cfg=strict_load(Path(record['outer_config']['path']))
    need(plan['config']==record['outer_config'] and plan['role']=='P2B' and plan['before_CreateProcess'] is True and
         plan['slots']==48 and cfg['version']=='v19' and cfg['first_wall']==auth['first_wall'] and
         plan['physical']>=6*1024**3 and plan['commit']>=16*1024**3,'OUTER_FROZEN_PLAN')
    cfgdir=Path(record['outer_config']['path']).parent
    for row in cfg['inputs']:
        archived=cfgdir/Path(row['path']).resolve().relative_to(ROOT)
        actual=binding(archived);need((actual['bytes'],actual['sha256'])==(row['bytes'],row['sha256']),'OUTER_FROZEN_SOURCE')
    rows=[json.loads(v) for v in Path(record['prepayment_prefix']['path']).read_text('utf8').splitlines() if v]
    label=Path(record['outer_wait']['path']).parent.name
    prepaid=[r for r in rows if r['kind']=='reserve' and r['id']==label]
    finished=[r for r in rows if r['kind']=='finish' and r['id']==label]
    need(len(prepaid)==len(finished)==1 and prepaid[0]['before_CreateProcess'] is True and prepaid[0]['slots']==plan['slots'] and
         prepaid[0]['argv']==plan['argv'] and prepaid[0]['worker_argv']==plan['worker_argv'] and finished[0]['tree_empty'] is True and
         prepaid[0]['wall']<=finished[0]['wall'],'OUTER_PREPAYMENT')
    raw=strict_load(Path(record['outer_wait']['path']));known=check_wait(raw,plan['slots'])
    quoted=' '.join('"'+str(a).replace('"','\\"')+'"' for a in plan['argv'])
    need(raw['records'][0]['command_line']==quoted and raw['root_exit']==9 and 'WORKER_SOFT_STOP' in raw['error'],'OUTER_ROOT_OR_FAILURE_CHANGED')
    need(len(record['identity_sources'])==3,'OUTER_REGISTERED_IDENTITY_COUNT');identities=[]
    for item,name in zip(record['identity_sources'],('owner-launch.json','host-process.json','host-entry.json')):
        need(Path(item['path']).resolve()==session/name,'OUTER_IDENTITY_POINTER');verify(item);value=strict_load(Path(item['path']))
        who=value['identity'] if name=='owner-launch.json' else value
        if name=='owner-launch.json':need(value['session_id']==op['session_id'] and value['pool_token']==op['pool_token'],'OUTER_OWNER_LAUNCH')
        key=win.identity_key(who)
        need(key in known and known[key]['exit_code']==9 and win.identity_alive(who) is False,'OUTER_REGISTERED_IDENTITY_UNKNOWN')
        identities.append(dict(identity=who,exited=True,actual_handle_wait=True,proof=SOURCE))
    archive_map=strict_load(Path(auth['archive_map']['path']))
    before=_archive_verify(proposal['pool_before'],archive_map);lease=_archive_verify(proposal['lease_before'],archive_map)
    need(before['state']=='BOUND' and before['token']==op['pool_token'] and before['job_id']==op['job_id'] and before['attempt_id']==op['attempt_id'] and
         lease['state']=='RELEASE_PENDING' and lease['binding']['pool_token']==op['pool_token'],'OUTER_ORIGINAL_RESERVATION')
    mappings=strict_load(Path(record['mapping_manifest']['path']))
    need(Path(record['mapping_manifest']['path']).resolve()==session/'shared-mappings.json','OUTER_MAPPING_POINTER')
    names=sorted({v['name'] for v in mappings.values() if isinstance(v,dict) and 'name' in v})
    need(len(names)==2,'OUTER_MAPPING_COUNT')
    from multiprocessing.shared_memory import SharedMemory
    closed=[]
    for name in names:
        try:sm=SharedMemory(name=name,create=False)
        except FileNotFoundError:closed.append(dict(name=name,cannot_reopen=True))
        else:sm.close();need(False,'OUTER_MAPPING_STILL_OPEN')
    need(not (session/'owner-cleanup.json').exists(),'OUTER_NORMAL_RECEIPT_MUST_REMAIN_UNKNOWN')
    if require_gate:
        gate=strict_load(Z/'preflight-gate.json')
        need(set(gate)=={'schema','result','plan','entry','ack','prior_failed_wait'} and gate['schema']=='gm053-inline-metadata-preflight-v1','OUTER_GATE_FIELDS')
        for item in ('result','plan','entry','ack','prior_failed_wait'):verify(gate[item])
        result=strict_load(Path(gate['result']['path']));entry=strict_load(Path(gate['entry']['path']));ack=strict_load(Path(gate['ack']['path']))
        current_plan=strict_load(Path(gate['plan']['path']));verify(current_plan['config']);current_cfg=strict_load(Path(current_plan['config']['path']))
        need(current_plan['metadata_only'] is True and current_plan['role']=='P0C' and current_plan['slots']==8 and
             current_plan['before_CreateProcess'] is True and current_cfg['qualification_focus']=='outer-native-closure' and
             ack['actual_handle_retained'] is True and win.identity_key(ack['identity'])==win.identity_key(entry['identity']),'OUTER_METADATA_GATE_IDENTITY')
        for item in current_cfg['inputs']:verify(item)
        prior=strict_load(Path(gate['prior_failed_wait']['path']))
        need(all(prior[k] is True for k in ('actual_root_wait','actual_all_wait','tree_empty','exact')),'OUTER_PRIOR_PREFLIGHT_WAIT')
        root=Path(gate['plan']['path']).parent;live=win.identity_alive(entry['identity'])
        need(live is not None,'OUTER_PREFLIGHT_ROOT_UNKNOWN')
        if live:
            caller=win.current_identity();registered=win.identity_key(caller)==win.identity_key(entry['identity'])
            for file in (root/'entries').glob('*/entry.json'):
                value=strict_load(file)
                if win.identity_key(value['identity'])==win.identity_key(caller):
                    retained=strict_load(file.parent/'ack.json')
                    registered=retained['actual_handle_retained'] is True and win.identity_key(retained['identity'])==win.identity_key(caller)
            need(registered,'OUTER_UNREGISTERED_METADATA_CALLER')
        else:
            wait=strict_load(root/'wait.json')
            need(all(wait[k] is True for k in ('actual_root_wait','actual_all_wait','tree_empty','exact')) and wait['root_exit']==0 and
                 any(win.identity_key(r)==win.identity_key(entry['identity']) and r['actual_handle_wait'] is True for r in wait['records']),
                 'OUTER_PREFLIGHT_LATE_WAIT_REQUIRED')
        need(result['status']=='OUTER_EXIT_ZERO_SCIENCE_PASS' and result['adoption']==binding(AUTH) and
             result['closure_build']==executor_closure()['sha256'] and type(result['new_science']) is int and result['new_science']==0,
             'OUTER_ZERO_SCIENCE_GATE')
    return auth,record,spec,identities,closed


def adopted_proof(path,spec,token):
    if not spec['semantics'].get('instance_context') or not AUTH.is_file():return None
    auth=strict_load(AUTH);proposal=strict_load(Path(auth['proposal']['path']))
    if Path(proposal['operation']['path']).resolve()!=Path(path).resolve():return None
    auth,record,original,identities,mappings=evidence()
    need(spec==original and token==record['operation']['pool_token'],'OUTER_DIFFERENT_OPERATION_OR_TOKEN')
    return dict(schema='cpu-first-fit-adopted-outer-exit-v1',proof_source=SOURCE,token=token,job_id=spec['job_id'],attempt_id=spec['attempt_id'],
        session_path=str(Path(path)/'session'),no_child_started=False,actual_wait=True,tree_empty=True,identities=identities,mappings=mappings,
        adoption=binding(AUTH),record=auth['record'],forced=True,host_normal_tail=None,normal_owner_cleanup='UNKNOWN_MISSING_NOT_RECONSTRUCTED')


def verify_adopted(proof,spec,session,token):
    need(type(proof) is dict and proof.get('forced') is True and proof.get('host_normal_tail','MISSING') is None and
         all(r.get('actual_handle_wait') is True for r in proof.get('identities',[])) and
         all(r.get('cannot_reopen') is True for r in proof.get('mappings',[])),'OUTER_PROOF_FLAG_TYPES')
    need(proof==adopted_proof(Path(session).parent,spec,token),'OUTER_PROOF_FIELDS_OR_BYTES')
