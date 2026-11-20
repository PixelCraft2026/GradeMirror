"""Metadata-only GradeMirror project operations.

This module deliberately does not decode images or load an algorithm runtime.
It stores the source as a linked path and embeds the exact reference bytes.

Public API
----------
``create_project(source_path, reference_path, project_path, *,
required_runtime=None)`` creates a new ``.grademirror`` directory and returns
the same metadata view as ``show_project``.

``show_project(project_path, *, available_runtimes=None)`` validates and reads
the project manifest and revision recipes without changing them. Runtime
availability is a mapping from runtime id to exact semantic version. A missing
required runtime is reported as ``ALGORITHM_VERSION_UNAVAILABLE`` and marks
the project read-only.

``relink_project(project_path, source_path, *, available_runtimes=None)``
updates only the linked source path after the candidate's SHA-256 and byte
count match the saved identity. A matching filename never overrides a hash
mismatch. Projects with a missing runtime or a newer project schema are
read-only and cannot be relinked by this API.

Project and recipe schemas are versioned independently of app/runtime
versions. The initial schema is 0.1.0. Future major versions are rejected;
future minor or patch versions in the known major can be shown read-only when
their fields remain recognizable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .common import writable_path


PROJECT_FORMAT = "grademirror.project"
RECIPE_FORMAT = "grademirror.recipe"
PROJECT_SCHEMA_VERSION = "0.1.0"
RECIPE_SCHEMA_VERSION = "0.1.0"
PORTABLE_PROJECT_SCHEMA_VERSION = "0.2.0"
APP_VERSION = "0.1.0"
INITIAL_REVISION_ID = "r000001"
_MAX_METADATA_BYTES = 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_REVISION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_EXTENSION_RE = re.compile(r"^\.[A-Za-z0-9]{1,12}$")


class ProjectError(Exception):
    """Base error for project metadata operations."""

    code = "PROJECT_ERROR"


class ProjectFormatError(ProjectError):
    """The project or a revision record is malformed or unsafe."""

    code = "PROJECT_FORMAT_INVALID"


class UnsupportedProjectSchema(ProjectError):
    """The project schema major version is not supported."""

    code = "PROJECT_SCHEMA_UNSUPPORTED"


class ProjectReadOnlyError(ProjectError):
    """An operation would modify a project that is marked read-only."""

    code = "PROJECT_READ_ONLY"


class ProjectIdentityMismatch(ProjectError):
    """A relink candidate does not match the saved source identity."""

    code = "INPUT_IDENTITY_MISMATCH"


class ProjectExistsError(ProjectError):
    """The requested project destination already exists."""

    code = "PROJECT_ALREADY_EXISTS"


def create_project(
    source_path: str | os.PathLike[str],
    reference_path: str | os.PathLike[str],
    project_path: str | os.PathLike[str],
    *,
    required_runtime: Mapping[str, str] | None = None,
    profile: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Create a new metadata-only project.

    The source is never copied. The reference is copied byte-for-byte to a
    hash-addressed file under ``references/``. The operation creates one
    identity revision with no algorithm parameters or image-derived data.
    ``required_runtime`` is optional and, when supplied, must contain exactly
    ``id`` and ``version``. The destination directory must not already exist.
    """

    from .engine import profiles
    selected = profiles.resolve_mode(mode,profile)
    bound = profiles.runtime(selected["id"])
    if required_runtime is not None and dict(required_runtime) != bound:
        profiles.get_profile(required_runtime.get("id"))
        raise ProjectFormatError("profile and required runtime disagree")
    required_runtime = bound
    source = _canonical_regular_file(source_path, label="source")
    reference = _canonical_regular_file(reference_path, label="reference")
    source_hash, source_bytes = _hash_file(source)
    runtime = _validate_runtime_requirement(required_runtime)
    algorithm_io = None
    if runtime and runtime["id"] in profiles.PROFILES:
        from .image_probe import inspect_pair
        reference_before = _hash_file(reference)
        source_info, reference_info = inspect_pair(source, reference)
        if selected["id"]==profiles.FINE:
            from .fine_contract import validate_limits
            validate_limits(source_info,reference_info)
        if _hash_file(source) != (source_hash, source_bytes) or _hash_file(reference) != reference_before:
            raise ProjectIdentityMismatch("inputs changed during profile header qualification")
        def identity(info):return {"dtype": "uint" + str(info["bits"]), "bit_depth": info["bits"]}
        algorithm_io = {"training_hw": source_info["training_grid"],
            "input_normalization": profiles.normalization(identity(source_info), identity(reference_info))}

    raw_root = Path(project_path).expanduser()
    root = writable_path(raw_root)
    if raw_root.exists() or raw_root.is_symlink() or root.exists() or root.is_symlink():
        raise ProjectExistsError(f"project destination already exists: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    try:
        root.mkdir()
        created.append(root)

        references_dir = root / "references"
        references_dir.mkdir()
        created.append(references_dir)

        revisions_dir = root / "revisions"
        revisions_dir.mkdir()
        created.append(revisions_dir)

        revision_dir = revisions_dir / INITIAL_REVISION_ID
        revision_dir.mkdir()
        created.append(revision_dir)

        reference_record = _copy_reference(reference, references_dir)
        created.append(root / reference_record["relative_path"])
        if algorithm_io is not None and (reference_record["byte_sha256"], reference_record["bytes"]) != reference_before:
            raise ProjectIdentityMismatch("reference changed after profile header qualification")

        source_record = {
            "storage": "linked",
            "canonical_path": str(source),
            "name": source.name,
            "relative_candidate_path": source.name,
            "byte_sha256": source_hash,
            "bytes": source_bytes,
        }
        reference_record["name"] = reference.name

        recipe = {
            "format": RECIPE_FORMAT,
            "schema_version": RECIPE_SCHEMA_VERSION,
            "revision_id": INITIAL_REVISION_ID,
            "artifact_kind": "metadata_only",
            "source_sha256": source_hash,
            "reference_sha256": reference_record["byte_sha256"],
            "required_runtime": runtime,
            "created_by": {"app": "GradeMirror", "app_version": APP_VERSION},
        }
        recipe_path = revision_dir / "recipe.json"
        _write_json(recipe_path, recipe, replace=False)
        created.append(recipe_path)

        manifest = {
            "format": PROJECT_FORMAT,
            "schema_version": PROJECT_SCHEMA_VERSION,
            "project_id": str(uuid.uuid4()),
            "created_by": {"app": "GradeMirror", "app_version": APP_VERSION},
            "current_revision": INITIAL_REVISION_ID,
            "source": source_record,
            "reference": reference_record,
            "revisions": [INITIAL_REVISION_ID],
            "extensions": ({"algorithm_profile": selected["id"], "algorithm_io": algorithm_io,
                "algorithm_contract_sha256": selected["contract_sha256"]} if algorithm_io is not None else {}),
        }
        _write_json(root / "project.json", manifest, replace=False)
    except Exception:
        _rollback_created_paths(created)
        raise

    return show_project(root, available_runtimes=profiles.runtimes())


def show_project(
    project_path: str | os.PathLike[str],
    *,
    available_runtimes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read a project and recognize its revisions without modifying files.

    ``available_runtimes`` maps runtime ids to installed semantic versions.
    Runtime matching is exact. A project with an unbound runtime remains
    metadata-editable; it does not imply that image processing is available.
    """

    root = _canonical_project_root(project_path)
    manifest, schema_forward = _read_project_manifest(root)
    runtimes = _validate_runtime_inventory(available_runtimes)
    if manifest["schema_version"] in {PORTABLE_PROJECT_SCHEMA_VERSION, "0.3.0", "0.4.0", "0.5.0"} and not schema_forward:
        from .portable_project import _show_portable_project
        return _show_portable_project(root, manifest, runtimes)

    source = dict(manifest["source"])
    source_path = Path(source["canonical_path"])
    source["link_exists"] = source_path.is_file()
    source["identity_verified"] = False

    reference = dict(manifest["reference"])
    reference_path = _resolve_project_relative_file(root, reference["relative_path"])
    reference_hash, reference_bytes = _hash_file(reference_path)
    if reference_hash != reference["byte_sha256"] or reference_bytes != reference["bytes"]:
        raise ProjectIdentityMismatch("embedded reference bytes do not match the project manifest")
    reference["local_path"] = str(reference_path)
    reference["integrity"] = "VERIFIED"

    revisions, disk_warnings = _read_revisions(root, manifest, runtimes)
    current = next((item for item in revisions if item["revision_id"] == manifest["current_revision"]), None)

    if schema_forward:
        status = "PROJECT_SCHEMA_NEWER_READ_ONLY"
        read_only = True
        reason = "PROJECT_SCHEMA_NEWER"
    elif current is not None and current["runtime_status"] == "MISSING":
        status = "ALGORITHM_VERSION_UNAVAILABLE"
        read_only = True
        reason = "ALGORITHM_VERSION_UNAVAILABLE"
    elif current is not None and current.get("schema_forward"):
        status = "REVISION_SCHEMA_NEWER_READ_ONLY"
        read_only = True
        reason = "REVISION_SCHEMA_NEWER"
    elif current is None or current["status"] in {"MISSING_DIRECTORY", "INVALID", "UNREGISTERED"}:
        status = "PROJECT_REVISION_UNAVAILABLE_READ_ONLY"
        read_only = True
        reason = "PROJECT_REVISION_UNAVAILABLE"
    else:
        status = "METADATA_ONLY"
        read_only = False
        reason = None

    from .retired_algorithms import is_retired
    required=(current or {}).get("required_runtime")
    profile_id=required.get("id") if isinstance(required,dict) else None
    retired=profile_id is None or is_retired(profile_id)
    if retired and not schema_forward:
        status="ALGORITHM_RETIRED";read_only=True;reason="ALGORITHM_RETIRED"
    return {
        "profile_id":profile_id,"algorithm_retired":retired,
        "format": PROJECT_FORMAT,
        "project_id": manifest["project_id"],
        "project_path": str(root),
        "schema_version": manifest["schema_version"],
        "app_version": manifest["created_by"]["app_version"],
        "current_revision": manifest["current_revision"],
        "source": source,
        "reference": reference,
        "revisions": revisions,
        "status": status,
        "read_only": read_only,
        "read_only_reason": reason,
        "metadata_only": True,
        "warnings": disk_warnings,
    }


def relink_project(
    project_path: str | os.PathLike[str],
    source_path: str | os.PathLike[str],
    *,
    available_runtimes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Relink the source only when its canonical bytes match the saved hash.

    The manifest is left byte-identical on any identity or schema failure.
    Explicit relinking is unavailable while the project is read-only.
    """

    root = _canonical_project_root(project_path)
    manifest, schema_forward = _read_project_manifest(root)
    view = show_project(root, available_runtimes=available_runtimes)
    if schema_forward or view["read_only"]:
        raise ProjectReadOnlyError(
            f"project is read-only: {view['read_only_reason'] or view['status']}"
        )

    candidate = _canonical_regular_file(source_path, label="relink candidate")
    candidate_hash, candidate_bytes = _hash_file(candidate)
    expected = manifest["source"]
    if candidate_hash != expected["byte_sha256"] or candidate_bytes != expected["bytes"]:
        if candidate.name.casefold() == expected["name"].casefold():
            detail = "same filename has different bytes"
        else:
            detail = "candidate bytes do not match the saved source"
        raise ProjectIdentityMismatch(detail)

    manifest["source"]["canonical_path"] = str(candidate)
    manifest["source"]["name"] = candidate.name
    manifest["source"]["relative_candidate_path"] = candidate.name
    _write_json(root / "project.json", manifest, replace=True)
    return show_project(root, available_runtimes=available_runtimes)


def read_revision(
    project_path: str | os.PathLike[str],
    revision_id: str,
) -> dict[str, Any]:
    """Return one recognized revision recipe without changing the project."""

    if not _REVISION_ID_RE.fullmatch(revision_id):
        raise ProjectFormatError("invalid revision id")
    root = _canonical_project_root(project_path)
    manifest, _ = _read_project_manifest(root)
    if manifest["schema_version"] in {PORTABLE_PROJECT_SCHEMA_VERSION, "0.3.0", "0.4.0", "0.5.0"}:
        from .portable_project import _read_portable_revision
        return _read_portable_revision(root, manifest, revision_id)
    recipe_path = _resolve_project_relative_file(root, f"revisions/{revision_id}/recipe.json")
    recipe = _read_json(recipe_path)
    _validate_recipe(recipe, revision_id)
    if recipe["source_sha256"] != manifest["source"]["byte_sha256"]:
        raise ProjectFormatError("revision source identity does not match the project")
    if recipe["reference_sha256"] != manifest["reference"]["byte_sha256"]:
        raise ProjectFormatError("revision reference identity does not match the project")
    return dict(recipe)


def _read_revisions(
    root: Path,
    manifest: dict[str, Any],
    runtimes: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    listed = manifest["revisions"]
    listed_set = set(listed)
    disk_ids: set[str] = set()
    warnings: list[str] = []
    revisions_root = root / "revisions"
    if revisions_root.exists():
        _assert_no_reparse_point(revisions_root)
        if not revisions_root.is_dir():
            raise ProjectFormatError("revisions path is not a directory")
        for child in revisions_root.iterdir():
            if child.is_dir() and not child.is_symlink():
                if _REVISION_ID_RE.fullmatch(child.name):
                    disk_ids.add(child.name)
                else:
                    warnings.append(f"ignored invalid revision directory: {child.name}")

    result: list[dict[str, Any]] = []
    for revision_id in sorted(listed_set | disk_ids):
        registered = revision_id in listed_set
        if revision_id not in disk_ids:
            result.append({
                "revision_id": revision_id,
                "registered": registered,
                "status": "MISSING_DIRECTORY",
                "runtime_status": "UNKNOWN",
                "read_only": True,
            })
            continue
        recipe_path = root / "revisions" / revision_id / "recipe.json"
        try:
            recipe_path = _resolve_project_relative_file(root, f"revisions/{revision_id}/recipe.json")
            recipe = _read_json(recipe_path)
            recipe_schema_forward = _validate_recipe(recipe, revision_id)
            if recipe["source_sha256"] != manifest["source"]["byte_sha256"]:
                raise ProjectFormatError("revision source identity does not match the project")
            if recipe["reference_sha256"] != manifest["reference"]["byte_sha256"]:
                raise ProjectFormatError("revision reference identity does not match the project")
            runtime_status = _runtime_status(recipe["required_runtime"], runtimes)
            status = "REVISION_SCHEMA_NEWER_READ_ONLY" if recipe_schema_forward else ("OK" if registered else "UNREGISTERED")
            result.append({
                "revision_id": revision_id,
                "registered": registered,
                "status": status,
                "schema_version": recipe["schema_version"],
                "artifact_kind": recipe["artifact_kind"],
                "required_runtime": recipe["required_runtime"],
                "runtime_status": runtime_status,
                "schema_forward": recipe_schema_forward,
                "read_only": runtime_status == "MISSING" or not registered or recipe_schema_forward,
            })
            if not registered:
                warnings.append(f"revision directory is not registered: {revision_id}")
        except (ProjectError, OSError, ValueError) as exc:
            result.append({
                "revision_id": revision_id,
                "registered": registered,
                "status": "INVALID",
                "runtime_status": "UNKNOWN",
                "read_only": True,
                "error": str(exc),
            })
    return result, warnings


def _read_project_manifest(root: Path) -> tuple[dict[str, Any], bool]:
    manifest_path = root / "project.json"
    _assert_no_reparse_point(manifest_path)
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ProjectFormatError("project.json must contain a JSON object")
    if manifest.get("format") != PROJECT_FORMAT:
        raise ProjectFormatError("unrecognized project format")
    schema_version = _validate_version(manifest.get("schema_version"), "project schema_version")
    schema_tuple = _version_tuple(schema_version)
    supported_tuple = _version_tuple("0.5.0")
    if schema_tuple[0] != supported_tuple[0]:
        raise UnsupportedProjectSchema(f"unsupported project schema major: {schema_version}")
    schema_forward = schema_tuple > supported_tuple or schema_version not in {"0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0"}

    required = {
        "format", "schema_version", "project_id", "created_by", "current_revision",
        "source", "reference", "revisions", "extensions",
    }
    if not required.issubset(manifest):
        missing = sorted(required - set(manifest))
        raise ProjectFormatError(f"project.json is missing fields: {', '.join(missing)}")
    if set(manifest) - required:
        raise ProjectFormatError("project.json contains unsupported top-level fields")
    if not isinstance(manifest["project_id"], str) or not manifest["project_id"]:
        raise ProjectFormatError("invalid project_id")
    _validate_created_by(manifest["created_by"])
    current = manifest["current_revision"]
    if not isinstance(current, str) or not _REVISION_ID_RE.fullmatch(current):
        raise ProjectFormatError("invalid current_revision")
    if not isinstance(manifest["extensions"], dict):
        raise ProjectFormatError("extensions must be a JSON object")
    if schema_version == "0.3.0" and manifest["extensions"].get("algorithm_profile") != "independent-c5-lab-m05-v1":
        schema_forward = True
    if schema_version == "0.5.0" and manifest["extensions"].get("algorithm_profile") != "independent-r0-p3-lld10-v1":
        raise ProjectFormatError("Fine project profile differs")
    if schema_version == "0.4.0" and manifest["extensions"].get("algorithm_profile") != "independent-r0-lab-m05-xy001-v1":
        schema_forward = True
    _validate_source_record(manifest["source"])
    _validate_reference_record(manifest["reference"])
    revisions = manifest["revisions"]
    if not isinstance(revisions, list) or not revisions:
        raise ProjectFormatError("revisions must be a non-empty list")
    if any(not isinstance(item, str) or not _REVISION_ID_RE.fullmatch(item) for item in revisions):
        raise ProjectFormatError("revisions contains an invalid id")
    if len(revisions) != len(set(revisions)) or current not in revisions:
        raise ProjectFormatError("revisions must be unique and include current_revision")
    return manifest, schema_forward


def _validate_source_record(value: Any) -> None:
    keys = {"storage", "canonical_path", "name", "relative_candidate_path", "byte_sha256", "bytes"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ProjectFormatError("invalid source record fields")
    if value["storage"] != "linked":
        raise ProjectFormatError("source must be linked, not copied")
    if not isinstance(value["canonical_path"], str) or not Path(value["canonical_path"]).is_absolute():
        raise ProjectFormatError("source canonical_path must be absolute")
    if not isinstance(value["name"], str) or not value["name"]:
        raise ProjectFormatError("invalid source name")
    if value["relative_candidate_path"] != value["name"]:
        raise ProjectFormatError("source relative candidate path must match its name")
    _validate_digest_and_size(value["byte_sha256"], value["bytes"], "source")


def _validate_reference_record(value: Any) -> None:
    keys = {"storage", "relative_path", "name", "byte_sha256", "bytes"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ProjectFormatError("invalid reference record fields")
    if value["storage"] != "embedded":
        raise ProjectFormatError("reference must be stored in the project")
    if not isinstance(value["name"], str) or not value["name"]:
        raise ProjectFormatError("invalid reference name")
    relative = value["relative_path"]
    if not isinstance(relative, str):
        raise ProjectFormatError("invalid reference relative_path")
    parts = PurePosixPath(relative).parts
    if len(parts) != 2 or parts[0] != "references" or parts[1] in ("", ".", ".."):
        raise ProjectFormatError("reference path must stay under references/")
    _validate_digest_and_size(value["byte_sha256"], value["bytes"], "reference")


def _validate_digest_and_size(digest: Any, size: Any, label: str) -> None:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ProjectFormatError(f"invalid {label} SHA-256")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ProjectFormatError(f"invalid {label} byte count")


def _validate_recipe(recipe: Any, expected_id: str) -> bool:
    if not isinstance(recipe, dict):
        raise ProjectFormatError("revision recipe must contain a JSON object")
    keys = {
        "format", "schema_version", "revision_id", "artifact_kind", "source_sha256",
        "reference_sha256", "required_runtime", "created_by",
    }
    if set(recipe) != keys:
        raise ProjectFormatError("invalid revision recipe fields")
    if recipe["format"] != RECIPE_FORMAT:
        raise ProjectFormatError("unrecognized revision recipe format")
    version = _validate_version(recipe["schema_version"], "recipe schema_version")
    version_tuple = _version_tuple(version)
    supported_tuple = _version_tuple(RECIPE_SCHEMA_VERSION)
    if version_tuple[0] != supported_tuple[0]:
        raise UnsupportedProjectSchema(f"unsupported recipe schema major: {version}")
    if recipe["revision_id"] != expected_id:
        raise ProjectFormatError("recipe revision_id does not match its directory")
    if recipe["artifact_kind"] != "metadata_only":
        raise ProjectFormatError("this module recognizes metadata_only recipes only")
    _safe_json_digest(recipe["source_sha256"], "revision source")
    _safe_json_digest(recipe["reference_sha256"], "revision reference")
    _validate_runtime_requirement(recipe["required_runtime"])
    _validate_created_by(recipe["created_by"])
    return version_tuple > supported_tuple


def _validate_created_by(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"app", "app_version"}:
        raise ProjectFormatError("invalid created_by metadata")
    if value["app"] != "GradeMirror":
        raise ProjectFormatError("unrecognized created_by app")
    _validate_version(value["app_version"], "app_version")


def _validate_runtime_requirement(value: Mapping[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"id", "version"}:
        raise ProjectFormatError("required_runtime must contain exactly id and version")
    runtime_id = value["id"]
    if not isinstance(runtime_id, str) or not runtime_id.strip():
        raise ProjectFormatError("required_runtime.id must be a non-empty string")
    version = _validate_version(value["version"], "required_runtime.version")
    return {"id": runtime_id, "version": version}


def _validate_runtime_inventory(value: Mapping[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ProjectFormatError("available_runtimes must map runtime ids to versions")
    result: dict[str, str] = {}
    for runtime_id, version in value.items():
        if not isinstance(runtime_id, str) or not runtime_id.strip():
            raise ProjectFormatError("available runtime id must be a non-empty string")
        result[runtime_id] = _validate_version(version, f"available runtime {runtime_id}")
    return result


def _runtime_status(requirement: dict[str, str] | None, runtimes: dict[str, str]) -> str:
    if requirement is None:
        return "NOT_REQUIRED"
    return "AVAILABLE" if runtimes.get(requirement["id"]) == requirement["version"] else "MISSING"


def _canonical_regular_file(path: str | os.PathLike[str], *, label: str) -> Path:
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectError(f"{label} path cannot be resolved: {path}") from exc
    if not resolved.is_file():
        raise ProjectError(f"{label} path is not a regular file: {resolved}")
    return resolved


def _canonical_project_root(path: str | os.PathLike[str]) -> Path:
    raw = Path(path).expanduser()
    _assert_no_reparse_point(raw)
    try:
        root = raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectError(f"project directory cannot be resolved: {path}") from exc
    _assert_no_reparse_point(root)
    if not root.is_dir():
        raise ProjectError(f"project path is not a directory: {root}")
    return root


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            while True:
                block = stream.read(_CHUNK_BYTES)
                if not block:
                    break
                digest.update(block)
                size += len(block)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise ProjectError(f"cannot read file bytes: {path}") from exc
    if _stat_identity(before) != _stat_identity(after) or size != after.st_size:
        raise ProjectError(f"file changed while reading: {path}")
    return digest.hexdigest(), size


def _copy_reference(source: Path, references_dir: Path) -> dict[str, Any]:
    temp_path = references_dir / f".copy-{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as src, temp_path.open("xb") as dst:
            before = os.fstat(src.fileno())
            while True:
                block = src.read(_CHUNK_BYTES)
                if not block:
                    break
                dst.write(block)
                digest.update(block)
                size += len(block)
            dst.flush()
            os.fsync(dst.fileno())
            after = os.fstat(src.fileno())
        if _stat_identity(before) != _stat_identity(after) or size != after.st_size:
            raise ProjectError(f"reference changed while being copied: {source}")

        source_hash, source_bytes = _hash_file(source)
        copy_hash = digest.hexdigest()
        if source_hash != copy_hash or source_bytes != size:
            raise ProjectError("reference bytes changed during the project copy")

        suffix = source.suffix.lower()
        if not _EXTENSION_RE.fullmatch(suffix):
            suffix = ".bin"
        final_name = f"{copy_hash}{suffix}"
        final_path = references_dir / final_name
        try:
            os.link(temp_path, final_path)
        except FileExistsError:
            existing_hash, existing_size = _hash_file(final_path)
            if existing_hash != copy_hash or existing_size != size:
                raise ProjectError("hash-addressed reference target has conflicting bytes")
        except OSError:
            if final_path.exists():
                existing_hash, existing_size = _hash_file(final_path)
                if existing_hash != copy_hash or existing_size != size:
                    raise ProjectError("hash-addressed reference target has conflicting bytes")
            else:
                # Some filesystems do not allow hard links. Create the target
                # exclusively so a pre-existing project asset cannot be replaced.
                descriptor = os.open(final_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    with os.fdopen(descriptor, "wb") as dst, temp_path.open("rb") as src:
                        while True:
                            block = src.read(_CHUNK_BYTES)
                            if not block:
                                break
                            dst.write(block)
                        dst.flush()
                        os.fsync(dst.fileno())
                except Exception:
                    try:
                        final_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
        temp_path.unlink()
        return {
            "storage": "embedded",
            "relative_path": PurePosixPath("references", final_name).as_posix(),
            "byte_sha256": copy_hash,
            "bytes": size,
        }
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _write_json(path: Path, value: dict[str, Any], *, replace: bool) -> None:
    writable_path(path)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if not replace and path.exists():
            raise ProjectExistsError(f"project metadata already exists: {path}")
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Any:
    _assert_no_reparse_point(path)
    try:
        if path.stat().st_size > _MAX_METADATA_BYTES:
            raise ProjectFormatError(f"metadata file exceeds {_MAX_METADATA_BYTES} bytes: {path}")
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except ProjectError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProjectFormatError(f"cannot read valid JSON metadata: {path}") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def _resolve_project_relative_file(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in ("", ".", "..") for part in pure.parts):
        raise ProjectFormatError("project-relative file path is unsafe")
    current = root
    for part in pure.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _assert_no_reparse_point(current)
    try:
        resolved = current.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectFormatError(f"project file is missing: {relative}") from exc
    if not resolved.is_relative_to(root):
        raise ProjectFormatError("project-relative file resolves outside the project")
    if not resolved.is_file():
        raise ProjectFormatError(f"project path is not a file: {relative}")
    return resolved


def _resolve_project_relative_dir(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in ("", ".", "..") for part in pure.parts):
        raise ProjectFormatError("project-relative directory path is unsafe")
    current = root
    for part in pure.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _assert_no_reparse_point(current)
    try:
        resolved = current.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectFormatError(f"project directory is missing: {relative}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        raise ProjectFormatError("project directory resolves outside the project or is not a directory")
    return resolved


def _assert_no_reparse_point(path: Path) -> None:
    try:
        if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
            raise ProjectFormatError(f"reparse points are not allowed in project paths: {path}")
    except OSError as exc:
        raise ProjectFormatError(f"cannot inspect project path: {path}") from exc


def _rollback_created_paths(paths: list[Path]) -> None:
    for path in reversed(paths):
        try:
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink(missing_ok=True)
        except OSError:
            # Keep any incomplete evidence if the operating system prevents
            # removal; never broaden cleanup beyond paths created above.
            pass


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _safe_json_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ProjectFormatError(f"invalid {label} SHA-256")
    return value


def _validate_version(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise ProjectFormatError(f"invalid {label}; expected semantic version")
    return value


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(value)
    assert match is not None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]

