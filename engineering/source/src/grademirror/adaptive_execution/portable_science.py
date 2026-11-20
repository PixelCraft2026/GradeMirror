"""GM064 task-local prepayment/claim hooks over original CPU execution."""
import os,time,uuid,json,hashlib
from pathlib import Path
from ..common import strict_load as _strict_load,atomic_json,canonical_json
from ..store import locked
from ..cpu_replay.contract import binding,verify,need
from . import portable_contract as k
from .portable_resources import stage

def strict_load(path):return _strict_load(Path(path))

COMPONENTS={
 'source':dict(source_decode=1),'reference':dict(reference_decode=1),
 'normalize_source':dict(normalizations=1,resizes=1),'normalize_reference':dict(normalizations=1,resizes=1),
 'fit':dict(fit=1,updates=1000,fit_forward=2001,forward=2001,Field=1),
 'R0':dict(R0_render=1,render_forward=96,forward=96,Field=1,state_load=1),
 'H':dict(area_H=1),'T':dict(target_T=1),'CG':dict(P3_geometry=1,scalar_CG=3),
 'range':dict(range_scan=1,P3_scalar_evaluations=72000000),
 'CPU':dict(CPU256_service=1,scalar_LLF_footprints=144,compositions=72,
            P3_scalar_evaluations=387220032,remap_pyramids=43176,D_calls=303408,U_calls=605640)}


def components(phase):
    return {'fit':('source','reference','normalize_source','normalize_reference','fit','R0'),
        'refine':('source','reference','R0','H','T','CG','range','CPU'),
        'fast_recovery':('source','R0'),'replay_fine':('source','R0','CPU')}[phase]


def counts(c):
    import copy,math
    table=copy.deepcopy(COMPONENTS);n=k.expected_tiles(c);updates=c['settings']['updates'];h,w,_=c['source_identity']['shape']
    table['R0'].update(render_forward=n,forward=n);table['fit'].update(updates=updates,fit_forward=2*updates+1,forward=2*updates+1)
    cores=(h+255)//256;table['CPU'].update(scalar_LLF_footprints=cores*6,compositions=cores*3)
    if h!=6000:table['CPU'].update(P3_scalar_evaluations=387220032,remap_pyramids=43176,D_calls=303408,U_calls=605640)
    return table


def budget(phase,c=None):
    out={}
    for component in components(phase):
        for key,value in (counts(c) if c else COMPONENTS)[component].items():out[key]=out.get(key,0)+value
    out.update(qualification_quantization=3 if phase in('fit','fast_recovery')else 6,
        preview=2,preview_JPG=2,native_JPG_encode=1,native_JPG_full_readback=1,source_copy=1,mix_traversals=4)
    return out


def events(c):
    p=k.e(c)/'science/events.jsonl'
    return [json.loads(x)for x in p.read_text('utf-8').splitlines()if x]if p.exists()else[]


def append(row,c):
    p=k.e(c)/'science/events.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('ab')as f:
        f.write(canonical_json(dict(row,wall=time.time(),monotonic=time.monotonic()))+b'\n');f.flush();os.fsync(f.fileno())


def claim(c):
    row=strict_load(Path(c['output_root'])/'science-claim.json')
    need(row['context']==binding(c['manifest_path']) and row['allocation']==c['science_allocation'],'GM064_CLAIM_BINDING')
    from .t34r3_c1_route import RESUME_QUALIFICATIONS
    if Path(c['qualification']['path']).name in RESUME_QUALIFICATIONS:
        from .t34r3_c2_history import RESERVATION
        rows = [item for item in events(c) if item['allocation'] == 'T4' and item['kind'] in ('claim','claim_continued')]
        need(len(rows) == 2 and rows[0]['kind'] == 'claim' and rows[0]['reservation'] == RESERVATION
             and row['kind'] == 'claim_continued' and row['reservation'] == RESERVATION
             and row == {k:v for k,v in rows[1].items() if k not in ('wall','monotonic')},
             'C2_CONTINUATION_LEDGER_BINDING')
    from .c2_cpu_tail import enabled
    if enabled(c):
        from .c2_fine_replay import OLD_CLAIM,verify_dispatch
        rows=[item for item in events(c) if item['kind']=='claim']
        need(row['kind']=='claim' and row['reservation']!=OLD_CLAIM and row['dispatch']==verify_dispatch(c)
             and len(rows)==1 and row=={k:v for k,v in rows[0].items() if k not in ('wall','monotonic')},
             'C2_REPLAY_CLAIM_BINDING')
    return row


