"""Start the packaged I/O host on behalf of its verified session owner.

The GUI duplicates the owner's finite Job. Windows assigns the suspended
child to that same object at birth. The owner retains the exact child
handle before the broker relinquishes its duplicate.
"""
import ctypes as c
from pathlib import Path
import threading
import time

from ..common import GMError, atomic_json, strict_load
from ..cpu_replay.contract import need
from .. import winproc as win
from .r0_startup import start


def _key(value):
    key = win.identity_key(value)
    need(key is not None, "HOST_BROKER_IDENTITY")
    return key


def _stop(session):
    return (session.parent / "cancel.signal").is_file() or (session / "stop.signal").is_file()


def _io_executable():
    from ..light.runtime import bundle_exe
    return bundle_exe("io").resolve()


def _identity_handle(who):
    h = win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION | win.SYNCHRONIZE, False, who["pid"])
    need(h, "HOST_BROKER_HANDLE")
    try:
        need(_key(win._identity_from_handle(h, who["pid"])) == _key(who), "HOST_BROKER_HANDLE_IDENTITY")
        return h
    except BaseException:
        win.close(h)
        raise


def _duplicate_job(owner_process, handle, limit):
    api = win.k
    api.GetCurrentProcess.restype = win.HANDLE
    api.DuplicateHandle.argtypes = [win.HANDLE, win.HANDLE, win.HANDLE, c.POINTER(win.HANDLE),
                                   win.DWORD, c.c_int, win.DWORD]
    api.DuplicateHandle.restype = c.c_int
    duplicate = win.HANDLE()
    need(type(handle) is int and handle > 0, "HOST_BROKER_JOB_HANDLE")
    if not api.DuplicateHandle(int(owner_process._handle), handle, api.GetCurrentProcess(),
                               c.byref(duplicate), 0, False, 2):
        raise c.WinError(c.get_last_error())
    job = win.OwnedJob.__new__(win.OwnedJob)
    job.handle = duplicate.value
    job.child = None
    job.peak_job_memory_bytes = 0
    job.peak_tree_rss_bytes = 0
    job.rss_sample_complete = True
    try:
        info = win.EXTENDED_LIMIT()
        need(api.QueryInformationJobObject(job.handle, win.JobObjectExtendedLimitInformation,
                                           c.byref(info), c.sizeof(info), None), "HOST_BROKER_JOB_TYPE")
        flags = win.JOB_OBJECT_LIMIT_JOB_MEMORY | win.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        need(info.BasicLimitInformation.LimitFlags == flags and info.JobMemoryLimit == limit and
             job.wait_empty(0), "HOST_BROKER_JOB_CONTRACT")
        return job
    except BaseException:
        job.close()
        raise


