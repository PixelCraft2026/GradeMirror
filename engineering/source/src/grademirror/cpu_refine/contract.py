"""Exact E54 authority; independent from the historical fixed E53 domain."""
import os
from pathlib import Path
from ..common import strict_load, writable_path
from ..cpu_replay.contract import ROOT, BACKEND, FLAGS, need, binding, verify, executor_closure

E=ROOT/'docs/evidence/gm-054/20261030-v1'
W=ROOT/'tests/workspaces/g54/20261030-v1'
FIELDS={'schema','id','phase','mode','TEST_ONLY','metadata_only','executor_build','runtime','interpreters',
        'source','reference','source_identity','reference_identity','settings','profile','color_policy',
        'output_root','evidence_root','pool_root','bundle','parent_bundle','science','flags','lease_seconds',
        'request_seconds','prepared_clock_shift','fault_point','fault_action','pause_at','force_grace_seconds'}
FAULTS={'','before_startup','after_claim','after_mkdir','after_spec','before_owner','owner_created',
        'prepared','before_commit','after_commit','before_success','release_pending','proof_written',
        'pool_freed','bundle_before_complete','bundle_after_complete','cleanup_once','before_LLF'}


def load(path,*,cleanup=False):
    path=Path(path).resolve();need(path.parent==E/'contexts','REFINE_CONTEXT_POINTER')
    c=strict_load(path);index=strict_load(E/'contexts/index.json')
    need(type(c) is dict and set(c)==FIELDS and c['schema']=='cpu-refine-execution-v1' and
         index.get(c['id'])==binding(path),'REFINE_CONTEXT_FIELDS')
    need(c['mode']=='fine' and c['phase'] in ('refine','render','frame','entry','small') and
         type(c['TEST_ONLY']) is bool and type(c['metadata_only']) is bool and c['flags']==FLAGS,
         'REFINE_CONTEXT_MODE')
    need(all(type(value) is bool for value in c['flags'].values()),'REFINE_FLAG_TYPES')
    need(Path(c['output_root']).resolve()==W/c['id'] and Path(c['evidence_root']).resolve()==E and
         Path(c['pool_root']).resolve().parent==W/'pools','REFINE_CONTEXT_SCOPE')
    need(cleanup or c['executor_build']==executor_closure(),'REFINE_BUILD_CHANGED')
    need(c['fault_point'] in FAULTS and c['fault_action'] in ('raise','exit') and
         c['pause_at'] in ('','before_ready','before_compute','prepared') and
         c['force_grace_seconds'] in (1,30),'REFINE_TEST_INSTRUCTION')
    shape=c['source_identity']['shape']
    if c['phase'] in ('frame','entry'):
        need(c['TEST_ONLY'] and c['metadata_only']==(c['phase']=='frame') and len(shape)==3 and shape[2]==3 and
             all(type(x) is int and 1<=x<=64 for x in shape[:2]),'REFINE_FRAME_SCOPE')
        if c['phase']=='entry':need(not any(c['science'].values()),'REFINE_ENTRY_ZERO_SCIENCE')
    elif c['phase']=='small':
        need(c['TEST_ONLY'] and not c['metadata_only'] and shape==[771,67,3],'REFINE_SMALL_SCOPE')
    else:
        need(not c['TEST_ONLY'] and not c['metadata_only'] and shape==[7008,4672,3] and
             c['source_identity']['dtype']=='uint8' and c['lease_seconds']==7200 and
             c['fault_point']=='' and c['pause_at']=='' and c['force_grace_seconds']==30,
             'REFINE_NATIVE_SCOPE')
    need(0<c['request_seconds']<=120 and 0<c['lease_seconds']<=7200 and
         c['prepared_clock_shift'] in (0,7000),'REFINE_CLOCK_SCOPE')
    if not cleanup:
        for item in c['interpreters'].values(): verify(item)
        need(c['runtime']['status']=='KNOWN_EXACT_RUNTIME','REFINE_RUNTIME_UNKNOWN')
        verify(c['runtime']['inventory'])
    return c


def context(spec,*,cleanup=False):
    sem=spec.get('semantics',spec);verify(sem['instance_context']);c=load(sem['instance_context']['path'],cleanup=cleanup)
    need(sem.get('cpu_refine') is True and sem['mode']=='fine' and
         sem['executor_build']==c['executor_build']['sha256'] and sem['output_root']==c['output_root'] and
         sem['source_sha256']==c['source']['sha256'] and sem.get('TEST_ONLY',False)==c['TEST_ONLY'],
         'REFINE_SESSION_CONTEXT')
    return c


def destination(value,c):
    path=writable_path(Path(value));need(path.is_absolute() and path.resolve().is_relative_to(Path(c['output_root'])),
                                      'REFINE_OUTPUT_SCOPE');return path


def validate_source(source,c):
    need(Path(source).is_absolute(),'REFINE_SOURCE_ABSOLUTE',exit_code=2)
    actual=binding(source)
    need((actual['bytes'],actual['sha256'])==(c['source']['bytes'],c['source']['sha256']),'REFINE_SOURCE_CHANGED')
    return actual
