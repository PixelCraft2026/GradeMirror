"""Bound packaged worker startup independently of synchronous native creation."""
from pathlib import Path
import threading
import time

from ..common import GMError, atomic_json


def start(job, args, cwd, env, work, trace, *, cancel, seconds=30, grace=10, role="r0", launch_profile="default"):
    stop = threading.Event()
    done = threading.Event()
    state = {}
    receipt_path = Path(work)/f"{role}-startup-cancel.json"
    if cancel():
        error = GMError("CANCELLED", f"CPU {role} 启动前已取消。", 8)
        error.startup_no_create = True
        raise error

    def create():
        try:
            state["identity"] = job.spawn(args, cwd, env, launch_trace=trace,
                                          atomic_job=True,
                                          cancel_before_resume=lambda: stop.is_set() or cancel(),
                                          launch_profile=launch_profile)
        except BaseException as error:
            state["error"] = error
        finally:
            done.set()

    thread = threading.Thread(target=create, name=f"cpu-{role}-create", daemon=True)
    thread.start()
    deadline = time.monotonic() + seconds
    reason = None
    while True:
        if cancel():
            reason = "CANCELLED"
            break
        if done.wait(.02):
            if "error" in state:
                raise state["error"]
            return state["identity"]
        if time.monotonic() >= deadline:
            reason = f"OWNED_{role.upper()}_CREATE_TIMEOUT"
            break

    stop.set()
    diagnostic = {"reason": reason, "monotonic": time.monotonic(), "atomic_birth": True}
    try:
        job.terminate()
        diagnostic["job_terminate_returned"] = True
    except Exception as error:
        diagnostic["job_terminate_error"] = str(error)
    error = GMError(reason, f"CPU {role} 创建阶段已停止，正在核对退出。", 8 if reason=="CANCELLED" else 5)

    def settle():
        # The startup thread may still return from CreateProcessW. Only close
        # the Job after that thread releases its use of the native handle.
        done.wait()
        result = dict(diagnostic, startup_thread_returned=True)
        try:
            code = job.wait(10000) if job.child else None
            result.update(actual_exit_code=code, job_empty=job.wait_empty(10000))
            # Child handles rejected inside spawn are tracked separately in its
            # launch trace. No child handle here must not become a false wait.
            result["actual_handle_wait"] = code is not None
        except Exception as failure:
            result.update(cleanup_error=str(failure), job_empty=None, actual_handle_wait=None)
        finally:
            job.close()
        atomic_json(receipt_path, result)

    error.startup_owns_job = True
    if done.wait(grace):
        try:
            settle()
        except Exception as failure:
            error.startup_cleanup_error = str(failure)
    else:
        error.startup_pending = True
        try:
            atomic_json(receipt_path,
                        dict(diagnostic, startup_thread_returned=False, actual_handle_wait=None,
                             job_empty=None, settlement="UNKNOWN"))
        except OSError as failure:
            error.startup_cleanup_error = str(failure)
        threading.Thread(target=settle, name=f"cpu-{role}-create-settle", daemon=True).start()
    raise error
