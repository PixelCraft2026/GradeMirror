"""Aggregate, prepaid native permissions. Idempotent queries never reserve again."""
import os
import time
from pathlib import Path
from ..common import atomic_json, strict_load, canonical_json
from ..store import locked
from .contract import authority, need, binding, verify


def reserve(path, spec):
    sem = spec['semantics']
    if 'preview_version' in sem:
        from ..preview.contract import SCHEMA, context
        c = context(spec)
        row = dict(schema=SCHEMA, request=sem['instance_context'], job_id=spec['job_id'],
                   attempt_id=spec['attempt_id'], key=c['key'])
        with (path / 'scientific-reservation.json').open('xb') as stream:
            stream.write(canonical_json(row) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        return row
    if sem.get('adaptive_application') is True:
        from ..adaptive_execution.runtime import reserve as application_reserve
        return application_reserve(path,spec)
    if sem.get('cpu_refine') is True:
        from ..cpu_refine.science import reserve as refine_reserve
        return refine_reserve(path,spec)
    if sem.get('instance_context'):
        from ..cpu_first_fit.science import reserve as first_fit_reserve
        return first_fit_reserve(path, spec)
    if sem.get('TEST_ONLY'):
        return None
    root = Path(authority()['evidence_root'])
    book = root / 'native-science.jsonl'
    with locked(root / 'native-science.lock'):
        rows = []
        if book.is_file():
            import json
            rows = [json.loads(line) for line in book.read_text('utf-8').splitlines() if line]
        mode = sem['mode']
        existing = [r for r in rows if r['mode'] == mode]
        allocation = 'initial'
        if existing:
            need(mode == 'fast' and len(existing) == 1 and not any(r.get('allocation') == 'conditional' for r in rows), 'NATIVE_PHASE_ALREADY_RESERVED')
            condition = strict_load(root / 'conditional-fast.json')
            need(set(condition) == {'schema','mode','original_failure','original_wait','original_operation_path','current_executor_build','p0','classification'}, 'CONDITIONAL_FIELDS')
            need(condition['schema'] == 'gm052-single-conditional-fast-v1' and condition['mode'] == 'fast'
                 and condition['classification'] == 'LOCATED_PRE_OWNER_NUL_GUARD_NON_NUMERICAL'
                 and condition['current_executor_build'] == sem['executor_build']
                 and condition['original_operation_path'] == existing[0]['operation_path'], 'CONDITIONAL_SCOPE')
            for field in ('original_failure', 'original_wait', 'p0'):
                verify(condition[field])
            old_wait = strict_load(Path(condition['original_wait']['path']))
            failure = strict_load(Path(condition['original_failure']['path']))
            p0 = strict_load(Path(condition['p0']['path']))
            need(all(old_wait[k] is True for k in ('actual_root_wait','actual_all_wait','tree_empty','exact'))
                 and p0['status'] == 'T03_PASS' and p0['algorithm_calls'] == 0
                 and p0.get('real_client_write_guard') is True, 'CONDITIONAL_PRECHECK_OR_WAIT')
            need(failure.get('error_code') == 'CPU_REPLAY_WRITE_SCOPE' and old_wait['actual_members'] == 6
                 and all(not any(token in r['command_line'] for token in
                     ('gm003_session_owner.py', 'gm003_session_host.py', 'gm052_worker.py')) for r in old_wait['records']),
                 'CONDITIONAL_NOT_PRE_OWNER_FAILURE')
            old_spec = strict_load(Path(existing[0]['operation_path']) / 'spec.json')
            need(all(old_spec['semantics'][k] == sem[k] for k in ('source_sha256','bundle_id','math_id','mode'))
                 and not (Path(existing[0]['operation_path']) / 'commit.json').exists(), 'CONDITIONAL_INPUT_CHANGED')
            allocation = 'conditional'
        if mode == 'fine':
            previous = next((r for r in reversed(rows) if r['mode'] == 'fast'), None)
            need(previous is not None, 'FAST_BEFORE_FINE_REQUIRED')
            old = Path(previous['operation_path'])
            need((old / 'commit.json').is_file() and strict_load(old / 'session/lease.json')['state'] == 'RELEASED',
                 'FAST_RELEASE_BEFORE_FINE_REQUIRED')
        budget = dict(R0_render=1, forward=140, Field=1, state_load=1, source_decode=1,
                      CPU256_service=1 if mode == 'fine' else 0, scalar_LLF_footprints=168 if mode == 'fine' else 0,
                      qualification_quantization=6 if mode == 'fine' else 3,
                      native_JPG_encode=2 if mode == 'fine' else 1,
                      native_JPG_full_readback=2 if mode == 'fine' else 1,
                      preview=5 if mode == 'fine' else 3, fit=0, CG=0, GPU=0, reference_decode=0)
        row = dict(mode=mode, allocation=allocation, operation_path=str(path), job_id=spec['job_id'], attempt_id=spec['attempt_id'],
                   intent_sha256=spec['intent_sha256'], before_decode_model=True, wall=time.time(), budget=budget,
                   original_failed_reservations_retained=True)
        with book.open('ab') as stream:
            stream.write(canonical_json(row)+b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        atomic_json(path / 'scientific-reservation.json', row)
        return row
