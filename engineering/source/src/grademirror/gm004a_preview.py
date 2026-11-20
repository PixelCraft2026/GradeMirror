"""Display-only GM-004A previews from resident integer S and float32 Y.

The session host admits/validates the lease, owns request ordering and transports
pixels in bounded binary frames. This module owns no cache or resident state and
never loads a model, decodes a photo, writes a file or changes source/Y.
"""
from __future__ import annotations

import base64
import hashlib
import math
import re
import time

import numpy as np
from PIL import Image

from .engine.group15_codec import COLOR_POLICY_ID, MAX_PIXELS, _icc
from .lease import BINDING_FIELDS
from .preview_limits import MAX_FIT_SIDE

ROW_CHUNK = 64
MAX_PROXY_SIDE = MAX_FIT_SIDE
MAX_ROI_SIDE = 512
MAX_ICC_BYTES = 16 * 1024


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _check(cancelled, deadline):
    if cancelled is not None and cancelled():
        raise InterruptedError("PREVIEW_CANCELLED")
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("PREVIEW_DEADLINE")


def _binding(binding):
    from .hybrid_replay import VERSION_FIELDS
    hybrid = isinstance(binding, dict) and bool(VERSION_FIELDS & binding.keys())
    if hybrid:
        from .hybrid_replay.contract import checked_binding
        checked_binding(binding, BINDING_FIELDS)
    _require(isinstance(binding, dict) and set(binding) == (BINDING_FIELDS | VERSION_FIELDS if hybrid else BINDING_FIELDS),
             "PREVIEW_BINDING_INVALID")
    _require(all(type(binding[k]) is str and 0 < len(binding[k]) <= 256
                 for k in ("handle_id", "owner_instance_id", "project_revision")),
             "PREVIEW_BINDING_INVALID")
    _require(type(binding["generation"]) is int and binding["generation"] >= 1
             and type(binding["recipe_sha256"]) is str
             and re.fullmatch(r"[0-9a-f]{64}", binding["recipe_sha256"]) is not None,
             "PREVIEW_BINDING_INVALID")
    return dict(binding)


def _array(array, dtype, code):
    _require(isinstance(array, np.ndarray) and array.dtype == dtype
             and array.ndim == 3 and array.shape[2] == 3
             and 0 < array.shape[0] * array.shape[1] <= MAX_PIXELS, code)


def _q8(block):
    """Existing FP64 round-to-even quantization, confined to a row block."""
    a = block.astype(np.float64)
    np.clip(a, 0.0, 1.0, out=a)
    a *= 255.0
    np.rint(a, out=a)
    return a.astype(np.uint8)


