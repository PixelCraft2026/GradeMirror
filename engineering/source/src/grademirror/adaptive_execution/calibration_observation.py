"""Default-off GM063 read-only observation; no monitor or numerical imports."""
import hashlib,json,os,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
E=ROOT/'docs/evidence/gm-063/20261104-v1'


def enabled():
    pointer=os.environ.get('GM063_OBSERVATION')
    if not pointer:return False
    p=Path(pointer).resolve()
    if p!=E/'observation.json':raise RuntimeError('GM063_OBSERVATION_SCOPE')
    value=json.loads(p.read_text(encoding='utf-8'))
    if value!={'schema':'gm063-readonly-observation-v1','task':'GM-063','enabled':True,
               'taskbook_sha256':'3f8e2b7a70eeeb30ddc69f2dcf88ca33ac01bfc555614ffab6a86a13556c7860'}:
        raise RuntimeError('GM063_OBSERVATION_CONFIG')
    return True


def process(identity):
    if not enabled():return None
    from ..winproc import process_peak_memory
    return process_peak_memory(identity)


def facts():
    if not enabled():return None
    from .memory_snapshot import ram_facts
    value=ram_facts()
    return dict(at=time.time(),monotonic=time.monotonic(),physical=value['physical'],
                commit=value['commit'],legacy_process_commit_available=value['legacy_process_commit_available'])


def cuda_peaks():
    """Never import torch, initialize CUDA/NVML or reset its allocator peaks."""
    if not enabled():return None
    torch=sys.modules.get('torch')
    if torch is None or not torch.cuda.is_initialized():return None
    return dict(allocated_lifetime_peak_bytes=torch.cuda.max_memory_allocated(),
                reserved_lifetime_peak_bytes=torch.cuda.max_memory_reserved(),
                source='existing CUDA context / no peak reset')


def stage(path,name,**details):
    if not enabled():return
    from ..winproc import current_identity,process_memory
    who=current_identity();p=Path(path).resolve()
    if not (p.is_relative_to(E) or p.is_relative_to(ROOT/'tests/workspaces/g63/20261104-v1')):
        raise RuntimeError('GM063_OBSERVATION_WRITE_SCOPE')
    row=dict(stage=name,at=time.time(),monotonic=time.monotonic(),identity=who,
             current=process_memory(who),OS_lifetime_peaks=process(who),system=facts(),
             cuda=cuda_peaks(),details=details)
    p.mkdir(parents=True,exist_ok=True)
    with (p/'measurement-stages.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n')
