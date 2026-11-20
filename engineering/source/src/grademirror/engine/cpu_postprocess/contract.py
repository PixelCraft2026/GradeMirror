"""Strict experimental execution manifest; standard library only."""
import hashlib,json,math,uuid
from pathlib import Path
from ...common import reject_reparse,writable_path
from . import EXECUTOR_ID

CONSTANTS=dict(sigma=.1,J=8,delta=1/64,range_margin=2/255,
               range_rule='pair-absolute-anchor-v1',P3_h=8,composition='FP32(O64+(LP64-LO64))')


def digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def runtime_torch():
    from ...light.runtime import operation
    plan=operation()
    if plan and plan["selection"]["backend"]=="cpu":
        return plan["environment"][plan["numeric_profile"]]["versions"]["torch"]
    return '2.5.1+cu124'


def validate(m):
    fields={'executor_id','job_id','attempt_id','owner','source','recipe','O','b','reference_wh','constants',
            'runtime','backend','core_rows','budget','deadline_monotonic','output_root'}
    if type(m)is not dict or set(m)!=fields:raise ValueError('EXECUTION_FIELDS')
    if m['executor_id']!=EXECUTOR_ID or m['backend']!='cpu':raise ValueError('EXECUTOR_VERSION')
    for key in ('job_id','attempt_id'):
        if str(uuid.UUID(m[key]))!=m[key]:raise ValueError('EXECUTION_ID')
    if m['constants']!=CONSTANTS or m['core_rows']not in (512,256):raise ValueError('EXECUTION_CONSTANTS')
    runtime=m['runtime']
    if runtime!=dict(torch=runtime_torch(),numpy='1.26.4',threads=4,interop=1):raise ValueError('RUNTIME_VERSION')
    for key in ('source','recipe','O','b'):
        item=m[key]
        if not isinstance(item,dict)or not isinstance(item.get('sha256'),str)or len(item['sha256'])!=64 or any(c not in '0123456789abcdef' for c in item['sha256']):raise ValueError('INPUT_HASH')
    h,w,c=m['O']['shape'];rw,rh=m['reference_wh']
    if any(type(v)is not int or v<=0 for v in (h,w,c,rw,rh))or c!=3 or m['O']['dtype']!='float32':raise ValueError('O_LAYOUT')
    if m['b']['dtype']!='float64' or m['b']['shape']!=[(math.ceil(rw/8)+1)*(math.ceil(rh/8)+1),3]:raise ValueError('B_LAYOUT')
    if not isinstance(m['owner'],dict)or not all(k in m['owner']for k in ('pid','creation_time','image_path')):raise ValueError('OWNER_ID')
    if not math.isfinite(m['deadline_monotonic']):raise ValueError('DEADLINE')
    if not isinstance(m['budget'],dict)or not m['budget']or any(type(v)is not int or v<0 for v in m['budget'].values()):raise ValueError('PREPAYMENT')
    root=Path(m['output_root'])
    if not root.is_absolute():raise ValueError('OUTPUT_ABSOLUTE')
    reject_reparse(root);writable_path(root)
    return m


class Coverage:
    """No duplicates, gaps, alias writes or stale identity can be committed."""
    def __init__(self,manifest):
        self.m=validate(manifest);self.next_row=0;self.complete=False

    def accept(self,job_id,attempt_id,start,end,shapes):
        h,w,_=self.m['O']['shape']
        if (job_id,attempt_id)!=(self.m['job_id'],self.m['attempt_id']):raise ValueError('BLOCK_IDENTITY')
        if self.complete or start!=self.next_row or end!=min(h,start+self.m['core_rows']):raise ValueError('BLOCK_ORDER')
        if set(shapes)!=set(('OP','LO','LP','Y'))or any(tuple(s)!=(end-start,w,3)for s in shapes.values()):raise ValueError('BLOCK_LAYOUT')
        self.next_row=end;self.complete=end==h

    def finish(self):
        if not self.complete:raise ValueError('MISSING_BLOCKS')


def capability():
    return dict(executor_id=EXECUTOR_ID,supported=False,qualified=False,production_activation=False,
                core_rows=[512,256],compute_vram_bytes=0,workspace_limit_bytes=512*1024**2,
                status='PENDING_GM015_QUALIFICATION')
