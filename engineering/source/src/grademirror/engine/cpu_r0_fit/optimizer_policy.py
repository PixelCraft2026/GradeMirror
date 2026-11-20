"""CPU-only initialization policy; original Adam math/step and Torch globals unchanged."""
from types import MethodType
import torch
from .contract import need
from ..cpu_r0_render.executor import configure_cpu as original_cpu_setup

def configure_cpu():
    original_cpu_setup()
    import cv2
    cv2.setNumThreads(4)

def _cpu_graph_check(optimizer):
    # CUDA graph eligibility is irrelevant to this strictly CPU, noncapturable scope.
    # Stock Adam checks cuda.is_available before reaching its capturable checks.
    # Replace only that instance's non-mathematical check, never Adam.step/global APIs.
    need(bool(optimizer.param_groups),'CPU_FIT_OPTIMIZER_EMPTY')
    for group in optimizer.param_groups:
        need(group.get('capturable')is False,'CPU_FIT_CAPTURABLE_FORBIDDEN')
        need(bool(group['params'])and all(p.device.type=='cpu'for p in group['params']),'CPU_FIT_OPTIMIZER_DEVICE')
    for state in optimizer.state.values():
        need(all(not isinstance(v,torch.Tensor)or v.device.type=='cpu'for v in state.values()),'CPU_FIT_OPTIMIZER_STATE_DEVICE')

def bind_cpu_optimizer_policy(optimizers):
    for optimizer in optimizers:
        need(type(optimizer)is torch.optim.Adam,'CPU_FIT_OPTIMIZER_TYPE')
        _cpu_graph_check(optimizer)
        optimizer._cuda_graph_capture_health_check=MethodType(_cpu_graph_check,optimizer)
