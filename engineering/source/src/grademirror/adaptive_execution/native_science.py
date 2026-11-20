"""GM056 gross reservation, unique component starts and actual completion receipts."""
import json,os,time,uuid
from pathlib import Path
from ..common import atomic_json,canonical_json
from .native_contract import strict_load
from ..store import locked
from ..cpu_replay.contract import need,binding,verify
from .native_contract import E,W,authorize
BASE=dict(source_decode=1,R0_render=1,forward=140,Field=1,state_load=1,CPU256_service=0,scalar_LLF_footprints=0,compositions=0,qualification_quantization=3,preview=4,preview_JPG=4,native_JPG_encode=2,native_JPG_full_readback=2,fit=0,update=0,CG=0,reference_decode=0,GPU=0,NVML=0,Q=0)
COMPONENTS={'source':dict(source_decode=1),'R0':dict(R0_render=1,forward=140,Field=1,state_load=1),'CPU':dict(CPU256_service=1,scalar_LLF_footprints=168,compositions=84)}
TOTAL=dict(source_decode=5,R0_render=4,forward=560,Field=4,state_load=4,CPU256_service=3,scalar_LLF_footprints=504,compositions=252)
def _new(c):return bool(c and c.get('schema')=='cpu-application-execution-v5')
def _independent_total(c):
    from .independent_science import TOTAL as caps
    return caps
def _root(c):
    if _new(c):
        from .independent_contract import E as current
        return current
    return E

def budget(mode):
    need(mode in ('fast','fine'),'SCIENCE_MODE')
    return dict(BASE,**(dict(CPU256_service=1,scalar_LLF_footprints=168,compositions=84,qualification_quantization=6) if mode=='fine' else {}))
def read_events(root=E):
    p=Path(root)/'science/events.jsonl'
    if not p.exists():return []
    raw=p.read_bytes();need(not raw or raw.endswith(b'\n'),'SCIENCE_LEDGER_PARTIAL_UNKNOWN')
    rows=[json.loads(x) for x in raw.splitlines()];need(all(type(x)is dict and 'kind'in x for x in rows),'SCIENCE_LEDGER_FIELDS_UNKNOWN');return rows
def append(value,root=E):
    p=Path(root)/'science/events.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('ab') as f:f.write(canonical_json(dict(value,wall=time.time()))+b'\n');f.flush();os.fsync(f.fileno())
def _claim(root,allocation,mode,path,ctx):
    root=Path(root)
    from .independent_science import allow_root
    need(root==E or root.is_relative_to(W/'fixtures') or allow_root(root,ctx),'SCIENCE_CLAIM_ROOT')
    with locked(root/'science/ledger.lock'):
        rows=read_events(root);old=[x for x in rows if x['kind']=='reserve' and x['allocation']==allocation]
        released={x['reservation'] for x in rows if x['kind']=='zero_release'}
        need(all(x['reservation'] in released for x in old),'SCIENCE_ALLOCATION_ALREADY_CLAIMED')
        from .independent_contract import E as current_E
        if root==current_E:
            from .independent_science import budget as active_budget
        else:active_budget=budget
        row=dict(kind='reserve',allocation=allocation,reservation=str(uuid.uuid4()),mode=mode,operation_path=str(path),context=ctx,budget=active_budget(mode),before_decode_model_math=True,gross_retained=True,TEST_ONLY=root!=E and root!=current_E)
        append(row,root);atomic_json(Path(path)/'scientific-reservation.json',row);return row

def _release_zero(root,reservation,proof):
    root=Path(root)
    from .independent_contract import E as current_E
    fixture=root!=E and root!=current_E
    if fixture:need(root.is_relative_to(W/'fixtures') and proof=={'TEST_ONLY':True,'fixture_no_compute_authority':True},'SCIENCE_FIXTURE_SCOPE')
    else:
        verify(proof);saved=strict_load(proof['path'])
        need(all(saved[k] is True for k in ('actual_all_wait','actual_root_wait','tree_empty','exact')) and saved['actual_members']==len(saved['records']) and all(x['actual_handle_wait'] is True and x['job_member_verified'] is True for x in saved['records']),'SCIENCE_ZERO_EXIT_UNKNOWN')
    with locked(root/'science/ledger.lock'):
        rows=read_events(root);need(sum(x['kind']=='reserve' and x['reservation']==reservation for x in rows)==1 and not any(x['kind'] in ('start','worker_claim','host_counter','zero_release') and x.get('reservation')==reservation for x in rows),'SCIENCE_ZERO_NOT_PROVEN_OR_ALREADY_RELEASED')
        append(dict(kind='zero_release',reservation=reservation,actual_zero_started=True,exit_proof=proof,gross_refund=False,TEST_ONLY=fixture),root)

