"""Reclaim verified reservations left by previous GradeMirror packages."""
import re
import time
from pathlib import Path
from uuid import UUID

from ..common import atomic_json, reject_reparse, strict_load
from ..cpu_replay.contract import BACKEND, binding, need, verify
from ..store import locked
from ..winproc import identity_alive, identity_key

# Manifest hashes and lengths come from the frozen package archives. Checking
# these saved bindings lets cleanup work even if the old installation moved.
LEGACY_MANIFESTS = {
    "6d2ba84db50085fa686e799c4bebe4763d48ce70a5056fbc4539e230aca2925c": {
        ("e2f3faeb4c9d2ca1015262885fc0f0972f65476d941fa47e749b3cefb5ff40cc", 3573710),
    },
    "10363f5362eac80c2cca9bb4808883aa936df40b2729ff3ee7cd07955d1de97a": {
        ("709e392b2b53f17ad0fae6e98d252da41a4f26e184c79502e9a315e2a27ccb15", 2140994),
        ("f730c92ac010e6399d9f0bf62c75075fc938ff29b7a71cbbced422928ac6e9ea", 3575383),
    },
}
PORTABLE_MANIFESTS = {
    "06dcce29542d094bc7b4b4afb84a628e45ea0cb3d6f456bc022dcdbde8cf225d": {
        ("728d5eb99cf8265b6b758042817bee99dcb63edafa9f2af6ff546c3fa2db44ec", 1953471),
        ("2dc40c6c49545383597c681b964d9715a58fd0ddf60ddd8f5254de265190acf7", 3262029),
    },
    "2a90d37dce408a8c8e20c98f46482dfdd9a2eb226a3f856653afbb1d6063297f": {
        ("38824e39f1711f53ee078867c3bdb38e6e25e6c87440a0cf8b81abc0f8385018", 1953471),
        ("603f7ec55d1398ca5db59ff4864c0b8770340552feb6b5a1eb5e9884daffe716", 3262029),
    },
    "c5a152bb229c287ed9612123137c77d1c624dd487f120c8a0be4e2556266bc47": {
        ("bfba0ca996c27d8171b1909c474c7c7604372caffd1430d0143eec66db682126", 1953471),
        ("7b9a01f9f4b27ad0ec434d18da25e93b82a51cdd14fc281fc766e77b583486e0", 3262029),
    },
    "b01704e29b568230f693763e276412a739361284fafccbd67be1fdb88f581790": {
        ("b66b425e83430ff76f1cfc31f353bc35df0a3cdeb8f7c7118ea9c8aea62699c0", 1953471),
        ("a8d0cb3db47c50f8d464bc5edf65e3c141fea47a5f1bde31ee0f2055f79dd95f", 3262029),
    },
}
PREVIOUS_MANIFESTS = LEGACY_MANIFESTS | PORTABLE_MANIFESTS


def _uuid(value):
    try:
        return isinstance(value,str) and str(UUID(value)) == value
    except ValueError:
        return False


def _same(*identities):
    keys = [identity_key(value) for value in identities]
    return keys[0] is not None and len(set(keys)) == 1


def _read(path):
    reject_reparse(path)
    return strict_load(path)


def _mappings_closed(session, fine):
    from multiprocessing.shared_memory import SharedMemory
    from ..adaptive_execution.portable_contract import receiver_directory

    p = session / "shared-mappings.json"
    shared = _read(p)
    names = {row["name"] for row in shared.values() if isinstance(row, dict) and "name" in row}
    need(all(key in shared and shared[key]["name"] in names for key in ("S","O")),
         "PREVIEW32_MAPPING_INVENTORY")
    receiver = receiver_directory(session)
    result_names = []
    if receiver.exists():
        reject_reparse(receiver)
        for path in receiver.rglob("executor-result.json"):
            result_names.append(_read(path)["shared_memory"])
    need(not fine or result_names, "PREVIEW32_MAPPING_INVENTORY")
    names.update(result_names)
    proof = []
    for name in sorted(names):
        need(isinstance(name,str) and re.fullmatch(r"(?:wnsm_|psm_)[0-9a-f]{8}",name) is not None,
             "PREVIEW32_MAPPING_NAME")
        try:
            mapping = SharedMemory(name=name,create=False)
        except FileNotFoundError:
            proof.append(dict(name=name,cannot_reopen=True))
        else:
            mapping.close()
            need(False,"PREVIEW32_MAPPING_STILL_OPEN")
    return proof


