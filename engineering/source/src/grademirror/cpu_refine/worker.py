"""Numerical orchestration only; all numerical kernels remain the originals."""
import gc
import time
from pathlib import Path
from ..common import atomic_json,strict_load
from ..cpu_replay.contract import need,binding
from ..adaptive_execution.first_hooks import start as _application_start,ready as _application_ready,selected as _application_selected


def preload(role,*,entry=False):
    if role in ('r0','range','prepare','cpu','small'):
        import numpy as np
        import torch
        from ..engine.cpu_r0_fit.optimizer_policy import configure_cpu
        if role!='cpu':configure_cpu()
        from ..engine.cpu_postprocess import executor,core,transport,service
        from ..engine.independent_r0_p3_lld10 import area
        from ..engine.cpu_fine_image import execution
        if role=='r0':
            from ..cpu_first_fit.worker import preload_runtime
            preload_runtime(False)
        need(np.__version__=='1.26.4' and torch.__version__=='2.5.1+cu124' and not torch.cuda.is_initialized(),
             'REFINE_NUMERIC_RUNTIME')
        if role=='cpu' and entry:
            # Exercise the exact original setup once. Actual CPU workers let
            # transport._runtime own this one-shot inter-op initialization.
            transport._runtime()
            need(torch.get_num_threads()==4 and torch.get_num_interop_threads()==1,'REFINE_CPU_THREAD_POLICY')
    elif role=='solver':
        import numpy as np
        import scipy
        from ..engine.independent_r0_p3_lld10 import p3_solver
        from ..cpu_replay.contract import ROOT
        from ..light.portable import load_worker
        load_worker(ROOT/"tools/gm008_solver_worker.py")
        need(np.__version__=='2.5.3' and scipy.__version__=='1.18.1','REFINE_SOLVER_RUNTIME')
    elif role in ('host','frame_sender'):
        from ..engine import group15_codec
        import numpy as np


def prepare(payload):
    import numpy as np
    from ..cpu_replay.numeric import attach
    from ..cpu_replay.host import array_hash
    from ..engine.independent_r0_p3_lld10.area import area_prefix
    from ..engine.cpu_postprocess.executor import ArrayProvider
    from ..engine.cpu_postprocess.core import Gate
    from ..engine.independent_r0_p3_lld10.ledger import Ledger
    from ..engine.cpu_fine_image.execution import range_scan
    from .solver_transport import solve
    work=Path(payload['work']);om=o=rm=reference=None
    _application_context=_application_selected()
    cancelled=lambda:Path(payload['cancel']).exists()
    def check():need(not cancelled(),'CANCELLED',exit_code=8)
    try:
        om,o=attach(payload['O']);rm,reference=attach(payload['reference']);check()
        # Original cpu_fine_image.api._prepare area and reference FP64 order.
        _application_start(_application_context,'H')
        h=area_prefix(o,payload['reference_wh'],check)
        _application_start(_application_context,'T')
        target=np.ascontiguousarray(reference.astype(np.float64)/np.iinfo(reference.dtype).max)
        hh,th=array_hash(h),array_hash(target)
        atomic_json(work/'P3-inputs.json',dict(H_sha256=hh,T_sha256=th,shape=list(h.shape),
            H_bytes=h.nbytes,T_bytes=target.nbytes,normalization='uint8.astype(float64)/255',identity_control=False))
        _application_ready(_application_context,'P3inputs',work)
        b,report=solve(work/'solver',h,target,[o.shape[1],o.shape[0]],cancelled=cancelled)
        _application_ready(_application_context,'CG',work)
        del h,target,reference;reference=None;rm.close();rm=None;gc.collect()
        need(b.dtype==np.float64 and b.flags.c_contiguous and np.isfinite(b).all(),'FINE_B_ARRAY')
        with (work/'b.npz').open('xb') as stream:np.savez(stream,b=b)
        ledger=Ledger(work/'range-scan-actual.json',dict(P3_scalar_evaluations=3*o.shape[0]*o.shape[1]),
                      cancel_path=payload['cancel'],deadline=time.monotonic()+600)
        _application_start(_application_context,'range')
        ranges=range_scan(ArrayProvider(o,b),dict(reference_wh=payload['reference_wh']),
                         Gate(ledger.take,cancelled,lambda **kw:None,time.monotonic()+600))
        ledger.save('COMPLETE')
        _application_ready(_application_context,'range',work)
        result=dict(status='PASS',b=binding(work/'b.npz'),b_shape=list(b.shape),b_sha256=array_hash(b),
                    ranges=ranges,solver=report,H_sha256=hh,T_sha256=th,
                    counts=dict(area_H=1,target_T=1,P3_geometry=1,scalar_CG=report['counts']['scalar_CG']))
        atomic_json(work/'prepare-result.json',result)
        return result
    finally:
        reference=o=None
        if rm:rm.close()
        if om:om.close()


def solver(directory):
    from ..cpu_replay.contract import ROOT
    from ..light.portable import load_worker
    original=load_worker(ROOT/"tools/gm008_solver_worker.py")
    return original['run'](Path(directory))
