"""GM063 task-local prepayment/claim hooks over original CPU execution."""
import os,time,uuid,json
from pathlib import Path
from ..common import strict_load,atomic_json,canonical_json
from ..store import locked
from ..cpu_replay.contract import binding,verify,need
from . import calibration_contract as k
from .calibration_observation import stage

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


def budget(phase):
    out={}
    for component in components(phase):
        for key,value in COMPONENTS[component].items():out[key]=out.get(key,0)+value
    out.update(qualification_quantization=3 if phase in('fit','fast_recovery')else 6,
        preview=2,preview_JPG=2,native_JPG_encode=1,native_JPG_full_readback=1,source_copy=1,mix_traversals=4)
    return out


def events():
    p=k.E/'science/events.jsonl'
    return [json.loads(x)for x in p.read_text('utf-8').splitlines()if x]if p.exists()else[]


def append(row):
    p=k.E/'science/events.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('ab')as f:
        f.write(canonical_json(dict(row,wall=time.time(),monotonic=time.monotonic()))+b'\n');f.flush();os.fsync(f.fileno())


def claim(c):
    row=strict_load(Path(c['output_root'])/'science-claim.json')
    need(row['context']==binding(c['manifest_path']) and row['allocation']==c['science_allocation'],'GM063_CLAIM_BINDING')
    return row


def reserve(path,spec,c=None):
    if c is None:
        from .context import context
        c=context(spec)
    q=k.authorize(c['qualification']['path'],c['request']);need(q['phase']=='native','GM063_DRY_NO_SCIENCE')
    allocations=strict_load(k.E/'allocations.json')['allocations'];key=c['science_allocation']
    need(allocations[key]['phase']==c['phase'] and allocations[key]['budget']==budget(c['phase'])==c['science'],'GM063_EXACT_ALLOCATION')
    # Root-level aggregate science reservation must already exist, before any
    # process or IO for this logical unit. It is never refunded by this hook.
    book=[json.loads(x)for x in (k.E/'science-budget.jsonl').read_text('utf-8').splitlines()if x]
    need(sum(x.get('kind')=='unit_reserve' and x.get('id')==key for x in book)==1,'GM063_UNIT_PREPAYMENT_REQUIRED')
    with locked(k.E/'science/ledger.lock'):
        need(not any(x['kind']=='claim' and x['allocation']==key for x in events()),'GM063_ALLOCATION_REUSED')
        row=dict(kind='claim',allocation=key,reservation=str(uuid.uuid4()),phase=c['phase'],operation_path=str(path),
                 context=binding(c['manifest_path']),budget=c['science'],before_any_science=True)
        append(row);atomic_json(Path(path)/'scientific-reservation.json',row);atomic_json(Path(c['output_root'])/'science-claim.json',row)
    return row


def start(c,component):
    need(component in components(c['phase']),'GM063_COMPONENT_PHASE')
    with locked(k.E/'science/ledger.lock'):
        r=claim(c);rows=events()
        need(not any(x['kind']=='component_started' and x['reservation']==r['reservation'] and x['component']==component for x in rows),'GM063_COMPONENT_REUSED')
        for key,value in COMPONENTS[component].items():
            used=sum(x['counts'].get(key,0)for x in rows if x['kind']=='component_started' and x['reservation']==r['reservation'])
            need(used+value<=c['science'].get(key,0),'GM063_COMPONENT_CAP:'+key)
        append(dict(kind='component_started',allocation=r['allocation'],reservation=r['reservation'],component=component,
                    counts=COMPONENTS[component],before_actual_call=True,permanently_consumed=True))
    stage(c['evidence_root'],'before_'+component,allocation=r['allocation'])


def before_launch(c,role):
    allowed={'fit':('fit','r0'),'refine':('r0','prepare','solver','cpu'),
             'fast_recovery':('r0',),'replay_fine':('r0','cpu')}[c['phase']]
    need(role in allowed,'GM063_WORKER_ROLE_PHASE')
    with locked(k.E/'science/ledger.lock'):
        r=claim(c);need(not any(x['kind']=='worker_allocation' and x['reservation']==r['reservation'] and x['role']==role for x in events()),'GM063_WORKER_REUSED')
        append(dict(kind='worker_allocation',allocation=r['allocation'],reservation=r['reservation'],role=role,before_CreateProcess=True))


def worker_claim(c,role,operation):
    from ..winproc import current_identity
    with locked(k.E/'science/ledger.lock'):
        r=claim(c);rows=events()
        need(Path(r['operation_path'])==Path(operation) and sum(x['kind']=='worker_allocation' and x['reservation']==r['reservation'] and x['role']==role for x in rows)==1 and
             not any(x['kind']=='worker_claim' and x['reservation']==r['reservation'] and x['role']==role for x in rows),'GM063_WORKER_UNIQUE_CLAIM')
        append(dict(kind='worker_claim',allocation=r['allocation'],reservation=r['reservation'],role=role,identity=current_identity(),before_preload=True))


def complete(c,component,receipt):
    verify(receipt)
    with locked(k.E/'science/ledger.lock'):
        r=claim(c);rows=events()
        need(sum(x['kind']=='component_started' and x['reservation']==r['reservation'] and x['component']==component for x in rows)==1 and
             not any(x['kind']=='completed' and x['reservation']==r['reservation'] and x['component']==component for x in rows),'GM063_COMPLETE_UNIQUE_START')
        if component=='CPU':
            ack=strict_load(Path(receipt['path']));need(ack['complete'] is True and ack['cores']==24 and ack['blocks']==6000 and ack['four_fields_per_ACK'] is True and len(ack['fields'])==96,'GM063_CPU_ACK')
        if component=='R0':
            actual=strict_load(Path(receipt['path']));need(actual['counts']['render']==1 and actual['counts']['forward_batches']==96 and actual['counts']['completed_tiles']==96,'GM063_R0_COUNTS')
        append(dict(kind='completed',allocation=r['allocation'],reservation=r['reservation'],component=component,receipt=receipt))
    stage(c['evidence_root'],'after_'+component,allocation=r['allocation'])


def host_count(host,delta):
    c=host.context;limits=c['science']
    need(all(type(v)is int and v>=0 and key in limits for key,v in delta.items()),'GM063_HOST_COUNT_FIELDS')
    for key,component in(('source_decode','source'),('reference_decode','reference')):
        if delta.get(key):start(c,component)
    with locked(k.E/'science/ledger.lock'):
        r=claim(c)
        for key,value in delta.items():need(host.counts.get(key,0)+value<=limits[key],'GM063_HOST_STAGE_CAP:'+key)
        for key,value in delta.items():host.counts[key]=host.counts.get(key,0)+value
        append(dict(kind='host_counter',allocation=r['allocation'],reservation=r['reservation'],delta=delta,counts=dict(host.counts),before_actual_IO=True))
        atomic_json(host.session/'io-counts.json',host.counts)


def stage_complete(c):
    with locked(k.E/'science/ledger.lock'):
        r=claim(c);rows=events()
        for component in components(c['phase']):
            need(sum(x['kind']=='completed' and x['reservation']==r['reservation'] and x['component']==component for x in rows)==1,'GM063_STAGE_INCOMPLETE:'+component)
    return dict(reservation=r['reservation'],all_components_completed=True,components=list(components(c['phase'])))
