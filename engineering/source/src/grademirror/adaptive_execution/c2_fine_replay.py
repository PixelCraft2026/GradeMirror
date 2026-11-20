"""Task-specific provenance and prepayment for one new GM064 fine replay."""
import os
import sys
import time
from pathlib import Path

from ..common import canonical_json
from ..cpu_replay.contract import binding, need, verify
from ..store import locked
from . import portable_contract as portable
from .c2_cpu_tail import QUALIFICATION, DRY_QUALIFICATION, QUALIFICATIONS
AUTHORITY = 'validation/t34r3-c2r-authority.json'
CONTROLLER = 'validation/t34r3-c2r-unit.py'
OLD_SESSION = '3a0e7df26bb1438197b6dfb40b3c640e'
OLD_CLAIM = 'c975cc03-1370-4e66-ae89-abf78e945d75'
JOB = '5963567d-482e-4e12-9362-94d7cbb52d5c'
ATTEMPT = '929a30a0-b179-4432-8d5d-f87ba711314d'
OLD_WORK = 'w/C4'
OP = f'{OLD_WORK}/store/gm004a-operations/{JOB}/{ATTEMPT}'
RECIPE = f'{OLD_WORK}/operations/53f13e31-c8f5-4712-ae14-d203771d926e/fine-recipe'
UNIT = 'T4'
KEY = 'GM064-T4-C2R'
FIRST_WALL = 1793788456.605
LAST_WALL = FIRST_WALL + 168 * 3600
MIN_START = 14850


def authority():
    path = portable.package_member(AUTHORITY)
    value = portable.read(path)
    need(set(value)=={'schema','old_session','new_session','old_claim','historical','recipe','science','goldens'}
         and value['schema']=='gm064-c2r-authority-v1'
         and value['old_session']==OLD_SESSION and value['old_claim']==OLD_CLAIM
         and value['new_session']!=OLD_SESSION and len(value['science'])==31, 'C2_REPLAY_AUTHORITY')
    return path,value


def validate_scope(q, root):
    path,value = authority()
    name = Path(q['manifest_path']).name
    mark = portable.read(root/'session.json')
    need(root.name==value['new_session'] and q['recovery']==binding(path)
         and mark.get('c2_fine')==q['recovery'] and mark['first_wall']==FIRST_WALL
         and mark['target_wall_seconds']==168*3600 and not mark.get('local_only')
         and q['domain']=='24mp' and q['request']['operation']=='replay' and q['request']['quality']=='fine'
         and q['allocations']=={'replay_fine':UNIT}
         and (name,q['phase']) in ((QUALIFICATION,'native'),(DRY_QUALIFICATION,'dry')),
         'C2_REPLAY_SCOPE')
    return value


def dispatch_scope(q,root,work,key):
    validate_scope(q,root)
    need(Path(q['manifest_path']).name==QUALIFICATION and work==root/'w/T4' and key==KEY,
         'C2_REPLAY_DISPATCH_SCOPE')


def _paths(root, records):
    return [dict(row,path=str(root/row['path'])) for row in records]


def historical_paths(root):
    _, value = authority()
    old=root.parent/OLD_SESSION
    return _paths(old,value['historical'])