def settle(pool, row, store):
    """Release a previous build's BOUND reservation using its exit evidence."""
    if row["state"] != "BOUND":
        return None
    from ..gm004a_operations import OperationStore
    from ..adaptive_execution.residency import lock as lease_lock

    ids = (row["job_id"],row["attempt_id"],row["token"])
    need(all(_uuid(value) for value in ids),"PREVIEW32_POOL_ADDRESS")
    path, spec = OperationStore(store, read_only=True).path(*ids[:2])
    sem = spec["semantics"]
    build = sem.get("executor_build")
    if sem.get("backend_id") != BACKEND or build not in PREVIOUS_MANIFESTS or not sem.get("preview_version"):
        return None
    session = path/"session"
    need(path == store/"gm004a-operations"/ids[0]/ids[1] and str(session)==row["session_path"]
         and store.name=="store" and store.parent.parent==pool.root.parent/"runs" and _uuid(store.parent.name),
         "PREVIEW32_POOL_ADDRESS")
    reject_reparse(session)
    request = Path(sem["instance_context"]["path"])
    need(request==store.parent/"request.json","PREVIEW32_POOL_AUTHORITY")
    verify(sem["instance_context"])
    c = strict_load(request)
    saved_manifest = c["package"]
    manifest_path = Path(saved_manifest["path"])
    need(c["manifest_path"]==str(request)
         and c["schema"]=="gm065-preview-request-v1" and c["executor_build"]["sha256"]==build
         and (saved_manifest["sha256"], saved_manifest["bytes"]) in PREVIOUS_MANIFESTS[build]
         and manifest_path.is_absolute() and manifest_path.name=="light-manifest.json"
         and c["pool_root"]==str(pool.root) and c["workspace"]==str(store.parent)
         and c["key"]==store.parent.name==sem["preview_key"]
         and c["mode"]==sem["mode"] and c["mode"] in ("fast","fine")
         and c["TEST_ONLY"] is sem["TEST_ONLY"] is False,
         "PREVIEW32_POOL_AUTHORITY")
    old_bundle=manifest_path.parent
    if build in LEGACY_MANIFESTS:
        legacy = old_bundle / ".runtime/gm059-v1"
        owner_images = (legacy / "control/python.exe",)
        host_images = (legacy / "io/python.exe",)
        client_images = (legacy / "desktop/python.exe",)
    else:
        owner_images = (old_bundle / "workers/control/GradeMirror-control.exe",)
        host_images = (old_bundle / "workers/io/GradeMirror-io.exe",)
        client_images = (old_bundle / "GradeMirror.exe", old_bundle / "GradeMirror-CLI.exe")
    with locked(path/"record.lock"), lease_lock(session):
        current=pool._read()
        if current is None or current["token"]!=row["token"] or current["state"]=="FREE":
            return current
        need(current==row,"PREVIEW32_POOL_CHANGED")
        launch=_read(session/"owner-launch.json")
        cleanup_path=session/"owner-cleanup.json"
        need(cleanup_path.is_file(), "PREVIEW32_POOL_WAIT_PROOF",
             "旧版 CPU 会话缺少退出回执，无法安全回收。")
        cleanup=_read(cleanup_path)
        session_id=_read(session/"session-spec.json")["session_id"]
        need(launch["pool_token"]==row["token"] and launch["session_id"]==cleanup["session_id"]==row["session_id"]==session_id
             and cleanup["actual_wait"] is cleanup["actual_host_handle_wait"] is cleanup["owned_tree_empty"] is True,
             "PREVIEW32_POOL_WAIT_PROOF")
        owner,host,client=row["owner"],row["host"],row["claimant"]
        need(_same(owner,launch["identity"],cleanup["owner"],_read(session/"owner.json"))
             and _same(host,cleanup["actual_host_identity"],cleanup["host"],_read(session/"host-entry.json"),
                       _read(session/"host-process.json"))
             and _same(client,c["client"],sem["preview_client"],spec["startup_owner"]),
             "PREVIEW32_POOL_IDENTITY")
        for who, images in ((owner,owner_images),(host,host_images),(client,client_images)):
            need(Path(who["image_path"]) in images
                 and identity_alive(who) is False,"PREVIEW32_POOL_PROCESS_NOT_EXITED")
        lease_path=session/"lease.json"
        lease=_read(lease_path)
        leased=lease["binding"]
        need(lease["state"] in ("PREPARED","RESIDENT","RELEASE_PENDING","RELEASED")
             and (leased["job_id"],leased["attempt_id"],leased["pool_token"])==ids
             and leased["handle_id"]==row["lease_handle_id"]
             and leased["owner_instance_id"]==session_id
             and leased["executor_build"]==build
             and _same(leased["owner"],owner) and _same(leased["host"],host),
             "PREVIEW32_POOL_LEASE")
        mappings=_mappings_closed(session,c["mode"]=="fine")
        proof=dict(token=row["token"],job_id=ids[0],attempt_id=ids[1],session_path=str(session),
                   no_child_started=False,actual_wait=True,tree_empty=True,mappings=mappings,
                   identities=[dict(identity=who,exited=True) for who in (owner,host,client)],
                   owner_cleanup=binding(cleanup_path))
        archive=pool.root/"upgrade-receipts"/(row["token"]+".json")
        reject_reparse(archive)
        if archive.exists():
            saved=_read(archive)
            need(saved["reservation_before"]==row and saved["proof"]==proof,"PREVIEW32_POOL_RECEIPT_CHANGED")
        else:
            atomic_json(archive,dict(source_build=build,reservation_before=row,lease_before=lease,
                         request=binding(request),spec=binding(path/"spec.json"),proof=proof,at=time.time()))
        if lease["state"]!="RELEASED":
            atomic_json(lease_path,dict(lease,state="RELEASED"))
        return pool.finalize(spec,session,row["token"],proof)