def reserve(path,spec,c=None):
    if c is None:
        from .context import context
        c=context(spec)
    from .c2_cpu_tail import enabled
    need(not enabled(c),'C2_REPLAY_REQUIRES_PREPARED_STARTUP')
    q, key = validate_reservation(c)
    with locked(k.e(c)/'science/ledger.lock'):
        from .t34r3_c1_route import selected,KEY
        route=selected(q,Path(c['workspace']),KEY)
        if route is not None:
            need(not route.resuming, 'C2_REQUIRES_PREPARED_STARTUP')
            route.verify_dispatch(c,time.time())
            route.verify_unclaimed()
        need(not any(x['kind']=='claim' and x['allocation']==key for x in events(c)),'GM064_ALLOCATION_REUSED')
        row=dict(kind='claim',allocation=key,reservation=str(uuid.uuid4()),phase=c['phase'],operation_path=str(path),
                 context=binding(c['manifest_path']),budget=c['science'],before_any_science=True)
        save_claim(path,c,row)
    return row


def validate_reservation(c):
    q=k.authorize(c['qualification']['path'],c['request']);need(q['phase']=='native','GM064_DRY_NO_SCIENCE')
    allocations=strict_load(k.e(c)/'allocations.json')['allocations'];key=c['science_allocation']
    need(allocations[key]['phase']==c['phase'] and allocations[key]['budget']==budget(c['phase'],c)==c['science'],'GM064_EXACT_ALLOCATION')
    # Root-level aggregate science reservation must already exist, before any
    # process or IO for this logical unit. It is never refunded by this hook.
    book=[json.loads(x)for x in (k.e(c)/'science-budget.jsonl').read_text('utf-8').splitlines()if x]
    reservations=[x for x in book if x.get('kind')=='unit_reserve' and x.get('id')==key]
    need(len(reservations)==1,'GM064_UNIT_PREPAYMENT_REQUIRED')
    from .c2_cpu_tail import enabled
    if enabled(c):
        from .c2_fine_replay import authority,OLD_CLAIM
        _,auth=authority()
        prepaid=reservations[0]
        need(prepaid['full_31_counters']==auth['science'] and prepaid['budget']==c['science']
             and prepaid['prior_claim']==OLD_CLAIM and prepaid['authority']==q['recovery']
             and prepaid['no_refund'] is True and prepaid['before_any_science_or_IO'] is True,
             'C2_REPLAY_PREPAYMENT')
    if strict_load(k.e(c)/'session.json').get('r4'):
        reservation=r4_authority(strict_load(k.e(c)/'session.json'))['reservation']
        need(key=='T2' and reservations[0].get('reservation_id')==reservation
             and reservations[0].get('budget')==c['science'],'GM064_R4_UNIT_PREPAYMENT_REQUIRED')
    return q, key


def save_claim(path,c,row):
    append(row,c)
    atomic_json(Path(path)/'scientific-reservation.json',row)
    atomic_json(Path(c['output_root'])/'science-claim.json',row)


