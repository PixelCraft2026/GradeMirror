"""Lifecycle helpers for the scoped GM064 saved-fine replay CPU worker."""
import time
from pathlib import Path

from .. import winproc as win
from ..common import atomic_json, strict_load
from ..cpu_replay.contract import binding, need

QUALIFICATION = 'T4-C2R.json'
DRY_QUALIFICATION = 'T4-C2Z.json'
QUALIFICATIONS = (QUALIFICATION, DRY_QUALIFICATION)


def enabled(context):
    return bool(context and context.get('phase') == 'replay_fine'
                and Path(context.get('qualification', {}).get('path', '')).name in QUALIFICATIONS)


def _exit_code(handle):
    code = win.DWORD()
    need(win.k.GetExitCodeProcess(handle, win.c.byref(code)), 'C2_CPU_EXIT_CODE', exit_code=9)
    return int(code.value)


def wait_retained(job, handle, identity, *, deadline, forced=False):
    """The caller owns an already checked handle. Never reopen its PID."""
    need(handle and win.identity_key(identity) is not None, 'C2_CPU_RETAINED_HANDLE', exit_code=9)
    started = time.monotonic()
    while True:
        need(time.monotonic() < deadline, 'OWNED_CPU_TIMEOUT', exit_code=9)
        state = win.k.WaitForSingleObject(handle, 0)
        need(state in (win.WAIT_TIMEOUT, win.WAIT_OBJECT_0), 'C2_CPU_ACTUAL_WAIT', exit_code=9)
        if state == win.WAIT_OBJECT_0 and job.poll() is not None and job.wait_empty(0):
            return dict(actual_wait=True, tree_empty=True, exit_code=_exit_code(handle), forced=forced,
                        actual_worker_handle_wait=True, worker_identity=identity,
                        actual_worker_identity=identity, launcher_exit_code=job.poll(),
                        started=started, waited_at=time.monotonic(),
                        peak_job_memory_bytes=job.memory_peak(), peak_tree_rss_bytes=job.peak_tree_rss_bytes,
                        rss_sample_complete=job.rss_sample_complete)
        job.memory_peak()
        time.sleep(.01)


def finish(job, handle, identity, work, terminal_at, absolute_deadline, *, role='cpu'):
    waited = wait_retained(job, handle, identity, deadline=min(terminal_at + 30, absolute_deadline))
    atomic_json(work/'actual-worker-wait.json', waited)
    need(waited['exit_code'] == waited['launcher_exit_code'] == 0, 'CPU_ACTUAL_WAIT', exit_code=9)
    need((work/'runtime-observation.json').is_file() and (work/'result.json').is_file(),
         'C2_CPU_TERMINAL_OBSERVATION_REQUIRED', exit_code=9)
    observation = strict_load(work/'runtime-observation.json')
    result = strict_load(work/'result.json')
    need(observation['role'] == role and observation['status'] == 'PASS'
         and win.identity_key(observation['identity']) == win.identity_key(identity)
         and observation.get('continuous_protection', {}).get('stage') == 'terminal'
         and result['event'] == 'complete' and win.identity_key(result['identity']) == win.identity_key(identity),
         'C2_CPU_TERMINAL_OBSERVATION_REQUIRED', exit_code=9)
    atomic_json(work/'cpu-tail-host.json', dict(received_complete_at=terminal_at,
                waited_at=waited['waited_at'], runtime=binding(work/'runtime-observation.json'),
                final_result=binding(work/'result.json')))
    return waited


def cleanup(job, handle, identity, work, conn, listener, primary, already_waited):
    """Close each owner-held resource once. Cleanup faults never mask the primary."""
    errors = []
    def attempt(label, action):
        try:
            return action()
        except BaseException as error:
            errors.append((label, error))
            return None
    if conn:
        attempt('connection_close', conn.close)
    if listener is not None:
        attempt('listener_close', listener.close)
    if job:
        if not already_waited:
            forced = False
            def settle():
                nonlocal forced
                forced = job.poll() is None or not job.wait_empty(0)
                if forced:
                    job.terminate()
                if handle:
                    return wait_retained(job, handle, identity, deadline=time.monotonic()+30, forced=forced)
                # The launcher is retained by OwnedJob. No worker identity is inferred.
                code = job.wait(30000)
                empty = job.wait_empty(0)
                need(empty, 'C2_CPU_CLEANUP_TREE', exit_code=9)
                return dict(actual_wait=True, tree_empty=empty, launcher_exit_code=code, forced=forced,
                            actual_worker_handle_wait=None, worker_identity=None)
            waited = attempt('job_cleanup_wait', settle)
        else:
            waited = None
        attempt('job_close', job.close)
    else:
        waited = None
    if handle:
        attempt('worker_handle_close', lambda: need(win.k.CloseHandle(handle), 'C2_CPU_HANDLE_CLOSE', exit_code=9))
    if primary is not None or errors:
        attempt('cleanup_receipt', lambda: atomic_json(work/'cpu-tail-cleanup.json', dict(
            at=time.monotonic(), primary=None if primary is None else getattr(primary, 'code', type(primary).__name__),
            wait=waited, errors=[dict(stage=label, code=getattr(error,'code',type(error).__name__), message=str(error))
                                 for label,error in errors])))
        if primary is not None:
            for label, error in errors:
                primary.add_note(f'CPU tail cleanup {label} {error!r}')
        elif errors:
            raise errors[0][1]
