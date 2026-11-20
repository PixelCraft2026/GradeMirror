"""Verify one preview TIFF by hashing its decoded RGB strips in raster order."""
import hashlib

from ..engine.group15_codec import _check_unchanged, _icc, _identity, _require, _tiff_header, MAX_TIFF_BYTES


def verify(path, expected_icc, expected_pixels_sha256, *, check):
    import numpy as np
    import tifffile

    if expected_icc is not None:
        _icc(expected_icc)
    before = _identity(path)
    _require(before["bytes"] <= MAX_TIFF_BYTES, "TIFF_OUTPUT_BUDGET")
    with open(path, "rb") as handle:
        info = _tiff_header(handle, single_output=True, allow_rgb8=True)
    _require(info["icc_bytes"] == expected_icc and info["ifds"][0]["compression"] in (8, 32946),
             "TIFF_OUTPUT_METADATA")
    height, width, bits = info["height"], info["width"], info["bits"]
    digest = hashlib.sha256()
    start = strips = max_rows = 0
    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        rows = page.rowsperstrip
        _require(not page.is_tiled and page.planarconfig == 1 and page.samplesperpixel == 3 and
                 type(rows) is int and 1 <= rows <= 64 and
                 len(page.dataoffsets) == (height + rows - 1) // rows, "TIFF_STREAM_STRIPS")
        for data, index, shape in page.segments(maxworkers=1, sort=False, buffersize=4 * 1024**2):
            check()
            length = min(rows, height - start)
            wanted = (1, length, width, 3)
            _require(length > 0 and tuple(index) == (0, 0, start, 0, 0) and
                     tuple(shape) == wanted and data is not None and data.shape == wanted and
                     data.dtype.kind == "u" and data.dtype.itemsize * 8 == bits, "TIFF_STREAM_COVERAGE")
            canonical = np.ascontiguousarray(data, dtype="<u2" if bits == 16 else "u1")
            digest.update(memoryview(canonical).cast("B"))
            start += length
            strips += 1
            max_rows = max(max_rows, length)
    actual = digest.hexdigest()
    _require(start == height and strips == (height + rows - 1) // rows, "TIFF_STREAM_COVERAGE")
    _require(actual == expected_pixels_sha256, "TIFF_READBACK_PIXEL_HASH_MISMATCH")
    _check_unchanged(path, before)
    return dict(mode="decoded_strips_raster_sha256", complete=True, pixel_sha256=actual,
                height=height, width=width, bits=bits, strips=strips, max_rows=max_rows,
                icc_sha256=info["color"]["icc_sha256"], encoded_sha256=before["sha256"])
