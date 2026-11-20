"""One durable GM-004A result-session admission for all operation stores.

The process lock serializes changes to the reservation record.  The record,
not the lock, remains occupied while a result host is resident.  This module
never starts, kills, or decodes anything; it only checks exact process identities.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from .common import GMError, atomic_json, strict_load, writable_path
from .image_limits import LEGACY_BUDGET, validate_budget
from .store import locked
from .winproc import current_identity, identity_alive

POOL_BYTES = 6 * 1024**3
COMPUTE_BYTES = 4 * 1024**3
HOST_BYTES = 1536 * 1024**2
CONTROL_IPC_BYTES = 512 * 1024**2
assert COMPUTE_BYTES + HOST_BYTES + CONTROL_IPC_BYTES == POOL_BYTES


def _same_identity(left: dict, right: dict) -> bool:
    return (isinstance(left, dict) and isinstance(right, dict)
            and left.get("pid") == right.get("pid")
            and left.get("creation_time") == right.get("creation_time")
            and os.path.normcase(left.get("image_path", ""))
                == os.path.normcase(right.get("image_path", "")))


class ResidentAdmission:
    """Instantiate with one fixed product-wide root, never a Store root."""

    def __init__(self, root: Path):
        self.root = writable_path(Path(root))
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.root / "admission.lock"
        self.record_path = self.root / "reservation.json"

    def _read(self):
        return strict_load(self.record_path) if self.record_path.is_file() else None

    def _write(self, record):
        atomic_json(self.record_path, record)
        # A per-token copy survives the next admission and is useful in STOP
        # reviews.  The current record is authoritative.
        atomic_json(self.root / "receipts" / (record["token"] + ".json"), record)

    @staticmethod
    def _identity_file(session: Path, name: str):
        path = session / name
        return strict_load(path) if path.is_file() else None

    def _reconcile_locked(self):
        row = self._read()
        if row is None or row["state"] == "FREE":
            return row
        if row["state"] not in {"STARTING", "BOUND"}:
            raise GMError("RESIDENT_RECORD_INVALID", "Unknown admission state", 7)
        session = Path(row["session_path"])
        saved_owner = self._identity_file(session, "owner.json")
        saved_host = self._identity_file(session, "host-process.json")
        if row["state"] == "BOUND":
            if not (_same_identity(row["owner"], saved_owner)
                    and _same_identity(row["host"], saved_host)):
                raise GMError("RESIDENT_RECORD_INVALID", "Process identity record differs", 7)
        # A crashed caller can leave the reservation in STARTING after child
        # launch.  Inspect child identities before deciding that it is free.
        owner = row.get("owner") or saved_owner
        host = row.get("host") or saved_host
        if owner is not None and host is not None:
            owner_live, host_live = self._session_live(session,owner), self._session_live(session,host)
            if owner_live is False and host_live is False:
                if not self._exit_proven(session):return row
                row.update(state="FREE", freed_at=time.time(),
                           free_reason="CONFIRMED_OWNER_HOST_EXIT",
                           owner_alive=False, host_alive=False)
                self._write(row)
            return row
        if owner is not None or host is not None:
            # An unrecorded child might still exist.  Only a startup cleanup
            # receipt with an actual wait and empty tree clears that ambiguity.
            cleanup = session / "startup-cleanup.json"
            if cleanup.is_file():
                receipt = strict_load(cleanup)
                known = owner or host
                if (receipt.get("actual_wait") is True
                        and receipt.get("host_exited") is True
                        and identity_alive(known) is False):
                    row.update(state="FREE", freed_at=time.time(),
                               free_reason="STARTUP_ACTUAL_WAIT")
                    self._write(row)
            return row
        claimant_live = identity_alive(row["claimant"])
        if claimant_live is False and not session.exists():
            row.update(state="FREE", freed_at=time.time(),
                       free_reason="DEAD_CLAIMANT_NO_SESSION")
            self._write(row)
        elif claimant_live is False:
            cleanup = session / "startup-cleanup.json"
            if cleanup.is_file():
                receipt = strict_load(cleanup)
                if receipt.get("actual_wait") is True and receipt.get("host_exited") is True:
                    row.update(state="FREE", freed_at=time.time(),
                               free_reason="STARTUP_ACTUAL_WAIT")
                    self._write(row)
        return row

    def inspect(self):
        """Reconcile confirmed exits; never infer vacancy from lease expiry alone."""
        with locked(self.lock_path):
            return self._reconcile_locked()

    def for_operation(self, job_id: str, attempt_id: str, session_path: Path):
        """Find a replay's token on a later CLI call, after exact reconciliation."""
        session = writable_path(Path(session_path))
        with locked(self.lock_path):
            row = self._reconcile_locked()
            if (row is None or row.get("job_id") != job_id
                    or row.get("attempt_id") != attempt_id
                    or row.get("session_path") != str(session)):
                raise GMError("RESIDENT_ADMISSION_STALE", "No matching result reservation", 7)
            return row

    def claim(self, job_id: str, attempt_id: str, store_root: Path,
              session_path: Path, *, wait_s: float = 0, cancelled=None, budget_bytes=None):
        for value in (job_id, attempt_id):
            try:
                uuid.UUID(value)
            except (ValueError, TypeError, AttributeError) as exc:
                raise GMError("ADDRESS_INVALID", "Admission needs job and attempt UUIDs", 2) from exc
        if wait_s < 0 or wait_s > 120:
            raise GMError("RESIDENT_WAIT_INVALID", "Wait must be within 0..120 seconds", 2)
        budget = validate_budget(LEGACY_BUDGET if budget_bytes is None else budget_bytes)
        session = writable_path(Path(session_path))
        store = writable_path(Path(store_root))
        claimant = current_identity()
        if not claimant or claimant.get("unknown"):
            raise GMError("PROCESS_IDENTITY_UNKNOWN", "Claimant identity unavailable", 9)
        deadline = time.monotonic() + wait_s
        while True:
            if cancelled is not None and cancelled():
                raise GMError("CANCELLED", "Admission cancelled", 8)
            with locked(self.lock_path):
                current = self._reconcile_locked()
                if current is None or current["state"] == "FREE":
                    row = {"schema_version": "0.2.0", "token": str(uuid.uuid4()),
                           "state": "STARTING", "job_id": job_id,
                           "attempt_id": attempt_id, "store_root": str(store),
                           "session_path": str(session), "claimant": claimant,
                           "owner": None, "host": None, "session_id": None,
                           "lease_handle_id": None, "lease_expires_at": None,
                           "claimed_at": time.time(),
                           "budget_bytes": budget}
                    self._write(row)
                    return row
            if time.monotonic() >= deadline:
                raise GMError("RESIDENT_POOL_BUSY", "Another result session holds the reserved RAM pool", 5)
            time.sleep(min(.05, max(0, deadline - time.monotonic())))

    def _current_token(self, token: str):
        row = self._read()
        if row is None or row.get("token") != token:
            raise GMError("RESIDENT_ADMISSION_STALE", "Admission token no longer owns pool", 7)
        return row

    def bind(self, token: str, session_id: str, owner: dict, host: dict):
        """Call immediately after start_session returns, before compute spawn."""
        with locked(self.lock_path):
            row = self._current_token(token)
            if row["state"] != "STARTING":
                raise GMError("RESIDENT_ADMISSION_STALE", "Session already bound", 7)
            session = Path(row["session_path"])
            spec = strict_load(session / "session-spec.json")
            if (spec.get("resource_budget", LEGACY_BUDGET) != row["budget_bytes"]
                    or spec.get("session_id") != session_id
                    or not _same_identity(owner, self._identity_file(session, "owner.json"))
                    or not _same_identity(host, self._identity_file(session, "host-process.json"))
                    or identity_alive(owner) is not True
                    or identity_alive(host) is not True):
                raise GMError("RESIDENT_BINDING_INVALID", "Session identity not live or mismatched", 7)
            row.update(state="BOUND", session_id=session_id, owner=owner, host=host,
                       bound_at=time.time())
            self._write(row)
            return row

    def bind_lease(self, token: str, lease: dict):
        """Retain the host lease identity; its TTL never frees a live process."""
        with locked(self.lock_path):
            row = self._current_token(token)
            if (row["state"] != "BOUND"
                    or lease.get("owner_instance_id") != row["session_id"]
                    or lease.get("state") != "RESIDENT"
                    or type(lease.get("bytes_reserved")) is not int
                    or not 0 <= lease["bytes_reserved"] <= row["budget_bytes"]["host"]):
                raise GMError("RESIDENT_LEASE_INVALID", "Lease exceeds host budget or binding differs", 7)
            if row.get('lease_handle_id') not in (None,lease['handle_id']):
                raise GMError('RESIDENT_LEASE_CONFLICT','Reservation already holds another lease',7)
            row.update(lease_handle_id=lease["handle_id"],
                       lease_expires_at=lease["expires_at"],
                       lease_bytes_reserved=lease["bytes_reserved"])
            self._write(row)
            return row

    @staticmethod
    def _session_live(session, identity):
        spec_path=session/'session-spec.json'
        if spec_path.is_file() and strict_load(spec_path).get('backend_id')=='cuda-r0-cpu-lld10-v1':
            from .hybrid_replay.recovery import process_state
            try:return process_state(identity)
            except GMError:return None
        return identity_alive(identity)

    @staticmethod
    def _exit_proven(session):
        spec_path=session/'session-spec.json'
        if not spec_path.is_file():
            return False
        spec=strict_load(spec_path)
        if not spec.get('light_exact_exit') and spec.get('backend_id')!='cuda-r0-cpu-lld10-v1':
            return True
        owner=session/'owner-cleanup.json'
        if owner.is_file():
            receipt=strict_load(owner)
            if (receipt.get('actual_wait') is True and receipt.get('owned_tree_empty') is True and
                receipt.get('actual_host_handle_wait') is True and receipt.get('session_id')==spec['session_id'] and
                _same_identity(receipt.get('owner'),strict_load(session/'owner.json')) and
                _same_identity(receipt.get('host'),strict_load(session/'host-process.json')) and
                _same_identity(receipt.get('actual_host_identity'),strict_load(session/'host-entry.json'))):return True
        for p in (session/'release-actual-waits.json',session.parent/'hybrid-recovery-cleanup-waits.json'):
            if p.is_file():
                rows=strict_load(p)
                expected=[strict_load(session/n) for n in('owner.json','host-process.json','host-entry.json')]
                if len(rows)==3 and all(r.get('actual_handle_wait') is True and _same_identity(r.get('identity'),v) for r,v in zip(rows,expected)):return True
        return False

    def recover_lease(self, token, job, attempt, store, session, session_id, owner, host, lease):
        """Compare exact original reservation; never claim or replace a token."""
        from .lease import validate_lease_handle
        validate_lease_handle(lease)
        with locked(self.lock_path,timeout=5):
            row=self._current_token(token)
            if (row['state']!='BOUND' or row['job_id']!=job or row['attempt_id']!=attempt or
                row['store_root']!=str(Path(store)) or row['session_path']!=str(Path(session)) or
                row['session_id']!=session_id or lease['owner_instance_id']!=session_id or
                not _same_identity(row['owner'],owner) or not _same_identity(row['host'],host) or
                not _same_identity(owner,self._identity_file(Path(session),'owner.json')) or
                not _same_identity(host,self._identity_file(Path(session),'host-process.json')) or
                self._session_live(Path(session),owner) is not True or
                self._session_live(Path(session),host) is not True or
                not 0<=lease['bytes_reserved']<=row['budget_bytes']['host'] or
                row.get('lease_handle_id') not in (None,lease['handle_id'])):
                raise GMError('RESIDENT_RECOVERY_CONFLICT','Exact reservation CAS rejected',7)
            if row.get('lease_handle_id') is None:
                row.update(lease_handle_id=lease['handle_id'],lease_expires_at=lease['expires_at'],
                           lease_bytes_reserved=lease['bytes_reserved'])
                self._write(row)
            return row

    def release_record(self, token, *, deadline):
        """Read the exact current or historical token without reconciling it."""
        try: uuid.UUID(token)
        except (ValueError, TypeError, AttributeError) as error:
            raise GMError('HYBRID_RELEASE_TOKEN_INVALID','Invalid original token',7) from error
        with locked(self.lock_path,timeout=max(0,min(5,deadline-time.monotonic()))):
            row=self._read()
            if row is not None and row.get('token')==token:return row
            path=self.root/'receipts'/(token+'.json')
            from .common import reject_reparse
            reject_reparse(path)
            if not path.is_file():raise GMError('HYBRID_RELEASE_HISTORY_MISSING','Original token receipt absent',7)
            row=strict_load(path)
            if row.get('token')!=token or row.get('state')!='FREE':
                raise GMError('HYBRID_RELEASE_HISTORY_CONFLICT','Original token not confirmed free',7)
            return row

    def close(self, token: str, *, outcome: str, deadline=None):
        """Release reservation only after exact owned owner/host exits."""
        if outcome not in {"RELEASED", "LOST", "STARTUP_FAILED"}:
            raise GMError("RESIDENT_OUTCOME_INVALID", "Unknown session outcome", 2)
        with locked(self.lock_path,timeout=10 if deadline is None else max(0,min(5,deadline-time.monotonic()))):
            current=self._read()
            if deadline is not None and current is not None and current.get('token')!=token:
                # A different Store may have claimed after exact reconciliation.
                # Never write its record; the original FREE receipt must stand.
                try:uuid.UUID(token)
                except (ValueError,TypeError,AttributeError) as error:
                    raise GMError('RESIDENT_ADMISSION_STALE','Invalid old token',7) from error
                row=strict_load(self.root/'receipts'/(token+'.json'))
                if row.get('token')!=token or row.get('state')!='FREE':
                    raise GMError('RESIDENT_ADMISSION_STALE','Original token not free',7)
                session=Path(row['session_path'])
                if not self._exit_proven(session):raise GMError('RESIDENT_EXIT_UNCONFIRMED','Old token exit proof missing',9)
                if any(self._session_live(session,strict_load(session/name)) is not False for name in('owner.json','host-process.json','host-entry.json')):
                    raise GMError('RESIDENT_EXIT_UNCONFIRMED','Old token identity not exited',9)
                return row
            row = self._current_token(token)
            if row["state"] == "FREE":
                return row
            session = Path(row["session_path"])
            owner = row.get("owner") or self._identity_file(session, "owner.json")
            host = row.get("host") or self._identity_file(session, "host-process.json")
            if row["state"] == "BOUND" and (
                    not _same_identity(row["owner"], self._identity_file(session, "owner.json"))
                    or not _same_identity(row["host"], self._identity_file(session, "host-process.json"))):
                raise GMError("RESIDENT_RECORD_INVALID", "Session process record changed", 7)
            if owner is not None or host is not None:
                if owner is None or host is None:
                    raise GMError("RESIDENT_EXIT_UNCONFIRMED", "Only one child identity recorded", 9)
                if self._session_live(session,owner) is not False or self._session_live(session,host) is not False:
                    raise GMError("RESIDENT_EXIT_UNCONFIRMED", "Owned process still live or unknown", 9)
                if not self._exit_proven(session):
                    raise GMError('RESIDENT_EXIT_UNCONFIRMED','Exact hybrid tree wait receipt missing',9)
            elif session.exists():
                receipt_path = session / "startup-cleanup.json"
                receipt = strict_load(receipt_path) if receipt_path.is_file() else None
                if not (receipt and (receipt.get("actual_wait") is True
                                     or receipt.get("no_child_created") is True)
                        and receipt.get("host_exited") is True):
                    raise GMError("RESIDENT_EXIT_UNCONFIRMED", "Startup tree exit not proven", 9)
            elif row["state"] == "STARTING":
                # No child/session exists.  Only the original claimant (or a
                # confirmed dead claimant) may withdraw this early claim.
                if (not _same_identity(current_identity(), row["claimant"])
                        and identity_alive(row["claimant"]) is not False):
                    raise GMError("RESIDENT_EXIT_UNCONFIRMED", "Claimant still owns startup", 9)
            row.update(state="FREE", freed_at=time.time(), free_reason=outcome,
                       owner_alive=False, host_alive=False)
            self._write(row)
            return row
