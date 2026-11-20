"""Fixed GM023 qualification prepayment and new-stage OS admission."""
import json,os,time
from pathlib import Path
from ..common import GMError,strict_load,atomic_json
from ..store import locked
from ..hybrid_replay.audit import append

START_CAPS=dict(P0=48,P1=240,P2=96,closeout=16)
SCIENCE_CAPS=dict(R0_fits=1,optimizer_updates=1000,backward_updates=1000,training_forwards=2001,
    model_forward_batches=2281,R0_renders=2,trained_R0_loads=1,source_decodes=2,reference_decodes=1,
    H64_builds=1,T64_normalizations=1,P3_solvers=1,scalar_CG=3,CPU_range_discoveries=1,CPU_global_scans=2,
    scalar_LLF_footprints=168,remap_pyramids=18088,D_calls=127792,U_calls=254408,scalar_compositions=84,
    P3_scalar_evaluations=676972800,base_remap_pixels=82322022912,qualification_quantization_passes=12,
    extra_internal_quantization=2,preview_generations=16,preview_JPG=6,export_requests=4,host_export_commands=2,native_JPG_encodes=2,
    native_export_full_readbacks=2,header_probe_calls=12)

def qualification(kind,label,counts):
    root=Path(os.environ['GM021_QUALIFICATION_ROOT']).resolve()
    expected=Path(__file__).resolve().parents[3]/'docs/evidence/gm-023/20261023-v1'
    if root!=expected:raise GMError('QUALIFICATION_ROOT_INVALID','GM023 root',7)
    q=strict_load(root/'queue.json')
    supplement=root/'continuation-6g.json'
    if supplement.is_file():
        extra=strict_load(supplement)
        if extra['physical_bytes']!=6*1024**3 or extra['commit_bytes']!=16*1024**3 or extra['stage_physical_margin_bytes']!=1024**3:
            raise GMError('QUALIFICATION_CONFIG_INVALID','GM023 startup supplement',7)
        q['launch_labels']={**q['launch_labels'],**extra['launch_labels']}
    phase=os.environ['GM021_PHASE'];prefix=os.environ['GM021_RUN_LABEL'];label=prefix+':'+label
    caps=dict(START_CAPS);global_cap=400
    console=root/'console-repair-authorization.json'
    if console.is_file():
        approved=strict_load(console)
        if approved['P1_launch_cap']!=245 or approved['global_launch_cap']!=400 or approved['science_caps_changed']is not False:
            raise GMError('QUALIFICATION_CONFIG_INVALID','GM023 console supplement',7)
        caps['P1']=245;q['launch_labels']={**q['launch_labels'],**approved['launch_labels']}
    solver=root/'solver-repair-authorization.json'
    if solver.is_file():
        approved=strict_load(solver)
        if approved['P1_launch_cap']!=258 or approved['global_launch_cap']!=400 or approved['science_caps_changed']is not False:
            raise GMError('QUALIFICATION_CONFIG_INVALID','GM023 SOLVER supplement',7)
        caps['P1']=258;q['launch_labels']={**q['launch_labels'],**approved['launch_labels']}
    cancellation=root/'T04-product-repair-authorization.json'
    if cancellation.is_file():
        approved=strict_load(cancellation)
        if approved['authorized']is not True or approved['base_P1_cap']!=291 or approved['global_cap']!=405 or approved['science_caps_changed']is not False:
            raise GMError('QUALIFICATION_CONFIG_INVALID','GM023 T04 product supplement',7)
        caps['P1']=291;global_cap=405;q['launch_labels']={**q['launch_labels'],**approved['launch_labels']}
    cleanup=root/'T05-cleanup-authorization.json'
    if cleanup.is_file():
        approved=strict_load(cleanup)
        if approved['authorized']is not True or approved['base_P1_cap']!=319 or approved['global_cap']!=437 or approved['science_caps_changed']is not False:
            raise GMError('QUALIFICATION_CONFIG_INVALID','GM023 T05 cleanup supplement',7)
        caps['P1']=319;global_cap=437;q['launch_labels']={**q['launch_labels'],**approved['launch_labels']}
    if q['launch_caps']!=START_CAPS or q['science_caps']!=SCIENCE_CAPS:raise GMError('QUALIFICATION_CONFIG_INVALID','GM023 caps changed',7)
    if any(type(v)is not int or v<0 for v in counts.values()):raise GMError('QUALIFICATION_COUNT_INVALID',label,7)
    file=root/('starts.jsonl'if kind=='starts'else 'science.jsonl')
    with locked(root/'accounting.lock'):
        old=[json.loads(x)for x in file.read_text(encoding='utf-8').splitlines()]if file.exists()else[]
        if any(x['label']==label for x in old):raise GMError('DUPLICATE_QUALIFICATION_CALL',label,7)
        if kind=='starts':
            require_label=q['launch_labels'].get(label)
            if not require_label:raise GMError('UNPLANNED_START',label,7)
            asked=counts['count'];reserved=require_label['count']
            if asked!=require_label['python_entries']:raise GMError('START_MEMBER_EXPECTATION_CHANGED',label,7)
            counts=dict(count=reserved)
            external=[]
            if cancellation.is_file():
                for name in ('independent-root-starts.jsonl','independent-identity-repair-starts.jsonl'):
                    source=root/name
                    if source.exists():external.extend(json.loads(v)for v in source.read_text(encoding='utf-8').splitlines()if v)
            if sum(x['counts']['count']for x in old if x['phase']==phase)+reserved>caps[phase]or sum(x['counts']['count']for x in old+external)+reserved>global_cap:raise GMError('START_CAP',label,7)
        elif phase=='P2':
            for k,v in counts.items():
                if k not in SCIENCE_CAPS or sum(x['counts'].get(k,0)for x in old if x['phase']=='P2')+v>SCIENCE_CAPS[k]:raise GMError('SCIENCE_CAP',k,7)
        elif phase=='P1':
            allowed=dict(source_small_JPG_decodes=q['P1_small_decode_cap'],saved_field_file_loads=q['P1_saved_field_load_cap'],
                preview_generations=128,preview_JPG=24,export_requests=24,native_JPG_encodes=24,native_export_full_readbacks=24,
                qualification_quantization_passes=q['P1_quantization_cap'],header_probe_calls=q['P1_header_probe_cap'])
            if any(k not in allowed and v for k,v in counts.items()):raise GMError('GM023_P1_NEW_ALGORITHM_FORBIDDEN',label,7)
            for k,v in counts.items():
                if k in allowed and sum(x['counts'].get(k,0)for x in old if x['phase']=='P1')+v>allowed[k]:raise GMError('GM023_P1_IO_CAP',k,7)
        elif any(counts.values()):raise GMError('SCIENCE_OUTSIDE_P2',label,7)
        append(file,dict(label=label,phase=phase,counts=counts,parent_pid=os.getpid(),wall=time.time(),monotonic=time.monotonic(),prepaid_before_call=True))

