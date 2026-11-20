"""Recipe identity bridge for the one resource-only CPU core change."""
import hashlib
from pathlib import Path

MEMBER = "grademirror/engine/cpu_postprocess/core.py"
OLD = b"    if workspace_bound(s)['total_bytes']>512*1024**2:raise MemoryError('CPU_WORKSPACE_CAP')"
NEW = (b"    from ...light.resources import workspace_limit\n"
       b"    if workspace_bound(s)['total_bytes']>workspace_limit(*s.shapes[0]):raise MemoryError('CPU_WORKSPACE_CAP')")


def restore_core_guard(files, *, expected):
    source = Path(__file__).resolve().parents[2] / MEMBER
    from .portable import source_record
    record = source_record(source)
    if record is not None:
        if record.get("restored_resource_guard_sha256") != expected:
            raise ValueError("CPU_MATH_BYTES_CHANGED")
        current = record["source_sha256"]
        if sum(row["path"] == MEMBER and row["sha256"] == current for row in files) != 1:
            raise ValueError("CPU_MATH_BRIDGE_MEMBER")
        return [dict(row, sha256=expected) if row["path"] == MEMBER else row for row in files]
    payload = source.read_bytes()
    if payload.count(NEW) != 1:
        raise ValueError("CPU_RESOURCE_GUARD_BRIDGE")
    reconstructed = payload.replace(NEW, OLD, 1)
    if hashlib.sha256(reconstructed).hexdigest() != expected:
        raise ValueError("CPU_MATH_BYTES_CHANGED")
    current = hashlib.sha256(payload).hexdigest()
    if sum(row["path"] == MEMBER and row["sha256"] == current for row in files) != 1:
        raise ValueError("CPU_MATH_BRIDGE_MEMBER")
    return [dict(row, sha256=expected) if row["path"] == MEMBER else row for row in files]


def compatible_hybrid_execution(saved_digest):
    from ..common import strict_load
    from ..hybrid_replay.contract import digest, execution_contract
    from .runtime import ROOT, application
    from .system import packaged

    current, current_digest = execution_contract()
    if saved_digest == current_digest:
        return True
    if not application():
        return False
    implementation = dict(current["implementation"])
    for path in ("tools/gm078/preview32-portable-admission-hybrid-bridge.json",
                 "tools/gm078/preview28-hybrid-bridge.json",
                 "tools/gm077/preview27-hybrid-bridge.json",
                 "tools/gm076/preview26-hybrid-bridge.json",
                 "tools/gm076/preview25-hybrid-bridge.json",
                 "tools/gm076/preview23-hybrid-bridge.json",
                 "tools/gm076/preview22-hybrid-bridge.json",
                 "tools/gm075/preview21-hybrid-bridge.json"):
        record = strict_load(packaged(ROOT/path))
        transitions = record.get("transitions")
        if transitions is None:
            transitions = [record.get("resources_only", record.get("orchestration_only", {}))]
        for changes in transitions:
            for name, fingerprints in changes.items():
                if name in implementation:
                    if implementation[name] != fingerprints["after"]:
                        return False
                    implementation[name] = fingerprints["before"]
            if saved_digest == digest(dict(current, implementation=implementation)):
                return True
    return False




_D1_BRIDGE = "tools/gm092/portable-d1-bridge.json"
_D1_BRIDGE_SHA256 = "f2e5e4d422632d357ce95fcab2e6900bbfe097446f06c8bf4cf05268d75e4f8c"
_NATIVE_RUNTIME = ("runtime/cpu/", "runtime/solver/", "runtime/cu124/", "runtime/io/", "runtime/cpython/")


def compatible_portable_d1(saved, path):
    """Authorize the exact D1 saved semantics against a pinned portable contract."""
    from ..common import sha_file, strict_load
    from ..hybrid_replay.contract import digest, execution_contract, require
    from . import portable, runtime
    from .system import manifest, packaged

    package = manifest() if portable.enabled() and runtime.application() else None
    if not package or package.get("edition") != "gpu":
        return False
    file = packaged(runtime.ROOT / _D1_BRIDGE)
    require(sha_file(file) == _D1_BRIDGE_SHA256, "HYBRID_PORTABLE_BRIDGE_CHANGED")
    bridge = strict_load(file)
    if saved.get("execution_contract_sha256") != bridge["saved_contract_sha256"]:
        return False
    require(saved.get("manifest_sha256") == bridge["saved_manifest_sha256"] and
            sha_file(path) == bridge["saved_file_sha256"] and
            saved.get("core_rows") == bridge["saved_tier"], "HYBRID_PORTABLE_SAVED_IDENTITY")
    current, _ = execution_contract()
    require(digest({key: value for key, value in current.items() if key != "primary_runtime"})
            == bridge["current_contract_without_launcher_sha256"], "HYBRID_PORTABLE_IMPLEMENTATION")
    primary = current["primary_runtime"]
    launcher = packaged(portable.executable("control"))
    expected = packaged(runtime.ROOT / "GradeMirror-CLI.exe")
    require(primary["amendment"] == "GM090-PORTABLE-EXE" and primary["python"] == "3.12.14" and
            primary["original_binding_retained"] is True and
            primary["original_sha256"] == bridge["expected_primary_original_sha256"] and
            primary["bytes"] == launcher.stat().st_size and primary["sha256"] == sha_file(expected),
            "HYBRID_PORTABLE_LAUNCHER")
    members = {name: row for name, row in package["files"].items() if name.startswith(_NATIVE_RUNTIME)}
    require(digest(members) == bridge["numerical_runtime_inventory_sha256"], "HYBRID_PORTABLE_RUNTIME")
    for name in sorted(members):
        packaged(runtime.ROOT / name)
    for role in ("io", "cpu", "cu124", "solver"):
        require(sha_file(packaged(portable.executable(role))) == primary["sha256"], "HYBRID_PORTABLE_ROLE")
    return True