def prepare_c2_start(c, key):
    from . import c2_fine_replay as replay
    q, allocation=validate_reservation(c)
    root=k.e(c)
    replay.dispatch_scope(q,root,Path(c['workspace']),key)
    replay.verify_prior(root)
    deadline=replay.window(root,time.time(),replay.MIN_START)
    pins=tuple(binding(p) for p in (c['manifest_path'],q['manifest_path'],q['recovery']['path'],
                root/'science/t34r3-c2r-window.json',root/'science-budget.jsonl',root/'allocations.json'))
    need(allocation==replay.UNIT,'C2_REPLAY_UNIT')
    return (*pins,replay.verify_dispatch(c)),deadline


def commit_c2_start(path,spec,c,prepared,running):
    from ..gm004a_operations import operation_lock
    from ..winproc import current_identity
    root=k.e(c)
    pins,deadline=prepared
    with locked(root/'science/ledger.lock'), operation_lock(Path(path),spec):
        for pin in pins:verify(pin)
        current=strict_load(Path(path)/'status.json')
        need(current==running and current['state']=='RUNNING' and spec['semantics']['instance_context']==pins[0]
             and not (Path(path)/'cancel.signal').exists(),'C2_STARTUP_CHANGED')
        need(current_identity()==spec['startup_owner'],'C2_STARTUP_OWNER_MISMATCH',exit_code=9)
        need(time.time()<min(deadline,spec['start_deadline_at']),'C2_STARTUP_DEADLINE_EXPIRED',exit_code=9)
        need((root/'science/events.jsonl').read_bytes()==b'','C2_REPLAY_TICKET_ALREADY_USED')
        row=dict(kind='claim',allocation=c['science_allocation'],reservation=str(uuid.uuid4()),phase=c['phase'],
                 operation_path=str(path),context=pins[0],budget=c['science'],before_any_science=True,dispatch=pins[-1])
        save_claim(path,c,row)
        return row


def prepare_resumed_start(c):
    from .t34r3_c1_route import selected,KEY
    q, key = validate_reservation(c)
    route = selected(q, Path(c['workspace']), KEY)
    need(route is not None and route.resuming and key == 'T4', 'C2_STARTUP_SCOPE')
    deadline = route.verify_dispatch(c,time.time())
    with locked(k.e(c)/'science/ledger.lock'):
        route.verify_unclaimed()
        pins = tuple(binding(p) for p in (
            c['manifest_path'], route.qualification, route.authority['path'], route.claim,
            route.window_path, k.e(c)/'allocations.json'))
    return route, pins, deadline


def commit_resumed_start(path,spec,c,prepared,running):
    from ..winproc import current_identity
    from ..gm004a_operations import operation_lock
    route, pins, deadline = prepared
    with locked(k.e(c)/'science/ledger.lock'), operation_lock(Path(path), spec):
        for pin in pins:verify(pin)
        need(time.time() <= deadline, 'C2_SCIENCE_WINDOW_EXPIRED')
        previous = route.verify_unclaimed()
        current = strict_load(Path(path)/'status.json')
        need(current == running and current['state'] == 'RUNNING'
             and current['job_id'] == spec['job_id'] and current['attempt_id'] == spec['attempt_id']
             and spec['semantics']['instance_context'] == pins[0]
             and not (Path(path)/'cancel.signal').exists(), 'C2_STARTUP_CHANGED')
        need(current_identity() == spec['startup_owner'], 'C2_STARTUP_OWNER_MISMATCH', exit_code=9)
        need(time.time() < spec['start_deadline_at'], 'C2_STARTUP_DEADLINE_EXPIRED', exit_code=9)
        row = dict(previous, kind='claim_continued', operation_path=str(path), context=pins[0],
                   previous_claim=route.a['resume']['files']['claim'], dispatch=pins[3])
        save_claim(path,c,row)
        return row


