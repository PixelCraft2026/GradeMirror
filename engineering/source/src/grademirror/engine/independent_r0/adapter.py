"""Thin in-memory R0 Lab M05 interface. Import inside the managed numeric worker only."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import ast
import io
import platform
import zipfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import torch

from . import core
from .profile import CORE_SHA256, PROFILE_CONTRACT_SHA256, PROFILE_ID, RUNTIME, SETTINGS


MAX_RECIPE_BYTES = 256 * 1024
RECIPE_SCHEMA_VERSION = "0.4.0"
# Fixed R0 data layout. Validation never instantiates a randomly initialized model.
from .profile import PARAMETER_SHAPES, MATH_SHA256
from ..profiles import normalization, settings_for



def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _strict_json(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("NONFINITE_JSON")
    result = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                        parse_constant=nonfinite)
    if not isinstance(result, dict):
        raise ValueError("RECIPE_METADATA_INVALID")
    _canonical(result)  # Also rejects overflowed exponents such as 1e999.
    return result


def _bounded_read(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_RECIPE_BYTES + 1)
    if len(data) > MAX_RECIPE_BYTES:
        raise ValueError("RECIPE_QUOTA")
    return data


def recipe_digest(metadata: dict) -> str:
    """Digest the entire effective recipe; archive container bytes are separate."""
    return _digest(_canonical({k: v for k, v in metadata.items()
                              if k not in {"recipe_sha256", "archive_sha256"}}))


def _hash_valid(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def array_identity(native: np.ndarray, *, file_sha256: str, file_size_bytes: int) -> dict:
    if (not isinstance(native, np.ndarray) or native.ndim != 3 or native.shape[2] != 3
            or native.dtype not in (np.uint8, np.uint16, np.float32) or min(native.shape) < 1):
        raise ValueError("RGB_SOURCE_INVALID")
    value = {"file_sha256": file_sha256, "file_size_bytes": file_size_bytes,
             "pixel_sha256": _digest(memoryview(np.ascontiguousarray(native)).cast("B")),
             "shape": list(native.shape), "dtype": str(native.dtype),
             "bit_depth": native.dtype.itemsize * 8}
    _validate_identity(value)
    return value


def _validate_identity(value) -> None:
    if not isinstance(value, dict) or set(value) != {"file_sha256", "file_size_bytes", "pixel_sha256", "shape", "dtype", "bit_depth"}:
        raise ValueError("INPUT_IDENTITY_INVALID")
    shape = value["shape"]
    if (not _hash_valid(value["file_sha256"]) or not _hash_valid(value["pixel_sha256"])
            or type(value["file_size_bytes"]) is not int or value["file_size_bytes"] < 0
            or not isinstance(shape, list) or len(shape) != 3 or shape[2] != 3
            or any(type(x) is not int or x < 1 for x in shape)
            or value["dtype"] not in {"uint8", "uint16", "float32"}
            or type(value["bit_depth"]) is not int
            or value["bit_depth"] != {"uint8": 8, "uint16": 16, "float32": 32}.get(value["dtype"])):
        raise ValueError("INPUT_IDENTITY_INVALID")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_runtime() -> dict[str, str]:
    actual = {name: importlib.metadata.version(name) for name in RUNTIME}
    if actual != RUNTIME:
        raise ValueError("RUNTIME_MISMATCH")
    from ...light.portable import source_sha
    if source_sha(core.__file__) != CORE_SHA256:
        raise ValueError("CORE_HASH_MISMATCH")
    for name, expected in MATH_SHA256.items():
        if source_sha(Path(core.__file__).parent / name) != expected:
            raise ValueError("MATH_HASH_MISMATCH")
    return actual


def _rgb_float(value: np.ndarray, label: str) -> np.ndarray:
    if not isinstance(value, np.ndarray) or value.dtype != np.float32 or value.ndim != 3 or value.shape[2] != 3:
        raise ValueError(f"{label}: expected HWC float32 RGB")
    if not np.isfinite(value).all() or (value < 0).any() or (value > 1).any():
        raise ValueError(f"{label}: expected finite normalized sRGB")
    return np.ascontiguousarray(value)


@dataclass(frozen=True)
class PreparedPair:
    before: np.ndarray
    after: np.ndarray
    source_sha256: str
    reference_sha256: str
    color_declaration: str
    resize_geometry: dict
    source_identity: dict
    reference_identity: dict
    color_policy: dict
    io_runtime: dict


def prepared_pair(before: np.ndarray, after: np.ndarray, *, source_sha256: str,
                  reference_sha256: str, color_declaration: str,
                  resize_geometry: dict, source_identity: dict | None = None,
                  reference_identity: dict | None = None, color_policy: dict | None = None,
                  io_runtime: dict | None = None) -> PreparedPair:
    b, a = _rgb_float(before, "source"), _rgb_float(after, "reference")
    if b.shape != a.shape or min(b.shape[:2]) < SETTINGS["window_side"]:
        raise ValueError("PAIR_GRID_INVALID")
    for value in (source_sha256, reference_sha256):
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("INPUT_HASH_INVALID")
    if color_declaration != "gamma-encoded-sRGB-float32":
        raise ValueError("COLOR_DECLARATION_INVALID")
    return PreparedPair(b, a, source_sha256, reference_sha256,
                        color_declaration, resize_geometry, source_identity,
                        reference_identity, color_policy, io_runtime)


def downscale_native(native: np.ndarray, long_side: int = 512) -> tuple[np.ndarray, dict]:
    """Normalize full integer precision before INTER_AREA; no implicit upscaling."""
    import cv2
    if not isinstance(native, np.ndarray) or native.ndim != 3 or native.shape[2] != 3 or native.dtype not in (np.uint8, np.uint16):
        raise ValueError("RGB_INTEGER_INPUT_REQUIRED")
    h, w = native.shape[:2]
    if max(h, w) < long_side:
        raise ValueError("UPSCALE_FORBIDDEN")
    scale = long_side / max(h, w)
    shape = (max(1, round(w * scale)), max(1, round(h * scale)))
    normalized = native.astype(np.float32) / np.iinfo(native.dtype).max
    resized = cv2.resize(normalized, shape, interpolation=cv2.INTER_AREA)
    # INTER_AREA averages valid integer RGB, but its float32 accumulation can
    # round a saturated value above 1. Keep in-range samples byte-identical and
    # restore the normalized domain before the strict prepared-pair validator.
    np.clip(resized, 0, 1, out=resized)
    return resized, {
        "native_hw": [h, w], "training_hw": [shape[1], shape[0]],
        "interpolation": "INTER_AREA", "normalize_before_resize": True}


@dataclass
class FittedRecipe:
    metadata: dict
    parameters: dict[str, np.ndarray]


@dataclass
class RenderResult:
    source: np.ndarray
    result: np.ndarray
    recipe_sha256: str


@dataclass
class EngineCounts:
    fit: int = 0
    render: int = 0


def _parameters(model: core.Field) -> dict[str, np.ndarray]:
    return {key: value.detach().cpu().numpy().copy() for key, value in model.state_dict().items()}


def _parameter_hash(parameters: dict[str, np.ndarray]) -> str:
    h = hashlib.sha256()
    for key in sorted(parameters):
        h.update(key.encode("utf-8") + b"\0")
        h.update(np.ascontiguousarray(parameters[key]).tobytes())
    return h.hexdigest()


def _device(device: str, *, recipe_kind: str, device_metadata: dict | None = None) -> dict:
    if not isinstance(device, str) or (device != "cpu" and not (device.startswith("cuda:") and device[5:].isdigit())):
        raise ValueError("EXPLICIT_DEVICE_REQUIRED")
    if recipe_kind == "development_image" and device == "cpu":
        raise ValueError("DEVELOPMENT_CUDA_REQUIRED")
    value = {"id": device, "cuda_version": torch.version.cuda,
             "memory_format": "channels_last", "dtype": "float32",
             "cudnn_benchmark": False, "cudnn_deterministic": True,
             "matmul_tf32": False, "cudnn_tf32": False,
             "deterministic_algorithms": torch.are_deterministic_algorithms_enabled()}
    if device == "cpu":
        value.update(name="cpu", uuid=None, driver_version=None)
    else:
        if not torch.cuda.is_available() or int(device[5:]) >= torch.cuda.device_count():
            raise ValueError("CUDA_DEVICE_UNAVAILABLE")
        if (not isinstance(device_metadata, dict) or set(device_metadata) != {"uuid", "driver_version"}
                or any(not isinstance(v, str) or not v for v in device_metadata.values())):
            raise ValueError("CUDA_DEVICE_METADATA_REQUIRED")
        props = torch.cuda.get_device_properties(device)
        actual_uuid = getattr(props, "uuid", None)
        if actual_uuid is not None and "GPU-" + str(uuid.UUID(bytes=bytes(actual_uuid.bytes))) != device_metadata["uuid"]:
            raise ValueError("CUDA_DEVICE_UUID_MISMATCH")
        value.update(name=props.name, **device_metadata)
    return value


def _pair_metadata(pair: PreparedPair, recipe_kind: str) -> dict:
    if recipe_kind not in {"synthetic_test", "development_image"}:
        raise ValueError("RECIPE_KIND_INVALID")
    source, reference = pair.source_identity, pair.reference_identity
    if recipe_kind == "synthetic_test":
        source = source or array_identity(pair.before, file_sha256=pair.source_sha256, file_size_bytes=pair.before.nbytes)
        reference = reference or array_identity(pair.after, file_sha256=pair.reference_sha256, file_size_bytes=pair.after.nbytes)
    for identity, digest in ((source, pair.source_sha256), (reference, pair.reference_sha256)):
        _validate_identity(identity)
        if identity["file_sha256"] != digest:
            raise ValueError("PAIR_FILE_IDENTITY_MISMATCH")
    value = {"source_identity": source, "reference_identity": reference,
             "color_policy": pair.color_policy or {"color_policy_id": "synthetic-test", "source": {"interpretation": "synthetic_sRGB"}, "reference": {"interpretation": "synthetic_sRGB"}},
             "io_runtime": pair.io_runtime or {"fixture": "in-memory-synthetic"},
             "resize_geometry": pair.resize_geometry,
             "training_shape": list(pair.before.shape),
             "training_source_pixel_sha256": _digest(pair.before.tobytes()),
             "training_reference_pixel_sha256": _digest(pair.after.tobytes())}
    if recipe_kind == "development_image" and (pair.color_policy is None or pair.io_runtime is None):
        raise ValueError("DEVELOPMENT_COLOR_IO_METADATA_REQUIRED")
    _validate_bindings(value, recipe_kind)
    return value


def _validate_bindings(meta: dict, recipe_kind: str) -> None:
    for key in ("source_identity", "reference_identity"):
        _validate_identity(meta[key])
    for key in ("training_source_pixel_sha256", "training_reference_pixel_sha256"):
        if not _hash_valid(meta[key]):
            raise ValueError("RECIPE_TRAINING_HASH_INVALID")
    shape = meta["training_shape"]
    if (not isinstance(shape, list) or len(shape) != 3 or shape[2] != 3
            or any(type(x) is not int or x < 1 for x in shape) or min(shape[:2]) < 13):
        raise ValueError("RECIPE_TRAINING_SHAPE_INVALID")
    color = meta["color_policy"]
    if (not isinstance(color, dict) or not isinstance(color.get("color_policy_id"), str)
            or not color["color_policy_id"] or any(not isinstance(color.get(k), dict)
            or not isinstance(color[k].get("interpretation"), str) or not color[k]["interpretation"]
            for k in ("source", "reference"))):
        raise ValueError("RECIPE_COLOR_POLICY_INVALID")
    runtime = meta["io_runtime"]
    if not isinstance(runtime, dict) or not runtime or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in runtime.items()):
        raise ValueError("RECIPE_IO_RUNTIME_INVALID")
    geometry = meta["resize_geometry"]
    if not isinstance(geometry, dict):
        raise ValueError("RECIPE_GEOMETRY_INVALID")
    if recipe_kind == "development_image":
        if color["color_policy_id"] != "untagged-assume-srgb-user-20261017":
            raise ValueError("DEVELOPMENT_COLOR_POLICY_INVALID")
        if set(geometry) != {"source", "reference"}:
            raise ValueError("RECIPE_GEOMETRY_INVALID")
        for name in ("source", "reference"):
            identity = meta[name + "_identity"]
            h, w = identity["shape"][:2]
            scale = 512 / max(h, w)
            expected = {"native_hw": [h, w], "training_hw": [max(1, round(h * scale)), max(1, round(w * scale))],
                        "interpolation": "INTER_AREA", "normalize_before_resize": True}
            if max(h, w) < 512 or geometry[name] != expected or expected["training_hw"] != shape[:2]:
                raise ValueError("RECIPE_GEOMETRY_INVALID")
        if any(meta[name + "_identity"]["dtype"] not in {"uint8", "uint16"} for name in ("source", "reference")):
            raise ValueError("DEVELOPMENT_INTEGER_INPUT_REQUIRED")


def fit(pair: PreparedPair, *, allow_development_profile: bool,
        device: str = "cuda:0", recipe_kind: str = "development_image",
        device_metadata: dict | None = None,
        progress: Callable[[dict], None] | None = None,
        counts: EngineCounts | None = None,
        _short_test: bool = False) -> tuple[FittedRecipe, dict]:
    if not allow_development_profile:
        raise ValueError("DEVELOPMENT_PROFILE_OPT_IN_REQUIRED")
    check_runtime()
    _rgb_float(pair.before, "source")
    _rgb_float(pair.after, "reference")
    if pair.before.shape != pair.after.shape:
        raise ValueError("PAIR_GRID_INVALID")
    if _short_test != (recipe_kind == "synthetic_test"):
        raise ValueError("SYNTHETIC_SETTINGS_OPT_IN_REQUIRED")
    bindings = _pair_metadata(pair, recipe_kind)
    device_info = _device(device, recipe_kind=recipe_kind, device_metadata=device_metadata)
    settings = settings_for(PROFILE_ID, list(pair.before.shape[:2]))
    if _short_test:
        settings = {**settings, "updates": 3}
    if counts is not None:
        counts.fit += 1
    if progress:
        progress({"stage": "before_fit", "update": 0})
    model, record = core.fit_pair(pair.before, pair.after, settings, device, progress)
    parameters = _parameters(model)
    metadata = {"recipe_schema_version": RECIPE_SCHEMA_VERSION, "recipe_kind": recipe_kind,
                "profile_id": PROFILE_ID,
                "profile_contract_sha256": PROFILE_CONTRACT_SHA256,
                "production_qualified": False,
                "core_sha256": CORE_SHA256, "runtime": RUNTIME,
                "python_version": platform.python_version(),
                "device": device_info, "settings": settings,
                "source_sha256": pair.source_sha256,
                "reference_sha256": pair.reference_sha256,
                "color_declaration": pair.color_declaration,
                **bindings, "input_normalization": normalization(pair.source_identity, pair.reference_identity), "parameter_sha256": _parameter_hash(parameters)}
    metadata["recipe_sha256"] = recipe_digest(metadata)
    return FittedRecipe(metadata, parameters), record


def save_recipe(recipe: FittedRecipe, directory: Path) -> dict:
    validate_recipe(recipe, allow_synthetic_test=True)
    directory = Path(directory)
    from grademirror.common import writable_path
    directory = writable_path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    weights = directory / "parameters.npz"
    np.savez(weights, **recipe.parameters)
    if weights.stat().st_size > MAX_RECIPE_BYTES:
        raise ValueError("RECIPE_QUOTA")
    meta = {**recipe.metadata, "archive_sha256": _digest(weights.read_bytes())}
    payload = json.dumps(meta, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > MAX_RECIPE_BYTES:
        raise ValueError("RECIPE_QUOTA")
    (directory / "recipe.json").write_bytes(payload)
    return meta


def validate_recipe(recipe: FittedRecipe, *, allow_synthetic_test: bool = False) -> None:
    check_runtime()
    meta = recipe.metadata
    required = {"recipe_schema_version", "recipe_kind", "profile_id", "profile_contract_sha256",
                "production_qualified", "core_sha256", "runtime", "python_version",
                "device", "settings", "source_sha256", "reference_sha256",
                "color_declaration", "resize_geometry", "parameter_sha256", "recipe_sha256",
                "source_identity", "reference_identity", "color_policy", "io_runtime", "training_shape",
                "training_source_pixel_sha256", "training_reference_pixel_sha256", "input_normalization"}
    if set(meta) != required or meta.get("production_qualified") is not False:
        raise ValueError("RECIPE_METADATA_INVALID")
    kind = meta.get("recipe_kind")
    if kind not in {"synthetic_test", "development_image"}:
        raise ValueError("RECIPE_KIND_INVALID")
    if kind == "synthetic_test" and not allow_synthetic_test:
        raise ValueError("SYNTHETIC_RECIPE_FORBIDDEN")
    if (meta.get("recipe_schema_version") != RECIPE_SCHEMA_VERSION or
        meta.get("profile_id") != PROFILE_ID or
        meta.get("profile_contract_sha256") != PROFILE_CONTRACT_SHA256 or
        meta.get("core_sha256") != CORE_SHA256 or
        meta.get("runtime") != RUNTIME or meta.get("python_version") != platform.python_version()):
        raise ValueError("RECIPE_PROFILE_RUNTIME_MISMATCH")
    for name in ("source_sha256", "reference_sha256"):
        value = meta.get(name)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("RECIPE_INPUT_HASH_INVALID")
    if meta.get("color_declaration") != "gamma-encoded-sRGB-float32" or not isinstance(meta.get("resize_geometry"), dict):
        raise ValueError("RECIPE_COLOR_GEOMETRY_INVALID")
    _canonical(meta)
    _validate_bindings(meta, kind)
    if any(meta[name + "_sha256"] != meta[name + "_identity"]["file_sha256"] for name in ("source", "reference")):
        raise ValueError("RECIPE_FILE_IDENTITY_MISMATCH")
    device = meta.get("device")
    device_fields = {"id", "name", "uuid", "driver_version", "cuda_version", "memory_format", "dtype",
                     "cudnn_benchmark", "cudnn_deterministic", "matmul_tf32", "cudnn_tf32", "deterministic_algorithms"}
    if (not isinstance(device, dict) or set(device) != device_fields
            or device["memory_format"] != "channels_last" or device["dtype"] != "float32"
            or device["cudnn_benchmark"] is not False or device["cudnn_deterministic"] is not True
            or device["matmul_tf32"] is not False or device["cudnn_tf32"] is not False
            or type(device["deterministic_algorithms"]) is not bool
            or not isinstance(device["id"], str)
            or (device["id"] != "cpu" and not (device["id"].startswith("cuda:") and device["id"][5:].isdigit()))
            or (kind == "development_image" and device["id"] == "cpu")
            or not isinstance(device["name"], str) or not device["name"]):
        raise ValueError("RECIPE_DEVICE_INVALID")
    if device["id"] != "cpu" and any(not isinstance(device[k], str) or not device[k] for k in ("uuid", "driver_version", "cuda_version")):
        raise ValueError("RECIPE_DEVICE_INVALID")
    settings = meta.get("settings")
    expected_settings = settings_for(PROFILE_ID, meta["training_shape"][:2])
    if kind == "synthetic_test": expected_settings = {**expected_settings, "updates": 3}
    if meta["input_normalization"] != normalization(meta["source_identity"], meta["reference_identity"]):
        raise ValueError("RECIPE_NORMALIZATION_INVALID")
    if _canonical(settings) != _canonical(expected_settings):
        raise ValueError("RECIPE_SETTINGS_INVALID")
    if set(recipe.parameters) != set(PARAMETER_SHAPES):
        raise ValueError("RECIPE_KEYS_INVALID")
    total = 0
    for key, shape in PARAMETER_SHAPES.items():
        arr = recipe.parameters[key]
        if not isinstance(arr, np.ndarray) or arr.dtype != np.float32 or arr.shape != shape or not np.isfinite(arr).all():
            raise ValueError("RECIPE_ARRAY_INVALID")
        total += arr.nbytes
    if total > MAX_RECIPE_BYTES:
        raise ValueError("RECIPE_QUOTA")
    if _parameter_hash(recipe.parameters) != meta.get("parameter_sha256"):
        raise ValueError("RECIPE_PARAMETER_HASH_INVALID")
    if recipe_digest(meta) != meta.get("recipe_sha256"):
        raise ValueError("RECIPE_DIGEST_INVALID")


def _preflight_npz(data: bytes) -> None:
    """Validate all bounded NPY headers and exact payloads before np.load."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        names = [item.filename for item in members]
        expected = {key + ".npy": shape for key, shape in PARAMETER_SHAPES.items()}
        if len(names) != len(set(names)):
            raise ValueError("DUPLICATE_ZIP_KEY")
        if set(names) != set(expected):
            raise ValueError("RECIPE_KEYS_INVALID")
        if sum(item.file_size for item in members) > MAX_RECIPE_BYTES:
            raise ValueError("RECIPE_EXPANDED_QUOTA")
        total = 0
        for item in members:
            if item.flag_bits & 1 or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValueError("RECIPE_ZIP_INVALID")
            with archive.open(item) as member:
                if member.read(6) != b"\x93NUMPY":
                    raise ValueError("RECIPE_NPY_MAGIC_INVALID")
                version = member.read(2)
                if version == b"\x01\x00":
                    width, encoding = 2, "latin1"
                elif version in {b"\x02\x00", b"\x03\x00"}:
                    width, encoding = 4, "utf-8" if version == b"\x03\x00" else "latin1"
                else:
                    raise ValueError("RECIPE_NPY_VERSION_INVALID")
                raw_length = member.read(width)
                if len(raw_length) != width:
                    raise ValueError("RECIPE_NPY_HEADER_INVALID")
                header_length = int.from_bytes(raw_length, "little")
                if not 1 <= header_length <= 4096:
                    raise ValueError("RECIPE_NPY_HEADER_QUOTA")
                raw_header = member.read(header_length)
                if len(raw_header) != header_length or not raw_header.endswith(b"\n"):
                    raise ValueError("RECIPE_NPY_HEADER_INVALID")
                try:
                    tree = ast.parse(raw_header.decode(encoding).strip(), mode="eval")
                    # literal_eval silently collapses duplicate dict keys; reject before it.
                    if not isinstance(tree.body, ast.Dict) or len(tree.body.keys) != 3:
                        raise ValueError("RECIPE_NPY_HEADER_INVALID")
                    keys = [ast.literal_eval(k) for k in tree.body.keys]
                    if len(keys) != len(set(keys)):
                        raise ValueError("RECIPE_NPY_HEADER_INVALID")
                    header = ast.literal_eval(tree)
                except (SyntaxError, UnicodeError, TypeError, RecursionError) as exc:
                    raise ValueError("RECIPE_NPY_HEADER_INVALID") from exc
                shape = expected[item.filename]
                if (set(header) != {"descr", "fortran_order", "shape"}
                        or header["descr"] != "<f4" or header["fortran_order"] is not False
                        or not isinstance(header["shape"], tuple)
                        or any(type(n) is not int for n in header["shape"])
                        or header["shape"] != shape):
                    raise ValueError("RECIPE_NPY_DTYPE_SHAPE_INVALID")
                payload_bytes = 4
                for n in shape:
                    payload_bytes *= n
                if item.file_size != 8 + width + header_length + payload_bytes:
                    raise ValueError("RECIPE_NPY_PAYLOAD_INVALID")
                total += payload_bytes
                if total > MAX_RECIPE_BYTES:
                    raise ValueError("RECIPE_EXPANDED_QUOTA")
                if len(member.read(payload_bytes + 1)) != payload_bytes:
                    raise ValueError("RECIPE_NPY_PAYLOAD_INVALID")


