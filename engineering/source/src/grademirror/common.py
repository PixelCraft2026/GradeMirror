"""Small, strict wire contracts and filesystem helpers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path


VERSION = "0.1.0"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
# Stable directory-leaf identifiers keep the historical write fences without
# embedding development repository names or fixture inputs in the release.
_PROTECTED_OUTPUT_ROOT_IDS = frozenset({
    'd64e80de75548e448ad8db302ecf0ff90c081b6bafeafc3bfe02f943fb727717',
    '8cd03200d091362e0b505cfe37f5df9f7023e0472940c68974e02c1ae066d282',
})
PROFILE_IDS = {"independent-r0-lab-m05-xy001-v1", "independent-r0-p3-lld10-v1"}
PRODUCT_ROOT = Path(__file__).resolve().parents[2]
GM003_CAPABILITY = "gm003-group15-v1"


class GMError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 2):
        super().__init__(message)
        self.code, self.exit_code = code, exit_code


def strict_load(path: Path):
    def bad_constant(value):
        raise GMError("NONFINITE_JSON", value)
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise GMError("DUPLICATE_JSON_KEY", key)
            result[key] = value
        return result
    with path.open("r", encoding="utf-8") as f:
        return json.load(f, parse_constant=bad_constant, object_pairs_hook=unique_object)


def canonical_json(value) -> bytes:
    def check(item):
        if isinstance(item, float) and not math.isfinite(item):
            raise GMError("NONFINITE_JSON", "Nonfinite number")
        if isinstance(item, dict):
            for v in item.values():
                check(v)
        elif isinstance(item, list):
            for v in item:
                check(v)
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json(value) + b"\n"
    fd, name = tempfile.mkstemp(prefix=".gm-", suffix=".partial", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        deadline = time.monotonic() + 2
        while True:
            try:
                os.replace(name, path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def path_identity(path: str, must_exist: bool = False) -> str:
    if not isinstance(path, str) or not path:
        raise GMError("PATH_INVALID", "Path must be a nonempty string", 3)
    raw = Path(path).expanduser().absolute()
    reject_reparse(raw)
    p = raw.resolve(strict=must_exist)
    return str(p)


def under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def reject_reparse(path: Path) -> None:
    # Windows st_file_attributes includes FILE_ATTRIBUTE_REPARSE_POINT.
    cur = path
    while True:
        if (cur.exists() or cur.is_symlink()) and (cur.stat(follow_symlinks=False).st_file_attributes & 0x400):
            raise GMError("REPARSE_PATH", str(cur), 3)
        if cur == cur.parent:
            return
        cur = cur.parent


def _protected_output_leaf(leaf: str) -> bool:
    return sha_bytes(os.path.normcase(leaf).encode("utf-8")) in _PROTECTED_OUTPUT_ROOT_IDS


def _under_protected_output_root(path: Path) -> bool:
    anchor = Path.cwd().resolve()
    if path.is_relative_to(anchor):
        suffix = path.relative_to(anchor).parts
        if suffix and _protected_output_leaf(suffix[0]):
            return True
    # Preserve the fence when a protected leaf exists as an alias and a caller
    # supplies the alias's physical location directly.
    for entry in anchor.iterdir():
        if _protected_output_leaf(entry.name) and under(path, entry):
            return True
    return False


def writable_path(path: Path) -> Path:
    """Check the original path before resolving links or creating directories."""
    raw = path.expanduser().absolute()
    reject_reparse(raw)
    resolved = raw.resolve()
    if _under_protected_output_root(resolved):
        raise GMError("RESEARCH_OUTPUT_FORBIDDEN", str(resolved), 3)
    return resolved


def writable_output_root(path: Path) -> Path:
    """Normalize a caller's output root once, before any filesystem mutation."""
    return writable_path(Path(path))


