"""Provider/sink CPU execution. Proofs are emitted in original HWC byte order."""
import hashlib,math,time
import numpy as np
from .contract import validate,Coverage
from .geometry import plan
from .core import p3_region,scalar_footprint,compose


def array_hash(a):
    if not a.flags.c_contiguous:raise ValueError('HASH_CONTIGUITY')
    return hashlib.sha256(memoryview(a).cast('B')).hexdigest()


class ArrayProvider:
    def __init__(self,o,b):
        if (o.dtype!=np.float32 or o.ndim!=3 or o.shape[-1]!=3 or not o.flags.c_contiguous or
                b.dtype!=np.float64 or b.ndim!=2 or b.shape[-1]!=3 or not np.isfinite(b).all()):raise ValueError('PROVIDER_LAYOUT')
        self.o=o.view();self.o.flags.writeable=False
        self.b=b.view();self.b.flags.writeable=False

    def read(self,start,end,channel):
        if not 0<=start<end<=self.o.shape[0] or channel not in (0,1,2):raise ValueError('PROVIDER_REGION')
        value=self.o[start:end,:,channel]
        if not np.isfinite(value).all():raise ValueError('PROVIDER_FINITE')
        return value


def execute(manifest,provider,sink,gate,expected_ranges):
    m=validate(manifest);o=provider.o;b=provider.b;h,w,_=o.shape
    if list(o.shape)!=m['O']['shape']or list(b.shape)!=m['b']['shape']:raise ValueError('PROVIDER_SHAPE')
    if array_hash(o)!=m['O']['sha256']or array_hash(b)!=m['b']['sha256']:raise ValueError('PROVIDER_HASH')
    ranges=[]
    for c in range(3):
        lo=float('inf');hi=float('-inf')
        for start in range(0,h,128):
            end=min(h,start+128);a=provider.read(start,end,c)
            p=p3_region(a,b,m['reference_wh'],(h,w),start,end,c,gate)
            lo=min(lo,float(a.min()),float(p.min()));hi=max(hi,float(a.max()),float(p.max()));del p
        kmin=math.floor((lo-2/255)*64)-1;kmax=math.ceil((hi+2/255)*64)+1
        ranges.append(dict(kmin=kmin,kmax=kmax,count=kmax-kmin+1,min=lo,max=hi,delta=1/64,sigma=.1,J=8))
    if ranges!=expected_ranges:raise ValueError('GLOBAL_RANGE_BRIDGE')
    hashes={key:hashlib.sha256()for key in ('OP','LO','LP','Y')};coverage=Coverage(m)
    stripes=plan(h,w,m['core_rows'])
    for index,s in enumerate(stripes):
        start,end=s.core;a,z=s.gaussian[0];core=slice(start-a,end-a)
        blocks={key:np.empty((end-start,w,3),np.float32)for key in hashes}
        for c in range(3):
            gate.check(stage='channel',channel=c,completed_core=index,total_core=len(stripes))
            source=np.ascontiguousarray(provider.read(a,z,c))
            lo=scalar_footprint(source,ranges[c],s,gate);blocks['LO'][...,c]=lo;del lo
            p=p3_region(source,b,m['reference_wh'],(h,w),a,z,c,gate)
            blocks['OP'][...,c]=p[core]
            lp=scalar_footprint(p,ranges[c],s,gate);blocks['LP'][...,c]=lp;del p,source
            blocks['Y'][...,c]=compose(provider.read(start,end,c),lp,blocks['LO'][...,c]);del lp
        if any(np.shares_memory(value,o)or np.shares_memory(value,b)or not np.isfinite(value).all() for value in blocks.values()):raise ValueError('SINK_ALIAS_OR_FINITE')
        coverage.accept(m['job_id'],m['attempt_id'],start,end,{k:v.shape for k,v in blocks.items()})
        for key,value in blocks.items():hashes[key].update(memoryview(value).cast('B'))
        sink(start,end,blocks)
        del blocks
        gate.check(stage='core_complete',completed_core=index+1,total_core=len(stripes))
    coverage.finish()
    return dict(hashes={k:v.hexdigest()for k,v in hashes.items()},ranges=ranges,core_count=len(stripes),cuda_initialized=False)