def verify_prior(root):
    old=portable.ancestors(root.parent/OLD_SESSION)
    _,value=authority()
    for row in _paths(old,value['historical']):verify(row)
    pool=portable.read(old/OLD_WORK/'pool/reservation.json')
    status=portable.read(old/OP/'status.json')
    reclaim=portable.read(old/OP/'reclaim-receipt.json')
    wait=portable.read(old/'runs/C3/T4/managed/wait.json')
    from ..winproc import identity_alive
    need(pool['state']=='FREE' and status['state']=='FAILED'
         and (status['job_id'],status['attempt_id'])==(JOB,ATTEMPT)
         and reclaim['tree_empty'] is True and reclaim['mappings']
         and all(item['cannot_reopen'] is True for item in reclaim['mappings'])
         and wait['root_exit']==1 and wait['exact'] and wait['actual_all_wait'] and wait['tree_empty']
         and len(wait['records'])==wait['actual_members']==16
         and all(row['actual_handle_wait'] is True and row['job_member_verified'] is True
                 and identity_alive(row) is False for row in wait['records']), 'C2_REPLAY_PRIOR_WAIT')
    import json
    events=[json.loads(line) for line in (old/'science/events.jsonl').read_bytes().splitlines()]
    old_t4=[row for row in events if row['allocation']=='T4']
    need([row['kind'] for row in old_t4 if row['kind'] in ('claim','claim_continued')]==['claim','claim_continued']
         and all(row['reservation']==OLD_CLAIM for row in old_t4)
         and any(row['kind']=='component_started' and row['component']=='CPU' for row in old_t4),
         'C2_REPLAY_PRIOR_CONSUMPTION')
    return old


def validate_saved_parent(pkg,q,m,root):
    value=validate_scope(q,root)
    old=portable.ancestors(root.parent/OLD_SESSION)
    recipe=portable.ancestors(old/RECIPE)
    files=_paths(recipe,value['recipe'])
    frozen={row['path']:row for row in _paths(old,value['historical'])}
    expected_producer={key:frozen[str(old/OP/leaf)] for key,leaf in (
        ('spec','spec.json'),('failure','error.json'),('reclaim','reclaim-receipt.json'))}
    for row in expected_producer.values():verify(row)
    spec=portable.read(expected_producer['spec']['path'])
    sem=spec['semantics']
    need(pkg['directory']==str(recipe) and pkg['files']==files and pkg['producer']==expected_producer
         and m['version']==3 and m['mode']=='fine' and m['math_id']==value['goldens']['math_id']
         and m['bundle_id']==value['goldens']['bundle_id']
         and m['provenance']['job_id']==JOB and m['provenance']['attempt_id']==ATTEMPT
         and spec['job_id']==JOB and spec['attempt_id']==ATTEMPT
         and sem['bundle_directory']==str(recipe) and sem['refine_prepare'] is True
         and sem['TEST_ONLY'] is False and m['producer_build']['sha256']==sem['executor_build']
         and sem['source_sha256']==q['source']['sha256'] and sem['reference_sha256']==q['reference']['sha256']
         and m['qualification']['status']=='FINE_GENERATED_PENDING_SCOPED_QUALIFICATION'
         and not any(m['qualification'][flag] for flag in ('execution_qualified','CPU_ENABLED','AUTO_ENABLED','PRODUCTION_ENABLED')),
         'C2_REPLAY_SAVED_FINE_BINDING')
    # The immutable file list is shared by the qualification, guard and reader.
    return True


def window(root, now, minimum=0):
    qpath=root/'qualifications'/QUALIFICATION
    q=portable.read(qpath)
    validate_scope(q,root)
    path=portable.ancestors(root/'science/t34r3-c2r-window.json')
    row=portable.read(path)
    need(set(row)=={'schema','authority','qualification','nonce','started_at','deadline_at','p0'}
         and row['schema']=='gm064-c2r-window-v1' and row['authority']==q['recovery']
         and row['qualification']==binding(qpath) and row['p0']==q['p0']
         and row['nonce']==portable.read(root/'session.json')['c2_nonce']
         and FIRST_WALL<=row['started_at']<=now<=row['deadline_at']
         and row['deadline_at']==row['started_at']+28800
         and min(LAST_WALL,row['deadline_at'])-now>=minimum, 'C2_REPLAY_WINDOW')
    return min(LAST_WALL,row['deadline_at'])


def _new(path, value):
    with path.open('xb') as stream:
        stream.write(canonical_json(value)+b'\n')
        stream.flush()
        os.fsync(stream.fileno())


