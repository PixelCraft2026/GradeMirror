"""Shared engineering workflow, bridge evidence and capacity contracts.
Pure metadata; never selects a producer, runtime callback or numerical backend.
"""
import hashlib,re
from pathlib import Path
from ..common import GMError,strict_load
FLOWS={'fit':('R0','H-T','b','P3','CPU'),'replay':('R0','CPU')}
ENTRIES={'R0':('tools/gm023_compute_worker.py','numeric'),
         'H-T':('tools/gm023_compute_worker.py','numeric'),
         'b':('tools/gm008_solver_worker.py','solver'),
         'P3':('tools/gm023_compute_worker.py','numeric'),
         'CPU':('tools/gm023_compute_worker.py','numeric')}
STAGE_KEYS={'R0':('R0_fits','trained_R0_loads','R0_renders','model_forward_batches'),
            'H-T':('H64_builds','T64_normalizations'),'b':('P3_solvers','scalar_CG'),
            'P3':('CPU_range_discoveries',),'CPU':('CPU_global_scans','scalar_LLF_footprints','remap_pyramids','D_calls','U_calls','scalar_compositions','P3_scalar_evaluations','base_remap_pixels')}
def workflow(operation):
    from .audit import SCIENCE_CAPS
    if operation not in FLOWS:raise GMError('VALIDATION_WORKFLOW','Unknown workflow',7)
    assert all(k in SCIENCE_CAPS for stage in FLOWS[operation]for k in STAGE_KEYS[stage])
    return tuple(dict(stage=s,entry=ENTRIES[s][0],runtime=ENTRIES[s][1],count_keys=STAGE_KEYS[s])for s in FLOWS[operation])
def count_keys():
    from .audit import SCIENCE_CAPS
    return frozenset(SCIENCE_CAPS)
def validate_bridge_stage(path,stage):
    path=Path(path);operation=strict_load(path/'execution-plan.json')['operation']
    stages=tuple(row['stage']for row in workflow(operation))
    if stage not in stages:raise GMError('QUALIFICATION_BRIDGE_STAGE',stage,7)
    index=stages.index(stage)
    for previous in stages[:index]:
        p=path/('bridge-'+previous+'-ack.json')
        if not p.exists():raise GMError('QUALIFICATION_BRIDGE_ORDER',stage,7)
        ack=strict_load(p)
        if ack.get('stage')!=previous or ack.get('pass')is not True:raise GMError('QUALIFICATION_BRIDGE_ORDER',stage,7)
    for future in stages[index+1:]:
        if(path/('bridge-'+future+'.json')).exists():raise GMError('QUALIFICATION_BRIDGE_ORDER',stage,7)
    return index
def bridge_evidence(stage,actual,expected,receipt):
    if stage not in ENTRIES:raise GMError('VALIDATION_STAGE',stage,7)
    if not isinstance(actual,dict)or actual!=expected:raise GMError('VALIDATION_HASH_OR_B',stage,7)
    required=('actual_wait','actual_worker_handle_wait','tree_empty')
    if not isinstance(receipt,dict)or any(receipt.get(k)is not True for k in required)or receipt.get('exit_code')!=0 or receipt.get('forced')is not False:
        raise GMError('VALIDATION_EXIT_RECEIPT',stage,7)
    return dict(stage=stage,**{'pass':True},saved_evidence=True)
def jpeg_category(file,intent,expected):
    p=Path(file)
    if p.suffix.lower()in('.jpg','.jpeg'):return 'JPG'
    if p.suffix.lower()!='.partial'or not isinstance(intent,dict):return 'ordinary'
    req=intent.get('request',{});key=expected['request_key']
    if intent.get('request_key')!=key or req.get('request_key')!=key:return 'ordinary'
    target=Path(expected['destination']).resolve()
    if p.resolve().parent!=target.parent or Path(intent.get('destination','')).resolve()!=target or Path(intent.get('temporary_path','')).resolve()!=p.resolve():return 'ordinary'
    prefix=hashlib.sha256(key.encode()).hexdigest()[:12]
    if re.fullmatch(r'\.gm004a-'+prefix+r'-[0-9a-f]{32}\.partial',p.name)is None:return 'ordinary'
    for k in ('format','bit_depth','width','height','execution_contract_sha256','execution_manifest_sha256'):
        if type(req.get(k))is not type(expected[k])or req[k]!=expected[k]:return 'ordinary'
    return 'JPG'if req['format']=='jpeg'and req['bit_depth']==8 else'ordinary'
def member_capacity(category,size):
    if type(size)is not int or size<0:raise GMError('STORAGE_UNKNOWN','Invalid size',7)
    cap=(32 if category=='JPG'else 8)*1024**2
    if size>cap:raise GMError('STORAGE_MEMBER_LIMIT',category,5)
    return cap
CAPACITY_KEYS=frozenset(('package_copy','logs','temporary_outputs','committed_outputs','failure_records','final_report'))
def future_capacity(used,planned,*,normal=192*1024**2,total=256*1024**2,closure_only=False):
    if type(used)is not int or used<0 or not isinstance(planned,dict)or set(planned)!=CAPACITY_KEYS or any(type(v)is not int or v<0 for v in planned.values()):
        raise GMError('STORAGE_UNKNOWN','Incomplete lifecycle estimate',7)
    amount=used+sum(planned.values());limit=total if closure_only else normal
    if amount>limit:raise GMError('STORAGE_PROJECTED_LIMIT','Lifecycle capacity unavailable',5)
    return dict(observed=used,planned=dict(planned),projected=amount,limit=limit,closure_only=closure_only)
