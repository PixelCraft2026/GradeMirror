"""Exact difference-then-add FP64 composition in bounded rows."""
import numpy as np

def compose(original, filtered_p3, filtered_original, ledger, *, out=None):
    if out is not None:
        if (not isinstance(out,np.ndarray)or out.dtype!=np.float32 or out.ndim!=3 or out.shape[-1]!=3 or out.shape!=original.shape or out.shape!=filtered_p3.shape or out.shape!=filtered_original.shape or not out.flags.c_contiguous or not out.flags.writeable or not out.flags.owndata):raise ValueError('COMPOSE_OUTPUT_OWNERSHIP')
        if np.shares_memory(out,original)or np.shares_memory(out,filtered_p3)or(out is not filtered_original and np.shares_memory(out,filtered_original)):raise ValueError('COMPOSE_OUTPUT_ALIAS')
    ledger.take(final_compositions=1)
    if out is None:out=np.empty_like(original)
    for start in range(0,len(original),64):
        ledger.check();end=min(len(original),start+64)
        out[start:end]=(original[start:end].astype(np.float64)+(
            filtered_p3[start:end].astype(np.float64)-filtered_original[start:end].astype(np.float64))).astype(np.float32)
    if not np.isfinite(out).all():raise FloatingPointError('FINE_NONFINITE_COMPOSITION')
    return out
