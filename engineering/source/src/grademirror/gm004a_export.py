"""GM-004A bounded export publication and recovery.

The module deliberately knows no image codec.  ``writer`` writes the explicitly
requested encoding to the supplied same-directory temporary path.  ``validator``
reads that temporary output once and returns its checked semantic metadata.  The
module binds those semantics and the encoded file hash into a small commit
manifest, then publishes with an atomic, no-overwrite hard link.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid
from typing import Any, Callable, Mapping

from .common import GMError, reject_reparse, writable_path


SCHEMA_VERSION = "0.1.0"
EXIF_SCHEMA_VERSION = "0.2.0"
THUMBNAIL_SCHEMA_VERSION = "0.3.0"

def _schema(request):
    from .export_tiff_compact import POLICY as COMPACT_POLICY
    if request.get("metadata_policy_id") in ("source-exif-v2",COMPACT_POLICY):return THUMBNAIL_SCHEMA_VERSION
    return EXIF_SCHEMA_VERSION if request.get("metadata_policy_id") == "source-exif-v1" else SCHEMA_VERSION
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_FORMATS = {"tiff", "jpeg", "png", "webp", "bmp"}
_ICC_POLICIES = {"preserve_source", "srgb", "none"}
_VALIDATED_FIELDS = (
    "alpha", "format", "bit_depth", "icc_sha256", "source_sha256",
    "recipe_sha256", "lease_id", "lease_generation", "width", "height",
    "pixels_sha256",
)


class ExportError(RuntimeError):
    """Stable machine-readable failure from the export transaction."""

    def __init__(self, code: str, message: str, exit_code: int = 7):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


Writer = Callable[[Path, Mapping[str, Any]], None]
Validator = Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]


def _fail(code: str, message: str) -> None:
    raise ExportError(code, message)


def _check_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("EXPORT_REQUEST_INVALID", "JSON numbers must be finite")
        return
    if isinstance(value, list):
        for child in value:
            _check_json(child)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail("EXPORT_REQUEST_INVALID", "JSON object keys must be strings")
            _check_json(child)
        return
    _fail("EXPORT_REQUEST_INVALID", "Request contains a non-JSON value")


def _canonical(value: Any) -> bytes:
    _check_json(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        _fail("EXPORT_FILE_INVALID", "Expected a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if _file_identity(before) != _file_identity(after) or (
        before.st_size, before.st_mtime_ns
    ) != (after.st_size, after.st_mtime_ns):
        _fail("EXPORT_FILE_CHANGED", "File changed while its identity was checked")
    return digest.hexdigest()


def _file_identity(st: os.stat_result) -> dict[str, int]:
    # st_ino is the file index on supported Windows filesystems and inode on
    # POSIX.  Keep it in the prepared record to recognize our hard link after a
    # crash between publication and writing the commit manifest.
    return {"device": int(st.st_dev), "inode": int(st.st_ino)}


def _norm_hash(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        _fail("EXPORT_REQUEST_INVALID", f"{field} must be a SHA-256 hex digest")
    return value.lower()


def _resolve_source(value: Any) -> str:
    if not isinstance(value, str) or not value:
        _fail("EXPORT_REQUEST_INVALID", "source_path is required")
    try:
        path = Path(value).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ExportError("EXPORT_SOURCE_MISSING", "source_path cannot be resolved") from exc
    try:
        st = path.stat()
    except OSError as exc:
        raise ExportError("EXPORT_SOURCE_MISSING", "source_path cannot be read") from exc
    if not stat.S_ISREG(st.st_mode):
        _fail("EXPORT_REQUEST_INVALID", "source_path must identify a regular file")
    return str(path)


def _normalize_request(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        _fail("EXPORT_REQUEST_INVALID", "request must be a mapping")
    normalized = dict(request)
    required = {
        "request_key", "alpha", "format", "bit_depth", "icc_policy",
        "icc_sha256", "source_path", "source_sha256", "recipe_sha256",
        "lease_id", "lease_generation", "width", "height",
        "expected_pixels_sha256",
    }
    missing = required - normalized.keys()
    if missing:
        _fail("EXPORT_REQUEST_INVALID", "Missing explicit export field(s): " +
              ", ".join(sorted(missing)))

    key = normalized["request_key"]
    if not isinstance(key, str) or not key or len(key) > 128:
        _fail("REQUEST_KEY_INVALID", "request_key length must be 1..128")
    if any(ord(ch) < 32 for ch in key):
        _fail("REQUEST_KEY_INVALID", "request_key cannot contain control characters")

    alpha = normalized["alpha"]
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        _fail("EXPORT_REQUEST_INVALID", "alpha must be a finite number in [0, 1]")
    alpha = float(alpha)
    if not math.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
        _fail("EXPORT_REQUEST_INVALID", "alpha must be a finite number in [0, 1]")
    normalized["alpha"] = alpha

    fmt = normalized["format"]
    if not isinstance(fmt, str) or fmt not in _FORMATS:
        _fail("EXPORT_FORMAT_UNSUPPORTED", "unsupported image export format")
    depth = normalized["bit_depth"]
    if isinstance(depth, bool) or not isinstance(depth, int):
        _fail("EXPORT_REQUEST_INVALID", "bit_depth must match an explicitly supported format")
    if (fmt, depth) not in {("tiff", 8), ("tiff", 16), ("png", 8), ("png", 16),
                            ("jpeg", 8), ("webp", 8), ("bmp", 8)}:
        _fail("EXPORT_REQUEST_INVALID", "unsupported image format or bit depth")

    policy = normalized["icc_policy"]
    if not isinstance(policy, str) or policy not in _ICC_POLICIES:
        _fail("EXPORT_REQUEST_INVALID", "icc_policy must be explicit")
    icc_hash = _norm_hash(normalized["icc_sha256"], "icc_sha256", nullable=True)
    if policy == "none" and icc_hash is not None:
        _fail("EXPORT_REQUEST_INVALID", "icc_policy 'none' requires icc_sha256=null")
    if policy != "none" and icc_hash is None:
        _fail("EXPORT_REQUEST_INVALID", "selected ICC policy requires icc_sha256")
    normalized["icc_sha256"] = icc_hash

    normalized["source_path"] = _resolve_source(normalized["source_path"])
    for field in ("source_sha256", "recipe_sha256", "expected_pixels_sha256"):
        normalized[field] = _norm_hash(normalized[field], field)
    if not isinstance(normalized["lease_id"], str) or not normalized["lease_id"]:
        _fail("EXPORT_REQUEST_INVALID", "lease_id is required")
    generation = normalized["lease_generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        _fail("EXPORT_REQUEST_INVALID", "lease_generation must be a nonnegative integer")
    for field in ("width", "height"):
        dimension = normalized[field]
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
            _fail("EXPORT_REQUEST_INVALID", f"{field} must be a positive integer")

    if 'metadata_policy_id' in normalized:
        from .export_metadata import POLICY,RULE
        from .export_tiff_compact import POLICY as COMPACT_POLICY,RULE as COMPACT_RULE
        from .export_thumbnail import POLICY as NEW_POLICY,RULE as NEW_RULE,THUMBNAIL_POLICY
        fields={'metadata_policy_id','normalization_rule','source_exif_state','source_exif_sha256','source_exif_semantic_sha256','metadata_component_sha256'}
        if not fields <= normalized.keys() or (normalized['metadata_policy_id'],normalized['normalization_rule']) not in ((POLICY,RULE),(NEW_POLICY,NEW_RULE),(COMPACT_POLICY,COMPACT_RULE)):
            _fail('EXPORT_METADATA_POLICY_INVALID','Incomplete metadata policy')
        if normalized['metadata_policy_id']==COMPACT_POLICY and normalized['format']!='tiff':
            _fail('EXPORT_METADATA_POLICY_INVALID','Compact TIFF policy requires TIFF')
        if normalized['metadata_policy_id'] in (NEW_POLICY,COMPACT_POLICY) and normalized.get('thumbnail_policy_id')!=THUMBNAIL_POLICY:
            _fail('EXPORT_METADATA_POLICY_INVALID','Processed thumbnail policy is required')
        if normalized['source_exif_state'] not in ('SOURCE_EXIF_PRESENT','SOURCE_EXIF_ABSENT'):
            _fail('EXPORT_METADATA_POLICY_INVALID','Unknown source metadata state')
        for name in ('source_exif_sha256','source_exif_semantic_sha256','metadata_component_sha256'):
            normalized[name]=_norm_hash(normalized[name],name,nullable=name=='source_exif_sha256')

    # Preserve all additional JSON fields in the semantic digest.  This keeps
    # newly added output semantics from silently collapsing onto an old key.
    _check_json(normalized)
    return normalized


def _canonical_destination(value: os.PathLike[str] | str) -> Path:
    try:
        supplied = Path(value)
        if supplied.name in ("", ".", ".."):
            _fail("EXPORT_PATH_INVALID", "destination must name a file")
        supplied = writable_path(supplied)
        parent = supplied.parent.resolve(strict=True)
    except (TypeError, OSError, RuntimeError, GMError) as exc:
        raise ExportError("EXPORT_PATH_INVALID", "destination parent must exist") from exc
    if not parent.is_dir() or supplied.name in ("", ".", ".."):
        _fail("EXPORT_PATH_INVALID", "destination must name a file in an existing directory")
    return parent / supplied.name


def _source_hash(request: Mapping[str, Any]) -> str:
    path = Path(request["source_path"])
    try:
        digest = _sha256_file(path)
    except ExportError:
        raise
    except OSError as exc:
        raise ExportError("EXPORT_SOURCE_MISSING", "source file cannot be hashed") from exc
    if digest != request["source_sha256"]:
        _fail("EXPORT_SOURCE_IDENTITY_MISMATCH", "source bytes differ from request identity")
    return digest


def _semantic_digest(request: Mapping[str, Any], destination: Path) -> str:
    semantic = {key: value for key, value in request.items() if key != "request_key"}
    semantic["destination"] = os.path.normcase(str(destination))
    semantic["export_schema_version"] = _schema(request)
    return _sha256_bytes(_canonical(semantic))


def _safe_load(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            _fail("EXPORT_MANIFEST_INVALID", "manifest must be a regular file")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    _fail("EXPORT_MANIFEST_INVALID", "duplicate manifest key")
                result[key] = value
            return result
        def reject_constant(value):
            _fail("EXPORT_MANIFEST_INVALID", "nonfinite JSON constant")
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=unique, parse_constant=reject_constant)
    except ExportError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExportError("EXPORT_MANIFEST_INVALID", "manifest is unreadable") from exc
    if not isinstance(value, dict):
        _fail("EXPORT_MANIFEST_INVALID", "manifest root must be an object")
    return value


def _fsync_directory(path: Path) -> None:
    # Directory fsync is available on POSIX.  Windows filesystems still get
    # file fsync plus the atomic same-volume operation; opening directories for
    # fsync is not supported by every Windows runtime/filesystem.
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


class _SessionLock:
    """Process-crash-released single-writer lock using the stdlib OS API."""

    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError) as exc:
            self.stream.close()
            self.stream = None
            raise ExportError("EXPORT_BUSY", "another export owns this session lock") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


def _atomic_json(path: Path, value: Mapping[str, Any], *, exclusive: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(dict(value)) + b"\n"
    fd, raw_tmp = tempfile.mkstemp(prefix=".gm004a-manifest-", suffix=".partial",
                                   dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            try:
                os.link(tmp, path)
            except FileExistsError as exc:
                raise ExportError("EXPORT_MANIFEST_EXISTS", "manifest already exists") from exc
            tmp.unlink()
        else:
            os.replace(tmp, path)
        _fsync_directory(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _request_dir(session_dir: Path, key: str) -> tuple[Path, str]:
    key_hash = _sha256_bytes(key.encode("utf-8"))
    from .adaptive_execution.portable_contract import export_transaction
    return export_transaction(session_dir, key_hash), key_hash


def _paths(session_dir: Path, key: str):
    directory, key_hash = _request_dir(session_dir, key)
    return directory, key_hash, directory / "intent.json", directory / "prepared.json", directory / "commit.json"


@contextmanager
def _pin_jpeg_temp(path: Path):
    # The validator may open the path again for Pillow, so keep this exact
    # ordinary file readable but deny concurrent write/delete on Windows.
    reject_reparse(path)
    if os.name != "nt":
        with path.open("rb") as stream:
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                _fail("EXPORT_FILE_INVALID", "temporary output is not an independent regular file")
            yield stream
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                          wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    open_file.restype = wintypes.HANDLE
    final_name = kernel.GetFinalPathNameByHandleW
    final_name.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final_name.restype = wintypes.DWORD
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_file(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        name = ctypes.create_unicode_buffer(32768)
        size = final_name(handle, name, len(name), 0)
        if not 0 < size < len(name):
            raise ctypes.WinError(ctypes.get_last_error())
        actual = name.value
        if actual.startswith("\\\\?\\UNC\\"):
            actual = "\\\\" + actual[8:]
        else:
            actual = actual.removeprefix("\\\\?\\")
        if Path(actual) != path:
            _fail("EXPORT_FILE_INVALID", "opened temporary path has changed")
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close_handle(handle)
        raise
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_file_attributes & 0x400:
            _fail("EXPORT_FILE_INVALID", "temporary output is not an independent regular file")
        yield stream


def _publish_validated_file(temp: Path, destination: Path, prepared: Mapping[str, Any], *, jpeg: bool):
    reject_reparse(destination)
    reject_reparse(temp)
    if not jpeg:
        os.link(temp, destination)
        return
    with _pin_jpeg_temp(temp) as stream:
        st = os.fstat(stream.fileno())
        if (_file_identity(st) != prepared["file_identity"] or st.st_size != prepared["size_bytes"] or
                hashlib.file_digest(stream, "sha256").hexdigest() != prepared["output_sha256"]):
            _fail("EXPORT_PUBLISH_IDENTITY_INVALID", "JPEG temporary output changed before publication")
        os.link(temp, destination)
        if _file_identity(destination.stat()) != prepared["file_identity"]:
            _fail("EXPORT_PUBLISH_IDENTITY_INVALID", "publish did not link the validated JPEG")


def _file_matches(path: Path, expected_hash: str, expected_size: int,
                  expected_identity: Mapping[str, Any] | None = None) -> bool:
    try:
        reject_reparse(path)
        if not path.is_file():
            return False
        st = path.stat()
        if st.st_size != expected_size:
            return False
        if expected_identity is not None and _file_identity(st) != expected_identity:
            return False
        return _sha256_file(path) == expected_hash
    except (OSError, ExportError, GMError):
        return False


def _success(intent: Mapping[str, Any], prepared: Mapping[str, Any], *,
             status: str, replay: bool) -> dict[str, Any]:
    return {
        "status": status,
        "operation_id": intent["operation_id"],
        "request_key": intent["request_key"],
        "intent_sha256": intent["intent_sha256"],
        "destination": intent["destination"],
        "output_sha256": prepared["output_sha256"],
        "size_bytes": prepared["size_bytes"],
        "metadata": prepared["metadata"],
        "idempotent_replay": replay,
        "metadata_preservation_proof": intent["request"].get("metadata_policy_id","LEGACY_NO_SOURCE_EXIF_PROOF"),
    }


def _cleanup_validated_temp(intent: Mapping[str, Any], prepared: Mapping[str, Any]) -> None:
    try:
        temp_path = Path(intent["temporary_path"])
        if (not temp_path.is_symlink() and temp_path.is_file() and
                _file_identity(temp_path.stat()) == prepared.get("file_identity")):
            temp_path.unlink()
            _fsync_directory(temp_path.parent)
    except (KeyError, OSError):
        pass


def _recover_locked(directory: Path, intent_path: Path, prepared_path: Path,
                    commit_path: Path, expected_digest: str | None = None,
                    *, replay: bool = True, publication_guard=None) -> dict[str, Any]:
    intent = _safe_load(intent_path)
    if intent.get("schema_version") not in (SCHEMA_VERSION, EXIF_SCHEMA_VERSION, THUMBNAIL_SCHEMA_VERSION):
        _fail("EXPORT_SCHEMA_UNSUPPORTED", "unsupported export manifest schema")
    if expected_digest is not None and intent.get("intent_sha256") != expected_digest:
        _fail("REQUEST_KEY_CONFLICT", "same request_key has different export semantics")
    request = intent.get("request")
    if not isinstance(request, dict) or intent.get("request_key") != request.get("request_key"):
        _fail("EXPORT_MANIFEST_INVALID", "intent request binding is invalid")
    normalized_request = _normalize_request(request)
    if _canonical(normalized_request) != _canonical(request):
        _fail("EXPORT_MANIFEST_INVALID", "intent request is not canonical")
    if intent["schema_version"] != _schema(request):
        _fail("EXPORT_SCHEMA_UNSUPPORTED", "Policy and schema differ")
    destination = _canonical_destination(intent.get("destination", ""))
    if str(destination) != intent.get("destination"):
        _fail("EXPORT_MANIFEST_INVALID", "intent destination is not canonical")
    if _semantic_digest(normalized_request, destination) != intent.get("intent_sha256"):
        _fail("EXPORT_MANIFEST_INVALID", "intent semantic digest is invalid")
    _source_hash(request)

    if commit_path.exists():
        commit = _safe_load(commit_path)
        prepared = _safe_load(prepared_path)
        if _schema(request) in (EXIF_SCHEMA_VERSION,THUMBNAIL_SCHEMA_VERSION):_check_metadata_receipt(prepared.get("metadata",{}).get("source_exif"),request)
        if (commit.get("schema_version") != _schema(request) or
                commit.get("request_key") != intent.get("request_key") or
                commit.get("intent_sha256") != intent.get("intent_sha256") or
                commit.get("operation_id") != intent.get("operation_id") or
                commit.get("output_sha256") != prepared.get("output_sha256") or
                commit.get("size_bytes") != prepared.get("size_bytes") or
                commit.get("destination") != intent.get("destination") or
                commit.get("metadata") != prepared.get("metadata") or
                commit.get("file_identity") != prepared.get("file_identity")):
            _fail("EXPORT_COMMIT_INVALID", "commit does not match its prepared intent")
        if not _file_matches(destination, prepared["output_sha256"], prepared["size_bytes"],
                             prepared.get("file_identity")):
            _fail("EXPORT_COMMIT_INVALID", "committed output bytes differ from manifest")
        _cleanup_validated_temp(intent, prepared)
        return _success(intent, prepared, status="SUCCEEDED", replay=replay)

    if not prepared_path.exists():
        return {"status": "INTERRUPTED", "operation_id": intent["operation_id"],
                "request_key": intent["request_key"], "intent_sha256": intent["intent_sha256"],
                "destination": str(destination), "reason": "NO_VALIDATED_OUTPUT"}

    prepared = _safe_load(prepared_path)
    if _schema(request) in (EXIF_SCHEMA_VERSION,THUMBNAIL_SCHEMA_VERSION):_check_metadata_receipt(prepared.get("metadata",{}).get("source_exif"),request)
    if (prepared.get("intent_sha256") != intent.get("intent_sha256") or
            prepared.get("operation_id") != intent.get("operation_id") or
            prepared.get("destination") != intent.get("destination")):
        _fail("EXPORT_MANIFEST_INVALID", "prepared output is not bound to this request")
    if (_schema(request) in (EXIF_SCHEMA_VERSION,THUMBNAIL_SCHEMA_VERSION) and not os.path.lexists(destination)
            and _file_matches(Path(intent['temporary_path']),prepared['output_sha256'],prepared['size_bytes'],prepared.get('file_identity'))):
        if 'operation_job_id' not in request or publication_guard is not None:
            with publication_guard() if publication_guard is not None else nullcontext():
                _publish_validated_file(Path(intent["temporary_path"]), destination, prepared,
                                        jpeg=request["format"] == "jpeg")
                _fsync_directory(destination.parent)
    if not _file_matches(destination, prepared["output_sha256"], prepared["size_bytes"],
                         prepared.get("file_identity")):
        if os.path.lexists(destination):
            _fail("EXPORT_PUBLISH_IDENTITY_INVALID", "published path is not the validated temp file")
        return {"status": "INTERRUPTED", "operation_id": intent["operation_id"],
                "request_key": intent["request_key"], "intent_sha256": intent["intent_sha256"],
                "destination": str(destination), "reason": "OUTPUT_NOT_PUBLISHED"}

    commit = {
        "schema_version": _schema(request),
        "request_key": intent["request_key"],
        "intent_sha256": intent["intent_sha256"],
        "operation_id": intent["operation_id"],
        "destination": intent["destination"],
        "output_sha256": prepared["output_sha256"],
        "size_bytes": prepared["size_bytes"],
        "metadata": prepared["metadata"],
        "file_identity": prepared["file_identity"],
    }
    _atomic_json(commit_path, commit, exclusive=True)
    _cleanup_validated_temp(intent, prepared)
    return _success(intent, prepared, status="SUCCEEDED_RECOVERED", replay=replay)


def _check_validation(metadata: Mapping[str, Any], request: Mapping[str, Any], metadata_receipt=None) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        _fail("EXPORT_VALIDATION_FAILED", "validator must return a metadata mapping")
    normalized = dict(metadata)
    missing = set(_VALIDATED_FIELDS) - normalized.keys()
    if missing:
        _fail("EXPORT_VALIDATION_FAILED", "validator omitted: " + ", ".join(sorted(missing)))
    expected = {
        "alpha": request["alpha"], "format": request["format"],
        "bit_depth": request["bit_depth"], "icc_sha256": request["icc_sha256"],
        "source_sha256": request["source_sha256"], "recipe_sha256": request["recipe_sha256"],
        "lease_id": request["lease_id"], "lease_generation": request["lease_generation"],
        "width": request["width"], "height": request["height"],
        "pixels_sha256": request["expected_pixels_sha256"],
    }
    checked = {field: normalized[field] for field in _VALIDATED_FIELDS}
    _check_json(checked)
    if _canonical(checked) != _canonical(expected):
        _fail("EXPORT_VALIDATION_MISMATCH", "encoded output metadata differs from request")
    if _schema(request) in (EXIF_SCHEMA_VERSION,THUMBNAIL_SCHEMA_VERSION):
        _check_metadata_receipt(metadata_receipt,request)
        checked['source_exif']=dict(metadata_receipt)
    return checked


def _check_metadata_receipt(receipt,request):
    if not isinstance(receipt,dict):
        _fail('EXPORT_METADATA_PROOF_MISSING','Complete EXIF proof is required')
    expected_status='SOURCE_EXIF_PRESERVED' if request['source_exif_state']=='SOURCE_EXIF_PRESENT' else 'SOURCE_EXIF_ABSENT'
    stream=receipt.get('image_stream_before');reader=receipt.get('independent_readback',{})
    if (receipt.get('status')!=expected_status or not isinstance(stream,dict) or
        not _HASH_RE.fullmatch(str(stream.get('image_sha256',''))) or stream.get('container')!=request['format'] or
        not _HASH_RE.fullmatch(str(receipt.get('output_semantic_sha256',''))) or
        reader.get('component_version')!='13.60' or reader.get('component_sha256')!=request['metadata_component_sha256'] or
        not isinstance(reader.get('records'),list) or len(reader['records'])!=2):
        _fail('EXPORT_METADATA_PROOF_INVALID','Incomplete typed metadata proof')
    if _schema(request)==THUMBNAIL_SCHEMA_VERSION:
        from .export_thumbnail import THUMBNAIL_POLICY,verify_receipt
        if request.get('thumbnail_policy_id')!=THUMBNAIL_POLICY or receipt.get('thumbnail_policy_id')!=THUMBNAIL_POLICY:
            _fail('EXPORT_METADATA_PROOF_INVALID','Thumbnail policy differs')
        try:verify_receipt(receipt.get('thumbnail'))
        except ValueError as exc:raise ExportError('EXPORT_METADATA_PROOF_INVALID',str(exc)) from exc
    for key in ('metadata_policy_id','normalization_rule','source_exif_state','source_exif_sha256','source_exif_semantic_sha256','metadata_component_sha256'):
        if receipt.get(key)!=request[key]:
            _fail('EXPORT_METADATA_PROOF_INVALID','Metadata proof binding differs: '+key)
    if (receipt.get('source_file_sha256')!=request['source_sha256'] or
        receipt.get('image_stream_before')!=receipt.get('image_stream_after') or
        not receipt.get('independent_readback',{}).get('actual_handle_wait') or
        receipt.get('independent_readback',{}).get('exit_code')!=0):
        _fail('EXPORT_METADATA_PROOF_INVALID','Metadata, stream or helper proof differs')


def publish_export(session_dir: os.PathLike[str] | str,
                   destination: os.PathLike[str] | str,
                   request: Mapping[str, Any],
                   writer: Writer,
                   validator: Validator,
                   publication_guard=None, metadata_check=None, metadata_progress=None) -> dict[str, Any]:
    """Encode, validate, and atomically publish one bounded export.

    ``request`` must explicitly carry request_key, alpha, format, bit_depth,
    ICC policy/hash, source path/hash, recipe hash, lease identity/generation,
    dimensions, and expected quantized pixel hash.  All extra JSON request
    fields also participate in the semantic digest.  ``writer(path, request)``
    must create the uniquely reserved path without overwriting (normally using
    exclusive-create mode such as ``xb``); ``validator(path, request)`` performs the one actual
    codec/readback validation and returns the required validated fields.

    A repeated identical key verifies the source and committed output byte
    hashes, then returns the saved manifest without invoking writer or
    validator.  An interrupted key is terminal and is never silently rerun.
    """
    normalized = _normalize_request(request)
    target = _canonical_destination(destination)
    session = Path(session_dir).resolve()
    session.mkdir(parents=True, exist_ok=True)
    key = normalized["request_key"]
    digest = _semantic_digest(normalized, target)
    directory, _, intent_path, prepared_path, commit_path = _paths(session, key)

    with _SessionLock(session / ".gm004a-export.lock"):
        if intent_path.exists():
            result = _recover_locked(directory, intent_path, prepared_path, commit_path,
                                     digest, replay=True, publication_guard=publication_guard)
            if result["status"] == "INTERRUPTED":
                return result
            return result

        _source_hash(normalized)
        if _schema(normalized) in (EXIF_SCHEMA_VERSION,THUMBNAIL_SCHEMA_VERSION):
            from .export_metadata import policy_request
            actual_policy=policy_request(normalized['source_path'],normalized['source_sha256'],policy=normalized['metadata_policy_id'])
            if any(normalized.get(k)!=v for k,v in actual_policy.items()):
                _fail('EXPORT_METADATA_POLICY_CHANGED','Source metadata or component changed')
        if os.path.lexists(target):
            _fail("EXPORT_DESTINATION_EXISTS", "destination already exists; exports never overwrite")

        operation_id = str(uuid.uuid4())
        temp_path = target.parent / f".gm004a-{_sha256_bytes(key.encode('utf-8'))[:12]}-{uuid.uuid4().hex}.partial"
        intent = {
            "schema_version": _schema(request),
            "request_key": key,
            "intent_sha256": digest,
            "operation_id": operation_id,
            "destination": str(target),
            "temporary_path": str(temp_path),
            "request": normalized,
        }
        _atomic_json(intent_path, intent, exclusive=True)

        metadata_receipt = None
        try:
            if os.path.lexists(temp_path):
                _fail("EXPORT_TEMP_EXISTS", "reserved temporary path already exists")
            writer(temp_path, normalized)
            if _schema(normalized) in (EXIF_SCHEMA_VERSION,THUMBNAIL_SCHEMA_VERSION):
                from .export_metadata import apply
                keys=("metadata_policy_id","normalization_rule","source_exif_state","source_exif_sha256","source_exif_semantic_sha256","metadata_component_sha256")
                if _schema(normalized)==THUMBNAIL_SCHEMA_VERSION:keys+=('thumbnail_policy_id',)
                metadata_receipt=apply(normalized["source_path"],temp_path,{k:normalized[k] for k in keys},normalized["width"],normalized["height"],metadata_check,metadata_progress)
        except ExportError:
            raise
        except InterruptedError:
            if temp_path.is_file() and not temp_path.is_symlink():temp_path.unlink()
            raise
        except Exception as exc:
            if str(exc).startswith('SOURCE_EXIF_'):
                raise ExportError('EXPORT_SOURCE_EXIF_FAILED',str(exc)) from exc
            raise ExportError("EXPORT_WRITER_FAILED", "writer failed; request remains interrupted") from exc

        try:
            reject_reparse(temp_path)
            if not temp_path.is_file():
                _fail("EXPORT_FILE_INVALID", "writer did not produce a regular temporary file")
            with temp_path.open("r+b") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            if normalized["format"] == "jpeg":
                with _pin_jpeg_temp(temp_path) as stream:
                    if stream.read(3) != b"\xff\xd8\xff":
                        _fail("EXPORT_FILE_INVALID", "temporary output is not a JPEG")
                    actual_metadata = _check_validation(validator(temp_path, normalized), normalized, metadata_receipt)
                    stream.seek(0)
                    output_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                    file_stat = os.fstat(stream.fileno())
            else:
                actual_metadata = _check_validation(validator(temp_path, normalized), normalized, metadata_receipt)
                output_hash = _sha256_file(temp_path)
                file_stat = temp_path.stat()
            if file_stat.st_size <= 0:
                _fail("EXPORT_FILE_INVALID", "encoded output is empty")
            if file_stat.st_dev != target.parent.stat().st_dev:
                _fail("EXPORT_TEMP_VOLUME_MISMATCH", "temporary file is not on the destination volume")
        except ExportError:
            raise
        except InterruptedError:
            if temp_path.is_file() and not temp_path.is_symlink():
                temp_path.unlink()
            raise
        except Exception as exc:
            raise ExportError("EXPORT_VALIDATION_FAILED", "output validation failed; request remains interrupted") from exc

        prepared = {
            "schema_version": _schema(request),
            "request_key": key,
            "intent_sha256": digest,
            "operation_id": operation_id,
            "destination": str(target),
            "temporary_path": str(temp_path),
            "output_sha256": output_hash,
            "size_bytes": file_stat.st_size,
            "file_identity": _file_identity(file_stat),
            "metadata": actual_metadata,
        }
        try:
            _atomic_json(prepared_path, prepared, exclusive=True)
        except ExportError:
            raise
        except OSError as exc:
            raise ExportError("EXPORT_PREPARE_WRITE_FAILED", "could not persist validated output identity") from exc

        # The host's operation record lock is the publication/cancel order
        # point. The guard checks that cancel did not win before the link.
        # Keep it held until the host commit receipt is durable.
        try:
            with publication_guard() if publication_guard is not None else nullcontext():
                try:
                    _publish_validated_file(temp_path, target, prepared, jpeg=normalized["format"] == "jpeg")
                except FileExistsError as exc:
                    _fail("EXPORT_DESTINATION_EXISTS", "destination appeared before atomic publish")
                except OSError as exc:
                    raise ExportError("EXPORT_PUBLISH_FAILED", "same-volume no-overwrite publish failed") from exc
                _fsync_directory(target.parent)

                commit = {
                    "schema_version": _schema(request),
                    "request_key": key,
                    "intent_sha256": digest,
                    "operation_id": operation_id,
                    "destination": str(target),
                    "output_sha256": output_hash,
                    "size_bytes": file_stat.st_size,
                    "metadata": actual_metadata,
                    "file_identity": _file_identity(target.stat()),
                }
                try:
                    _atomic_json(commit_path, commit, exclusive=True)
                except ExportError:
                    raise
                except OSError as exc:
                    raise ExportError("EXPORT_COMMIT_WRITE_FAILED", "output published but commit needs recovery") from exc
        except InterruptedError:
            # Only a confirmed unpublished cancellation may discard this exact
            # validated temporary inode. Other interrupted writes stay auditable.
            if not os.path.lexists(target) and _file_matches(temp_path, output_hash, file_stat.st_size,
                                                            _file_identity(file_stat)):
                temp_path.unlink()
                _atomic_json(directory / "cancelled.json", {"request_key": key,
                    "operation_id": operation_id, "temporary_path": str(temp_path),
                    "temporary_removed": True, "published": False}, exclusive=True)
            raise
        try:
            temp_path.unlink()
        except OSError:
            pass
        return _success(intent, prepared, status="SUCCEEDED", replay=False)


def recover_export(session_dir: os.PathLike[str] | str,
                   request_key: str,
                   validator: Validator | None = None) -> dict[str, Any]:
    """Read or complete a saved export transaction without encoding.

    The validator argument is accepted for host-call symmetry but is not called
    for a prepared or committed transaction: the prepared record proves that
    the temporary file already passed codec validation before its atomic link.
    Recovery only checks byte hash, size, and hard-link identity.
    """
    del validator
    if not isinstance(request_key, str) or not request_key or len(request_key) > 128:
        _fail("REQUEST_KEY_INVALID", "request_key length must be 1..128")
    session = Path(session_dir).resolve()
    directory, _, intent_path, prepared_path, commit_path = _paths(session, request_key)
    if not intent_path.exists():
        _fail("EXPORT_REQUEST_NOT_FOUND", "no export reservation exists for request_key")
    with _SessionLock(session / ".gm004a-export.lock"):
        return _recover_locked(directory, intent_path, prepared_path, commit_path)