def _dispatch_binding(c):
    root=portable.e(c)
    return dict(request_key=KEY,context=binding(c['manifest_path']),
                qualification=c['qualification'],window=binding(root/'science/t34r3-c2r-window.json'))


def verify_dispatch(c):
    root=portable.e(c)
    path=portable.ancestors(root/'science/t34r3-c2r-dispatch.json')
    need(portable.read(path)==_dispatch_binding(c),'C2_REPLAY_DISPATCH_BINDING')
    return binding(path)


def _result_operation(c,result):
    from ..gm004a_operations import OperationStore
    found=OperationStore(Path(c['workspace'])/'store',read_only=True).lookup_request(KEY,saved_only=True)
    need(found is not None,'C2_REPLAY_RESULT_OPERATION')
    op,spec=found
    need(spec['semantics']['instance_context']==binding(c['manifest_path']) and
         all(result[k]==spec[k] for k in ('job_id','attempt_id')),'C2_REPLAY_RESULT_CONTEXT')
    return op,spec


def before_dispatch(c):
    from ..cpu_replay.api import check_binding
    root=portable.e(c)
    ticket=portable.ancestors(root/'science/t34r3-c2r-dispatch.json')
    result=portable.ancestors(root/'science/t34r3-c2r-result.json')
    with locked(root/'science/ledger.lock'):
        if ticket.exists():
            proof=verify_dispatch(c)
            need(result.is_file(),'C2_REPLAY_DISPATCH_ALREADY_STARTED')
            saved=portable.read(result)
            op,spec=_result_operation(c,saved['result'])
            need(saved['dispatch']==proof and saved['commit']==binding(op/'commit.json'),'C2_REPLAY_RESULT_CHANGED')
            # The public controller is not a context worker. Use the same
            # exact native closure as the already-authorized qualification.
            check_binding(op,spec,saved['result']['binding'],portable_native=True)
            return dict(saved['result'],idempotent_hit=True)
        need(not result.exists(),'C2_REPLAY_RESULT_WITHOUT_DISPATCH')
        main=getattr(sys.modules['__main__'],'__file__',None)
        need(main is not None and binding(main)==binding(portable.package_member(CONTROLLER)),
             'C2_REPLAY_CONTROLLER_MAIN')
        window(root,time.time(),MIN_START)
        need((root/'science/events.jsonl').read_bytes()==b'','C2_REPLAY_TICKET_ALREADY_USED')
        _new(ticket,_dispatch_binding(c))
    return None


def remember(c,result):
    if result.get('state')=='SUCCEEDED' and result.get('resident') is True:
        op,_=_result_operation(c,result)
        _new(portable.e(c)/'science/t34r3-c2r-result.json',dict(
            dispatch=verify_dispatch(c),commit=binding(op/'commit.json'),result=result))


def reserve_unit(root,q):
    from .portable_science import budget
    verify_prior(root)
    auth=validate_scope(q,root)
    window(root,time.time(),MIN_START)
    cap=auth['science']
    nonzero={key:value for key,value in cap.items() if value}
    need(nonzero==budget('replay_fine',q),'C2_REPLAY_CAP')
    p=root/'science-budget.jsonl'
    with locked(root/'science-budget.lock'):
        need(p.read_bytes()==b'' and (root/'science/events.jsonl').read_bytes()==b'', 'C2_REPLAY_TICKET_ALREADY_USED')
        row=dict(kind='unit_reserve',id=UNIT,domain='24mp',phase='replay_fine',
                 before_any_science_or_IO=True,no_refund=True,budget=nonzero,full_31_counters=cap,
                 historical_book=binding(root.parent/OLD_SESSION/'science-budget.jsonl'),
                 prior_claim=OLD_CLAIM,authority=q['recovery'],wall=time.time())
        with p.open('ab') as stream:
            stream.write(canonical_json(row)+b'\n');stream.flush();os.fsync(stream.fileno())
    return row