def writable_destination(path: Path, root: Path, *, forbidden=(), allow_existing=False) -> Path:
    """Fence a public file target against its raw path, root and inputs."""
    if not Path(root).is_absolute():
        raise GMError("OUTPUT_ROOT_RELATIVE", str(root), 3)
    target = writable_path(Path(path))
    normalized_root = writable_path(Path(root))
    if not normalized_root.is_absolute() or not under(target, normalized_root) or target == normalized_root:
        raise GMError("OUTPUT_PATH_INVALID", str(target), 3)
    inputs = tuple(Path(item).resolve() for item in forbidden)
    if any(target == item or (target.exists() and item.exists() and target.samefile(item))
           for item in inputs):
        raise GMError("OUTPUT_PATH_INVALID", "Input overwrite forbidden", 3)
    if target.exists() and not allow_existing:
        raise GMError("OUTPUT_EXISTS", str(target), 3)
    return target


def _version(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise GMError("SCHEMA_VERSION_INVALID", str(value))
    if value.split(".")[0] != "0":
        raise GMError("SCHEMA_MAJOR_UNSUPPORTED", value)
    if value != VERSION:
        raise GMError("SCHEMA_VERSION_UNSUPPORTED", value, 4)


POLICY_FIELDS = {"cpu_threads", "interop_threads", "ram_limit_bytes", "gpu_fraction",
                 "gpu_free_min_bytes", "wait_limit_s", "stage_timeout_s", "wall_limit_s",
                 "project_budget_bytes", "cache_budget_bytes", "log_budget_bytes",
                 "output_budget_bytes", "capability_table_id"}
REQUEST_FIELDS = {"schema_version", "operation", "project", "revision", "pair", "recipe",
                  "profile", "resource_policy", "output_path", "output_settings", "request_key",
                  "allow_development_profile", "extensions"}


def normalize_request(request: dict, *, test_mode: bool = False) -> dict:
    if not isinstance(request, dict) or set(request) - REQUEST_FIELDS:
        raise GMError("REQUEST_FIELDS_INVALID", "Unknown or forbidden request fields")
    _version(request.get("schema_version"))
    op = request.get("operation")
    if not isinstance(op, str) or op not in {"solve", "replay", "export"}:
        raise GMError("OPERATION_INVALID", str(op))
    if not isinstance(request.get("profile"), dict) or set(request["profile"]) != {"id", "contract_sha256"}:
        raise GMError("PROFILE_INVALID", "Profile id and contract_sha256 required")
    profile = request["profile"]
    if not isinstance(profile["id"], str) or not re.fullmatch(r"[0-9a-f]{64}", str(profile["contract_sha256"])):
        raise GMError("PROFILE_INVALID", "Invalid profile identity")
    if profile["id"] == "__test_mock__" and not test_mode:
        raise GMError("BACKEND_NOT_IMPLEMENTED", "Test profile unavailable", 4)
    from .retired_algorithms import is_retired
    if is_retired(profile["id"]) or profile["id"] in {"native64-f100-v1","w256-f100-p3-lld10-cpu-v1"}:
        from .engine.profiles import retired_error
        raise retired_error()
    if profile["id"] not in PROFILE_IDS and not (test_mode and profile["id"] == "__test_mock__"):
        raise GMError("ALGORITHM_VERSION_UNAVAILABLE", profile["id"], 4)
    if profile["id"] in PROFILE_IDS:
        from .engine.profiles import get_profile
        try:bound=get_profile(profile["id"],profile['contract_sha256'])
        except GMError as exc:raise GMError("PROFILE_CONTRACT_MISMATCH",profile['id'],4) from exc
        if profile["contract_sha256"] != bound["contract_sha256"]:
            raise GMError("PROFILE_CONTRACT_MISMATCH", profile["id"], 4)
    if profile["id"] in PROFILE_IDS and request.get("allow_development_profile") is not True:
        raise GMError("DEVELOPMENT_PROFILE_OPT_IN_REQUIRED", profile["id"], 4)
    policy = request.get("resource_policy")
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
        raise GMError("RESOURCE_POLICY_INVALID", "Explicit policy fields required")
    for k, v in policy.items():
        if k == "capability_table_id":
            if not isinstance(v, str) or not v:
                raise GMError("RESOURCE_POLICY_INVALID", k)
        elif isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise GMError("RESOURCE_POLICY_INVALID", k)
        if (k.endswith("_bytes") or k in {"cpu_threads", "interop_threads"}) and type(v) is not int:
            raise GMError("RESOURCE_POLICY_INVALID", k + " must be an integer")
    for k in ("cpu_threads", "interop_threads", "ram_limit_bytes", "wait_limit_s",
              "stage_timeout_s", "wall_limit_s", "log_budget_bytes", "output_budget_bytes"):
        if policy[k] <= 0:
            raise GMError("RESOURCE_POLICY_INVALID", k)
    if policy["cpu_threads"] > 4 or policy["interop_threads"] > 1 or policy["gpu_fraction"] > .8:
        raise GMError("RESOURCE_POLICY_INVALID", "Policy exceeds GM-001 bounds", 5)
    if not isinstance(request.get("output_path"), str) or not request["output_path"]:
        raise GMError("OUTPUT_INVALID", "Explicit output_path required")
    if not isinstance(request.get("pair"), dict) or set(request["pair"]) != {"source", "reference"}:
        raise GMError("PAIR_INVALID", "source/reference required")
    norm = dict(request)
    pair = {}
    for name in ("source", "reference"):
        identity = request["pair"][name]
        if not isinstance(identity, dict) or set(identity) != {"path", "sha256"}:
            raise GMError("INPUT_IDENTITY_INVALID", name)
        p = Path(path_identity(identity["path"], True))
        if not p.is_file() or not re.fullmatch(r"[0-9a-f]{64}", str(identity["sha256"])) or sha_file(p) != identity["sha256"]:
            raise GMError("INPUT_IDENTITY_MISMATCH", name, 3)
        reject_reparse(p)
        pair[name] = {"path": str(p), "sha256": identity["sha256"]}
    norm["pair"] = pair
    out = writable_path(Path(request["output_path"]))
    if any(under(out, Path(i["path"])) or under(Path(i["path"]), out) for i in pair.values()):
        raise GMError("OUTPUT_INPUT_OVERLAP", str(out), 3)
    norm["output_path"] = str(out)
    norm.setdefault("project", None)
    norm.setdefault("revision", None)
    norm.setdefault("recipe", None)
    norm.setdefault("output_settings", {})
    norm.setdefault("extensions", {})
    norm.setdefault("allow_development_profile", False)
    if type(norm["allow_development_profile"]) is not bool:
        raise GMError("REQUEST_FIELDS_INVALID", "allow_development_profile must be boolean")
    for name in ("project", "revision"):
        if norm[name] is not None and (not isinstance(norm[name], str) or not norm[name]):
            raise GMError("REQUEST_FIELDS_INVALID", name + " must be a nonempty string or null")
    if norm["recipe"] is not None and not isinstance(norm["recipe"], dict):
        raise GMError("REQUEST_FIELDS_INVALID", "recipe must be an object or null")
    if not isinstance(norm["extensions"], dict) or not isinstance(norm["output_settings"], dict):
        raise GMError("REQUEST_FIELDS_INVALID", "extensions/output_settings must be objects")
    key = norm.get("request_key")
    if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 128):
        raise GMError("REQUEST_KEY_INVALID", "request_key length 1..128")
    if len(canonical_json(norm)) > 256 * 1024:
        raise GMError("REQUEST_TOO_LARGE", "Request exceeds 256 KiB")
    if policy["capability_table_id"] == GM003_CAPABILITY:
        validate_gm003_request(norm)
    return norm


def validate_gm003_request(norm: dict) -> None:
    raise GMError("ALGORITHM_RETIRED","GM-003 fixed B0 execution is retired; create a new R0 project",4)


def public_backend(normalized: dict) -> str:
    raise GMError("BACKEND_NOT_IMPLEMENTED","Use the R0 project create / fit / replay workflow",4)


def intent_digest(normalized: dict) -> str:
    return sha_bytes(canonical_json({k: v for k, v in normalized.items() if k != "request_key"}))
