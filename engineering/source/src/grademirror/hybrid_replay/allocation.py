"""Allocation-point weak references only. Never inspects frames or object heaps.

Tracked storage peaks supplement the frozen conservative lifetime table; NumPy
expression temporaries not exposed at these points remain covered by that table.
"""
import weakref

class Allocations:
    def __init__(self, limit=512*1024**2):
        if type(limit) is not int or limit <= 0:
            raise ValueError("OBSERVED_WORKSPACE_CONTRACT")
        self.limit=limit
        self.refs={};self.originals=[];self.calls=0;self.peak=0;self.excluded=[];self.peak_objects=0
    def track(self,value):
        import numpy as np,torch
        if isinstance(value,(np.ndarray,torch.Tensor)):
            self.refs[id(value)]=weakref.ref(value);self.calls+=1
            if self.calls%128==0:self.snapshot()
        return value
    def snapshot(self):
        import numpy as np,torch
        sizes={};dead=[]
        for ident,ref in self.refs.items():
            v=ref()
            if v is None:dead.append(ident);continue
            if isinstance(v,np.ndarray):address=int(v.__array_interface__['data'][0]);size=v.nbytes
            else:
                if v.device.type!='cpu':raise RuntimeError('ALLOCATION_CUDA')
                st=v.untyped_storage();address=st.data_ptr();size=st.nbytes();del st
            if not any(a<=address<a+n for a,n in self.excluded):sizes[(address,size)]=size
            del v
        for ident in dead:self.refs.pop(ident,None)
        self.peak=max(self.peak,sum(sizes.values()));self.peak_objects=max(self.peak_objects,len(sizes))
        if sum(sizes.values())>self.limit:raise MemoryError('OBSERVED_WORKSPACE_LIMIT')
    def install(self):
        import torch,numpy as np
        def patch(obj,name):
            original=getattr(obj,name)
            def call(*args,**kwargs):return self.track(original(*args,**kwargs))
            self.originals.append((obj,name,original));setattr(obj,name,call)
        for name in ('arange','from_numpy','zeros_like','where','isfinite'):patch(torch,name)
        for name in ('clone','index_select','to','__getitem__','__add__','__sub__','__mul__','__truediv__','__ge__','__lt__','__and__','floor','long','float','clamp'):patch(torch.Tensor,name)
        for name in ('empty','empty_like','ascontiguousarray','arange'):patch(np,name)
    def close(self):
        self.snapshot()
        for obj,name,original in reversed(self.originals):setattr(obj,name,original)
        self.originals.clear()
    def record(self):return dict(allocation_point_calls=self.calls,tracked_storage_peak_bytes=self.peak,peak_storage_entries=self.peak_objects,method='weakref allocation outputs; no frames/gc/object enumeration',unexposed_numpy_expression_temporaries='covered by frozen conservative capacity table',not_exact_continuous_RSS=True)
