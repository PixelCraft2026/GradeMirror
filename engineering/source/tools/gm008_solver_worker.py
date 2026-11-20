"""Managed CPU FP64 P3 child; no torch, images, research imports or caches."""
from pathlib import Path
import hashlib,importlib.metadata,json,os,sys,time,traceback
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'));sys.dont_write_bytecode=True
from grademirror.common import strict_load,atomic_json
from grademirror.gm003_ipc import connect,send_json,recv_json
from grademirror.fine_ipc import send_f64,recv_f64,MAX_ARRAY,MAX_TOTAL
from grademirror.engine.independent_r0_p3_lld10.ledger import Ledger
from grademirror.winproc import current_identity

def run(directory):
    import numpy as np
    from grademirror.engine.independent_r0_p3_lld10 import p3_solver as solver
    spec=strict_load(directory/'solver-spec.json')
    from grademirror.fine_contract import validate_limits
    validate_limits(dict(width=spec['native_wh'][0],height=spec['native_wh'][1]),dict(width=spec['reference_wh'][0],height=spec['reference_wh'][1]))
    actual={name:importlib.metadata.version(name)for name in ('numpy','scipy')}
    if actual!={'numpy':'2.5.3','scipy':'1.18.1'}:raise ValueError('P3_RUNTIME_MISMATCH')
    from grademirror.fine_resources import solver_seconds
    seconds=solver_seconds(spec['reference_wh'][0]*spec['reference_wh'][1])
    if spec.get('solver_seconds',seconds)!=seconds:raise ValueError('P3_SOLVER_DEADLINE_BINDING')
    L=Ledger(directory/'solver-counts.json',{'scalar_CG':6 if spec['identity_control']else 3},cancel_path=directory/'cancel.signal',deadline=time.monotonic()+seconds)
    try:
        with connect(spec['address'],spec['auth_hex'])as conn:
            send_json(conn,dict(identity=current_identity(),runtime=actual))
            h=recv_f64(conn,spec['reference_shape'],max_bytes=MAX_ARRAY);t=recv_f64(conn,spec['reference_shape'],max_bytes=MAX_ARRAY)
            if h.nbytes+t.nbytes>MAX_TOTAL:raise ValueError('P3_IPC_TOTAL_QUOTA')
            L.check();started=time.perf_counter();g=solver.geometry(spec['native_wh'],spec['reference_wh']);geometry_seconds=time.perf_counter()-started;L.check()
            coefficients=[];reports=[];controls=[]
            for c in range(3):
                b,row=solver.solve(g,h[...,c],t[...,c],L);coefficients.append(b);reports.append(row);L.save()
            if spec['identity_control']:
                for c in range(3):
                    b,row=solver.solve(g,h[...,c],h[...,c],L);assert np.max(abs(b))<=1e-12;controls.append(row);L.save()
            b=np.ascontiguousarray(np.stack(coefficients,axis=1),dtype='<f8')
            report=dict(status='PASS',runtime=actual,geometry_seconds=geometry_seconds,main=reports,identity=controls,counts=L.counts,coefficient_sha256=hashlib.sha256(b.tobytes()).hexdigest(),shape=list(b.shape))
            from grademirror.engine.independent_r0_p3_lld10.profile import LIMITS
            atomic_json(directory/'solver-result.json',report);send_json(conn,report);send_f64(conn,b,max_bytes=LIMITS['archive_bytes'])
            del g,h,t,coefficients,b
        L.save('PASS')
    except BaseException:L.save('FAILED');raise

if __name__=='__main__':
    if sys.argv[1]=='ownership-probe':
        out=Path(sys.argv[2]).resolve();atomic_json(out/'probe-identity.json',dict(identity=current_identity(),CG=0,LLF=0,models=0))
        until=time.monotonic()+15
        while not(out/'probe-finish').exists()and not(out/'cancel.signal').exists():
            if time.monotonic()>until:raise TimeoutError('OWNERSHIP_PROBE_TIMEOUT')
            time.sleep(.01)
        raise SystemExit(8 if(out/'cancel.signal').exists()else 0)
    out=Path(sys.argv[1]).resolve()
    if os.environ.get('GM063_LAUNCH_CONFIG'):
        from grademirror.adaptive_execution.calibration_launch import enter
        enter('P3_solver',out/'solver-observation')
    try:run(out)
    except BaseException as exc:
        atomic_json(out/'solver-failure.json',dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc()));raise
