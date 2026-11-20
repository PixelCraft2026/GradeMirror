"""New task reservations never transfer/refund historical or failed budgets."""
import json
import os
import time
from pathlib import Path
from ..common import strict_load, canonical_json, atomic_json
from ..store import locked
from ..cpu_replay.contract import need,verify,binding
from .contract import E, context

INITIAL = dict(fit=3, updates=1004, forward=2292, render=4, state_load=4, Field=7,
               source_decode=2, reference_decode=1, native_quantization=6, native_JPG_encode=3,
               native_JPG_readback=3, native_preview=4, preview_JPG=4)
ZERO = ('P3', 'CG', 'LLF', 'Q', 'GPU', 'NVML')


def _conditional(c,rows,*,probe=False):
    plan=E/'conditional-small-cold.json'
    if not plan.is_file():return None
    value=strict_load(plan)
    if c['id'] not in value.get('contexts',[]):return None
    expected={'schema','stage','contexts','original_operation','original_error','original_wait','original_snapshot','precompute_proof','p0','executor_build','bundle_id','math_id','budget'}
    need(set(value)==expected and value['schema']=='gm053-single-conditional-stage-v1' and value['stage']=='P1-small-cold' and
         value['contexts']==[c['id']] and c['TEST_ONLY'] is True and c['phase']=='render' and
         value['executor_build']==c['executor_build']['sha256'],'CONDITIONAL_STAGE_SCOPE')
    need(not any(row['allocation']=='conditional' for row in rows),'CONDITIONAL_PACKAGE_ALREADY_SPENT')
    for key in ('original_error','original_wait','original_snapshot','precompute_proof','p0'):
        need(Path(value[key]['path']).resolve().is_relative_to(E),'CONDITIONAL_EVIDENCE_SCOPE')
        verify(value[key])
    wait=strict_load(Path(value['original_wait']['path']));error=strict_load(Path(value['original_error']['path']));p0=strict_load(Path(value['p0']['path']))
    need(all(wait[k] is True for k in ('actual_all_wait','actual_root_wait','tree_empty','exact')) and
         all(row['actual_handle_wait'] is True and row['job_member_verified'] is True and 'gm053_worker.py' not in row['command_line'] for row in wait['records']) and
         wait['actual_members']==len(wait['records']),'CONDITIONAL_OLD_TREE_UNKNOWN')
    # Cold workers never reopen producer data. The zero-science controller
    # verifies and freezes the old failure's bytes and pre-compute disposition.
    proof=strict_load(Path(value['precompute_proof']['path']))
    need(proof['status']=='SAVED_PRECOMPUTE_EVIDENCE' and proof['original_operation']==value['original_operation'] and
         proof['archived_spec']==value['original_snapshot'] and proof['original_error']==value['original_error'] and
         proof['original_wait']==value['original_wait'] and
         (proof['original_spec']['bytes'],proof['original_spec']['sha256'])==(value['original_snapshot']['bytes'],value['original_snapshot']['sha256']) and
         proof['compute_go_absent'] is True and proof['R0_directory_absent'] is True and proof['no_numeric_worker_seen'] is True,
         'CONDITIONAL_ORIGINAL_FAILURE_PROOF')
    old=strict_load(Path(value['original_snapshot']['path']))
    need(old['semantics']['TEST_ONLY'] is True and old['semantics']['source_sha256']==c['source']['sha256'] and
         (old['semantics']['bundle_id'],old['semantics']['math_id'])==(value['bundle_id'],value['math_id']),'CONDITIONAL_ALREADY_COMPUTED')
    need(error['type']=='PermissionError' and error['error'].startswith('COLD_PRODUCER_DATA_READ_FORBIDDEN:') and
         '/pools/' in error['error'].replace('\\','/').lower() and p0['status']=='P0B_PASS' and
         p0.get('cold_own_pool_and_producer_isolation') is True,'CONDITIONAL_NOT_LOCATED_ENGINEERING')
    cap={k:0 for k in INITIAL};cap.update(forward=1,render=1,state_load=1,Field=1)
    need(value['budget']==c['science']==cap and c['bundle']['bundle_id']==value['bundle_id'] and
         c['bundle']['math_id']==value['math_id'],'CONDITIONAL_SCIENCE_CAP')
    if not probe:
        gate=strict_load(E/'effective-groups.json')['P0B'];verify(gate);result=strict_load(Path(gate['path']))
        need(result['status']=='P0B_PASS' and result.get('conditional_dryrun_plan')==binding(plan) and
             result.get('conditional_probe_zero') is True,'CONDITIONAL_GUARDED_DRYRUN_REQUIRED')
    return dict(plan=binding(plan),stage=value['stage'])


