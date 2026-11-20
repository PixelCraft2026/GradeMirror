"""New reservations are independent, prepaid, never refunded or made UNKNOWN=0."""
import json
import os
import time
from pathlib import Path
from ..common import atomic_json,canonical_json,strict_load
from ..store import locked
from ..cpu_replay.contract import need,verify,binding
from .contract import E,context,ROOT


def initial():
    return strict_load(ROOT/'docs/evidence/gm-054/20261030-plan-v1/capacity.json')['science_initial']


def conditional(c,rows):
    plan=E/'conditional-small-CPU.json'
    if not plan.is_file():return None
    value=strict_load(plan)
    if c['id'] not in value['contexts']:return None
    need(set(value)=={'schema','stage','contexts','classification','original_context','original_reservation',
        'error','wait','disposition','p0','executor_build','saved','budget'} and
        value['schema']=='gm054-single-affected-stage-v1' and value['stage']=='P1-small-CPU' and
        value['classification']=='DUPLICATE_INTEROP_BEFORE_CPU_LEDGER' and c['phase']=='small' and c['TEST_ONLY'] is True and
        value['executor_build']==c['executor_build']['sha256'] and not any(row['allocation']=='conditional' for row in rows),
        'REFINE_CONDITIONAL_SCOPE')
    for key in ('original_reservation','error','wait','disposition','p0'):verify(value[key])
    wait=strict_load(Path(value['wait']['path']));disposition=strict_load(Path(value['disposition']['path']))
    error=strict_load(Path(value['error']['path']));p0=strict_load(Path(value['p0']['path']))
    need(all(wait[k] is True for k in ('actual_root_wait','actual_all_wait','tree_empty','exact')) and
         all(row['actual_handle_wait'] is True for row in wait['records']) and
         disposition['CPU_actual_ledger_absent'] is True and disposition['no_LLF_entered'] is True and
         disposition['fixture_generation_actual']==1 and disposition['all_mappings_cannot_reopen'] is True and
         error['type']=='RuntimeError' and 'set number of interop threads' in error['error'] and
         p0['status']=='T01_PASS' and p0['CPU_transport_setup_once'] is True,'REFINE_CONDITIONAL_PROOF')
    cap=strict_load(ROOT/'docs/evidence/gm-054/20261030-plan-v1/capacity.json')['science_conditional_single_affected_stage']
    need(value['budget']==c['science'] and all(c['science'][key]<=cap[key] for key in cap) and
         all(c['science'][key]==0 for key in ('fit','updates','R0_render','Field_forward','Field_construct','state_load',
               'area_H','target_T','P3_geometry','scalar_CG','fixture_generation','source_decode','reference_decode',
               'JPG_encode','JPG_readback','full_quantization','native_preview','preview_JPG')),
         'REFINE_CONDITIONAL_SINGLE_CPU_STAGE')
    for item in value['saved'].values():verify(item)
    original=[row for row in rows if row['context']==value['original_context']]
    need(len(original)==1 and original[0]['allocation']=='initial','REFINE_CONDITIONAL_ORIGINAL_RESERVATION')
    return binding(plan)


