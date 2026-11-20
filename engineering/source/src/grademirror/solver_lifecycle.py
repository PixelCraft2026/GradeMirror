"""Shared owned solver channel, identity barrier, and independent settlement."""
import hashlib
import math
import secrets
import socket
import threading
import time
from multiprocessing.connection import Connection, answer_challenge, deliver_challenge

from .common import atomic_json, canonical_json, strict_load
from .store import locked
from .fine_ipc import MAX_ARRAY, recv_f64, send_f64
from .gm003_ipc import recv_json
from .solver_handshake import SolverHandshake
from . import winproc as win


class SolverChannel:
    def __init__(self, token):
        self.key = bytes.fromhex(token)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.interrupt = None
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(.2)
        except BaseException:
            listener.close()
            raise
        self.listener = listener
        self.address = listener.getsockname()

    def accept(self):
        while not self.stop.is_set():
            try:
                raw, _ = self.listener.accept()
            except TimeoutError:
                continue
            with raw, self.lock:
                if self.stop.is_set():
                    break
                raw.setblocking(True)
                self.interrupt = raw.dup()
                return Connection(raw.detach())
        raise InterruptedError("P3_CHANNEL_CLOSED")

    def close_listener(self):
        self.stop.set()
        self.listener.close()

    def interrupt_channel(self):
        self.stop.set()
        with self.lock:
            if self.interrupt is not None:
                with self.interrupt as endpoint:
                    try:
                        endpoint.shutdown(socket.SHUT_RDWR)
                    except OSError as error:
                        # Peer already closed a completed connection.
                        if getattr(error, "winerror", None) not in (10057, 10058):
                            raise
                self.interrupt = None


def launcher_exit(job, timeout=0):
    state = win.k.WaitForSingleObject(job.child, timeout)
    if state == win.WAIT_TIMEOUT:
        if timeout:
            raise TimeoutError("P3_LAUNCHER_WAIT_TIMEOUT")
        return None
    if state != win.WAIT_OBJECT_0:
        win._err("P3 launcher wait")
    code = win.DWORD()
    if not win.k.GetExitCodeProcess(job.child, win.c.byref(code)):
        win._err("P3 launcher exit code")
    return code.value


class PendingSettlement:
    """Retry only publication of the original attempt receipt."""
    def __init__(self, run, token, receipt):
        self.run = run.resolve()
        self.token = token
        self.receipt = receipt

    def persist(self):
        path = self.run / "solver-cleanup.json"
        with locked(self.run / "solver-settlement.lock", timeout=2):
            if strict_load(self.run / "solver-settlement-ticket.json") != {"token": self.token}:
                raise ValueError("P3_SETTLEMENT_ATTEMPT_CHANGED")
            if path.exists():
                if strict_load(path) != self.receipt:
                    raise ValueError("P3_SETTLEMENT_RECEIPT_CONFLICT")
                return path
            atomic_json(path, self.receipt)
        return path


def _settlement_path(run, token):
    from .light.runtime import application, data_root
    from .common import reject_reparse, sha_bytes, writable_path
    if not application():
        return None
    root = data_root()
    run = run.resolve()
    if not run.is_relative_to(root) or run.name != "solver":
        return None
    reject_reparse(run)
    if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError("P3_SETTLEMENT_TOKEN_INVALID")
    name = sha_bytes(canonical_json([str(run), token]))
    return writable_path(root / "solver-settlement-outbox" / (name + ".json"))


def save_pending_settlement(pending):
    from .common import sha_bytes
    path = _settlement_path(pending.run, pending.token)
    if path is None:
        return False
    value = dict(run=str(pending.run), token=pending.token, owner=win.current_identity(),
                 receipt=pending.receipt, receipt_sha256=sha_bytes(canonical_json(pending.receipt)))
    with locked(path.with_suffix(".lock"), timeout=2):
        if path.exists() and strict_load(path) != value:
            raise ValueError("P3_SETTLEMENT_OUTBOX_CONFLICT")
        if not path.exists():
            atomic_json(path, value)
    return True


def recover_saved_settlement(run):
    from .common import sha_bytes
    run = run.resolve()
    ticket = run / "solver-settlement-ticket.json"
    if not ticket.is_file():
        return None
    token = strict_load(ticket)["token"]
    path = _settlement_path(run, token)
    if path is None or not path.is_file():
        return None
    value = strict_load(path)
    receipt = value["receipt"]
    if (value["run"] != str(run) or value["token"] != token or receipt["attempt_token"] != token
            or sha_bytes(canonical_json(receipt)) != value["receipt_sha256"]):
        raise ValueError("P3_SETTLEMENT_OUTBOX_IDENTITY")
    if win.identity_alive(value["owner"]) is not False:
        return None
    return PendingSettlement(run, token, receipt).persist()