def reserve(path, spec):
    c = context(spec)
    budget = c['science']
    need(set(budget) == set(INITIAL) and all(type(v) is int and v >= 0 for v in budget.values()), 'FIRST_FIT_SCIENCE_FIELDS')
    if any(budget.values()):
        gates=strict_load(E/'effective-groups.json')
        required=['P0A','P0B','P0C']+(['P1','P1cancel'] if not c['TEST_ONLY'] else [])
        for name in required:
            need(name in gates,'FIRST_FIT_PREDECESSOR_REQUIRED:'+name)
            verify(gates[name]);result=strict_load(Path(gates[name]['path']))
            need(result['status'] in ('P0A_PASS','P0B_PASS','P0C_PASS','P1_PASS','P1_CANCEL_PASS'),'FIRST_FIT_PREDECESSOR_FAILED')
    with locked(E / 'science.lock'):
        book = E / 'science.jsonl'
        rows = [json.loads(v) for v in book.read_text('utf8').splitlines() if v] if book.is_file() else []
        need(not any(r['context'] == c['id'] for r in rows), 'FIRST_FIT_PHASE_ALREADY_RESERVED')
        conditional=_conditional(c,rows)
        if not conditional:
            need(all(sum(r['budget'][k] for r in rows if r['allocation']=='initial')+budget[k] <= INITIAL[k] for k in INITIAL), 'FIRST_FIT_INITIAL_SCIENCE_CAP')
        row = dict(context=c['id'], operation_path=str(path), job_id=spec['job_id'], attempt_id=spec['attempt_id'],
                   before_decode_model=True, wall=time.time(), budget=budget, allocation='conditional' if conditional else 'initial',
                   conditional=conditional,zero={k:0 for k in ZERO})
        with book.open('ab') as f:
            f.write(canonical_json(row)+b'\n'); f.flush(); os.fsync(f.fileno())
        atomic_json(Path(path) / 'scientific-reservation.json', row)
    return row


def host_count(host, delta):
    c = host.context
    caps = dict(source_decode=c['science']['source_decode'], reference_decode=c['science']['reference_decode'],
                R0_render=c['science']['render'], forward=c['science']['forward'],
                qualification_quantization=c['science']['native_quantization'] if not c['TEST_ONLY'] else 2,
                preview=c['science']['native_preview'] if not c['TEST_ONLY'] else 12,
                preview_JPG=c['science']['preview_JPG'] if not c['TEST_ONLY'] else 12,
                native_JPG_encode=c['science']['native_JPG_encode'] if not c['TEST_ONLY'] else 4,
                native_JPG_full_readback=c['science']['native_JPG_readback'] if not c['TEST_ONLY'] else 4,
                CPU256_service=0, source_copy=1)
    for k,v in delta.items():
        need(type(v) is int and v >= 0, 'FIRST_FIT_COUNT_DELTA')
        need(k not in caps or host.counts.get(k,0)+v <= caps[k], 'FIRST_FIT_HOST_CAP:'+k)
        host.counts[k] = host.counts.get(k,0)+v
    atomic_json(host.session / 'io-counts.json', host.counts)