def inspect_legacy_recipe(directory: Path) -> dict:
    """Read-only recognition of historical test metadata; never loads a model."""
    directory = Path(directory)
    from grademirror.common import reject_reparse
    reject_reparse(directory)
    manifest = directory / "recipe.json"
    reject_reparse(manifest)
    if manifest.stat().st_size > MAX_RECIPE_BYTES:
        raise ValueError("RECIPE_QUOTA")
    meta = _strict_json(_bounded_read(manifest))
    if meta.get("recipe_schema_version") != "0.1.0":
        raise ValueError("LEGACY_RECIPE_VERSION_INVALID")
    return {"read_only": True, "usable_for_render": False, "metadata": meta}


def load_recipe(directory: Path, *, allow_synthetic_test: bool = False) -> FittedRecipe:
    directory = Path(directory)
    from grademirror.common import reject_reparse
    reject_reparse(directory)
    weights, manifest = directory / "parameters.npz", directory / "recipe.json"
    reject_reparse(weights)
    reject_reparse(manifest)
    if weights.stat().st_size > MAX_RECIPE_BYTES or manifest.stat().st_size > MAX_RECIPE_BYTES:
        raise ValueError("RECIPE_QUOTA")
    meta = _strict_json(_bounded_read(manifest))
    if meta.get("recipe_schema_version") != RECIPE_SCHEMA_VERSION:
        raise ValueError("RECIPE_VERSION_UNSUPPORTED")
    if meta.get("recipe_kind") == "synthetic_test" and not allow_synthetic_test:
        raise ValueError("SYNTHETIC_RECIPE_FORBIDDEN")
    data = _bounded_read(weights)
    if len(data) > MAX_RECIPE_BYTES:
        raise ValueError("RECIPE_QUOTA")
    if _digest(data) != meta.pop("archive_sha256", None):
        raise ValueError("RECIPE_ARCHIVE_HASH_INVALID")
    _preflight_npz(data)
    with np.load(io.BytesIO(data), allow_pickle=False) as archive:
        parameters = {key: archive[key].copy() for key in archive.files}
    recipe = FittedRecipe(meta, parameters)
    validate_recipe(recipe, allow_synthetic_test=allow_synthetic_test)
    return recipe


