"""Export a small, shareable snapshot of operation diagnostics."""
from datetime import datetime, timezone
from itertools import islice
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from ..common import writable_path


_TAIL_BYTES = 256 * 1024
_MAX_ROWS = 80
_RUN_NAME = re.compile(r"(?:gm-new|gm004b)-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_PLAN_NAME = re.compile(r"[0-9a-f]{32}\.json")
_LOG_NAME = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\.jsonl")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,79}")
_FRAME = re.compile(r'^\s*File "([^"\n]+)", line ([0-9]+), in ([A-Za-z0-9_<>.]+)$', re.M)
_APP_SOURCE = re.compile(r'(?:src/grademirror/(?:[a-z0-9_]+/)*|tools/)[a-z0-9_]+\.py')
_TOKEN_FIELDS = frozenset("""
    event kind code primary_code error_code message_code state stage backend light_backend
    operation quality reason point profile_id version protocol_version unit cleanup
    reservation_state attempt_id job_id selection_basis role roles disposition
    cancel_disposition stage_id state_code schema numeric_profile device_detection
    driver_version cleanup_outcome backend_id phase model_id plan_id type resource file_name scope
""".split())
_NUMBERS = frozenset("""
    at timestamp stamp seq elapsed elapsed_seconds seconds done total count limit
    available required gap reserved allocated ram commit vram width height bits channels
    exit_code worker_exit_code pid creation_time fit_count render_count render_tiles
    model_forward_batches completed_tiles known_identity_count known_processes total_processes
    coverage identity_coverage memory_coverage rss_coverage private_commit_coverage
    sample_started_wall sample_finished_wall sampled_monotonic free_mib total_mib required_free_mib
    index compute_capability source_hw reference_hw native_wh
    cuda_free nvml_free effective_free effective_total
    rejected_credit_count ambiguous_backing_count rejected_count errno winerror path_utf16_length
""".split())
_BOOLEANS = frozenset("""
    complete identity_complete job_membership_complete rss_complete allowed waiting
    actual_wait actual_host_handle_wait actual_worker_handle_wait tree_empty owned_tree_empty
    cannot_reopen prior_live_sample_unresolved member job_member rss_sample_complete can_choose_cpu
""".split())
_CONTAINERS = frozenset("""
    measurement snapshot sample required resources processes identity owner host launcher worker
    shortages candidates selection admission member_snapshots known_identities mappings
    physical product stages plan result resource_plan device device_state peak per_process
    source reference counts resource_shadow predicted_usage uncertainty_upper safety_margin
    required_additional capacity_limit applied_credit failure rejected_modules
""".split())
_RECEIPTS = (
    "status.json", "events.jsonl", "fit-failure.json", "preview-failure-cleanup.json",
    "rss-watchdog-stop.json", "process-memory.jsonl", "phase.json", "fit-progress.jsonl",
    "render-progress.json", "pre-handoff.json", "checkpoint-r0.json", "gpu-wait.json", "gpu-admission.json",
    "dynamic-gpu-ram-initial.json", "dynamic-gpu-ram-locked.json", "progress.json",
    "admission.json", "reclaim-receipt.json", "cleanup.json", "io-counts.json",
    "host-ready.json", "host-errors.jsonl", "owner-cleanup.json", "owned-wait.json",
    "gm004a-abort-cleanup.json", "dynamic-host-before-put-y.json",
    "dynamic-gpu-training-prepare.json", "dynamic-gpu-training-vram.json", "dynamic-gpu-training-host.json",
    "dynamic-gpu-r0-vram.json", "dynamic-gpu-r0-host.json", "dynamic-gpu-p3-prepare.json",
    "dynamic-gpu-p3-solve.json", "dynamic-gpu-p3-expand-vram.json", "dynamic-gpu-p3-expand-host.json",
    "dynamic-LLF-O-vram.json", "dynamic-LLF-O-host.json", "dynamic-LLF-P-vram.json", "dynamic-LLF-P-host.json",
    "dynamic-gpu-compose.json", "dynamic-cpu-p3-prepare.json", "dynamic-cpu-fine.json",
    "dynamic-cpu_inner_admission.json", "dynamic-mixed-cpu-handoff.json",
    "light-entry-error.json", "r0-role-stage.json", "solver-cleanup.json",
)
_LEAVES = ("session", "session/host", "session/R0", "session/P3", "session/P3/solver", "session/CPU")