def serve(process, owner, spec, session, plan):
    session = Path(session)
    intent = session / "host-birth-intent.json"
    receipt = session / "host-birth-result.json"
    start_ack = session / "owner-launch-ack.json"
    until = time.monotonic() + 35
    job = None
    native_started = False
    who = None
    row = None
    events = []
    work = session / "host"
    try:
        while not intent.is_file():
            if process.poll() is not None or time.monotonic() >= until:
                return
            time.sleep(.015)
        row = strict_load(intent)
        need(row["token"] == spec["pool_token"] and row["attempt"] == spec["attempt_id"] and
             row["plan_id"] == plan["plan_id"] and _key(row["owner"]) == _key(owner) and
             _key(win._identity_from_handle(int(process._handle), process.pid)) == _key(owner),
             "HOST_BROKER_INTENT_IDENTITY")
        need(start_ack.is_file() and strict_load(start_ack)["retained_before_ack"] is True,
             "HOST_BROKER_OWNER_ACK")
        job = _duplicate_job(process, row["job_handle"], plan["selection"]["jobs"]["pool"]["pool"])
        from ..light.runtime import ROOT, worker
        payload = session / "host-payload.json"
        need(strict_load(payload) == {"session": str(session)}, "HOST_BROKER_PAYLOAD")
        args, env = worker("cpu_host", ("host", payload), work, value=plan)
        def trace(stage, fields):
            events.append(dict(stage=stage, monotonic=time.monotonic(), caller=threading.get_native_id(), **fields))
            atomic_json(work / "host-native-launch.json", dict(role="host", broker=win.current_identity(),
                                                               events=events))
        def cancelled():
            return process.poll() is not None or _stop(session)
        if cancelled():
            atomic_json(receipt, dict(state="NO_CREATE", token=row["token"]))
            return
        native_started = True
        who = start(job, args, str(ROOT), env, work, trace, cancel=cancelled, role="host")
        atomic_json(receipt, dict(state="LAUNCHED", token=row["token"], identity=who))
        ack = session / "host-birth-ack.json"
        end = time.monotonic() + 10
        while not ack.is_file():
            need(not cancelled() and time.monotonic() < end, "HOST_BROKER_HANDOFF_TIMEOUT")
            time.sleep(.015)
        saved = strict_load(ack)
        need(saved["token"] == row["token"] and _key(saved["identity"]) == _key(who) and
             _key(saved["owner"]) == _key(owner), "HOST_BROKER_HANDOFF_ID")
        atomic_json(session / "host-birth-handoff.json", dict(state="OWNER_RETAINED", token=row["token"],
                    owner=owner, identity=who, broker=win.current_identity()))
    except BaseException as error:
        pending = getattr(error, "startup_owns_job", False)
        native_pending = bool(getattr(error, "startup_pending", False))
        settlement = None
        early_wait = None
        native_failed = any(item["stage"] == "create_failed" for item in events)
        try:
            if job is not None and not pending and job.child:
                if job.poll() is None:
                    job.terminate()
                settlement = dict(exit_code=job.wait(10000), tree_empty=job.wait_empty(10000), actual_wait=True)
                if who and settlement["tree_empty"] and not (session / "host-birth-ack.json").is_file():
                    early_wait = dict(identity=who, exit_code=settlement["exit_code"], actual_wait=True,
                                      tree_empty=True, before_owner_handoff=True)
            if not native_pending:
                early = next((item for item in reversed(events) if item["stage"] == "launch_failure_cleanup"), None)
                if early and early["actual_wait"] and win.identity_key(early.get("identity")) is not None:
                    cancel_path = work / "host-startup-cancel.json"
                    empty = (strict_load(cancel_path)["job_empty"] if pending and cancel_path.is_file()
                             else job.wait_empty(10000) if not pending and job is not None else None)
                    if empty is True:
                        early_wait = dict(identity=early["identity"], exit_code=early["exit_code"],
                                          actual_wait=True, tree_empty=True, before_owner_handoff=True)
        except BaseException as cleanup:
            settlement = dict(error=repr(cleanup), actual_wait=None, tree_empty=None)
        finally:
            # The startup thread owns its duplicate while the native call is pending.
            if pending:
                job = None
            atomic_json(session / "host-birth-error.json", dict(error=repr(error), settlement=settlement,
                        startup_pending=native_pending,
                        native_started=native_started and not getattr(error, "startup_no_create", False),
                        native_failed=native_failed,
                        early_wait=early_wait, token=spec["pool_token"], attempt=spec["attempt_id"], plan_id=plan["plan_id"]))
    finally:
        if job is not None:
            job.close()


def request(job, session, plan):
    session = Path(session)
    spec = strict_load(session / "session-spec.json")
    owner = win.current_identity()
    token = spec["pool_token"]
    atomic_json(session / "host-birth-intent.json", dict(token=token, attempt=spec["attempt_id"],
                plan_id=plan["plan_id"], owner=owner, job_handle=int(job.handle), monotonic=time.monotonic()))
    receipt = session / "host-birth-result.json"
    error_file = session / "host-birth-error.json"
    until = time.monotonic() + 48
    confirmed_no_create = False
    early_exit = None
    try:
        while not receipt.is_file():
            if error_file.is_file():
                failure = strict_load(error_file)
                need((failure["token"], failure["attempt"], failure["plan_id"]) ==
                     (token, spec["attempt_id"], plan["plan_id"]), "HOST_BROKER_ERROR_TOKEN")
                confirmed_no_create = failure["native_started"] is False or failure["native_failed"] is True
                proof = failure["early_wait"]
                if (proof and proof["actual_wait"] is True and proof["tree_empty"] is True and
                    proof["before_owner_handoff"] is True and win.identity_key(proof["identity"]) is not None and
                    Path(proof["identity"]["image_path"]).resolve() == _io_executable()):
                    if job.wait_empty(0) and win.identity_alive(proof["identity"]) is False:
                        early_exit = proof
                raise GMError("HOST_BROKER_START_FAILED", failure["error"], 9)
            need(time.monotonic() < until, "HOST_BROKER_RESULT_UNSETTLED")
            time.sleep(.015)
        result = strict_load(receipt)
        need(result["token"] == token, "HOST_BROKER_RECEIPT_TOKEN")
        if result["state"] == "NO_CREATE":
            confirmed_no_create = True
            raise GMError("CANCELLED", "CPU I/O 启动前已取消", 8)
        need(result["state"] == "LAUNCHED", "HOST_BROKER_RESULT_STATE")
        who = result["identity"]
        h = _identity_handle(who)
        try:
            need(job.member(who["pid"]), "HOST_BROKER_MEMBERSHIP")
            atomic_json(session / "host-birth-ack.json", dict(token=token, owner=owner, identity=who))
        except BaseException:
            win.close(h)
            raise
        job.child = h
        return who
    except BaseException as error:
        if early_exit is not None:
            error.startup_exit_proof = early_exit
        elif not confirmed_no_create:
            error.startup_pending = True
            try:
                job.terminate()
            except Exception as cleanup:
                error.add_note(f"host Job terminate: {cleanup!r}")
        raise