def record_launch(label,argv,cwd):
    root=os.environ.get('GM021_QUALIFICATION_ROOT')
    if root!=str(Path(__file__).resolve().parents[3]/'docs/evidence/gm-023/20261023-v1'):return
    full=os.environ['GM021_RUN_LABEL']+':'+label
    append(Path(root)/'launch-details.jsonl',dict(label=full,argv=list(map(str,argv)),cwd=str(cwd),parent_pid=os.getpid(),
        before_spawn=True,wall=time.time(),out=None))

def bridge(path,stage):
    """No old expected arrays enter a compute worker; an external saved-data
    comparator may acknowledge a qualification boundary. No public provider."""
    root=os.environ.get('GM021_QUALIFICATION_ROOT')
    if not root or os.environ.get('GM021_PHASE')!='P2':return
    path=Path(path);request=path/('bridge-'+stage+'.json');ack=path/('bridge-'+stage+'-ack.json')
    if request.exists()or ack.exists():raise GMError('QUALIFICATION_BRIDGE_REUSED',stage,7)
    from .validation_protocol import validate_bridge_stage
    validate_bridge_stage(path,stage)
    atomic_json(request,dict(stage=stage,operation_path=str(path),wall=time.time(),no_expected_array_payload=True))
    end=time.monotonic()+30
    while not ack.is_file():
        if(path/'cancel.signal').exists():raise GMError('CANCELLED','Qualification boundary cancelled',8)
        if time.monotonic()>end:raise GMError('QUALIFICATION_BRIDGE_TIMEOUT',stage,7)
        time.sleep(.01)
    reply=strict_load(ack)
    if reply.get('stage')!=stage or reply.get('pass')is not True:raise GMError('QUALIFICATION_NUMERICAL_BRIDGE_FAILED',stage,7)

def admit(path,plan,stage,*,qualification=False,cancelled=lambda:False):
    from ..resource_execution.admission import recheck
    recheck(path,stage)
    from ..hybrid_replay.resources import memory
    required=plan['capacities'][stage];physical=required['additional_bytes']+plan['capacities']['physical_margin']
    commit=required['minimum_commit_headroom']
    from ..light.resources import mixed_stage
    physical,commit=mixed_stage(plan,stage,physical,commit)
    if qualification:physical=max(physical,6*1024**3);commit=max(commit,16*1024**3)
    for attempt in range(3):
        if cancelled():raise GMError('CANCELLED','Before new-stage allocation',8)
        try:
            now=memory();valid=all(type(now.get(k))is int and now[k]>=0 for k in ('available','commit_headroom'))
            row=dict(stage=stage,attempt=attempt,complete=valid,wall=time.time(),required_physical=physical,required_commit=commit,**now)
        except (OSError,ValueError,GMError)as exc:valid=False;row=dict(stage=stage,attempt=attempt,complete=False,wall=time.time(),error=str(exc))
        append(Path(path)/'resource-admission.jsonl',row)
        if valid:break
        if attempt<2:time.sleep(.02)
    if not valid:raise GMError('CRITICAL_UNKNOWN','New stage RAM/commit unknown after bounded raw attempts',5)
    if now['available']<physical or now['commit_headroom']<commit:raise GMError('HYBRID_FIT_STAGE_RESOURCE_INSUFFICIENT',stage,5)
    return now