def solve_owned(run, h, target, native_wh, *, seconds, cancelled, deadline,
                make_job, launch, cpu_entry, identity_control, child_limit, archive_bytes):
    started = time.monotonic()
    end = started + seconds if deadline is None else min(started + seconds, deadline)
    channel = job = identity = handshake = thread = None
    primary = None
    result = {}
    done = threading.Event()
    token = secrets.token_hex(32)
    forced = False
    cleanup_errors = []

    def attempt(step, fn):
        try:
            return fn()
        except Exception as error:
            cleanup_errors.append(dict(step=step, type=type(error).__name__, message=str(error)[:512]))
            return None

    def retain_job(owned):
        nonlocal job
        job = owned

    def communicate():
        try:
            with channel.accept() as conn:
                deliver_challenge(conn, channel.key)
                answer_challenge(conn, channel.key)
                handshake.retain(recv_json(conn))
                send_f64(conn, h, max_bytes=MAX_ARRAY)
                send_f64(conn, target, max_bytes=MAX_ARRAY)
                report = recv_json(conn)
                expected_shape = [(math.ceil(h.shape[1] / 8) + 1) * (math.ceil(h.shape[0] / 8) + 1), 3]
                if (report.get("status") != "PASS" or report.get("shape") != expected_shape
                        or report.get("runtime") != {"numpy": "2.5.3", "scipy": "1.18.1"}):
                    raise ValueError("P3_SOLVER_FAILED")
                b = recv_f64(conn, report["shape"], max_bytes=archive_bytes)
                if hashlib.sha256(b.tobytes()).hexdigest() != report.get("coefficient_sha256"):
                    raise ValueError("P3_SOLVER_HASH_MISMATCH")
                result.update(b=b, report=report)
        except BaseException as error:
            result["error"] = error
        finally:
            done.set()

    settlement_token = secrets.token_hex(16)
    run.mkdir(parents=True, exist_ok=True)
    import os
    with (run / "solver-settlement-ticket.json").open("xb") as stream:
        stream.write(canonical_json({"token": settlement_token}) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())

    try:
        channel = SolverChannel(token)
        job = make_job() if make_job is not None else None
        spec = dict(address=list(channel.address), auth_hex=token, native_wh=list(native_wh),
                    reference_wh=[h.shape[1], h.shape[0]], reference_shape=list(h.shape),
                    identity_control=identity_control, solver_seconds=seconds)
        spec_path = run / "solver-spec.json"
        atomic_json(spec_path, spec)
        job, identity = launch(job, spec_path, retain_job)
        if not cpu_entry:
            atomic_json(run / "solver-process.json", identity)
        handshake = SolverHandshake(run, identity, job, end, cancelled,
                                    entry_file=run / "actual-entry.json" if cpu_entry else None)
        thread = threading.Thread(target=communicate, name="gm-p3-solver-ipc")
        thread.start()
        while not done.wait(.05):
            if cpu_entry:
                handshake.acknowledge_entry()
            else:
                handshake.check()
            if launcher_exit(job) is not None and not done.wait(.2):
                raise RuntimeError("P3_CHILD_EARLY_EXIT")
            job.memory_peak()
        if "error" in result:
            raise result["error"]
        handshake.check()
    except BaseException as error:
        primary = error
        raise
    finally:
        if channel is not None:
            attempt("listener_close", channel.close_listener)
        if primary is not None and job is not None and job.child:
            # A failed poll is not evidence of exit. Terminate the owned Job in that case.
            observed = attempt("launcher_poll", lambda: launcher_exit(job))
            already_empty = attempt("job_empty_before_terminate", lambda: job.wait_empty(0)) if observed is not None else None
            if observed is None or not already_empty:
                forced = True
                attempt("terminate", job.terminate)
        if channel is not None:
            attempt("channel_shutdown", channel.interrupt_channel)
        if thread is not None and thread.ident is not None:
            attempt("ipc_join", lambda: thread.join(10))
        ipc_exited = thread is None or not thread.is_alive()
        code = empty = peak = None
        if job is not None:
            if job.child:
                code = attempt("launcher_wait", lambda: launcher_exit(job, 10000))
            empty = attempt("job_empty", job.wait_empty)
            peak = attempt("memory_peak", job.memory_peak)
        actual_wait = False
        if handshake is not None and ipc_exited:
            actual_wait = attempt("actual_wait", handshake.wait) is True
        actual = handshake.receipt() if handshake is not None and ipc_exited else None
        if job is not None:
            attempt("job_close", job.close)
        if handshake is not None and ipc_exited:
            attempt("actual_close", handshake.close)
        # If the first interrupt failed, termination/Job close may now have unwound the IO.
        if not ipc_exited:
            attempt("channel_shutdown_retry", channel.interrupt_channel)
            attempt("ipc_join_retry", lambda: thread.join(10))
            ipc_exited = not thread.is_alive()
            if ipc_exited and handshake is not None:
                actual_wait = attempt("actual_wait", handshake.wait) is True
                actual = handshake.receipt()
                attempt("actual_close", handshake.close)
        verified = bool(ipc_exited and empty and actual_wait and code is not None)
        receipt = dict(identity=identity, actual_worker_identity=actual["identity"] if actual else None,
                       actual_wait=code is not None, actual_worker_handle_wait=actual_wait, exit_code=code,
                       tree_empty=empty, owned_tree_empty=empty, identity_exited=code is not None,
                       forced=forced, seconds=time.monotonic()-started, peak_job_memory_bytes=peak,
                       peak_tree_rss_bytes=getattr(job, "peak_tree_rss_bytes", None),
                       ram_limit_bytes=child_limit, actual_worker=actual, ipc_thread_exited=ipc_exited,
                       settlement="VERIFIED" if verified else "UNKNOWN",
                       primary_error=type(primary).__name__ if primary else None,
                       cleanup_errors=cleanup_errors)
        receipt["settled_monotonic"] = time.monotonic()
        receipt["attempt_token"] = settlement_token
        pending = PendingSettlement(run, settlement_token, receipt)
        if attempt("receipt_write", pending.persist) is None:
            attempt("receipt_outbox", lambda: save_pending_settlement(pending))
        if primary is not None:
            primary.solver_cleanup = receipt
            primary.solver_settlement = pending
        elif not verified or code != 0 or actual["exit_code"] != 0 or cleanup_errors:
            error = RuntimeError("P3_CHILD_EXIT_INVALID" if not verified or code != 0
                                 or actual["exit_code"] != 0 else "P3_CHILD_CLEANUP_FAILED")
            error.solver_cleanup = receipt
            error.solver_settlement = pending
            raise error
    handshake.check()
    return result["b"], result["report"]
