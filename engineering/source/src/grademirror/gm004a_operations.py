"""Small durable operation journal for GM-004A replay and export.

The operation identity is independent of the already successful solve job.
No recovery path starts a worker; recovery only verifies a saved commit.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

from .common import (GMError, atomic_json, canonical_json, reject_reparse,
                     sha_bytes, sha_file, strict_load, writable_path)
from .fit_publication import operation_lock, is_current as current_fit, recover_locked as recover_fit_locked
from .live_json import read_live_json
from .store import locked
from .winproc import current_identity, identity_alive

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
START_LIMIT_SECONDS = 10
OPERATION_LIMIT_SECONDS = 1800
CANCEL_GRACE_SECONDS = 5


def _invalid(message: str):
    raise GMError("COMMIT_INVALID", message, 7)


def _json_line(line: bytes):
    def unique(pairs):
        row = {}
        for key, value in pairs:
            if key in row:
                raise GMError("JOURNAL_INVALID", "Duplicate operation event key", 7)
            row[key] = value
        return row
    def nonfinite(value):
        raise GMError("JOURNAL_INVALID", "Nonfinite operation event", 7)
    try:
        return json.loads(line, object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GMError("JOURNAL_INVALID", "Unreadable operation event", 7) from exc


def _hash(value):
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _append(path: Path, state: dict, code: str):
    journal = path / "events.jsonl"
    if journal.exists():
        with journal.open("rb") as previous:
            seq = sum(1 for _ in previous) + 1
    else:
        seq = 1
    row = {"protocol_version": "0.2.0", "job_id": state["job_id"],
           "attempt_id": state["attempt_id"], "seq": seq,
           "timestamp": time.time(), "state": state["state"],
           "stage": state["stage"], "done": state.get("done"),
           "total": state.get("total"), "unit": state.get("unit"),
           "message_code": code, "snapshot": state}
    with journal.open("ab") as stream:
        stream.write(canonical_json(row) + b"\n")
        stream.flush(); os.fsync(stream.fileno())
    atomic_json(path / "status.json", state)


def _journal(path: Path):
    raw = (path / "events.jsonl").read_bytes()
    rows = []
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            raise GMError("JOURNAL_INVALID", "Partial operation event", 7)
        row = _json_line(line)
        if (not isinstance(row, dict) or row.get("seq") != len(rows) + 1
                or not isinstance(row.get("snapshot"), dict)):
            raise GMError("JOURNAL_INVALID", "Operation event sequence", 7)
        rows.append(row)
    if not rows:
        raise GMError("JOURNAL_INVALID", "Empty operation journal", 7)
    return rows


class OperationStore:
    def __init__(self, root: Path, *, namespace="gm004a-operations", read_only=False):
        # This check must precede locked(), atomic_json(), and even mkdir().
        # Resolving first would hide a junction in the caller's path.
        if namespace != "gm004a-operations" and not re.fullmatch(r"[a-z2-7]{52}",namespace):
            raise ValueError("OPERATION_STORE_NAMESPACE")
        self.root = writable_path(Path(root) / namespace)
        if not read_only:
            self.root.mkdir(parents=True, exist_ok=True)
        reject_reparse(self.root)

    def path(self, job_id: str, attempt_id: str):
        for value in (job_id, attempt_id):
            try: uuid.UUID(value)
            except (ValueError, TypeError, AttributeError) as exc:
                raise GMError("ADDRESS_INVALID", "Operation UUID required", 2) from exc
        path = self.root / job_id / attempt_id
        reject_reparse(path)
        if not (path / "spec.json").is_file():
            raise GMError("ADDRESS_NOT_FOUND", "Operation not found", 2)
        spec = strict_load(path / "spec.json")
        if (spec.get("job_id") != job_id or spec.get("attempt_id") != attempt_id
                or spec.get("intent_sha256") != sha_bytes(canonical_json({
                    "kind": spec.get("kind"), "semantics": spec.get("semantics")}))):
            raise GMError("ADDRESS_MISMATCH", "Operation identity mismatch", 2)
        return path, spec

    def lookup_request(self, request_key, *, saved_only=False):
        if request_key is None:
            return None
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
            raise GMError("REQUEST_KEY_INVALID", "Operation request key length 1..128", 2)
        from contextlib import nullcontext
        with (nullcontext() if saved_only else locked(self.root / "index.lock")):
            index = self.root / "keys" / (sha_bytes(request_key.encode("utf-8")) + ".json")
            if not index.is_file():
                return None
            saved = strict_load(index)
            path, spec = self.path(saved["job_id"], saved["attempt_id"])
            if (saved.get("request_key") != request_key or saved.get("digest") != spec["intent_sha256"]
                    or spec.get("request_key") != request_key):
                raise GMError("ADDRESS_MISMATCH", "Request key binding differs", 2)
            return path, spec

    def begin(self, kind: str, semantics: dict, request_key: str | None = None):
        if kind in {'fit','replay'}:
            from .resource_execution.contract import attach
            semantics=attach(semantics)
        if kind not in {"fit", "replay", "export"}:
            raise GMError("OPERATION_UNKNOWN", kind, 2)
        if request_key is not None and (not isinstance(request_key, str)
                or not 1 <= len(request_key) <= 128):
            raise GMError("REQUEST_KEY_INVALID", "Operation request key length 1..128", 2)
        digest = sha_bytes(canonical_json({"kind": kind, "semantics": semantics}))
        existing = None
        with locked(self.root / "index.lock"):
            key_path = (self.root / "keys" / (sha_bytes(request_key.encode("utf-8")) + ".json")) if request_key else None
            if key_path and key_path.is_file():
                key = strict_load(key_path)
                if key["digest"] != digest or key["request_key"] != request_key:
                    raise GMError("REQUEST_KEY_CONFLICT", "Operation semantics differ", 2)
                path, spec = self.path(key["job_id"], key["attempt_id"])
                existing = (path, spec)
            if existing is None:
                return self._begin_new_locked(kind, semantics, request_key, digest, key_path)
        # Recovery may perform IPC. Never hold index.lock across status().
        path, spec = existing
        status = self.status(spec["job_id"], spec["attempt_id"])
        checked_path, checked_spec = self.path(spec["job_id"], spec["attempt_id"])
        if checked_path != path or checked_spec != spec:
            raise GMError("ADDRESS_MISMATCH", "Idempotent operation changed", 7)
        return path, spec, {**status, "idempotent_hit": True}

    def _begin_new_locked(self, kind, semantics, request_key, digest, key_path):
            owner = current_identity()
            if not isinstance(owner, dict) or not owner.get("pid"):
                raise GMError("OPERATION_OWNER_UNAVAILABLE", "Cannot bind startup owner", 9)
            accepted_at = time.time()
            job_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
            path = self.root / job_id / attempt_id
            path.mkdir(parents=True, exist_ok=False)
            spec = {"schema_version": "0.2.0", "job_id": job_id, "attempt_id": attempt_id,
                    "kind": kind, "semantics": semantics, "intent_sha256": digest,
                    "request_key": request_key, "accepted_at": accepted_at,
                    "startup_owner": owner,
                    "start_deadline_at": accepted_at + START_LIMIT_SECONDS,
                    "operation_deadline_at": accepted_at +
                    (6600 if kind=='fit' and semantics.get('fit_contract')=='gm023-hybrid-fit-v1' else
                     5400 if semantics.get('backend_id')=='cuda-r0-cpu-lld10-v1' else
                     21660 if semantics.get("profile_id")=="independent-r0-p3-lld10-v1" else OPERATION_LIMIT_SECONDS)}
            atomic_json(path / "spec.json", spec)
            status = {"job_id": job_id, "attempt_id": attempt_id, "state": "QUEUED",
                      "stage": "accepted", "error_code": None, "exit_code": None,
                      "owner": owner, "result_lease": None}
            _append(path, status, "OPERATION_ACCEPTED")
            if key_path:
                key_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(key_path, {"request_key": request_key, "digest": digest,
                                      "job_id": job_id, "attempt_id": attempt_id})
            return path, spec, {**status, "idempotent_hit": False}

    def transition(self, job_id, attempt_id, state, stage, code, **fields):
        path, spec = self.path(job_id, attempt_id)
        with operation_lock(path, spec):
            current = dict(_journal(path)[-1]["snapshot"])
            from .hybrid_replay.publication import is_hybrid, recover_locked
            if is_hybrid(spec):
                recovered = recover_locked(self, path, spec, current)
                if recovered is not None:
                    return recovered
            if current["state"] in TERMINAL:
                if state == "RUNNING":
                    raise GMError("OPERATION_TERMINAL", "Terminal operation cannot restart", 8)
                return current
            if state not in {"RUNNING", "FAILED", "CANCELLED"}:
                raise GMError("STATE_INVALID", "Commit is the only success transition", 7)
            if current["state"] == "CANCEL_REQUESTED" and state == "RUNNING":
                raise GMError("CANCELLED", "Cancel precedes commit", 8)
            if state == "RUNNING" and current["state"] == "QUEUED":
                owner_matches = current_identity() == spec.get("startup_owner")
                before_deadline = time.time() < spec.get("start_deadline_at", 0)
                if not (owner_matches and before_deadline):
                    context = spec.get("semantics", {}).get("instance_context")
                    # Keep the public error contract and preserve the independent facts.
                    error = GMError("OPERATION_OWNER_MISMATCH", "Startup owner or deadline differs", 9)
                    if context:
                        error.add_note(f"startup_owner_matches={owner_matches}; startup_before_deadline={before_deadline}")
                    raise error
            current.update(fields, state=state, stage=stage)
            _append(path, current, code)
            return current

    def commit(self, job_id, attempt_id, artifacts: list[dict], lease=None):
        path, spec = self.path(job_id, attempt_id)
        if spec.get('semantics', {}).get('backend_id') == 'r0-cpu-portable-v1':
            from .cpu_replay.publication import commit
            return commit(self, path, spec, artifacts, lease)
        with operation_lock(path, spec):
            current = dict(_journal(path)[-1]["snapshot"])
            from .hybrid_replay.publication import is_hybrid, commit_locked
            if is_hybrid(spec):
                return commit_locked(self,path,spec,current,artifacts,lease)
            if current_fit(spec):
                recovered = recover_fit_locked(self, path, spec, current)
                if recovered is not None:
                    return recovered
                raise GMError("FIT_PUBLICATION_REQUIRED", "Fit needs its complete publication marker", 7)
            if current["state"] == "CANCEL_REQUESTED":
                recovered = self._recover_published_export_locked(path, spec, current)
                if recovered is not None:
                    return recovered
                raise GMError("CANCELLED", "Cancel precedes commit", 8)
            if current["state"] == "CANCELLED":
                recovered = self._recover_published_export_locked(path, spec, current)
                if recovered is not None:
                    return recovered
            if (current["state"] != "RUNNING"
                    and not (spec["kind"] == "export" and current["state"] == "FAILED")):
                raise GMError("COMMIT_INVALID", "Operation cannot commit from this state", 7)
            manifest = {"state": "COMPLETE", "job_id": job_id,
                        "attempt_id": attempt_id, "intent_sha256": spec["intent_sha256"],
                        "kind": spec["kind"], "artifacts": artifacts,
                        "result_lease": lease}
            self._verify_manifest(manifest, spec, path)
            atomic_json(path / "commit.json", manifest)
            current.update(state="SUCCEEDED", stage="terminal", exit_code=0,
                           error_code=None,
                           result_lease=lease)
            _append(path, current, "OPERATION_COMMITTED")
            return current

    @staticmethod
    def _recover_published_export_locked(path, spec, current):
        """Promote only a complete, byte-verified host publication, under record.lock."""
        from .hybrid_replay.publication import is_hybrid, recover_locked
        if is_hybrid(spec):
            return recover_locked(OperationStore(path.parents[2]),path,spec,current)
        if current_fit(spec):
            return recover_fit_locked(OperationStore(path.parents[2]), path, spec, current)
        if spec["kind"] != "export":
            return None
        semantic = spec["semantics"]
        key = spec["request_key"]
        if (not isinstance(key, str) or not isinstance(semantic, dict)
                or not _uuid(semantic.get("replay_job_id"))
                or not _uuid(semantic.get("replay_attempt_id"))):
            return None
        replay_path = (path.parents[1] / semantic["replay_job_id"]
                       / semantic["replay_attempt_id"])
        from .adaptive_execution.portable_contract import export_transaction
        tx = export_transaction(replay_path / "session", sha_bytes(key.encode("utf-8")))
        published_path = tx / "commit.json"
        if not published_path.is_file():
            return None
        published = strict_load(published_path)
        replay_compute = strict_load(replay_path / "compute-result.json")
        result_path = path / "export-result.json"
        if not result_path.is_file():
            version_fields={}
            if semantic.get('binding',{}).get('backend_id') is not None:
                from .hybrid_replay.contract import versions
                version_fields=versions(semantic['binding'])
            atomic_json(result_path, {"status": "SUCCEEDED_RECOVERED",
                **version_fields,
                "operation_id": published["operation_id"], "request_key": key,
                "intent_sha256": published["intent_sha256"],
                "destination": published["destination"],
                "output_sha256": published["output_sha256"],
                "size_bytes": published["size_bytes"], "metadata": published["metadata"],
                "idempotent_replay": True, "fit_count": replay_compute["fit_count"],
                "render_count": replay_compute["render_count"]})
        artifacts = [artifact(p) for p in (Path(semantic["destination"]), result_path,
            tx / "intent.json", tx / "prepared.json", published_path)]
        manifest = {"state": "COMPLETE", "job_id": spec["job_id"],
            "attempt_id": spec["attempt_id"], "intent_sha256": spec["intent_sha256"],
            "kind": "export", "artifacts": artifacts, "result_lease": None}
        OperationStore._verify_manifest(manifest, spec, path)
        atomic_json(path / "commit.json", manifest)
        current.update(state="SUCCEEDED", stage="recovered", exit_code=0,
                       error_code=None, result_lease=None)
        _append(path, current, "OPERATION_RECOVERED_PUBLISHED_EXPORT")
        return current

    @staticmethod
    def _verify_manifest(manifest, spec, path):
        if spec.get('semantics', {}).get('backend_id') == 'r0-cpu-portable-v1':
            from .cpu_replay.publication import verify_commit
            return verify_commit(manifest, spec, path)
        if (not isinstance(manifest, dict)
                or manifest.get("state") != "COMPLETE" or manifest.get("job_id") != spec["job_id"]
                or manifest.get("attempt_id") != spec["attempt_id"]
                or manifest.get("intent_sha256") != spec["intent_sha256"]
                or manifest.get("kind") != spec["kind"]):
            raise GMError("COMMIT_INVALID", "Operation binding differs", 7)
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            _invalid("Operation artifacts cannot be empty")
        by_path = {}
        for item in artifacts:
            if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
                _invalid("Operation artifact entry invalid")
            supplied = item["path"]
            if not isinstance(supplied, str) or not Path(supplied).is_absolute():
                _invalid("Operation artifact path must be absolute")
            p = Path(supplied)
            reject_reparse(p)
            if (str(p.resolve()) != supplied or type(item["bytes"]) is not int
                    or item["bytes"] <= 0 or not _hash(item["sha256"])):
                _invalid("Operation artifact identity malformed")
            key = os.path.normcase(str(p))
            if key in by_path:
                _invalid("Duplicate operation artifact")
            if not p.is_file() or p.stat().st_size != item["bytes"] or sha_file(p) != item["sha256"]:
                _invalid("Operation artifact identity differs")
            by_path[key] = item
        if spec["kind"] == "fit":
            OperationStore._verify_fit(manifest, spec, path, by_path)
        elif spec["kind"] == "replay":
            OperationStore._verify_replay(manifest, spec, path, by_path)
        elif spec["kind"] == "export":
            OperationStore._verify_export(manifest, spec, path, by_path)
        else:
            _invalid("Unknown operation kind")

    @staticmethod
    def _receipt(by_path, required: Path):
        entry = by_path.get(os.path.normcase(str(required.resolve())))
        if entry is None:
            _invalid("Required operation receipt missing: " + required.name)
        try:
            value = strict_load(required)
        except Exception as exc:
            raise GMError("COMMIT_INVALID", "Operation receipt is unreadable", 7) from exc
        if not isinstance(value, dict):
            _invalid("Operation receipt must be an object")
        return value

    @staticmethod
    def _verify_fit(manifest, spec, path, by_path):
        from .hybrid_fit import is_fit_type
        if is_fit_type(spec):
            from .hybrid_fit.publication import verify
            return verify(manifest,spec,path,by_path)
        if current_fit(spec):
            from .fit_publication import verify
            return verify(manifest, spec, path, by_path)
        semantic = spec.get("semantics")
        if (not isinstance(semantic, dict) or
                not all(_hash(semantic.get(k)) for k in ("source_sha256", "reference_sha256"))
                or not isinstance(semantic.get("project_path"), str)
                or not isinstance(semantic.get("output_root"), str)
                or semantic.get("revision") != "r000002"):
            _invalid("Fit semantic identity missing")
        compute = OperationStore._receipt(by_path, path / "compute-result.json")
        cleanup = OperationStore._receipt(by_path, path / "cleanup.json")
        identity = OperationStore._receipt(by_path, path / "fit-identity.json")
        lease = manifest.get("result_lease")
        if (compute.get("status") != "PASS" or compute.get("mode") != "development_pair"
                or compute.get("fit_count") != 1 or compute.get("render_count") != 1
                or compute.get("source_identity", {}).get("file_sha256") != semantic["source_sha256"]
                or compute.get("reference_identity", {}).get("file_sha256") != semantic["reference_sha256"]
                or not _hash(compute.get("recipe_sha256"))
                or not _hash(compute.get("float32_sha256"))
                or compute.get("lease") != lease):
            _invalid("Fit compute identity or counts differ")
        if (cleanup.get("mode") != "development_pair" or cleanup.get("worker_exit_code") != 0
                or cleanup.get("actual_wait") is not True
                or cleanup.get("owned_processes_exited") is not True):
            _invalid("Fit worker exit receipt incomplete")
        if (identity.get("fit_count") != 1 or identity.get("render_count") != 1
                or identity.get("recipe_sha256") != compute["recipe_sha256"]
                or identity.get("float32_sha256") != compute["float32_sha256"]
                or identity.get("source_sha256") != semantic["source_sha256"]
                or identity.get("reference_sha256") != semantic["reference_sha256"]
                or identity.get("worker_cleanup") != cleanup
                or not _hash(identity.get("half_q16_sha256"))):
            _invalid("Fit identity receipt differs")
        if (not isinstance(lease, dict) or lease.get("state") != "RESIDENT"
                or lease.get("recipe_sha256") != compute["recipe_sha256"]
                or lease.get("project_revision") != semantic["revision"]):
            _invalid("Fit lease identity invalid")
        from .portable_project import open_portable_project
        view = open_portable_project(semantic["project_path"],
            available_runtimes={"independent-b0-v1": "0.2.0"})
        if (view["recipe_sha256"] != compute["recipe_sha256"]
                or view["revision_id"] != semantic["revision"]
                or view["source"]["byte_sha256"] != semantic["source_sha256"]
                or view["reference"]["byte_sha256"] != semantic["reference_sha256"]):
            _invalid("Fitted project revision differs")

    @staticmethod
    def _verify_replay(manifest, spec, path, by_path):
        from .hybrid_replay.publication import is_hybrid, verify
        if is_hybrid(spec):
            return verify(manifest,spec,path,by_path)
        semantic = spec.get("semantics")
        if (not isinstance(semantic, dict) or
                not all(_hash(semantic.get(k)) for k in
                        ("recipe_sha256", "source_sha256", "reference_sha256")) or
                not isinstance(semantic.get("revision"), str) or
                not isinstance(semantic.get("output_root"), str)):
            _invalid("Replay semantic identity missing")
        compute = OperationStore._receipt(by_path, path / "compute-result.json")
        cleanup = OperationStore._receipt(by_path, path / "cleanup.json")
        identity = OperationStore._receipt(by_path, path / "replay-identity.json")
        lease = manifest.get("result_lease")
        source = compute.get("source_identity")
        if semantic.get("profile_id")=="independent-r0-p3-lld10-v1":
            from .fine_contract import validate_compute
            validate_compute(compute,semantic["fine_hashes"],replay=True)
        if (compute.get("status") != "PASS" or compute.get("mode") != "replay"
                or compute.get("fit_count") != 0 or compute.get("render_count") != 1
                or compute.get("recipe_sha256") != semantic["recipe_sha256"]
                or not isinstance(source, dict)
                or source.get("file_sha256") != semantic["source_sha256"]
                or not _hash(compute.get("float32_sha256"))
                or compute.get("lease") != lease):
            _invalid("Replay compute identity or counts differ")
        if (cleanup.get("mode") != "replay" or cleanup.get("worker_exit_code") != 0
                or cleanup.get("actual_wait") is not True
                or cleanup.get("owned_processes_exited") is not True):
            _invalid("Replay worker exit receipt incomplete")
        if (identity.get("fit_count") != 0 or identity.get("render_count") != 1
                or identity.get("tiles") != semantic.get("tiles", 117)
                or identity.get("float32_sha256") != compute["float32_sha256"]
                or not _hash(identity.get("half_q16_sha256"))
                or identity.get("recipe_sha256") != semantic["recipe_sha256"]
                or identity.get("source_sha256") != semantic["source_sha256"]
                or identity.get("reference_sha256") != semantic["reference_sha256"]
                or identity.get("worker_cleanup") != cleanup):
            _invalid("Replay identity receipt differs")
        if (not isinstance(lease, dict) or lease.get("state") != "RESIDENT"
                or lease.get("recipe_sha256") != semantic["recipe_sha256"]
                or lease.get("project_revision") != semantic["revision"]
                or not isinstance(lease.get("handle_id"), str)
                or type(lease.get("generation")) is not int):
            _invalid("Replay lease identity invalid")

    @staticmethod
    def _verify_export(manifest, spec, path, by_path):
        semantic = spec.get("semantics")
        key = spec.get("request_key")
        if (not isinstance(semantic, dict) or not isinstance(key, str) or not key
                or not _uuid(semantic.get("replay_job_id"))
                or not _uuid(semantic.get("replay_attempt_id"))
                or not isinstance(semantic.get("destination"), str)
                or not Path(semantic["destination"]).is_absolute()
                or not _hash(semantic.get("source_sha256"))
                or not _hash(semantic.get("recipe_sha256"))
                or not isinstance(semantic.get("binding"), dict)):
            _invalid("Export semantic identity missing")
        destination = Path(semantic["destination"])
        reject_reparse(destination)
        if str(destination.resolve()) != semantic["destination"]:
            _invalid("Export destination is not canonical")
        result = OperationStore._receipt(by_path, path / "export-result.json")
        destination_item = by_path.get(os.path.normcase(str(destination)))
        if destination_item is None:
            _invalid("Exported file missing from commit")
        # The replay may reside under the same operation store; its session
        # transaction is the independent no-overwrite publication receipt.
        from .adaptive_execution.portable_contract import export_transaction
        replay_path = path.parents[1] / semantic["replay_job_id"] / semantic["replay_attempt_id"]
        tx = export_transaction(replay_path / "session", sha_bytes(key.encode("utf-8")))
        try:
            replay_spec = strict_load(replay_path / "spec.json")
        except Exception as exc:
            raise GMError("COMMIT_INVALID", "Referenced replay identity unavailable", 7) from exc
        if (replay_spec.get("kind") not in {"fit", "replay"}
                or replay_spec.get("job_id") != semantic["replay_job_id"]
                or replay_spec.get("attempt_id") != semantic["replay_attempt_id"]
                or replay_spec.get("semantics", {}).get("source_sha256") != semantic["source_sha256"]
                or (replay_spec.get("kind") == "replay" and
                    replay_spec.get("semantics", {}).get("recipe_sha256") != semantic["recipe_sha256"])):
            _invalid("Referenced replay binding differs")
        from .refinement import expected_fit_count as fitted_count
        expected_fit_count = fitted_count(replay_spec["semantics"]) if replay_spec["kind"] == "fit" else 0
        if replay_spec["kind"] == "fit":
            fitted_compute = strict_load(replay_path / "compute-result.json")
            if fitted_compute.get("recipe_sha256") != semantic["recipe_sha256"]:
                _invalid("Referenced fitted recipe differs")
        intent = OperationStore._receipt(by_path, tx / "intent.json")
        prepared = OperationStore._receipt(by_path, tx / "prepared.json")
        published = OperationStore._receipt(by_path, tx / "commit.json")
        request = intent.get("request")
        metadata = result.get("metadata")
        binding = semantic["binding"]
        if binding.get('backend_id') is not None:
            from .hybrid_replay.contract import versions
            if versions(binding)!=versions(request) or versions(result)!=versions(binding):
                _invalid('Hybrid export version differs')
        if (not isinstance(request, dict) or not isinstance(metadata, dict)
                or result.get("status") not in {"SUCCEEDED", "SUCCEEDED_RECOVERED"}
                or result.get("request_key") != key
                or result.get("destination") != semantic["destination"]
                or result.get("output_sha256") != destination_item["sha256"]
                or result.get("size_bytes") != destination_item["bytes"]
                or result.get("fit_count") != expected_fit_count
                or result.get("render_count") != 1
                or request.get("request_key") != key
                or request.get("operation_job_id") != spec["job_id"]
                or request.get("operation_attempt_id") != spec["attempt_id"]
                or request.get("source_sha256") != semantic["source_sha256"]
                or request.get("recipe_sha256") != semantic["recipe_sha256"]
                or request.get("lease_id") != binding.get("handle_id")
                or request.get("lease_generation") != binding.get("generation")
                or request.get("alpha") != semantic.get("alpha")
                or request.get("format") != semantic.get("format")
                or request.get("bit_depth") != semantic.get("bit_depth")
                or request.get("icc_policy") != semantic.get("icc_policy")
                or request.get("icc_sha256") != semantic.get("icc_sha256")
                or metadata.get("pixels_sha256") != request.get("expected_pixels_sha256")
                or metadata.get("source_sha256") != semantic["source_sha256"]
                or metadata.get("recipe_sha256") != semantic["recipe_sha256"]
                or metadata.get("alpha") != semantic.get("alpha")
                or metadata.get("format") != semantic.get("format")
                or metadata.get("bit_depth") != semantic.get("bit_depth")
                or metadata.get("lease_id") != request.get("lease_id")
                or metadata.get("lease_generation") != request.get("lease_generation")
                or metadata.get("width") != request.get("width")
                or metadata.get("height") != request.get("height")
                or metadata.get("icc_sha256") != semantic.get("icc_sha256")):
            _invalid("Export semantic or readback receipt differs")
        host_semantic = {k: v for k, v in request.items() if k != "request_key"}
        host_semantic["destination"] = os.path.normcase(semantic["destination"])
        from .gm004a_export import _schema,_check_metadata_receipt,EXIF_SCHEMA_VERSION,THUMBNAIL_SCHEMA_VERSION
        host_semantic['export_schema_version']=_schema(request)
        if _schema(request) in (EXIF_SCHEMA_VERSION,THUMBNAIL_SCHEMA_VERSION):
            _check_metadata_receipt(metadata.get('source_exif'),request)
            if _schema(request)==THUMBNAIL_SCHEMA_VERSION and semantic.get('thumbnail_policy_id')!=request.get('thumbnail_policy_id'):_invalid('Export thumbnail policy differs')
            for field in ('metadata_policy_id','normalization_rule','source_exif_state','source_exif_sha256','source_exif_semantic_sha256','metadata_component_sha256'):
                if semantic.get(field)!=request.get(field):_invalid('Export metadata policy differs')
        if intent.get("intent_sha256") != sha_bytes(canonical_json(host_semantic)):
            _invalid("Export host intent digest differs")
        for receipt in (intent, prepared, published):
            if (receipt.get("destination") != semantic["destination"]
                    or receipt.get("request_key") != key
                    or receipt.get("operation_id") != result.get("operation_id")):
                _invalid("Export publication binding differs")
        if (prepared.get("intent_sha256") != intent.get("intent_sha256")
                or published.get("intent_sha256") != intent.get("intent_sha256")
                or result.get("intent_sha256") != intent.get("intent_sha256")
                or prepared.get("output_sha256") != destination_item["sha256"]
                or published.get("output_sha256") != destination_item["sha256"]
                or prepared.get("size_bytes") != destination_item["bytes"]
                or published.get("size_bytes") != destination_item["bytes"]
                or prepared.get("metadata") != metadata
                or published.get("metadata") != metadata
                or prepared.get("file_identity") != published.get("file_identity")):
            _invalid("Export publication receipt differs")

    @staticmethod
    def _fit_residency(path, spec, state):
        from .hybrid_replay.publication import is_hybrid, residency
        if is_hybrid(spec):
            return residency(path,spec,state)
        if spec["kind"] in {"fit","replay"}:
            from .retired_algorithms import is_retired
            identity=spec["semantics"].get("profile_id")
            if identity is None or is_retired(identity):
                return {**state,"algorithm_retired":True,"resident":False,"binding":None,
                    "persistent_solve":state["state"]=="SUCCEEDED"}
        if spec["kind"] != "fit":
            return state
        session = path / "session"
        actual = strict_load(session / "lease.json") if (session / "lease.json").is_file() else None
        resident = False
        if actual is not None and state["state"] == "SUCCEEDED":
            try:
                from .lease import validate_lease_handle
                saved = state.get("result_lease")
                keys = ("handle_id", "generation", "owner_instance_id", "project_revision", "recipe_sha256")
                if not isinstance(saved, dict):
                    raise GMError("RESULT_LEASE_INVALID", "Committed fit has no binding", 7)
                validate_lease_handle(actual, binding={key: saved[key] for key in keys})
                resident = all(identity_alive(strict_load(session / name)) is True
                               for name in ("owner.json", "host-process.json"))
                session_spec = strict_load(session / "session-spec.json")
                resident = resident and actual["owner_instance_id"] == session_spec["session_id"]
            except (GMError, OSError, ValueError, KeyError):
                resident = False
        if actual is not None and actual.get("state") == "RESIDENT" and not resident:
            actual = {**actual, "state": "LOST"}
        return {**state, "result_lease": actual, "resident": resident,
                "resident_state": actual.get("state", "UNAVAILABLE") if actual else "UNAVAILABLE",
                "persistent_solve": state["state"] == "SUCCEEDED"}

    def progress_snapshot(self, job_id, attempt_id):
        """Read live progress without running publication recovery on each UI tick.

        This read holds only record.lock, never subsequently acquires project.lock,
        verifies no artifact and grants no resident/result binding. Exact-dead
        owners still go through status(), outside the read lock, for recovery.
        """
        path, spec = self.path(job_id, attempt_id)
        with locked(path / "record.lock", timeout=.25):
            current = dict(_journal(path)[-1]["snapshot"])
        owner = current.get("owner") or spec.get("startup_owner")
        result={**current,"progress_only":True}
        if spec.get('semantics',{}).get('backend_id')=='cuda-r0-cpu-lld10-v1':
            from .hybrid_replay.monitor_completion import project
            if current['state'] in TERMINAL or current['state']=='CANCEL_REQUESTED':return project(path,spec,result,light=True)
            from .hybrid_replay.resource_journal import latest_progress
            result.update(latest_progress(path))
            return project(path,spec,result,light=True)
        if identity_alive(owner) is False:
            return self.status(job_id, attempt_id)
        if current['state'] in TERMINAL or current['state'] == 'CANCEL_REQUESTED':
            return result
        if spec["kind"]=="export":
            phase=read_live_json(path/"light-export-progress.json")
            if phase:
                result.update(detail_stage=phase.get("stage"),gm066_monotonic=phase.get("stamp"))
        # The final compute phase remains on disk during publication. Do not
        # let it hide committing/encoding, or inherit counters on a new task.
        if current['stage'] in {'fit_render', 'render'}:
            from .operation_progress import compute_progress
            result.update(compute_progress(path, spec.get('semantics', {}).get('profile_id')))
        if current["state"]=="RUNNING" and (path/"light-publish-progress.json").is_file():
            phase=read_live_json(path/"light-publish-progress.json")
            if phase:
                result.update(detail_stage=phase.get("stage"),gm066_monotonic=phase.get("stamp"))
        if current['stage'] in {'preflight', 'fit_render', 'render'} and 'detail_stage' not in result:
            waiting = read_live_json(path / "gpu-wait.json")
            if waiting.get('waiting'):
                sample = waiting.get('snapshot', {})
                result.update(detail_stage='gpu_wait',gpu_wait=dict(
                    free_mib=sample.get('free_mib'),required_free_mib=sample.get('required_free_mib'),
                    wait_seconds=waiting.get('wait_seconds'),wait_limit_s=waiting.get('wait_limit_s'),
                    reason=waiting.get('reason')))
        return result

    def status(self, job_id, attempt_id):
        path, spec = self.path(job_id, attempt_id)
        if spec['semantics'].get('adaptive_parent') is True:
            from .adaptive_execution.composite import recover
            return recover(self,path,spec)
        if spec.get('semantics', {}).get('backend_id') == 'r0-cpu-portable-v1':
            from .cpu_replay.publication import recover
            return recover(self, path, spec)
        from .resource_execution import binding as resource_binding
        plan_error=resource_binding.diagnostic(path,spec)
        from .hybrid_replay.publication import is_hybrid
        if is_hybrid(spec):
            from .hybrid_replay.recovery import recover
            return recover(self, path, spec)
        with operation_lock(path, spec):
            current = dict(_journal(path)[-1]["snapshot"])
            from .hybrid_replay.publication import is_hybrid, recover_locked
            if is_hybrid(spec):
                recovered = recover_locked(self,path,spec,current)
                if recovered is not None:
                    return self._fit_residency(path,spec,recovered)
            if current_fit(spec):
                recovered = recover_fit_locked(self, path, spec, current)
                if recovered is not None:
                    return resource_binding.project(self._fit_residency(path, spec, recovered),plan_error)
                if (path / "commit.json").exists():
                    raise GMError("FIT_RECOVERY_CONFLICT", "Fit commit has no publication marker", 7)
            commit = path / "commit.json"
            if commit.is_file():
                manifest = strict_load(commit)
                self._verify_manifest(manifest, spec, path)
                if (current["state"] != "SUCCEEDED" or current.get("exit_code") != 0
                        or current.get("error_code") is not None):
                    current.update(state="SUCCEEDED", stage="recovered", exit_code=0,
                                   error_code=None,
                                   result_lease=manifest.get("result_lease"))
                    _append(path, current, "OPERATION_RECOVERED_COMMIT")
            else:
                owner = current.get("owner") or spec.get("startup_owner")
                owner_live = identity_alive(owner) if isinstance(owner, dict) else False
                if current["state"] != "RUNNING" or owner_live is False:
                    recovered = self._recover_published_export_locked(path, spec, current)
                    if recovered is not None:
                        return resource_binding.project(self._fit_residency(path, spec, recovered),plan_error)
            if not commit.is_file() and current["state"] not in TERMINAL:
                owner = current.get("owner") or spec.get("startup_owner")
                live = identity_alive(owner) if isinstance(owner, dict) else False
                now = time.time()
                if current["state"] == "CANCEL_REQUESTED" and (
                        live is False or now >= current.get("cancel_deadline_at", 0)):
                    current.update(state="CANCELLED", stage="recovered", exit_code=8,
                                   error_code="CANCELLED")
                    _append(path, current, "OPERATION_CANCELLED_AFTER_INTERRUPT")
                elif (live is False or now >= spec.get("operation_deadline_at", 0)
                      or (current["state"] == "QUEUED"
                          and now >= spec.get("start_deadline_at", 0))):
                    current.update(state="FAILED", stage="recovered", exit_code=9,
                                   error_code="INTERRUPTED")
                    _append(path, current, "OPERATION_INTERRUPTED")
            if current_fit(spec) and current["state"] in TERMINAL and identity_alive(current.get("owner", {})) is False:
                from .fit_publication import _clear_claim_locked
                _clear_claim_locked(Path(spec["semantics"]["project_path"]), path, spec, "DEAD_UNPUBLISHED_OWNER")
            return resource_binding.project(self._fit_residency(path, spec, current),plan_error)

    def events(self, job_id, attempt_id, after_seq=0):
        path, spec = self.path(job_id, attempt_id)
        with operation_lock(path, spec):
            return [row for row in _journal(path) if row["seq"] > after_seq]

    def cancel(self, job_id, attempt_id):
        path, spec = self.path(job_id, attempt_id)
        if spec['semantics'].get('adaptive_parent') is True:
            from .adaptive_execution.composite import recover
            return recover(self,path,spec,cancel=True)
        if spec.get('semantics', {}).get('backend_id') == 'r0-cpu-portable-v1':
            from .cpu_replay.publication import cancel
            return cancel(self, path, spec)
        with operation_lock(path, spec):
            current = dict(_journal(path)[-1]["snapshot"])
            if current_fit(spec):
                recovered = recover_fit_locked(self, path, spec, current)
                if recovered is not None:
                    recovered["cancel_requested_at"] = time.time()
                    _append(path, recovered, "OPERATION_CANCEL_AFTER_FIT_PUBLICATION")
                    return {**self._fit_residency(path, spec, recovered), "cancel_disposition": "ALREADY_COMMITTED"}
            if (path / "commit.json").is_file():
                manifest = strict_load(path / "commit.json")
                self._verify_manifest(manifest, strict_load(path / "spec.json"), path)
                if (current["state"] != "SUCCEEDED" or current.get("exit_code") != 0
                        or current.get("error_code") is not None):
                    current.update(state="SUCCEEDED", stage="recovered", exit_code=0,
                                   error_code=None, result_lease=manifest.get("result_lease"))
                    _append(path, current, "OPERATION_RECOVERED_COMMIT")
                return {**current, "cancel_disposition": "ALREADY_COMMITTED"}
            recovered = self._recover_published_export_locked(path, spec, current)
            if recovered is not None:
                return {**recovered, "cancel_disposition": "ALREADY_COMMITTED"}
            if current["state"] in TERMINAL:
                return {**current, "cancel_disposition": "ALREADY_TERMINAL"}
            if current["state"] == "QUEUED":
                current.update(state="CANCELLED", stage="terminal",
                               error_code="CANCELLED", exit_code=8,
                               cancel_requested_at=time.time())
                _append(path, current, "OPERATION_CANCELLED_BEFORE_START")
                with (path / "cancel.signal").open("xb") as stream:
                    stream.write(b"1\n"); stream.flush(); os.fsync(stream.fileno())
                return {**current, "cancel_disposition": "CANCELLED_BEFORE_START"}
            if current["state"] != "CANCEL_REQUESTED":
                current.update(state="CANCEL_REQUESTED", stage="cancelling",
                               cancel_requested_at=time.time(),
                               cancel_deadline_at=time.time() + CANCEL_GRACE_SECONDS)
                _append(path, current, "OPERATION_CANCEL_REQUESTED")
                with (path / "cancel.signal").open("xb") as stream:
                    stream.write(b"1\n"); stream.flush(); os.fsync(stream.fileno())
            return {**current, "cancel_disposition": "REQUESTED"}


def artifact(path: Path):
    return {"path": str(path.resolve()), "bytes": path.stat().st_size,
            "sha256": sha_file(path)}