def _validate_output(source: np.ndarray, recipe: FittedRecipe, out) -> None:
    """Validate a borrowed target without converting, allocating or writing it."""
    if out is None:
        return
    if not isinstance(out, np.ndarray):
        raise ValueError("RENDER_OUTPUT_TYPE")
    if out.dtype != np.dtype(np.float32) or not out.dtype.isnative:
        raise ValueError("RENDER_OUTPUT_DTYPE")
    if out.shape != source.shape:
        raise ValueError("RENDER_OUTPUT_SHAPE")
    if not out.flags.writeable or not out.flags.c_contiguous or any(s < 0 for s in out.strides):
        raise ValueError("RENDER_OUTPUT_LAYOUT")
    if np.shares_memory(out, source) or any(np.shares_memory(out, p) for p in recipe.parameters.values()):
        raise ValueError("RENDER_OUTPUT_OVERLAP")


def render(source: np.ndarray, recipe: FittedRecipe, *, tile_side: int = 512,
           device: str = "cuda:0", source_identity: dict | None = None,
           allow_synthetic_test: bool = False,
           cancelled: Callable[[], bool] | None = None,
           counts: EngineCounts | None = None, out: np.ndarray | None = None,
           progress=None, execution_device: dict | None = None) -> RenderResult:
    validate_recipe(recipe, allow_synthetic_test=allow_synthetic_test)
    if not isinstance(source, np.ndarray) or source.dtype not in (np.uint8, np.uint16, np.float32) or source.ndim != 3 or source.shape[2] != 3:
        raise ValueError("RGB_SOURCE_INVALID")
    if source.dtype == np.float32:
        _rgb_float(source, "source")
    _validate_identity(source_identity)
    if source_identity != recipe.metadata["source_identity"]:
        raise ValueError("RENDER_SOURCE_IDENTITY_MISMATCH")
    actual = array_identity(source, file_sha256=source_identity["file_sha256"],
                            file_size_bytes=source_identity["file_size_bytes"])
    if actual != source_identity:
        raise ValueError("RENDER_SOURCE_PIXELS_MISMATCH")
    expected_device = recipe.metadata["device"]
    if execution_device is not None:
        if (device == "cpu" or expected_device["id"] == "cpu" or
                set(execution_device) != {"name", "uuid", "driver_version"}):
            raise ValueError("RENDER_EXECUTION_DEVICE_INVALID")
        # The saved device is provenance. Bind CUDA to this attempt's admitted device.
        expected_device = {**expected_device, **execution_device}
    supplied = None if device == "cpu" else {k: expected_device[k] for k in ("uuid", "driver_version")}
    if _device(device, recipe_kind=recipe.metadata["recipe_kind"], device_metadata=supplied) != expected_device:
        raise ValueError("RENDER_DEVICE_MISMATCH")
    if type(tile_side) is not int or tile_side < 1 or (recipe.metadata["recipe_kind"] == "development_image" and tile_side != SETTINGS["tile_side"]):
        raise ValueError("RENDER_GEOMETRY_INVALID")
    _validate_output(source, recipe, out)
    if cancelled and cancelled():
        raise InterruptedError("RENDER_CANCELLED")
    if counts is not None:
        counts.render += 1
    core.configure_torch(recipe.metadata["settings"]["seed"])
    cfg = {k:v for k,v in recipe.metadata["settings"].items() if k not in ("seed", "long_side", "tile_side")}
    model = core.Field(cfg, recipe.metadata["settings"]["seed"]).to(device=device, memory_format=torch.channels_last)
    model.load_state_dict({key: torch.from_numpy(value.copy()) for key, value in recipe.parameters.items()}, strict=True)
    if out is None:
        out = np.empty(source.shape, dtype=np.float32)
    if progress is not None:
        completed=0
        total=((source.shape[0]+tile_side-1)//tile_side)*((source.shape[1]+tile_side-1)//tile_side)
        progress(0,total)
    for (y0, y1, x0, x1), part in core.render_tiles(model, source, tile_side):
        if counts is not None: counts.render_tiles = getattr(counts, "render_tiles", 0) + 1
        if cancelled and cancelled():
            raise InterruptedError("RENDER_CANCELLED")
        out[y0:y1, x0:x1] = part
        if progress is not None:
            completed+=1
            progress(completed,total)
    if cancelled and cancelled():
        raise InterruptedError("RENDER_CANCELLED")
    return RenderResult(source, out, recipe.metadata["recipe_sha256"])


def blend(rendered: RenderResult, alpha: float) -> np.ndarray:
    if not isinstance(alpha, (float, int)) or not np.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError("ALPHA_INVALID")
    source = rendered.source.astype(np.float32)
    if rendered.source.dtype.kind == "u":
        source /= np.iinfo(rendered.source.dtype).max
    if alpha == 0:
        return source
    if alpha == 1:
        return rendered.result.copy()
    return source + np.float32(alpha) * (rendered.result - source)