def _allocation(c):
    if _new(c):
        from .independent_science import allocation
        return allocation(c)
    a=strict_load(E/'allocations.json');allocation=c['science_allocation'];rows=a['allocations']
    need(a['schema']=='gm056-scientific-allocations-v1' and allocation in rows,'SCIENCE_ALLOCATION_SCOPE')
    initial=[r for r in rows.values() if r['kind']=='initial'];extra=[r for r in rows.values() if r['kind']=='conditional'];supplement=[r for r in rows.values() if r['kind']=='independent_N02_recovery']
    need(len(initial)==2 and sorted(r['mode'] for r in initial)==['fast','fine'] and len(extra)<=1 and len(supplement)<=2 and len(rows)==len(initial)+len(extra)+len(supplement),'SCIENCE_ALLOCATION_COUNT')
    need(all(type(v)is int and v>=0 for r in rows.values() for v in r['budget'].values()),'SCIENCE_ALLOCATION_COUNTER_TYPE_UNKNOWN')
    row=rows[allocation];need(row['mode']==c['mode'] and row['budget']==budget(c['mode']),'SCIENCE_ALLOCATION_BUDGET')
    if row['kind'] in ('conditional','independent_N02_recovery'):
        verify(row['engineering_condition']);p=strict_load(row['engineering_condition']['path'])
        need(p['status']=='ENGINEERING_REPAIR_ZERO_SCIENCE_PASS' and p['mode']==c['mode'] and p['numerical_resource_identity_hard_failure'] is False and p['prior_tree_complete_actual_wait'] is True and p['executor_build']==c['executor_build']['sha256'],'SCIENCE_CONDITIONAL_NOT_QUALIFIED')
    if row['kind']=='independent_N02_recovery':
        exact={'independent-fine-retain-1':('2143d24b912cf948ef642bb2075d0954a6d1e66af2b9051b5535c2086ef8e60b','N02-independent-authority-v1.json'),'independent-fine-contract-2':('0c95714942feb3ec918bfb28ff21e0a1b4fa02ca9ca069241bb5ec93a81210a8','N02-contract-authority-v2.json')}
        need(allocation in exact and c['mode']=='fine' and row['supplement_authority']['sha256']==exact[allocation][0] and Path(row['supplement_authority']['path'])==E/'science'/exact[allocation][1],'SCIENCE_INDEPENDENT_N02_SCOPE')
        verify(row['supplement_authority'])
    return allocation,a

def reserve(path,spec):
    from .context import context
    c=context(spec)
    if _new(c) and c['TEST_ONLY']:
        from .independent_science import reserve_frame
        return reserve_frame(path,spec,c)
    q=authorize(c['qualification']['path'],c['request']);need(q['phase']=='native','NATIVE_DRY_ENTRY_NO_COMPUTE')
    allocation,_=_allocation(c);root=_root(c)
    if c['mode']=='fine':
        gate=strict_load(root/'N01-numerical-and-release-pass.json');need(gate['status']=='N01_NUMERICAL_RELEASE_PASS' and gate['all_numerical_bridges'] is True and gate['public_released'] is True,'N01_BEFORE_N02_REQUIRED');verify(gate['result']);verify(gate['reclaim'])
    return _claim(root,allocation,c['mode'],path,binding(c['manifest_path']))
def _reservation(c):
    rows=read_events(_root(c));released={x['reservation'] for x in rows if x['kind']=='zero_release'}
    matches=[x for x in rows if x['kind']=='reserve' and x['context']==binding(c['manifest_path']) and x['reservation'] not in released]
    need(len(matches)==1,'SCIENTIFIC_RESERVATION_UNKNOWN');return matches[0]
def release_zero(c,proof):
    r=_reservation(c);return _release_zero(_root(c),r['reservation'],proof)
def start(c,component,counts):
    root=_root(c)
    if _new(c):
        from .independent_resources import stage
        stage(c,component)
    need(component in COMPONENTS and counts==COMPONENTS[component] and (component!='CPU' or c['mode']=='fine'),'SCIENCE_COMPONENT_BUDGET')
    with locked(root/'science/ledger.lock'):
        r=_reservation(c);rows=read_events(root);need(not any(x['kind']=='start' and x.get('reservation')==r['reservation'] and x.get('component')==component for x in rows),'SCIENCE_COMPONENT_ALREADY_STARTED')
        for k,v in counts.items():need(sum(x['counts'].get(k,0) for x in rows if x['kind']=='start')+v<=(_independent_total(c) if _new(c) else TOTAL)[k],'SCIENCE_GLOBAL_START_CAP:'+k)
        append(dict(kind='start',allocation=r['allocation'],reservation=r['reservation'],component=component,counts=counts,before_actual_call=True),root);atomic_json(Path(r['operation_path'])/('science-start-'+component+'.json'),dict(reservation=r['reservation'],component=component,counts=counts,before_actual_call=True))
