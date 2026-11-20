"""Bounded RGB image I/O for frozen group15 and new independent pairs.

Import only in the dedicated CPU/I/O runtime. Inspection reads headers but
does not decode pixels. Strict readers retain the frozen TIFF16/PNG8 contract;
development_pair admits supported RGB photo formats at native size.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import struct
import zlib

import numpy as np
import tifffile
from PIL import Image, ImageCms, ImageOps
import imagecodecs

COLOR_POLICY_ID = "untagged-assume-srgb-user-20261017"
SOURCE_SHA256 = "e5e3e7ea36dfa4c1040e39a8cadcec56e7e43e1278ed3e48d0e0da46e5be8e91"
REFERENCE_SHA256 = "a73141ca05b535b784e8d059891ef87b6d106917e7322dd0016e1793c8fa31c1"
SOURCE_ICC_SHA256 = "2b3aa1645779a9e634744faf9b01e9102b0c9b88fd6deced7934df86b949af7e"
from ..image_limits import MAX_PIXELS, MAX_IMAGE_BYTES, check_geometry

MAX_TIFF_BYTES = MAX_IMAGE_BYTES
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class CodecError(ValueError):
    """An explicit identity, schema, colour or pixel codec rejection."""


def _require(condition, message):
    if not condition:
        raise CodecError(message)


def file_sha256(path):
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def pixel_sha256(pixels):
    """Hash C-order integer bytes; RGB16 is canonical little endian."""
    a = np.asarray(pixels)
    _require(a.dtype in (np.dtype("uint8"), np.dtype("uint16")), "PIXEL_DTYPE")
    a = np.ascontiguousarray(a, dtype="<u2" if a.dtype.itemsize == 2 else "u1")
    return hashlib.sha256(memoryview(a).cast("B")).hexdigest()


def _identity(path):
    p = Path(path).resolve(strict=True)
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": file_sha256(p)}


def _check_unchanged(path, before):
    _require(_identity(path) == before, "INPUT_IDENTITY_CHANGED")


def _icc(icc_bytes):
    _require(isinstance(icc_bytes, bytes) and 128 <= len(icc_bytes) <= 1024**2,
             "COLOR_ICC_MALFORMED")
    _require(struct.unpack(">I", icc_bytes[:4])[0] == len(icc_bytes)
             and icc_bytes[36:40] == b"acsp" and icc_bytes[16:20] == b"RGB ",
             "COLOR_ICC_MALFORMED")
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_bytes))
        description = ImageCms.getProfileDescription(profile).strip()
    except Exception as exc:
        raise CodecError("COLOR_ICC_MALFORMED") from exc
    _require("srgb" in description.lower(), "COLOR_ICC_UNSUPPORTED")
    return {"icc_sha256": hashlib.sha256(icc_bytes).hexdigest(),
            "icc_size": len(icc_bytes), "icc_description": description,
            "icc_color_space": "RGB "}


def _color(icc_bytes=None, srgb=None, gamma=None, chromaticity=None):
    out = {"color_policy_id": COLOR_POLICY_ID, "working_domain": "gamma-encoded sRGB",
           "icc_present": icc_bytes is not None, "srgb_present": srgb is not None,
           "png_srgb": srgb, "png_gamma": gamma, "png_chromaticity": chromaticity,
           "icc_sha256": None, "icc_size": 0}
    if icc_bytes is not None:
        out.update(_icc(icc_bytes))
    if srgb is not None:
        _require(type(srgb) is int and 0 <= srgb <= 3, "COLOR_SRGB_MALFORMED")
    if gamma is not None:
        _require(gamma == 45455, "COLOR_GAMMA_CONFLICT_OR_UNSUPPORTED")
    if chromaticity is not None:
        _require(tuple(chromaticity) == (31270, 32900, 64000, 33000, 30000,
                                         60000, 15000, 6000), "COLOR_CHRM_CONFLICT_OR_UNSUPPORTED")
    out["interpretation"] = ("embedded_sRGB_ICC" if icc_bytes is not None else
                             "marked_sRGB" if srgb is not None else "assumed_sRGB")
    return out


def _tag(page, code, default=None):
    item = page.tags.get(code)
    return default if item is None else item.value


def _tiff_header(handle, *, strict_group15=False, single_output=False, allow_rgb8=False):
    from ..exif_structure import Graph, tiff_layout
    handle.seek(0, os.SEEK_END)
    graph = Graph(handle, handle.tell())
    try:
        layout = tiff_layout(graph, allow_rgb8=allow_rgb8, single_output=single_output)
    except ValueError as exc:
        raise CodecError(str(exc)) from exc
    headers = layout['headers']
    icc_bytes = layout['icc_bytes']
    color = _color(icc_bytes)
    if strict_group15:
        _require(len(headers) == 2, "GROUP15_TIFF_IFDS")
        _require((headers[0]["width"], headers[0]["height"], headers[0]["compression"])
                 == (4160, 6240, 32946), "GROUP15_TIFF_MAIN")
        _require((headers[1]["width"], headers[1]["height"], headers[1]["compression"])
                 == (107, 160, 1), "GROUP15_TIFF_THUMBNAIL")
        _require(color["icc_sha256"] == SOURCE_ICC_SHA256 and len(icc_bytes) == 3144,
                 "GROUP15_ICC_IDENTITY")
    return {"format": "TIFF", "width": headers[0]["width"], "height": headers[0]["height"],
            "channels": 3, "bits": headers[0]["bits_per_sample"][0],
            "orientation": 1, "selected_ifd": 0,
            "ifds": headers, "color": color, "icc_bytes": icc_bytes}


def inspect_source(path, *, strict_group15=True, development_pair=False):
    _require(not (strict_group15 and development_pair), "SOURCE_MODE_CONFLICT")
    before = _identity(path)
    if strict_group15:
        _require(before["bytes"] == 146342866 and before["sha256"] == SOURCE_SHA256,
                 "GROUP15_SOURCE_IDENTITY")
    with open(path, "rb") as handle:
        out = (_development_header(path, handle) if development_pair else
               _tiff_header(handle, strict_group15=strict_group15))
    if not strict_group15 and not development_pair:
        _require(out["width"] <= 512 and out["height"] <= 512, "SYNTHETIC_SIZE_LIMIT")
    _check_unchanged(path, before)
    return {**before, **out, "decoder_id": _decoder_id(out)}


def _png_header(handle, *, allow_rgb16=False):
    _require(handle.read(8) == b"\x89PNG\r\n\x1a\n", "PNG_SIGNATURE")
    seen = set()
    tags = {}
    while True:
        head = handle.read(8)
        _require(len(head) == 8, "PNG_TRUNCATED")
        n, tag = struct.unpack(">I4s", head)
        _require(n <= (MAX_IMAGE_BYTES if tag == b"IDAT" else 16 * 1024**2), "PNG_CHUNK_LIMIT")
        _require(tag not in (b"acTL", b"fcTL", b"fdAT"), "PNG_ANIMATION_UNSUPPORTED")
        if tag in (b"IHDR", b"iCCP", b"sRGB", b"gAMA", b"cHRM", b"eXIf"):
            _require(tag not in seen, "PNG_DUPLICATE_COLOR_OR_HEADER")
            seen.add(tag)
            data = handle.read(n)
            crc = handle.read(4)
            _require(len(data) == n and len(crc) == 4 and
                     zlib.crc32(tag + data) & 0xffffffff == struct.unpack(">I", crc)[0], "PNG_METADATA_CRC")
            tags[tag] = data
        else:
            handle.seek(n, os.SEEK_CUR)
            _require(len(handle.read(4)) == 4, "PNG_TRUNCATED")
        if tag == b"IEND":
            _require(n == 0 and handle.read(1) == b"", "PNG_TRAILING_DATA")
            break
    _require(b"IHDR" in tags and len(tags[b"IHDR"]) == 13, "PNG_IHDR")
    w, h, bits, kind, comp, filt, inter = struct.unpack(">IIBBBBB", tags[b"IHDR"])
    _require(bits in ((8, 16) if allow_rgb16 else (8,)) and kind == 2
             and comp == 0 and filt == 0 and inter in (0, 1),
             "PNG_RGB_SCHEMA" if allow_rgb16 else "PNG_RGB8_SCHEMA")
    check_geometry(w, h)
    icc_bytes = None
    if b"iCCP" in tags:
        try:
            name, packed = tags[b"iCCP"].split(b"\x00", 1)
            _require(1 <= len(name) <= 79 and packed[:1] == b"\x00", "COLOR_ICC_MALFORMED")
            inflater = zlib.decompressobj()
            icc_bytes = inflater.decompress(packed[1:], 1024**2 + 1)
            _require(inflater.eof and not inflater.unused_data and not inflater.unconsumed_tail,
                     "COLOR_ICC_MALFORMED")
        except (ValueError, zlib.error) as exc:
            raise CodecError("COLOR_ICC_MALFORMED") from exc
    def unpack_tag(tag, fmt, count):
        if tag not in tags:
            return None
        _require(len(tags[tag]) == count, "PNG_COLOR_MALFORMED")
        result = struct.unpack(fmt, tags[tag])
        return result[0] if len(result) == 1 else list(result)
    color = _color(icc_bytes, unpack_tag(b"sRGB", ">B", 1),
                   unpack_tag(b"gAMA", ">I", 4), unpack_tag(b"cHRM", ">8I", 32))
    if b'eXIf' in tags:
        from ..exif_structure import Graph
        graph=Graph(io.BytesIO(tags[b'eXIf']),len(tags[b'eXIf']))
        for row in graph.rows:
            if row['ifd']=='IFD0' and row['tag']==274:
                _require(graph.ints(row)==[1], 'PNG_EXIF_ORIENTATION_UNSUPPORTED')
    return {"format": "PNG", "width": w, "height": h, "channels": 3, "bits": bits,
            "orientation": 1, "color": color, "icc_bytes": icc_bytes}


def _decoder_id(info):
    return (f"tifffile-{tifffile.__version__}:IFD0" if info["format"] == "TIFF" else
            f"imagecodecs-{imagecodecs.__version__}:PNG-RGB" if info["format"] == "PNG" else
            f"Pillow-{Image.__version__}:{info['format']}-RGB")


def _development_header(path, handle):
    suffix = Path(path).suffix.lower()
    expected = {".tif": "TIFF", ".tiff": "TIFF", ".png": "PNG",
                ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".bmp": "BMP"}
    _require(suffix in expected, "IMAGE_FORMAT_UNSUPPORTED")
    fmt = expected[suffix]
    if fmt == "TIFF":
        return _tiff_header(handle, allow_rgb8=True)
    if fmt == "PNG":
        return _png_header(handle, allow_rgb16=True)
    with Image.open(handle, formats=[fmt]) as image:
        _require(image.format == fmt and getattr(image, "n_frames", 1) == 1,
                 "IMAGE_FORMAT_MISMATCH_OR_ANIMATED")
        _require(image.mode == "RGB", "IMAGE_RGB_SCHEMA")
        w, h = image.size
        check_geometry(w, h)
        orientation = image.getexif().get(274, 1)
        _require(type(orientation) is int and 1 <= orientation <= 8,
                 "IMAGE_ORIENTATION_UNSUPPORTED")
        if orientation in (5, 6, 7, 8):
            w, h = h, w
        icc = image.info.get("icc_profile")
        _require(fmt != "BMP" or icc is None, "BMP_ICC_UNSUPPORTED")
        return {"format": fmt, "width": w, "height": h, "channels": 3,
                "bits": 8, "orientation": 1, "original_orientation": orientation,
                "color": _color(icc), "icc_bytes": icc}


def inspect_reference(path, *, strict_group15=True, development_pair=False):
    _require(not (strict_group15 and development_pair), "REFERENCE_MODE_CONFLICT")
    before = _identity(path)
    if strict_group15:
        _require(before["bytes"] == 2741798 and before["sha256"] == REFERENCE_SHA256,
                 "GROUP15_REFERENCE_IDENTITY")
    with open(path, "rb") as handle:
        out = _development_header(path, handle) if development_pair else _png_header(handle)
    if strict_group15:
        _require((out["width"], out["height"]) == (1024, 1536)
                 and not out["color"]["icc_present"] and not out["color"]["srgb_present"]
                 and out["color"]["png_gamma"] is None and out["color"]["png_chromaticity"] is None,
                 "GROUP15_REFERENCE_SCHEMA")
    elif not development_pair:
        _require(out["width"] <= 512 and out["height"] <= 512, "SYNTHETIC_SIZE_LIMIT")
    _check_unchanged(path, before)
    return {**before, **out, "decoder_id": _decoder_id(out)}


def _read_development(path, info):
    fmt = info["format"]
    if fmt == "TIFF":
        with open(path, "rb") as handle, tifffile.TiffFile(handle) as tf:
            pixels = tf.pages[0].asarray(maxworkers=4)
    elif fmt == "PNG":
        pixels = imagecodecs.png_decode(Path(path).read_bytes())
    else:
        with open(path, "rb") as handle, Image.open(handle, formats=[fmt]) as image:
            _require(image.format == fmt and image.mode == "RGB", "IMAGE_DECODE_SCHEMA")
            with ImageOps.exif_transpose(image) as oriented:
                pixels = np.array(oriented, dtype=np.uint8, copy=True)
    dtype = np.uint16 if info["bits"] == 16 else np.uint8
    _require(pixels.dtype == dtype and pixels.shape == (info["height"], info["width"], 3),
             "IMAGE_DECODE_SCHEMA")
    return pixels


def read_source(path, *, strict_group15=True, development_pair=False):
    info = inspect_source(path, strict_group15=strict_group15,
                          development_pair=development_pair)
    if development_pair:
        pixels = _read_development(path, info)
    else:
        with open(path, "rb") as handle, tifffile.TiffFile(handle) as tf:
            pixels = tf.pages[0].asarray(maxworkers=4)
        _require(pixels.dtype == np.uint16 and pixels.shape == (info["height"], info["width"], 3),
                 "TIFF_DECODE_SCHEMA")
    _check_unchanged(path, {k: info[k] for k in ("path", "bytes", "sha256")})
    info["pixel_sha256"] = pixel_sha256(pixels)
    pixels.flags.writeable = False
    return pixels, info


def read_reference(path, *, strict_group15=True, development_pair=False):
    info = inspect_reference(path, strict_group15=strict_group15,
                             development_pair=development_pair)
    if development_pair:
        pixels = _read_development(path, info)
    else:
        with open(path, "rb") as handle, Image.open(handle, formats=["PNG"]) as image:
            _require(image.mode == "RGB" and image.format == "PNG", "PNG_DECODE_SCHEMA")
            pixels = np.array(image, dtype=np.uint8, copy=True)
        _require(pixels.shape == (info["height"], info["width"], 3), "PNG_DECODE_GEOMETRY")
    _check_unchanged(path, {k: info[k] for k in ("path", "bytes", "sha256")})
    info["pixel_sha256"] = pixel_sha256(pixels)
    pixels.flags.writeable = False
    return pixels, info


def _rgb_array(pixels):
    a = np.asarray(pixels)
    _require(a.ndim == 3 and a.shape[2] == 3 and 0 < a.shape[0] * a.shape[1] <= MAX_PIXELS,
             "RGB_GEOMETRY")
    return a


def _quantize(pixels, maximum, dtype):
    a = _rgb_array(pixels)
    _require(np.issubdtype(a.dtype, np.floating), "QUANTIZE_FLOAT_REQUIRED")
    # Bounded row chunks preserve the specified FP64 operation at native size.
    out = np.empty(a.shape, dtype=dtype)
    for y in range(0, a.shape[0], 64):
        _require(np.isfinite(a[y:y+64]).all(), "PIXELS_NONFINITE")
        block = a[y:y+64].astype(np.float64)
        np.clip(block, 0.0, 1.0, out=block)
        block *= maximum
        np.rint(block, out=block)
        out[y:y+64] = block.astype(dtype)
    return out


def quantize16(rgb_float):
    return _quantize(rgb_float, 65535, np.uint16)


def quantize8(rgb_float):
    return _quantize(rgb_float, 255, np.uint8)


def write_tiff(path, q16, icc_bytes, *, byteorder=None):
    a = _rgb_array(q16)
    _require(a.dtype in (np.uint8, np.uint16), "TIFF_INTEGER_REQUIRED")
    if icc_bytes is not None:
        _icc(icc_bytes)
    # Caller owns staging/atomic publication. Exclusive create forbids overwrite.
    with open(path, "xb") as handle:
        tifffile.imwrite(handle, a, photometric="rgb", planarconfig="contig", compression="deflate",
                         predictor=2, rowsperstrip=64, iccprofile=icc_bytes, metadata=None,
                         software=False, extratags=[(274, "H", 1, 1, False)], maxworkers=4, byteorder=byteorder)
        handle.flush()
        os.fsync(handle.fileno())
    _require(Path(path).stat().st_size <= MAX_TIFF_BYTES, "TIFF_OUTPUT_BUDGET")
    return {**_identity(path), "pixel_sha256": pixel_sha256(a), "shape": list(a.shape),
            "bits": a.dtype.itemsize * 8, "icc_sha256": hashlib.sha256(icc_bytes).hexdigest()
            if icc_bytes is not None else None, "orientation": 1}


def readback_tiff(path, expected_icc, *, expected_pixels=None, expected_pixel_sha256=None):
    if expected_icc is not None:
        _icc(expected_icc)
    before = _identity(path)
    _require(before["bytes"] <= MAX_TIFF_BYTES, "TIFF_OUTPUT_BUDGET")
    with open(path, "rb") as handle:
        info = _tiff_header(handle, single_output=True, allow_rgb8=True)
    _require(info["icc_bytes"] == expected_icc and info["ifds"][0]["compression"] in (8, 32946),
             "TIFF_OUTPUT_METADATA")
    with open(path, "rb") as handle, tifffile.TiffFile(handle) as tf:
        actual = tf.pages[0].asarray(maxworkers=4)
    _require(actual.dtype == (np.uint16 if info["bits"] == 16 else np.uint8)
             and actual.shape == (info["height"], info["width"], 3),
             "TIFF_READBACK_SCHEMA")
    digest = pixel_sha256(actual)
    if expected_pixels is not None:
        expected = _rgb_array(expected_pixels)
        _require(expected.dtype == actual.dtype and expected.shape == actual.shape
                 and np.array_equal(actual, expected), "TIFF_READBACK_PIXEL_MISMATCH")
    if expected_pixel_sha256 is not None:
        _require(digest == expected_pixel_sha256, "TIFF_READBACK_PIXEL_HASH_MISMATCH")
    _require(expected_pixels is not None or expected_pixel_sha256 is not None,
             "TIFF_READBACK_EXPECTED_PIXELS_REQUIRED")
    _check_unchanged(path, before)
    return {**before, "pixel_sha256": digest, "pixel_exact": True, "shape": list(actual.shape),
            "bits": info["bits"], "orientation": 1, "compression": info["ifds"][0]["compression"],
            "icc_sha256": info["color"]["icc_sha256"]}


def write_jpg(path, rgb_float_or_q8, icc_bytes):
    if icc_bytes is not None:
        _icc(icc_bytes)
    a = _rgb_array(rgb_float_or_q8)
    q8 = a if a.dtype == np.uint8 else quantize8(a)
    with open(path, "xb") as handle:
        Image.fromarray(q8).save(handle, format="JPEG", quality=100, subsampling=0,
                                 icc_profile=icc_bytes, optimize=False, progressive=False)
        handle.flush()
        os.fsync(handle.fileno())
    with open(path, "rb") as handle, Image.open(handle, formats=["JPEG"]) as image:
        _require(image.mode == "RGB" and image.size == (q8.shape[1], q8.shape[0])
                 and image.info.get("icc_profile") == icc_bytes, "JPEG_HEADER_MISMATCH")
        _require(all(layer[1:3] == (1, 1) for layer in image.layer), "JPEG_SUBSAMPLING_MISMATCH")
        _require(all(v == 1 for table in image.quantization.values() for v in table),
                 "JPEG_QUALITY_MISMATCH")
    return {**_identity(path), "shape": list(q8.shape), "quality": 100, "subsampling": "4:4:4",
            "q8_sha256": pixel_sha256(q8), "icc_sha256": hashlib.sha256(icc_bytes).hexdigest()
            if icc_bytes is not None else None}


def write_common(path, pixels, fmt, icc_bytes):
    """Write PNG/WebP/BMP; all lossless at their source bit depth."""
    a = _rgb_array(pixels)
    _require(fmt in {"png", "webp", "bmp"}, "IMAGE_FORMAT_UNSUPPORTED")
    _require(a.dtype in (np.uint8, np.uint16), "IMAGE_INTEGER_REQUIRED")
    _require(fmt == "png" or a.dtype == np.uint8, "IMAGE_BIT_DEPTH_UNSUPPORTED")
    if icc_bytes is not None:
        _icc(icc_bytes)
    _require(fmt != "bmp" or icc_bytes is None, "BMP_ICC_UNSUPPORTED")
    with open(path, "xb") as handle:
        if fmt == "png":
            encoded = imagecodecs.png_encode(a)
            if icc_bytes is not None:
                profile = b"GradeMirror-sRGB\x00\x00" + zlib.compress(icc_bytes)
                chunk = struct.pack(">I", len(profile)) + b"iCCP" + profile
                chunk += struct.pack(">I", zlib.crc32(b"iCCP" + profile) & 0xffffffff)
                encoded = encoded[:33] + chunk + encoded[33:]
            handle.write(encoded)
        else:
            Image.fromarray(a).save(handle, format=fmt.upper(),
                **({"lossless": True, "icc_profile": icc_bytes} if fmt == "webp" else {}))
        handle.flush()
        os.fsync(handle.fileno())
    _require(Path(path).stat().st_size <= MAX_TIFF_BYTES, "IMAGE_OUTPUT_BUDGET")
    return {**_identity(path), "shape": list(a.shape), "bits": a.dtype.itemsize * 8,
            "pixel_sha256": pixel_sha256(a), "icc_sha256":
            hashlib.sha256(icc_bytes).hexdigest() if icc_bytes is not None else None}


def readback_common(path, fmt, expected_icc, expected_pixel_sha256):
    before = _identity(path)
    _require(before["bytes"] <= MAX_TIFF_BYTES, "IMAGE_OUTPUT_BUDGET")
    with open(path, "rb") as handle:
        if fmt == "png":
            info = _png_header(handle, allow_rgb16=True)
        else:
            with Image.open(handle, formats=[fmt.upper()]) as image:
                _require(image.format == fmt.upper() and image.mode == "RGB"
                         and getattr(image, "n_frames", 1) == 1, "IMAGE_READBACK_SCHEMA")
                info = {"format": fmt.upper(), "width": image.width,
                        "height": image.height, "bits": 8,
                        "icc_bytes": image.info.get("icc_profile")}
    _require(info["icc_bytes"] == expected_icc, "IMAGE_READBACK_ICC")
    actual = _read_development(path, info)
    digest = pixel_sha256(actual)
    _require(digest == expected_pixel_sha256, "IMAGE_READBACK_PIXEL_HASH_MISMATCH")
    _check_unchanged(path, before)
    return {**before, "pixel_sha256": digest, "pixel_exact": True,
            "shape": list(actual.shape), "bits": info["bits"], "orientation": 1,
            "icc_sha256": hashlib.sha256(expected_icc).hexdigest()
            if expected_icc is not None else None}