def start(c,component):
    mark=k.read(k.e(c)/'session.json')
    need(time.time()-mark['first_wall']<=mark.get('target_wall_seconds',168*3600),'GM064_SESSION_WALL_LIMIT')
    if mark.get('task_budget'):
        need(time.time()-mark['task_first_wall']<=240*3600,'GM064_R1_TASK_WALL_LIMIT')
    from .t34r3_c1_route import selected,KEY,QUALIFICATIONS
    qualification=Path(c['qualification']['path'])
    if qualification.name in QUALIFICATIONS:
        route=selected(k.read(qualification),Path(c['workspace']),KEY)
        need(route is not None,'C1_ROUTE_CLAIM_REQUIRED')
        route.verify_dispatch(c,time.time())
    else:
        target_science_clock(mark,k.e(c),time.time(),c['science_allocation'])
    need(component in components(c['phase']),'GM064_COMPONENT_PHASE')
    with locked(k.e(c)/'science/ledger.lock'):
        r=claim(c);rows=events(c)
        need(not any(x['kind']=='component_started' and x['reservation']==r['reservation'] and x['component']==component for x in rows),'GM064_COMPONENT_REUSED')
        for key,value in counts(c)[component].items():
            used=sum(x['counts'].get(key,0)for x in rows if x['kind']=='component_started' and x['reservation']==r['reservation'])
            need(used+value<=c['science'].get(key,0),'GM064_COMPONENT_CAP:'+key)
        append(dict(kind='component_started',allocation=r['allocation'],reservation=r['reservation'],component=component,
                    counts=counts(c)[component],before_actual_call=True,permanently_consumed=True),c)
    stage(c,'before_'+component,component=component)


def before_launch(c,role):
    from .portable_resources import admit
    admit(c,'before_launch_'+role,role=role)
    allowed={'fit':('fit','r0'),'refine':('r0','prepare','solver','cpu'),
             'fast_recovery':('r0',),'replay_fine':('r0','cpu')}[c['phase']]
    need(role in allowed,'GM064_WORKER_ROLE_PHASE')
    with locked(k.e(c)/'science/ledger.lock'):
        r=claim(c);need(not any(x['kind']=='worker_allocation' and x['reservation']==r['reservation'] and x['role']==role for x in events(c)),'GM064_WORKER_REUSED')
        append(dict(kind='worker_allocation',allocation=r['allocation'],reservation=r['reservation'],role=role,before_CreateProcess=True),c)


def worker_claim(c,role,operation):
    from ..winproc import current_identity
    with locked(k.e(c)/'science/ledger.lock'):
        r=claim(c);rows=events(c)
        need(Path(r['operation_path'])==Path(operation) and sum(x['kind']=='worker_allocation' and x['reservation']==r['reservation'] and x['role']==role for x in rows)==1 and
             not any(x['kind']=='worker_claim' and x['reservation']==r['reservation'] and x['role']==role for x in rows),'GM064_WORKER_UNIQUE_CLAIM')
        append(dict(kind='worker_claim',allocation=r['allocation'],reservation=r['reservation'],role=role,identity=current_identity(),before_preload=True),c)