def authorize_worker(c,role,operation):
    root=_root(c)
    component='R0' if role=='r0' else 'CPU'
    with locked(root/'science/ledger.lock'):
        r=_reservation(c);rows=read_events(root);need(Path(r['operation_path'])==operation and sum(x['kind']=='start' and x.get('reservation')==r['reservation'] and x.get('component')==component for x in rows)==1 and not any(x['kind']=='worker_claim' and x.get('reservation')==r['reservation'] and x.get('component')==component for x in rows),'SCIENCE_WORKER_UNIQUE_START_REQUIRED')
        from ..winproc import current_identity
        append(dict(kind='worker_claim',reservation=r['reservation'],component=component,identity=current_identity(),before_preload_math=True),root)
def complete(c,component,counts,receipt):
    root=_root(c)
    verify(receipt)
    with locked(root/'science/ledger.lock'):
        r=_reservation(c);rows=read_events(root);starts=[x for x in rows if x['kind']=='start' and x.get('reservation')==r['reservation'] and x.get('component')==component]
        need(len(starts)==1 and counts==starts[0]['counts'] and not any(x['kind']=='complete' and x.get('reservation')==r['reservation'] and x.get('component')==component for x in rows),'SCIENCE_COMPLETION_WITHOUT_UNIQUE_START')
        if component=='CPU':
            ack=strict_load(receipt['path']);need(ack['complete'] is True and ack['four_fields_per_ACK'] is True and ack['cores']==28 and ack['blocks']==7008 and len(ack['fields'])==112,'SCIENCE_CPU_ACK_GEOMETRY')
        append(dict(kind='complete',allocation=r['allocation'],reservation=r['reservation'],component=component,counts=counts,receipt=receipt,completed=True,count_provenance='original worker counts' if component=='R0' else 'fixed geometry and complete original four-field ACKs' if component=='CPU' else 'original source decode receipt'),root)
def host_count(host,delta):
    c=host.context;root=_root(c)
    if _new(c) and c['TEST_ONLY']:
        need(all(type(v)is int and v==0 for v in delta.values()),'INDEPENDENT_FRAME_UNEXPECTED_SCIENCE_OR_IO')
        atomic_json(host.session/'io-counts.json',host.counts);return
    c=host.context;_,a=_allocation(c);recovery=a.get('output_recovery',False);need(type(recovery)is bool,'SCIENCE_OUTPUT_RECOVERY_UNKNOWN')
    limits=dict(budget(c['mode']),source_copy=1,mix_traversals=10)
    if recovery:
        for k,v in dict(qualification_quantization=6,preview=4,preview_JPG=4,native_JPG_encode=2,native_JPG_full_readback=2).items():limits[k]+=v
    if _new(c):
        from .independent_science import host_limits
        limits,independent_global=host_limits(c)
    need(all(type(v)is int and v>=0 for v in delta.values()),'NATIVE_COUNT_UNKNOWN')
    with locked(root/'science/ledger.lock'):
        r=_reservation(c);rows=read_events(root)
        global_limits=dict(qualification_quantization=9+(6 if recovery else 0),preview=8+(4 if recovery else 0),preview_JPG=8+(4 if recovery else 0),native_JPG_encode=4+(2 if recovery else 0),native_JPG_full_readback=4+(2 if recovery else 0))
        if _new(c):global_limits=independent_global
        for k,v in delta.items():
            need(k in limits and host.counts.get(k,0)+v<=limits[k],'NATIVE_COUNT_LIMIT:'+k)
            if k in global_limits:need(sum(x['delta'].get(k,0) for x in rows if x['kind']=='host_counter')+v<=global_limits[k],'NATIVE_GLOBAL_IO_LIMIT:'+k)
        for k,v in delta.items():host.counts[k]=host.counts.get(k,0)+v
        append(dict(kind='host_counter',allocation=r['allocation'],reservation=r['reservation'],delta=delta,counts=dict(host.counts),call_reservation_not_completion=True),root);atomic_json(host.session/'io-counts.json',host.counts)