def _project(value, field="", depth=0):
    """Keep structured identifiers and measurements. Free-form strings are excluded."""
    if depth > 10:
        return None
    if isinstance(value, dict) and (not field or field in _CONTAINERS):
        result = {}
        for key, item in islice(value.items(), 160):
            child = _project(item, key, depth + 1)
            if child is not None:
                result[key] = child
        return result or None
    if isinstance(value, list) and field in _CONTAINERS | _NUMBERS | _TOKEN_FIELDS:
        return [safe for item in value[:64 if field in ("rejected_modules", "shortages") else 32]
                if (safe := _project(item, field, depth + 1)) is not None]
    if type(value) is bool and field in _BOOLEANS:
        return value
    if type(value) in (int, float) and (field in _NUMBERS or
            field.endswith("_bytes") and _TOKEN.fullmatch(field)):
        if type(value) is int or math.isfinite(value):
            return value
    if isinstance(value, str) and field in _TOKEN_FIELDS and _TOKEN.fullmatch(value):
        return value
    return None


def _plain(path, root):
    """Only read regular files and directories inside the managed data root."""
    if not path.is_relative_to(root):
        return False
    try:
        for part in (*path.parents[:len(path.parts) - len(root.parts)], path):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return False
        return path.is_file() or path.is_dir()
    except OSError:
        return False


def _newest(parent, pattern, root, *, directory, exclude=None):
    if not _plain(parent, root) or not parent.is_dir():
        return None
    candidates = []
    for path in parent.iterdir():
        if path == exclude or not pattern.fullmatch(path.name) or not _plain(path, root):
            continue
        try:
            info = path.stat()
            if stat.S_ISDIR(info.st_mode) == directory:
                candidates.append((info.st_mtime_ns, path))
        except OSError:
            continue
    return max(candidates)[1] if candidates else None


def _rows(path, root, limit):
    if not _plain(path, root) or not path.is_file():
        return None
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        if path.suffix == ".jsonl":
            stream.seek(max(0, size - _TAIL_BYTES))
            if size > _TAIL_BYTES:
                stream.readline(_TAIL_BYTES)
            data = stream.read(_TAIL_BYTES).splitlines()[-_MAX_ROWS:]
        elif size <= limit:
            stream.seek(0)
            data = [stream.read(limit)]
        else:
            return None
    result = []
    for row in data:
        try:
            clean = _project(json.loads(row))
        except (ValueError, UnicodeError, RecursionError):
            continue
        if clean:
            result.append(clean)
    return result or None


def _attempt(store, root):
    operations = store / "gm004a-operations"
    job = _newest(operations, _UUID, root, directory=True)
    return _newest(job, _UUID, root, directory=True) if job else None


def _operation_error(path, root):
    """Summarize a local exception without copying message, stack text or user paths."""
    if not _plain(path, root) or not path.is_file():
        return None
    with path.open("rb") as stream:
        raw = stream.read(_TAIL_BYTES + 1)
    if len(raw) > _TAIL_BYTES:
        return None
    doc = json.loads(raw)
    if not isinstance(doc, dict):
        return None
    detail = _project({key: doc.get(key) for key in
                       ("type", "code", "errno", "winerror", "path_utf16_length")}) or {}
    if "winerror" not in detail and isinstance(doc.get("error"), str):
        match = re.match(r'\[WinError ([0-9]{1,10})\]', doc["error"])
        if match:
            detail["winerror"] = int(match[1])
    trace = doc.get("traceback")
    if isinstance(trace, str):
        frames = []
        for path_text, line, function in _FRAME.findall(trace):
            path_text = path_text.replace("\\", "/")
            source = next((path_text[i + 1:] for i, c in enumerate(path_text) if c == "/"
                           and _APP_SOURCE.fullmatch(path_text[i + 1:])), None)
            if source and len(line) <= 8 and len(function) <= 80:
                frames.append(dict(source=source, line=int(line), function=function))
        if frames:
            detail["frames"] = frames[-8:]
    return detail or None