def complete(c,component,receipt):
    verify(receipt)
    with locked(k.e(c)/'science/ledger.lock'):
        r=claim(c);rows=events(c)
        need(sum(x['kind']=='component_started' and x['reservation']==r['reservation'] and x['component']==component for x in rows)==1 and
             not any(x['kind']=='completed' and x['reservation']==r['reservation'] and x['component']==component for x in rows),'GM064_COMPLETE_UNIQUE_START')
        if component=='CPU':
            ack=strict_load(Path(receipt['path']));need(ack['complete'] is True and ack['cores']==(c['source_identity']['shape'][0]+255)//256 and ack['blocks']==c['source_identity']['shape'][0] and ack['four_fields_per_ACK'] is True and len(ack['fields'])==4*((c['source_identity']['shape'][0]+255)//256),'GM064_CPU_ACK')
        if component=='R0':
            actual=strict_load(Path(receipt['path']));need(actual['counts']['render']==1 and actual['counts']['forward_batches']==k.expected_tiles(c) and actual['counts']['completed_tiles']==k.expected_tiles(c),'GM064_R0_COUNTS')
        append(dict(kind='completed',allocation=r['allocation'],reservation=r['reservation'],component=component,receipt=receipt),c)
    stage(c,'after_'+component,component=component)


def host_count(host,delta):
    c=host.context;limits=c['science']
    need(all(type(v)is int and v>=0 and key in limits for key,v in delta.items()),'GM064_HOST_COUNT_FIELDS')
    for key,component in(('source_decode','source'),('reference_decode','reference')):
        if delta.get(key):start(c,component)
    with locked(k.e(c)/'science/ledger.lock'):
        r=claim(c)
        for key,value in delta.items():need(host.counts.get(key,0)+value<=limits[key],'GM064_HOST_STAGE_CAP:'+key)
        for key,value in delta.items():host.counts[key]=host.counts.get(key,0)+value
        append(dict(kind='host_counter',allocation=r['allocation'],reservation=r['reservation'],delta=delta,counts=dict(host.counts),before_actual_IO=True),c)
        atomic_json(host.session/'io-counts.json',host.counts)


def stage_complete(c):
    with locked(k.e(c)/'science/ledger.lock'):
        r=claim(c);rows=events(c)
        for component in components(c['phase']):
            need(sum(x['kind']=='completed' and x['reservation']==r['reservation'] and x['component']==component for x in rows)==1,'GM064_STAGE_INCOMPLETE:'+component)
    return dict(reservation=r['reservation'],all_components_completed=True,components=list(components(c['phase'])))


def totals(rows, domain):
    result = {}
    for row in rows:
        if row['domain'] == domain:
            for key, value in row['budget'].items():
                need(type(value) is int and value >= 0, 'GM064_R1_INVALID_COUNT')
                result[key] = result.get(key, 0) + value
    return result


def check_capacity(rows, domain, caps):
    total = totals(rows, domain)
    if domain == 'small':
        total['decode_total'] = total.get('source_decode', 0) + total.get('reference_decode', 0)
        total['jpg_total'] = total.get('preview_JPG', 0) + total.get('native_JPG_encode', 0)
    for key, cap in caps.items():
        need(total.get(key, 0) <= cap, 'GM064_R1_SCIENCE_CAP:'+key)
    return total


def inherited_rows(authority):
    need(authority['first_wall'] == 1793750783.138, 'GM064_R1_FIRST_WALL')
    inherited = []
    for source in authority['historical']:
        verify(source)
        rows = [json.loads(line) for line in Path(source['path']).read_text('utf-8').splitlines() if line]
        need(len(rows) == 1 and rows[0]['kind'] == 'unit_reserve' and rows[0]['id'] == 'S1'
             and rows[0]['no_refund'] is True, 'GM064_R1_HISTORICAL_RESERVATION')
        inherited.extend(rows)
    need(len(inherited) == 3 and len({x['sha256'] for x in authority['historical']}) == 3
         and all(totals(inherited, 'small').get(key) == count
                 for key, count in {'fit':3, 'updates':12, 'forward':33, 'R0_render':3}.items()),
         'GM064_R1_HISTORICAL_TOTALS')
    return inherited


def reserve_task_unit(authority_binding, root, row, caps, *, recovery=None):
    verify(authority_binding)
    authority = strict_load(authority_binding['path'])
    need(time.time() - authority['first_wall'] <= 240*3600, 'GM064_R1_TASK_WALL_LIMIT')
    journal = k.ancestors(authority['incremental_journal'])
    need(journal.parent == Path(authority_binding['path']).parent, 'GM064_R1_JOURNAL_SCOPE')
    with locked(journal.with_suffix('.lock')):
        raw = journal.read_bytes()
        prior = [json.loads(line) for line in raw.splitlines() if line]
        ticket_id = ('N'+row['id'][1:] if row['domain']=='24mp' else row['id'])
        if recovery is not None:
            verify(recovery)
            auth = strict_load(recovery['path'])
            prefix = auth['science_prefix']
            verify(prefix)
            need(auth['schema']=='gm064-r1-n2-recovery-authority-v1'
                 and auth['first_wall']==authority['first_wall']
                 and ticket_id=='N2' and row['phase']=='replay_fine'
                 and raw.startswith(Path(prefix['path']).read_bytes())
                 and sum(x['unit']=='N2' for x in prior)==1
                 and all(x['unit']!='N2-recovery-1' for x in prior), 'GM064_N2_RECOVERY_ONCE')
            ticket_id = 'N2-recovery-1'
        need(not any(x['unit'] == ticket_id for x in prior), 'GM064_R1_UNIT_ALREADY_RESERVED')
        charged = dict(row, unit=ticket_id, reservation=str(uuid.uuid4()), session=str(root),
                       task_first_wall=authority['first_wall'])
        total = check_capacity(inherited_rows(authority)+prior+[charged], row['domain'], caps)
        with journal.open('ab') as stream:
            stream.write(canonical_json(charged)+b'\n')
            stream.flush()
            os.fsync(stream.fileno())
    return dict(charged, task_totals_after_reservation=total)


TARGET_CAPS=dict(fit=2,updates=2000,forward=4578,R0_render=6,scalar_CG=6,CPU256_service=3,
                 source_decode=8,reference_decode=4,preview=12,preview_JPG=12,native_JPG_encode=6,
                 native_JPG_full_readback=6,qualification_quantization=36,range_scan=4)


def r2_prior(mark):
    authority=mark['r2_continuation']
    need(authority['schema']=='gm064-r2-fixed-target-v1' and authority['old_first_wall']==mark['task_first_wall']
         and authority['new_manifest']==binding(k.ROOT/'portable-manifest.json'),'GM064_R2_CONTINUATION_BINDING')
    for row in authority['old_files'].values():
        verify(row)
    old=[json.loads(line) for line in Path(authority['old_files']['science-budget.jsonl']['path']).read_bytes().splitlines()]
    need(len(old)==1 and old[0]['id']=='T1' and old[0]['phase']=='fast_recovery' and old[0]['no_refund'] is True,
         'GM064_R2_OLD_T1_RESERVATION')
    if 'r3_previous' in authority:
        for row in authority['r3_files'].values():
            verify(row)
        previous=[json.loads(line) for line in Path(authority['r3_files']['science-budget.jsonl']['path']).read_bytes().splitlines()]
        need(len(previous)==1 and previous[0]['id']=='T1' and previous[0]['phase']=='fast_recovery'
             and previous[0]['no_refund'] is True,'GM064_R3_OLD_T1_RESERVATION')
        old.extend(previous)
    return old


def r4_authority(mark):
    leaf='validation/r4-s1-science-budget.json' if mark['r4'].get('recovery') else 'validation/r4-science-budget.json'
    authority=k.read(k.package_member(leaf))
    expected='GM064-R4-S1-T2-ONCE' if mark['r4'].get('recovery') else 'GM064-R4-T2-ONCE'
    need(authority['reservation']==expected,'GM064_R4_RESERVATION_VERSION')
    return authority


def r4_projection(mark,q,current_budget):
    from .portable_qualification import MEMBER,SCHEMA
    contract=k.read(k.package_member(MEMBER))
    authority=r4_authority(mark)
    need(contract['protocol']==SCHEMA
         and mark['task_first_wall']==authority['first_wall'] and current_budget==authority['additional_t2']
         and q['domain']=='24mp' and q['request']['operation']=='replay' and q['request']['quality']=='fine'
         and q['allocations']=={'replay_fine':'T2'},'GM064_R4_T2_SCOPE')
    files=mark['r4']['historical_files']
    for item in files.values():verify(item)
    ledger=Path(files['science-budget.jsonl']['path']).read_bytes().splitlines(keepends=True)
    need(len(ledger)==2 and hashlib.sha256(ledger[0]).hexdigest()==contract['old_t1_ledger_sha256'],
         'GM064_R4_CARRIED_T1_CHANGED')
    resumed=[json.loads(line) for line in ledger]
    need([row['id'] for row in resumed]==['T1','T2']
         and all(row['no_refund'] is True and row['before_any_science_or_IO'] is True for row in resumed)
         and resumed[1]['budget']==authority['additional_t2'],'GM064_R4_OLD_T2_LEDGER')
    caps=dict(authority['cumulative_explicit_caps'],**authority['additional_workload_caps'])
    previous=[]
    if mark['r4'].get('recovery'):
        recovery=mark['r4']['recovery']
        for row in recovery['files'].values():verify(row)
        previous=[json.loads(line) for line in Path(recovery['files']['science-budget.jsonl']['path']).read_bytes().splitlines()]
        need(len(previous)==1 and previous[0]['id']=='T2'
             and previous[0]['reservation_id']=='GM064-R4-T2-ONCE'
             and previous[0]['budget']==current_budget and previous[0]['no_refund'] is True,
             'GM064_R4_S1_OLD_RESERVATION')
    projection=r2_prior(mark)+resumed+previous+authority['retained_T3_T4']+[dict(domain='24mp',budget=current_budget)]
    total=check_capacity(projection,'24mp',caps)
    need(total==authority['history_plus_reserved_T3_T4_plus_R4_projection'] and set(total)<=set(caps),
         'GM064_R4_COMPLETE_BUDGET_PROJECTION')
    return caps,total


def reserve_unit(q):
    """Permanent per-session allocation, before scientific processes or IO."""
    root=k.session(q['manifest_path']);phase=next(iter(q['allocations']));name=q['allocations'][phase]
    b=budget(phase,q);mark=k.read(root/'session.json');domain=q['domain']
    local=mark.get('local_only') is True
    if mark.get('c2_fine'):
        from .c2_fine_replay import reserve_unit as reserve_c2
        return reserve_c2(root,q)
    if mark.get('t34r3_recovery'):
        from .saved_fit_funding import reserve
        return reserve(q)
    caps=(dict(fit=4,updates=16,forward=128,R0_render=8,scalar_CG=6,CPU256_service=4,source_decode=16,reference_decode=16,preview=16,preview_JPG=16,native_JPG_encode=16,native_JPG_full_readback=16) if domain=='small' else
          dict(fit=0,updates=0,forward=288,R0_render=3,scalar_CG=0,CPU256_service=2,source_decode=3,reference_decode=0,preview=6,preview_JPG=6,native_JPG_encode=3,native_JPG_full_readback=3,qualification_quantization=18,range_scan=2) if local else
          TARGET_CAPS)
    if local:
        need(mark.get('task_budget') is not None, 'GM064_R1_CUMULATIVE_LEDGER_REQUIRED')
        if domain == 'small':
            caps.update(fit=6, updates=24, decode_total=16, jpg_total=24)
    with locked(root/'science-budget.lock'):
        p=root/'science-budget.jsonl';rows=[json.loads(x)for x in p.read_text('utf-8').splitlines()if x]
        need(not any(x['id']==name for x in rows),'GM064_UNIT_PREPAY_REUSED')
        if mark.get('r4'):
            need(not rows and not local and name=='T2','GM064_R4_ONE_RESERVATION')
            caps,total=r4_projection(mark,q,b)
            row=dict(kind='unit_reserve',id=name,domain=domain,phase=phase,budget=b,
                     wall=time.time(),before_any_science_or_IO=True,no_refund=True,
                     reservation_id=r4_authority(mark)['reservation'],complete_projection=total)
            with p.open('ab') as stream:
                stream.write(canonical_json(row)+b'\n');stream.flush();os.fsync(stream.fileno())
            return row
        charged_rows=r2_prior(mark)+rows if mark.get('r2_continuation') else rows
        for key,cap in caps.items():
            used=sum(x['budget'].get(key,0)for x in charged_rows if x['domain']==domain)
            need(used+b.get(key,0)<=cap,'GM064_SESSION_SCIENCE_CAP:'+key)
        row=dict(kind='unit_reserve',id=name,domain=domain,phase=phase,budget=b,wall=time.time(),before_any_science_or_IO=True,no_refund=True)
        if local:
            row = reserve_task_unit(mark['task_budget'], root, row, caps, recovery=mark.get('n2_recovery'))
        with p.open('ab')as stream:stream.write(canonical_json(row)+b'\n');stream.flush();os.fsync(stream.fileno())
    return row


def t34_window_rules(window,mark,authority,manifest,root,now,unit):
    """Exact one-session metadata authority; it grants no algorithm or IO call."""
    need(set(window)=={'schema','scope','session','original_first_wall','started_at','deadline_at',
                       'seconds','manifest','authority','nonce'},'T34R1_WINDOW_FIELDS')
    need(authority['schema']=='gm064-t34r1-authority-v1' and authority['scope']=='GM064-T34R1-20261106'
         and authority['window_seconds']==28800 and authority['original_first_wall']==1793788456.605,
         'T34R1_WINDOW_AUTHORITY')
    need(window['schema']=='gm064-t34r1-window-v1' and window['scope']==authority['scope']
         and window['session']==str(root) and window['manifest']==manifest
         and window['original_first_wall']==mark['task_first_wall']==1793788456.605
         and window['seconds']==28800 and unit in ('T3','T4') and mark.get('local_only') is False,
         'T34R1_WINDOW_SCOPE')
    state=mark['t34_recovery']
    need(state['schema']=='gm064-t34r1-session-v1' and state['authority']==window['authority']
         and type(window['nonce'])is str and len(window['nonce'])==32 and
         all(x in '0123456789abcdef' for x in window['nonce']) and state['nonce']==window['nonce'],
         'T34R1_WINDOW_NONCE_OR_AUTHORITY')
    need(type(window['started_at']) in (int,float) and type(window['deadline_at']) in (int,float)
         and window['deadline_at']==window['started_at']+28800
         and 1793788456.605<=window['started_at']<=now<=window['deadline_at']
         and now<=1793788456.605+168*3600,'T34R1_WINDOW_EXPIRED_OR_INVALID')
    return window['deadline_at']


def target_science_clock(mark,root,now,unit):
    """Retain the original clock unless this exact separately approved scope applies."""
    if mark.get('c2_fine'):
        from .c2_fine_replay import window,UNIT
        need(unit==UNIT,'C2_REPLAY_UNIT')
        return window(root,now)
    if mark.get('t34r3_recovery'):
        from .saved_fit_funding import clock
        return clock(mark,root,now,unit)
    if mark.get('t34_recovery'):
        root=Path(root);need(root==k.session_path(root),'T34R1_WINDOW_SESSION_PATH')
        state=mark['t34_recovery'];need(set(state)=={'schema','authority','window','nonce'},'T34R1_SESSION_AUTH_FIELDS')
        authority_path=k.package_member('validation/t34r1-authority.json')
        need(state['authority']==binding(authority_path),'T34R1_AUTHORITY_BINDING')
        need(Path(state['window']['path'])==root/'science/t34r1-window.json','T34R1_WINDOW_PATH')
        verify(state['window']);window=strict_load(state['window']['path'])
        return t34_window_rules(window,mark,strict_load(authority_path),binding(k.ROOT/'portable-manifest.json'),root,now,unit)
    if mark.get('r2_continuation'):
        need(0<=now-mark['task_first_wall']<=48*3600,'GM064_R2_TARGET_SCIENCE_CLOCK_LIMIT')
    return None
