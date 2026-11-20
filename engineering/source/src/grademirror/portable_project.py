"""Audited GM-003 delivery import and movable, file-backed B0 project views.

Only small recipe, parameter and reference files are embedded. This module uses
the standard library and does not decode photographs or import the model.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Mapping

from .project import (
    APP_VERSION, INITIAL_REVISION_ID, PORTABLE_PROJECT_SCHEMA_VERSION,
    PROJECT_FORMAT, ProjectError, ProjectExistsError, ProjectFormatError,
    ProjectIdentityMismatch, RECIPE_FORMAT, _canonical_project_root,
    _canonical_regular_file, _copy_reference, _hash_file, _read_json,
    _read_project_manifest, _resolve_project_relative_file, _rollback_created_paths,
    _runtime_status, _safe_json_digest, _validate_runtime_inventory, _write_json,
)
from .common import PRODUCT_ROOT, writable_path


APPROVED_JOB = "0065c6de-e287-488b-bde9-43c09e143d51"
APPROVED_ATTEMPT = "73ea00ec-4591-4e67-b250-43f27be30ee7"
APPROVED_DELIVERY = "gm003-group15-01b4a0a3c259"
APPROVED_RECOVERY = "gm003-group15-half-recovery-27e38e6ca934"
SOURCE_SHA256 = "e5e3e7ea36dfa4c1040e39a8cadcec56e7e43e1278ed3e48d0e0da46e5be8e91"
REFERENCE_SHA256 = "a73141ca05b535b784e8d059891ef87b6d106917e7322dd0016e1793c8fa31c1"
RECIPE_SHA256 = "6ffbdf22a2a892000b227f97229614b212a6ee4bdf86b7481b8931dc422516e7"
RECIPE_FILE_SHA256 = "1cb299458a5d5ddc5781510334b5290d91187e93a65541e0348f7c027ef61dff"
PARAMETERS_SHA256 = "b99c6509ba83ab95e3b6c408e787afdc160940dd5f85436d3bed2b93b4d9b1b1"
PARAMETER_VALUES_SHA256 = "c4007c90471d061885fef4838d4a1f26b90fa800bf47f1ecf9b5f68c1b64d251"
CORE_SHA256 = "8070e8d3a1bc483130cdfe071e869d72569e5e86ffeddf1a899d1b9a4381bfbd"
ORIGINAL_COMMIT_SHA256 = "5812fdfb593db51016ec19b7a2e9e27c3e151d227e70bde1c39c571ee095feb1"
SEMANTIC_AUDIT_SHA256 = "d5ba82d4a82d05a089a62972ef9a934168588f800ec54c0ec131052c5eab6827"
RECOVERY_COMMIT_SHA256 = "7d2dedba6ac5e7d94434dd35141e693e48af682df8ab58a996141d7b5898d7fe"
HALF_Q16_SHA256 = "bfa798fab15e60040773272e3390aeb98e1e5a62ba46596c9880eef7284c7924"
ONE_Q16_SHA256 = "d38224e3f12641bc8035134e93938b7783dd4400f37ff73b518c92ce970e75de"
SOURCE_Q16_SHA256 = "fd076e350316264f47895c3ef8c51b5ad64dafbc5d3fd838b8ec238d091171c2"
RUNTIME = {"id": "independent-b0-v1", "version": "0.2.0"}
PROJECT_REVISION_FORMAT = "grademirror.project_revision"
ARTIFACT_INDEX_FORMAT = "grademirror.artifacts"
PROJECT_REVISION_FILE = f"revisions/{INITIAL_REVISION_ID}/project-revision.json"
ALGORITHM_RECIPE_FILE = f"revisions/{INITIAL_REVISION_ID}/recipe.json"
PARAMETERS_FILE = f"revisions/{INITIAL_REVISION_ID}/parameters.npz"
ARTIFACT_INDEX_FILE = f"revisions/{INITIAL_REVISION_ID}/artifacts.json"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProjectIdentityMismatch(message)


def _verified_file(path: Path, digest: str, byte_count: int | None = None) -> Path:
    path = _canonical_regular_file(path, label="audited delivery file")
    actual_hash, actual_bytes = _hash_file(path)
    _require(actual_hash == digest and (byte_count is None or actual_bytes == byte_count),
             f"audited file identity mismatch: {path}")
    return path


def _canonical_digest(recipe: Mapping[str, Any]) -> str:
    effective = {key: value for key, value in recipe.items()
                 if key not in {"recipe_sha256", "archive_sha256"}}
    payload = json.dumps(effective, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _copy_small_file(source: Path, destination: Path, expected: str, *, max_bytes: int) -> None:
    _verified_file(source, expected)
    _require(source.stat().st_size <= max_bytes, "embedded file exceeds the project size cap")
    with source.open("rb") as inp, destination.open("xb") as out:
        shutil.copyfileobj(inp, out, 1024 * 1024)
        out.flush()
        os.fsync(out.fileno())
    _verified_file(destination, expected)


def _audit_origin(delivery: Path) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    """Verify fixed accepted evidence and every original commit artifact byte."""
    approved = PRODUCT_ROOT / "outputs" / APPROVED_DELIVERY
    _require(delivery == approved.resolve(strict=True), "delivery is not the reviewed GM-003 bundle")
    origin = (PRODUCT_ROOT / "jobs/gm003" / APPROVED_JOB / APPROVED_ATTEMPT)
    commit_path = _verified_file(origin / "commit.json", ORIGINAL_COMMIT_SHA256)
    audit_path = _verified_file(
        PRODUCT_ROOT / "docs/evidence/gm-003/group15-headless-20261017-v1/final-semantic-audit.json",
        SEMANTIC_AUDIT_SHA256)
    commit, audit = _read_json(commit_path), _read_json(audit_path)
    _require(commit.get("state") == "COMPLETE" and commit.get("job_id") == APPROVED_JOB
             and commit.get("attempt_id") == APPROVED_ATTEMPT,
             "original durable commit identity is invalid")
    _require(audit.get("decision") == "TASK_NOT_COMPLETED"
             and audit.get("reason") == "ALPHA_HALF_EXPORT_ROUTED_TO_ZERO"
             and audit.get("job_id") == APPROVED_JOB,
             "original semantic failure record is missing")
    _require(commit.get("input_identity", {}).get("source", {}).get("sha256") == SOURCE_SHA256
             and commit.get("input_identity", {}).get("reference", {}).get("sha256") == REFERENCE_SHA256,
             "original commit input binding is invalid")
    artifact_rows = commit.get("artifacts")
    _require(isinstance(artifact_rows, list) and len(artifact_rows) == 11,
             "original commit artifact set is incomplete")
    artifacts: dict[str, dict[str, Any]] = {}
    for row in artifact_rows:
        relative = row.get("path")
        _require(isinstance(relative, str) and relative not in artifacts and "\\" not in relative
                 and not relative.startswith("/") and all(part not in {"", ".", ".."}
                                                         for part in relative.split("/")),
                 "original artifact path is unsafe or repeated")
        _verified_file(delivery.joinpath(*relative.split("/")), row["sha256"], row["bytes"])
        artifacts[relative] = row
    _require(artifacts["recipe/recipe.json"]["sha256"] == RECIPE_FILE_SHA256
             and artifacts["recipe/parameters.npz"]["sha256"] == PARAMETERS_SHA256
             and artifacts["project/references/" + REFERENCE_SHA256 + ".png"]["sha256"] == REFERENCE_SHA256,
             "original recipe, parameter or embedded reference binding changed")
    old_recipe = _read_json(delivery / "recipe/recipe.json")
    _require(old_recipe.get("recipe_schema_version") == "0.2.0"
             and old_recipe.get("recipe_kind") == "development_image"
             and old_recipe.get("recipe_sha256") == RECIPE_SHA256
             and _canonical_digest(old_recipe) == RECIPE_SHA256
             and old_recipe.get("archive_sha256") == PARAMETERS_SHA256
             and old_recipe.get("parameter_sha256") == PARAMETER_VALUES_SHA256
             and old_recipe.get("core_sha256") == CORE_SHA256
             and old_recipe.get("source_sha256") == SOURCE_SHA256
             and old_recipe.get("reference_sha256") == REFERENCE_SHA256
             and old_recipe.get("production_qualified") is False,
             "approved algorithm recipe identity changed")
    _require(old_recipe.get("settings", {}).get("updates") == 1000
             and old_recipe.get("settings", {}).get("windows") == 484
             and old_recipe.get("settings", {}).get("seed") == 42,
             "approved B0 settings changed")
    old_project = _read_json(delivery / "project/project.json")
    _require(old_project.get("schema_version") == "0.1.0"
             and old_project.get("source", {}).get("byte_sha256") == SOURCE_SHA256
             and old_project.get("reference", {}).get("byte_sha256") == REFERENCE_SHA256,
             "original metadata project binding changed")
    return old_project, old_recipe, origin, commit_path


def _recovery_artifact(recovery, path):
    rows=recovery.get("artifacts", {})
    exact=rows.get(str(path))
    if exact is not None:
        return exact
    # The commit itself has the exact frozen SHA. A portable copy can retain
    # the original absolute artifact names while its files are relocated.
    matches=[item for name,item in rows.items() if Path(name).name==path.name]
    _require(len(matches)==1,"recovery artifact identity is ambiguous")
    return matches[0]


def _audit_recovery(commit=None, root=None) -> tuple[dict[str, Any], dict[str, Any], Path]:
    _require((commit is None)==(root is None),"both recovery paths are required")
    recovery_path = _verified_file(
        commit if commit is not None else
        PRODUCT_ROOT / "docs/evidence/gm-003/half-recovery-20261017-v1/recovery-commit.json",
        RECOVERY_COMMIT_SHA256)
    recovery = _read_json(recovery_path)
    output = _canonical_project_root(root) if root is not None else PRODUCT_ROOT / "outputs" / APPROVED_RECOVERY
    _require(recovery.get("decision") == "HALF_EXPORT_RECOVERY_ENGINEERING_COMPLETE"
             and recovery.get("recipe_sha256") == RECIPE_SHA256
             and recovery.get("half_q16_sha256") == HALF_Q16_SHA256
             and recovery.get("fit_count") == 0 and recovery.get("render_count") == 1,
             "recovery semantic binding changed")
    expected = {
        "alpha0.5.jpg": "0d77edd9d4fcef46c3845c0c73e1eeb21d73e1cc4da387cb77d3cb3e3b84af31",
        "alpha0.5.tif": "e73996fc5c0aea26042fd8afd743e09e0235c1a815b79eb94d2655b7872595b4",
        "provenance.json": "9d5b1b38ac91927795dcf46ea3e66cce96c9747a0791bc12460392b0ee85a888",
    }
    for name, digest in expected.items():
        path = output / name
        item = _recovery_artifact(recovery,path)
        _require(isinstance(item, dict) and item.get("sha256") == digest,
                 "recovery artifact is absent from its commit")
        _verified_file(path, digest, item["bytes"])
    provenance = _read_json(output / "provenance.json")
    _require(provenance.get("origin_job_id") == APPROVED_JOB
             and provenance.get("origin_attempt_id") == APPROVED_ATTEMPT
             and provenance.get("recipe_sha256") == RECIPE_SHA256
             and provenance.get("half_q16_sha256") == HALF_Q16_SHA256
             and provenance.get("origin_half_outputs") == "INVALID_HALF_STRENGTH_RETAINED",
             "recovery provenance does not bind the reviewed original")
    _require(recovery.get("tiff_export", {}).get("receipt", {}).get("readback", {}).get("pixel_exact") is True
             and recovery["tiff_export"]["receipt"]["readback"]["pixel_sha256"] == HALF_Q16_SHA256,
             "recovered TIFF has no valid full-pixel readback")
    return recovery, provenance, output


def _artifact_index(original: Path, commit: dict[str, Any], recovery: dict[str, Any],
                    recovery_root: Path) -> dict[str, Any]:
    original_rows = {row["path"]: row for row in commit["artifacts"]}
    entries: list[dict[str, Any]] = []
    for alpha, status, root, prefix, q16 in [
        (1.0, "VALID", original, "alpha1", ONE_Q16_SHA256),
        (0.5, "VALID", recovery_root, "alpha0.5", HALF_Q16_SHA256),
        (0.5, "INVALID_HALF_STRENGTH", original, "alpha0.5", SOURCE_Q16_SHA256),
    ]:
        for ext in ("jpg", "tif"):
            name = f"{prefix}.{ext}"
            file = root / name
            row = (original_rows[name] if root == original else _recovery_artifact(recovery,file))
            entries.append({
                "alpha_requested": alpha, "status": status,
                "alpha_actual": 0.0 if status != "VALID" else alpha,
                "format": "TIFF" if ext == "tif" else "JPEG",
                "external_path": str(file), "bytes": row["bytes"], "sha256": row["sha256"],
                "q16_sha256": q16,
                "evidence": "original_commit_and_semantic_audit" if root == original else "recovery_commit_and_provenance",
            })
    return {
        "format": ARTIFACT_INDEX_FORMAT, "schema_version": PORTABLE_PROJECT_SCHEMA_VERSION,
        "recipe_sha256": RECIPE_SHA256, "source_sha256": SOURCE_SHA256,
        "origin_job_id": APPROVED_JOB, "origin_attempt_id": APPROVED_ATTEMPT,
        "original_commit_sha256": ORIGINAL_COMMIT_SHA256,
        "semantic_audit_sha256": SEMANTIC_AUDIT_SHA256,
        "recovery_commit_sha256": RECOVERY_COMMIT_SHA256,
        "entries": entries,
    }


def import_gm003_delivery(
    delivery_root: str | os.PathLike[str],
    project_path: str | os.PathLike[str],
    *,
    source_path: str | os.PathLike[str] | None = None,
    recovery_commit: Path | None = None,
    recovery_output: Path | None = None,
) -> dict[str, Any]:
    """Explicitly import the fixed, reviewed GM-003 original plus half recovery.

    The destination must be a new ``.grademirror`` directory. This verifies
    frozen bytes and writes only the small files needed to reopen and replay.
    It never executes a model or decodes source/reference pixels.
    """
    delivery = _canonical_project_root(delivery_root)
    old_project, old_recipe, origin, commit_path = _audit_origin(delivery)
    recovery, _, recovery_root = _audit_recovery(recovery_commit,recovery_output)
    commit = _read_json(commit_path)
    original_source = Path(old_project["source"]["canonical_path"])
    source = _canonical_regular_file(source_path or original_source, label="source")
    source_hash, source_bytes = _hash_file(source)
    _require(source_hash == SOURCE_SHA256 and source_bytes == old_project["source"]["bytes"],
             "source bytes do not match the approved recipe")

    raw = Path(project_path).expanduser()
    destination = writable_path(raw)
    if raw.exists() or raw.is_symlink() or destination.exists() or destination.is_symlink():
        raise ProjectExistsError(f"project destination already exists: {destination}")
    if destination.suffix != ".grademirror":
        raise ProjectFormatError("portable project destination must end in .grademirror")
    destination.parent.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        destination.mkdir()
        created.append(destination)
        references_dir = destination / "references"
        references_dir.mkdir()
        created.append(references_dir)
        revisions_dir = destination / "revisions"
        revisions_dir.mkdir()
        created.append(revisions_dir)
        revision_dir = revisions_dir / INITIAL_REVISION_ID
        revision_dir.mkdir()
        created.append(revision_dir)
        embedded = _copy_reference(
            delivery / "project/references" / f"{REFERENCE_SHA256}.png", references_dir)
        created.append(destination / embedded["relative_path"])
        _require(embedded["byte_sha256"] == REFERENCE_SHA256
                 and embedded["bytes"] == old_project["reference"]["bytes"],
                 "embedded reference copy changed")
        embedded["name"] = "ref15.png"
        recipe_file = revision_dir / "recipe.json"
        _copy_small_file(delivery / "recipe/recipe.json", recipe_file,
                         RECIPE_FILE_SHA256, max_bytes=256 * 1024)
        created.append(recipe_file)
        parameter_file = revision_dir / "parameters.npz"
        _copy_small_file(delivery / "recipe/parameters.npz", parameter_file,
                         PARAMETERS_SHA256, max_bytes=4 * 1024 * 1024)
        created.append(parameter_file)
        index_file = revision_dir / "artifacts.json"
        _write_json(index_file, _artifact_index(delivery, commit, recovery, recovery_root), replace=False)
        created.append(index_file)
        index_sha, _ = _hash_file(index_file)
        revision = {
            "format": PROJECT_REVISION_FORMAT,
            "schema_version": PORTABLE_PROJECT_SCHEMA_VERSION,
            "revision_id": INITIAL_REVISION_ID,
            "artifact_kind": "independent_b0_recipe",
            "source_sha256": SOURCE_SHA256,
            "reference_sha256": REFERENCE_SHA256,
            "required_runtime": RUNTIME,
            "algorithm_recipe_relative_path": ALGORITHM_RECIPE_FILE,
            "algorithm_recipe_file_sha256": RECIPE_FILE_SHA256,
            "algorithm_recipe_sha256": RECIPE_SHA256,
            "parameters_relative_path": PARAMETERS_FILE,
            "parameters_sha256": PARAMETERS_SHA256,
            "artifacts_relative_path": ARTIFACT_INDEX_FILE,
            "artifacts_sha256": index_sha,
            "created_by": {"app": "GradeMirror", "app_version": APP_VERSION},
        }
        revision_file = revision_dir / "project-revision.json"
        _write_json(revision_file, revision, replace=False)
        created.append(revision_file)
        manifest = {
            "format": PROJECT_FORMAT, "schema_version": PORTABLE_PROJECT_SCHEMA_VERSION,
            "project_id": str(uuid.uuid4()),
            "created_by": {"app": "GradeMirror", "app_version": APP_VERSION},
            "current_revision": INITIAL_REVISION_ID,
            "source": {"storage": "linked", "canonical_path": str(source),
                       "name": source.name, "relative_candidate_path": source.name,
                       "byte_sha256": SOURCE_SHA256, "bytes": source_bytes},
            "reference": embedded, "revisions": [INITIAL_REVISION_ID],
            "extensions": {"import_origin": "GM-003-group15-reviewed"},
        }
        manifest_file = destination / "project.json"
        _write_json(manifest_file, manifest, replace=False)
        created.append(manifest_file)
    except Exception:
        _rollback_created_paths(created)
        raise
    return open_portable_project(destination, available_runtimes={RUNTIME["id"]: RUNTIME["version"]})


def promote_fitted_recipe(project_path: str | os.PathLike[str],
                          recipe_dir: str | os.PathLike[str],
                          *, result_float32_sha256: str,
                          half_q16_sha256: str, _staging_root: Path | None = None,
                          _expected_parent_sha256: str | None = None) -> dict[str, Any]:
    """Publish a new pair's fitted R0 recipe as the next portable revision.

    The original metadata revision remains on disk. The project manifest is
    switched only after every small asset and its binding has been verified.
    """
    from .store import locked
    from .engine.profiles import recipe_profile, project_profile, runtime, runtimes

    root = _canonical_project_root(project_path)
    recipe_root = Path(recipe_dir).resolve(strict=True)
    result_float32_sha256 = _safe_json_digest(result_float32_sha256, "result float32")
    half_q16_sha256 = _safe_json_digest(half_q16_sha256, "half strength Q16")
    with locked(root / "project.lock"):
        manifest, forward = _read_project_manifest(root)
        if (forward or manifest["schema_version"] != "0.1.0"
                or manifest["current_revision"] != INITIAL_REVISION_ID):
            raise ProjectFormatError("project is not a new metadata-only pair")
        if _expected_parent_sha256 is not None:
            from .common import canonical_json, sha_bytes
            if sha_bytes(canonical_json(manifest)) != _expected_parent_sha256:
                raise ProjectIdentityMismatch("fit parent manifest changed")
        source = manifest["source"]
        reference = manifest["reference"]
        _verified_file(Path(source["canonical_path"]), source["byte_sha256"], source["bytes"])
        _verified_file(_resolve_project_relative_file(root, reference["relative_path"]),
                       reference["byte_sha256"], reference["bytes"])
        recipe_file = recipe_root / "recipe.json"
        parameters_file = recipe_root / "parameters.npz"
        recipe = _read_json(recipe_file)
        profile = recipe_profile(recipe)
        parent_recipe = _read_json(root / "revisions/r000001/recipe.json")
        parent_bound=project_profile(parent_recipe,manifest['extensions'].get('algorithm_contract_sha256'))
        if parent_bound['contract_sha256']!=profile['contract_sha256'] or parent_bound["id"] != profile["id"]:
            raise ProjectIdentityMismatch("fitted recipe profile differs from frozen project")
        version = profile["version"]; required = runtime(profile["id"])
        parameters_sha, _ = _hash_file(parameters_file)
        if (recipe.get("recipe_kind") != "development_image"
                or recipe.get("profile_id") != profile["id"]
                or recipe.get("source_sha256") != source["byte_sha256"]
                or recipe.get("reference_sha256") != reference["byte_sha256"]
                or recipe.get("archive_sha256") != parameters_sha
                or recipe.get("core_sha256") != profile["core_sha256"]
                or recipe.get("recipe_sha256") != _canonical_digest(recipe)):
            raise ProjectIdentityMismatch("new fitted recipe does not bind the project inputs")
        recipe_sha, _ = _hash_file(recipe_file)
        revision_id = "r000002"
        asset_root = writable_path(_staging_root) if _staging_root is not None else root
        if _staging_root is not None:
            asset_root.mkdir(exist_ok=False)
            (asset_root / "revisions").mkdir()
        revision_dir = asset_root / "revisions" / revision_id
        if revision_dir.exists() or revision_dir.is_symlink():
            raise ProjectExistsError("fitted revision already exists")
        created: list[Path] = []
        try:
            revision_dir.mkdir()
            created.append(revision_dir)
            target_recipe = revision_dir / "recipe.json"
            _copy_small_file(recipe_file, target_recipe, recipe_sha, max_bytes=256 * 1024)
            created.append(target_recipe)
            target_parameters = revision_dir / "parameters.npz"
            _copy_small_file(parameters_file, target_parameters, parameters_sha,
                             max_bytes=4 * 1024 * 1024)
            created.append(target_parameters)
            if profile["id"]=="independent-r0-p3-lld10-v1":
                from .fine_contract import validate_recipe
                validate_recipe(recipe,recipe_root/"p3.npz")
                target_p3=revision_dir/"p3.npz"
                from .engine.independent_r0_p3_lld10.profile import LIMITS
                _copy_small_file(recipe_root/"p3.npz",target_p3,recipe["p3_archive_sha256"],max_bytes=LIMITS['archive_bytes'])
                created.append(target_p3)
            artifacts_path = revision_dir / "artifacts.json"
            _write_json(artifacts_path, {
                "format": ARTIFACT_INDEX_FORMAT, "schema_version": version,
                "recipe_sha256": recipe["recipe_sha256"],
                "source_sha256": source["byte_sha256"],
                "result_float32_sha256": result_float32_sha256, "entries": [],
                "half_q16_sha256": half_q16_sha256,
            }, replace=False)
            created.append(artifacts_path)
            artifacts_sha, _ = _hash_file(artifacts_path)
            prefix = f"revisions/{revision_id}"
            revision_path = revision_dir / "project-revision.json"
            _write_json(revision_path, {
                "format": PROJECT_REVISION_FORMAT,
                "schema_version": version,
                "revision_id": revision_id, "artifact_kind": profile["artifact_kind"],
                "source_sha256": source["byte_sha256"],
                "reference_sha256": reference["byte_sha256"],
                "required_runtime": required,
                "algorithm_recipe_relative_path": f"{prefix}/recipe.json",
                "algorithm_recipe_file_sha256": recipe_sha,
                "algorithm_recipe_sha256": recipe["recipe_sha256"],
                "parameters_relative_path": f"{prefix}/parameters.npz",
                "parameters_sha256": parameters_sha,
                "artifacts_relative_path": f"{prefix}/artifacts.json",
                "artifacts_sha256": artifacts_sha,
                "created_by": {"app": "GradeMirror", "app_version": APP_VERSION},
            }, replace=False)
            created.append(revision_path)
            next_manifest = dict(manifest)
            next_manifest["schema_version"] = version
            next_manifest["current_revision"] = revision_id
            next_manifest["revisions"] = [*manifest["revisions"], revision_id]
            next_manifest["extensions"] = {**manifest["extensions"],
                                             "import_origin": "new-pair-fit"}
            _read_portable_revision(asset_root, next_manifest, revision_id)
            if _staging_root is None:
                _write_json(root / "project.json", next_manifest, replace=True)
        except Exception:
            _rollback_created_paths(created)
            raise
    if _staging_root is not None:
        return next_manifest
    return open_portable_project(root, available_runtimes=runtimes())


def _read_portable_revision(root: Path, manifest: dict[str, Any], revision_id: str) -> dict[str, Any]:
    from .engine.profiles import metadata_profile, metadata_recipe_profile
    if revision_id not in manifest["revisions"]:
        raise ProjectFormatError("portable revision is not registered")
    path = _resolve_project_relative_file(root, f"revisions/{revision_id}/project-revision.json")
    revision = _read_json(path)
    required = {
        "format", "schema_version", "revision_id", "artifact_kind", "source_sha256",
        "reference_sha256", "required_runtime", "algorithm_recipe_relative_path",
        "algorithm_recipe_file_sha256", "algorithm_recipe_sha256",
        "parameters_relative_path", "parameters_sha256", "artifacts_relative_path",
        "artifacts_sha256", "created_by",
    }
    if not isinstance(revision, dict) or set(revision) != required:
        raise ProjectFormatError("portable revision fields are invalid")
    profile = metadata_profile(revision["required_runtime"].get("id"))
    version = profile["version"]
    if manifest["schema_version"] != version:
        raise ProjectFormatError("project schema differs from revision runtime")
    if revision["format"] != PROJECT_REVISION_FORMAT or revision["schema_version"] != version:
        raise ProjectFormatError("portable revision format or schema is invalid")
    if revision["revision_id"] != revision_id or revision["artifact_kind"] != profile["artifact_kind"]:
        raise ProjectFormatError("portable revision identity is invalid")
    if revision["source_sha256"] != manifest["source"]["byte_sha256"] or revision["reference_sha256"] != manifest["reference"]["byte_sha256"]:
        raise ProjectIdentityMismatch("portable revision input binding is invalid")
    for field in ("algorithm_recipe_file_sha256", "algorithm_recipe_sha256", "parameters_sha256", "artifacts_sha256"):
        _safe_json_digest(revision[field], field)
    if revision["required_runtime"] != {"id":profile["id"],"version":profile["version"]}:
        raise ProjectFormatError("portable runtime binding is unsupported")
    paths = {
        "algorithm_recipe_relative_path": f"revisions/{revision_id}/recipe.json",
        "parameters_relative_path": f"revisions/{revision_id}/parameters.npz",
        "artifacts_relative_path": f"revisions/{revision_id}/artifacts.json",
    }
    for field, exact in paths.items():
        if revision[field] != exact:
            raise ProjectFormatError(f"portable {field} is unsafe")
    for relative, digest in (
        (revision["algorithm_recipe_relative_path"], revision["algorithm_recipe_file_sha256"]),
        (revision["parameters_relative_path"], revision["parameters_sha256"]),
        (revision["artifacts_relative_path"], revision["artifacts_sha256"]),
    ):
        file = _resolve_project_relative_file(root, relative)
        _verified_file(file, digest)
    recipe = _read_json(_resolve_project_relative_file(root, revision["algorithm_recipe_relative_path"]))
    if recipe.get("profile_id", "independent-b0-v1") != profile["id"]:
        raise ProjectIdentityMismatch("recipe profile differs from revision runtime")
    if recipe.get("recipe_sha256") != revision["algorithm_recipe_sha256"] or _canonical_digest(recipe) != recipe["recipe_sha256"]:
        raise ProjectIdentityMismatch("embedded algorithm recipe digest is invalid")
    if recipe.get("archive_sha256") != revision["parameters_sha256"] or recipe.get("source_sha256") != revision["source_sha256"] or recipe.get("reference_sha256") != revision["reference_sha256"]:
        raise ProjectIdentityMismatch("embedded algorithm recipe asset bindings are invalid")
    if profile["id"] != "independent-b0-v1" and metadata_recipe_profile(recipe)["id"] != profile["id"]:
        raise ProjectIdentityMismatch("recipe profile differs from revision runtime")
    if profile["id"]=="independent-r0-p3-lld10-v1":
        from .fine_contract import validate_recipe
        validate_recipe(recipe,_resolve_project_relative_file(root,f"revisions/{revision_id}/p3.npz"))
    index = _read_json(_resolve_project_relative_file(root, revision["artifacts_relative_path"]))
    if index.get("format") != ARTIFACT_INDEX_FORMAT or index.get("schema_version") != version or index.get("recipe_sha256") != recipe["recipe_sha256"] or index.get("source_sha256") != manifest["source"]["byte_sha256"]:
        raise ProjectIdentityMismatch("artifact index binding is invalid")
    if not isinstance(index.get("entries"), list):
        raise ProjectFormatError("artifact index entries are invalid")
    return revision


def _show_portable_project(root: Path, manifest: dict[str, Any], runtimes: dict[str, str]) -> dict[str, Any]:
    current = manifest["current_revision"]
    revision = _read_portable_revision(root, manifest, current)
    source = dict(manifest["source"])
    source_file = Path(source["canonical_path"])
    source["link_exists"] = source_file.is_file()
    source["identity_verified"] = False
    reference = dict(manifest["reference"])
    reference_file = _resolve_project_relative_file(root, reference["relative_path"])
    _verified_file(reference_file, reference["byte_sha256"], reference["bytes"])
    reference["local_path"] = str(reference_file)
    reference["integrity"] = "VERIFIED"
    runtime_status = _runtime_status(revision["required_runtime"], runtimes)
    from .retired_algorithms import is_retired
    retired=is_retired(revision["required_runtime"]["id"])
    missing = runtime_status == "MISSING" or retired
    return {"algorithm_retired":retired,
        "format": PROJECT_FORMAT, "project_id": manifest["project_id"],
        "project_path": str(root), "schema_version": manifest["schema_version"],
        "app_version": manifest["created_by"]["app_version"],
        "current_revision": current, "source": source, "reference": reference,
        "revisions": [{"revision_id": current, "registered": True, "status": "OK",
                       "schema_version": revision["schema_version"],
                       "artifact_kind": revision["artifact_kind"],
                       "required_runtime": revision["required_runtime"],
                       "runtime_status": runtime_status, "read_only": missing}],
        "status": "ALGORITHM_RETIRED" if retired else "ALGORITHM_VERSION_UNAVAILABLE" if missing else "PORTABLE_REPLAY_READY",
        "read_only": missing,
        "read_only_reason": "ALGORITHM_RETIRED" if retired else "ALGORITHM_VERSION_UNAVAILABLE" if missing else None,
        "metadata_only": False, "warnings": [],
        "profile_id": revision["required_runtime"]["id"],
    }


def open_portable_project(
    project_path: str | os.PathLike[str], *, verify_source: bool = True,
    available_runtimes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Verify a moved project using its own assets and linked source only.

    External historical artifact links are descriptive; replay needs none of
    their bytes or Store records. They are never promoted by filename.
    """
    root = _canonical_project_root(project_path)
    manifest, forward = _read_project_manifest(root)
    if forward or manifest["schema_version"] not in {PORTABLE_PROJECT_SCHEMA_VERSION, "0.3.0", "0.4.0", "0.5.0"}:
        raise ProjectFormatError("not a supported portable project")
    runtimes = _validate_runtime_inventory(available_runtimes)
    view = _show_portable_project(root, manifest, runtimes)
    if verify_source and not view.get("algorithm_retired"):
        source = _canonical_regular_file(manifest["source"]["canonical_path"], label="linked source")
        _verified_file(source, manifest["source"]["byte_sha256"], manifest["source"]["bytes"])
        view["source"]["identity_verified"] = True
    revision = _read_portable_revision(root, manifest, manifest["current_revision"])
    index = _read_json(_resolve_project_relative_file(root, revision["artifacts_relative_path"]))
    recipe_dir = root / "revisions" / manifest["current_revision"]
    view.update({
        "revision_id": manifest["current_revision"],
        "recipe_sha256": revision["algorithm_recipe_sha256"],
        "recipe_dir": str(recipe_dir),
        "algorithm_recipe_path": str(recipe_dir / "recipe.json"),
        "parameters_path": str(recipe_dir / "parameters.npz"),
        "reference_path": view["reference"]["local_path"],
        "source_path": view["source"]["canonical_path"],
        "artifacts": index["entries"],
        "artifacts_index_path": str(recipe_dir / "artifacts.json"),
    })
    return view