def write_support_log(destination, root, *, store, receipt, events, version, edition=None):
    """Publish the ZIP atomically once its bounded snapshot has been collected."""
    destination = writable_path(Path(destination))
    root = Path(root).absolute()
    jobs = root / "jobs"
    store = Path(store).absolute() if store else None
    if not store or store.parent != jobs or not _plain(store, root):
        store = _newest(jobs, _RUN_NAME, root, directory=True)
    entries = {"desktop/current.json": [_project(row) for row in events[-_MAX_ROWS:] if row]}
    errors = []
    # Progress polling may push the error out of the last 80 general events.
    for event in [row for row in events if row.get("event") in ("error", "light_resource_choice")][-16:]:
        if event.get("failure") or event.get("shortages"):
            errors.append(dict(origin="desktop", **_project(event)))
    skipped = []
    remaining = 4 * 1024 * 1024

    def add(label, path, limit=_TAIL_BYTES):
        nonlocal remaining
        try:
            rows = _rows(path, root, limit)
        except OSError:
            skipped.append(label)
        else:
            if rows:
                size = len(json.dumps(rows, ensure_ascii=False, indent=2).encode("utf8"))
                if size <= remaining:
                    entries[label] = rows
                    remaining -= size
                else:
                    skipped.append(label)

    previous = _newest(jobs / "desktop-logs", _LOG_NAME, root, directory=False,
                       exclude=Path(receipt).absolute())
    if previous:
        add("desktop/previous.json", previous)

    if store:
        route = store / "light-route.json"
        add("operation/route.json", route)
        cpu_store = None
        if _plain(route, root) and route.is_file():
            try:
                with route.open("rb") as stream:
                    bindings = json.loads(stream.read(_TAIL_BYTES))
                key = bindings.get("cpu_key")
                if isinstance(key, str) and _UUID.fullmatch(key):
                    cpu_store = root / "cpu" / "runs" / key / "store"
                pointer = bindings.get("plan_path")
                if isinstance(pointer, str):
                    plan = Path(pointer)
                    if plan.parent == root / "plans" and _PLAN_NAME.fullmatch(plan.name):
                        add("operation/plan.json", plan, limit=4 * 1024 * 1024)
            except (OSError, ValueError, AttributeError, RecursionError):
                skipped.append("operation/route-bindings")
        for scope, location in (("operation", store), ("cpu-operation", cpu_store)):
            if location is None:
                continue
            attempt = _attempt(location, root)
            if not attempt:
                continue
            add(f"{scope}/status.json", attempt / "status.json")
            add(f"{scope}/progress.json", attempt / "progress.json")
            status = entries.get(f"{scope}/status.json", [{}])[-1]
            progress = entries.get(f"{scope}/progress.json", [{}])[-1]
            for leaf in ("", *_LEAVES):
                folder = attempt / leaf
                if _plain(folder, root) and folder.is_dir():
                    for name in _RECEIPTS:
                        if (leaf or name not in ("status.json", "progress.json")) and (folder / name).is_file():
                            member = f"{scope}/{leaf + '/' if leaf else ''}{Path(name).stem}.json"
                            add(member, folder / name)
                    try:
                        detail = _operation_error(folder / "error.json", root)
                    except (OSError, ValueError, RecursionError):
                        skipped.append(f"{scope}/{leaf}/error-summary")
                    else:
                        if detail:
                            role = {"": "launcher", "session": "entry", "session/host": "host",
                                    "session/P3/solver": "solver"}.get(leaf, folder.name)
                            stage = progress.get("stage") if role in ("entry", "host", "launcher") else role
                            errors.append(dict(origin=scope, job_id=attempt.parent.name, attempt_id=attempt.name,
                                               role=role, stage=stage or status.get("stage"),
                                               operation_error_code=status.get("error_code"), **detail))

    if errors:
        entries["errors.json"] = errors

    summary = {
        "schema": "grademirror-support-log-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "edition": edition,
        "windows_build": sys.getwindowsversion().build if sys.platform == "win32" else None,
        "operation_receipts_found": any(key.startswith(("operation/", "cpu-operation/")) for key in entries),
        "omitted_receipts": skipped,
    }
    with tempfile.NamedTemporaryFile(prefix=".gm-support-", suffix=".tmp", dir=destination.parent,
                                     delete=False) as temp:
        staged = Path(temp.name)
    try:
        with ZipFile(staged, "w", ZIP_DEFLATED) as archive:
            for name, value in {"summary.json": summary, **entries}.items():
                archive.writestr(name, json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)
