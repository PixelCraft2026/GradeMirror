"""Shared native photo bounds and RAM envelopes (no imaging dependencies)."""
from __future__ import annotations

import ctypes
import math

from .common import GMError

MAX_PIXELS = 210_000_000  # Headroom for nominal 200 MP camera dimensions.
MAX_IMAGE_BYTES = 1536 * 1024**2  # Includes RGB16 noise + codec/container overhead.
MAX_ARRAY_BYTES = MAX_PIXELS * 3 * 4  # Native RGB float32; frames remain 4 MiB.
MIB = 1024**2
GIB = 1024**3
LEGACY_BUDGET = {"pool": 6 * GIB, "compute": 4 * GIB,
                 "host": 1536 * MIB, "control_ipc": 512 * MIB}


def check_geometry(width, height):
    if (type(width) is not int or type(height) is not int or min(width, height) <= 0
            or width * height > MAX_PIXELS):
        raise GMError("IMAGE_GEOMETRY", f"图片 {width} × {height} 超出上限 "
                      f"{MAX_PIXELS:,} 像素（2.1 亿）", 3)


def available_memory():
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in
            ("total", "available", "page_total", "page_available",
             "virtual_total", "virtual_available", "extended")]
    state = Memory()
    state.length = ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        raise GMError("RAM_STATUS_UNAVAILABLE", "无法读取系统可用内存", 5)
    return state.available


def require_memory(required):
    def refusal(code,message,available):
        from .light.runtime import operation
        active=operation()
        if active and active["selection"]["backend"]=="gpu":
            from .light.resource_retry import InnerResource
            return InnerResource(code,message,"gpu_inner_ram",[("ram",available,required)])
        return GMError(code,message,5)

    try:
        available = available_memory()
    except GMError as error:
        if error.code=="RAM_STATUS_UNAVAILABLE":
            raise refusal(error.code,str(error),None) from error
        raise
    if available < required:
        raise refusal("IMAGE_RAM_REQUIRED", f"此尺寸预计需要至少 {required/GIB:.1f} GiB "
                      f"可用内存，当前 {available/GIB:.1f} GiB。请关闭占用内存的程序后重试。", available)
    return available


def photo_budget(source, reference):
    """Upper envelopes, not allocations. Large sessions also check free RAM.

    Compute includes both integer inputs, one full normalization/output and the
    unchanged tile model. Host covers source/Y/blend/quantization plus lossless
    encode/readback and reference storage. No full native float disk cache.
    """
    for info in (source, reference):
        check_geometry(info["width"], info["height"])
    p = source["width"] * source["height"]
    r = reference["width"] * reference["height"]
    sbytes, rbytes = p * 3 * source["bits"] // 8, r * 3 * reference["bits"] // 8
    rounded = lambda value: math.ceil(value / (256 * MIB)) * 256 * MIB
    compute = max(LEGACY_BUDGET["compute"], rounded(2 * GIB + sbytes + rbytes + 12 * max(p, r)))
    host = max(LEGACY_BUDGET["host"], rounded(512 * MIB + rbytes + 64 * p))
    budget = {"compute": compute, "host": host, "control_ipc": 512 * MIB}
    budget["pool"] = sum(budget.values())
    return budget


def admit_photos(source, reference):
    budget = photo_budget(source, reference)
    # Existing small-image envelope remains compatible. The new high-resolution
    # range must have physical headroom before any native pixel allocation.
    if max(source["width"] * source["height"], reference["width"] * reference["height"]) > 64_000_000:
        require_memory(budget["pool"] + GIB)
    return budget


def operation_seconds(pixels):
    return max(120, math.ceil(pixels / (4160 * 6240) * 120))


def validate_budget(budget):
    if (not isinstance(budget, dict) or set(budget) != set(LEGACY_BUDGET)
            or any(type(v) is not int or not 0 < v <= 256 * GIB for v in budget.values())
            or budget["pool"] != sum(budget[k] for k in ("compute", "host", "control_ipc"))):
        raise GMError("RAM_BUDGET_INVALID", "Invalid bounded image resource envelope", 5)
    return dict(budget)