def independent_cold(c,rows):
    if c['id']!='v16-native-cold':return None
    authority=dict(path=str(E/'supplement-cold-1/science-authority.json'),bytes=2335,
        sha256='958cdb7b8874baac3278a9230d11952eefd98622634983bc9c4919ed08abf5c5')
    verify(authority);grant=strict_load(Path(authority['path']))
    need(grant['schema']=='gm054-explicit-independent-cold-science-v1' and
        grant['user_answer']=='批准这一次冷重放独立科学额度及原余额收尾' and
        c['phase']=='render' and c['TEST_ONLY'] is False and c['metadata_only'] is False and
        c['science']==grant['budget'] and not any(row['allocation']=='supplement-cold-1' for row in rows),
        'REFINE_INDEPENDENT_COLD_SCOPE')
    for key in ('failure','p0','prior_science','prior_seal','prior_final','move_map'):verify(grant[key])
    failure=strict_load(Path(grant['failure']['path']));p0=strict_load(Path(grant['p0']['path']))
    need(failure['error_code']=='CPU_REPLAY_WRITE_SCOPE' and failure['cold_IO_not_started'] is True and
        p0['status']=='T02_PASS' and p0['actual_runtime_guard'] is True and p0['input_unchanged'] is True and
        p0['new_science']==0,'REFINE_INDEPENDENT_COLD_PROOF')
    move=strict_load(Path(grant['move_map']['path']))
    need(Path(c['bundle']['directory']).resolve()==Path(move['resolved_destination']).resolve(),
         'REFINE_INDEPENDENT_COLD_BUNDLE')
    for item in move['files']:verify(item['moved'])
    need((E/'science.jsonl').read_bytes().startswith(Path(grant['prior_science']['path']).read_bytes()),
         'REFINE_ORIGINAL_SCIENCE_PREFIX')
    return authority


def reserve(path,spec):
    c=context(spec);budget=c['science'];cap=initial()
    need(c['phase']!='entry','REFINE_ENTRY_OPERATION_FORBIDDEN')
    need(set(budget)==set(cap) and all(type(v) is int and v>=0 for v in budget.values()),'REFINE_SCIENCE_FIELDS')
    if any(budget.values()):
        gates=strict_load(E/'effective-groups.json')
        for group in ('T01','T02','T03','T04','T05','T06')+(() if c['TEST_ONLY'] else ('P1','P1cancel')):
            need(group in gates,'REFINE_PREDECESSOR_REQUIRED:'+group);verify(gates[group])
            need(strict_load(Path(gates[group]['path']))['status']==group+'_PASS','REFINE_PREDECESSOR_FAILED')
    with locked(E/'science.lock'):
        book=E/'science.jsonl';rows=[json.loads(line) for line in book.read_text('utf8').splitlines() if line] if book.is_file() else []
        need(not any(row['context']==c['id'] for row in rows),'REFINE_PHASE_ALREADY_RESERVED')
        separate=independent_cold(c,rows);extra=None if separate else conditional(c,rows)
        if not extra and not separate:
            need(all(sum(row['budget'][key] for row in rows if row['allocation']=='initial')+budget[key]<=cap[key] for key in cap),'REFINE_INITIAL_SCIENCE_CAP')
        row=dict(context=c['id'],operation_path=str(path),before_decode_model=True,wall=time.time(),
                 budget=budget,allocation='supplement-cold-1' if separate else 'conditional' if extra else 'initial',
                 conditional=extra,independent_authority=separate,zero=dict(fit=0,updates=0,GPU=0,NVML=0,Q=0))
        with book.open('ab') as stream:stream.write(canonical_json(row)+b'\n');stream.flush();os.fsync(stream.fileno())
        atomic_json(Path(path)/'scientific-reservation.json',row)
    return row


def host_count(host,delta):
    c=host.context;b=c['science'];frame=c['phase']=='frame'
    caps=dict(source_decode=b['source_decode'],reference_decode=b['reference_decode'],source_copy=1,
        R0_render=b['R0_render'],forward=b['Field_forward'],CPU256_service=1 if b['scalar_LLF_footprints'] else 0,
        qualification_quantization=b['full_quantization'],preview=b['native_preview'],preview_JPG=b['preview_JPG'],
        native_JPG_encode=b['JPG_encode'],native_JPG_full_readback=b['JPG_readback'])
    if frame:
        caps.update(qualification_quantization=6,preview=0,preview_JPG=0,native_JPG_encode=0,native_JPG_full_readback=0)
    for key,value in delta.items():
        need(type(value) is int and value>=0 and (key in host.counts or key in ('reference_decode',)),'REFINE_COUNT_FIELD')
        need(key not in caps or host.counts.get(key,0)+value<=caps[key],'REFINE_HOST_CAP:'+key)
        host.counts[key]=host.counts.get(key,0)+value
    atomic_json(host.session/'io-counts.json',host.counts)