def get_preview(source, y, reference, icc, binding, *, view="result", alpha=1.0,
                max_side=1024, roi=None, request_id, cancelled=None, deadline=None,
                color_interpretation=None, reference_roi=False, progress=None):
    """Return ``(RGB8_pixels, JSON_safe_metadata)`` without mutating S/Y.

    ``roi=(x0,y0,x1,y1)`` uses source half-open coordinates and returns at most
    512x512 native samples. Reference supports its own full-view proxy only;
    source-grid ROIs cannot silently be interpreted on the reference grid.
    Proxies quantize first, then use Pillow LANCZOS with an exact long-side cap.
    ``deadline`` is an optional absolute monotonic time; the host also guards
    native calls and IPC with its operation deadline.
    """
    bound = _binding(binding)
    _require(type(request_id) is str and 0 < len(request_id) <= 128,
             "PREVIEW_REQUEST_ID_INVALID")
    _require(view in ("source", "reference", "result"), "PREVIEW_VIEW_INVALID")
    _require(type(alpha) in (float, int) and math.isfinite(alpha) and 0 <= alpha <= 1,
             "ALPHA_INVALID")
    alpha = float(alpha)
    _require(type(max_side) is int and 1 <= max_side <= MAX_PROXY_SIDE,
             "PREVIEW_MAX_SIDE_INVALID")
    _require(deadline is None or (type(deadline) in (float, int) and math.isfinite(deadline)),
             "PREVIEW_DEADLINE_INVALID")
    _require(cancelled is None or callable(cancelled), "PREVIEW_CANCEL_INVALID")
    _require(icc is None or isinstance(icc, bytes) and len(icc) <= MAX_ICC_BYTES,
             "PREVIEW_ICC_INVALID")
    icc_info = _icc(icc) if icc is not None else {"icc_sha256": None}
    _require(source.dtype in (np.dtype("uint8"), np.dtype("uint16")), "PREVIEW_SOURCE_SCHEMA")
    _array(source, source.dtype, "PREVIEW_SOURCE_SCHEMA")
    _array(y, np.dtype("float32"), "PREVIEW_RESULT_SCHEMA")
    _require(y.shape == source.shape, "PREVIEW_RESULT_GEOMETRY")
    _check(cancelled, deadline)

    if view == "reference":
        _require(reference is not None, "PREVIEW_REFERENCE_UNAVAILABLE")
        _require(reference.dtype in (np.dtype("uint8"), np.dtype("uint16")), "PREVIEW_REFERENCE_SCHEMA")
        _array(reference, reference.dtype, "PREVIEW_REFERENCE_SCHEMA")
        _require(roi is None or reference_roi, "PREVIEW_REFERENCE_SOURCE_ROI_UNSUPPORTED")
        height, width = reference.shape[:2]
    else:
        height, width = source.shape[:2]

    if roi is not None:
        _require(type(roi) in (tuple, list) and len(roi) == 4
                 and all(type(n) is int for n in roi), "PREVIEW_ROI_INVALID")
        x0, y0, x1, y1 = roi
        _require(0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height,
                 "PREVIEW_ROI_OUT_OF_BOUNDS")
        _require(x1-x0 <= MAX_ROI_SIDE and y1-y0 <= MAX_ROI_SIDE,
                 "PREVIEW_ROI_TOO_LARGE")
    else:
        x0, y0, x1, y1 = 0, 0, width, height

    q8 = np.empty((y1-y0, x1-x0, 3), dtype=np.uint8)
    for row in range(y0, y1, ROW_CHUNK):
        _check(cancelled, deadline)
        end = min(row + ROW_CHUNK, y1)
        region = np.s_[row:end, x0:x1, :]
        if progress:
            progress('preview_quantize', row-y0, y1-y0)
        if view == "reference":
            q8[row-y0:end-y0] = (reference[region] if reference.dtype == np.uint8 else
                _q8(reference[region].astype(np.float32) / np.float32(65535)))
            continue
        if view == "source" or alpha == 0.0:
            mixed = source[region].astype(np.float32)
            mixed /= np.float32(np.iinfo(source.dtype).max)
        elif alpha == 1.0:
            # Match the existing alpha1 endpoint: Y is already float32.
            mixed = y[region]
        else:
            normalized = source[region].astype(np.float32)
            normalized /= np.float32(np.iinfo(source.dtype).max)
            mixed = y[region] - normalized
            mixed *= np.float32(alpha)
            mixed += normalized
        _require(np.isfinite(mixed).all(), "PREVIEW_PIXELS_NONFINITE")
        q8[row-y0:end-y0] = _q8(mixed)

    _check(cancelled, deadline)
    if progress:
        progress('preview_quantize', y1-y0, y1-y0)
    quantized_size = [q8.shape[1], q8.shape[0]]
    resized = roi is None and max(q8.shape[:2]) > max_side
    if resized:
        # Integer floor defines the short side; the long side is exactly max_side.
        out_w = max(1, width * max_side // max(width, height))
        out_h = max(1, height * max_side // max(width, height))
        if progress:
            progress('preview_resize')
        with Image.fromarray(q8) as full:
            with full.resize((out_w, out_h), Image.Resampling.LANCZOS) as proxy:
                pixels = np.array(proxy, dtype=np.uint8, copy=True)
    else:
        pixels = q8
    _check(cancelled, deadline)
    pixels = np.ascontiguousarray(pixels)
    metadata = {
        "schema_version": "0.2.0", "request_id": request_id, "binding": bound,
        "view": view, "alpha": alpha,
        "effective_alpha": alpha if view == "result" else 0.0 if view == "source" else None,
        "width": pixels.shape[1], "height": pixels.shape[0], "shape": list(pixels.shape),
        "dtype": "uint8", "channels": 3, "bits": 8, "pixel_order": "RGB",
        "pixel_sha256": hashlib.sha256(memoryview(pixels).cast("B")).hexdigest(),
        "source_grid": {"width": source.shape[1], "height": source.shape[0]},
        "view_grid": {"width": width, "height": height},
        "coordinate_space": "reference_grid" if view == "reference" else "source_grid",
        "roi": list(roi) if roi is not None else None,
        "native_pixel_roi": roi is not None, "resized": resized,
        "max_side": max_side if roi is None else None, "quantized_size": quantized_size,
        "quantization": "float64_rint_clip_0_1_times255",
        "proxy_order": "Q8_then_Pillow_LANCZOS" if roi is None else "native_grid_then_Q8",
        "working_domain": "gamma-encoded sRGB", "color_policy_id": COLOR_POLICY_ID,
        "color_interpretation": color_interpretation or (
            "assumed_sRGB" if view == "reference" else "embedded_sRGB_ICC"),
        "icc_sha256": icc_info["icc_sha256"], "icc_bytes": len(icc or b""),
        "icc_encoding": "base64", "icc_profile_base64": base64.b64encode(icc or b"").decode("ascii"),
        "row_chunk": ROW_CHUNK, "fit_count_delta": 0, "render_count_delta": 0,
    }
    return pixels, metadata
