"""Independent portable owner: exact host Job, bounded stop and expiry."""
import time
from pathlib import Path
from ..common import strict_load, atomic_json
from .. import winproc as win
from . import runtime, lifecycle as life
from .contract import need, worker_authority, io_memory_sample


def main(session):
    session = Path(session)
    runtime.enter('owner', session)
    spec = worker_authority(session)
    owner = win.current_identity()
    atomic_json(session / 'owner.json', owner)
    job = actual_handle = None
    actual = host = None
    samples = []
    last_sample = -1.
    stop_since = None
    forced = False
    failure = None
    host_startup_pending = False
    early_host_exit = None
    broker_child_retained = False
    c = life.context(spec)
    if spec['semantics'].get('cpu_refine') is True and 'preview_version' not in spec['semantics']:
        runtime.require_runtime(session)
    grace = c['force_grace_seconds'] if c else 30
    try:
        until = time.monotonic()+30
        while not (session / 'owner-launch-ack.json').is_file():
            if life.stopping(session, spec):
                return 8
            need(time.monotonic() < until, 'OWNER_LAUNCH_ACK_TIMEOUT')
            time.sleep(.02)
        payload = session / 'host-payload.json'
        atomic_json(payload, dict(session=str(session)))
        job, host = runtime.launch('host', payload, session / 'host', memory=3*1024**3 if c and c.get('TEST_ONLY') else 6*1024**3)
        atomic_json(session / "host-process.json", host)
        if (session / "host-birth-ack.json").is_file():
            broker_child_retained = (win.identity_key(win._identity_from_handle(job.child, host["pid"])) ==
                                     win.identity_key(host))
        while job.poll() is None:
            if actual_handle is None and (session / 'host-entry.json').is_file():
                actual = strict_load(session / 'host-entry.json')
                actual_handle = win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION | win.SYNCHRONIZE, False, actual['pid'])
                need(actual_handle and win.identity_key(win.process_identity(actual['pid'])) == win.identity_key(actual), 'HOST_ACTUAL_IDENTITY')
                atomic_json(session / 'host-retained.json', dict(identity=actual, retained=True))
            job.memory_peak()
            now = time.monotonic()
            if actual_handle and now-last_sample >= 1 and win.k.WaitForSingleObject(actual_handle, 0) == win.WAIT_TIMEOUT:
                try:
                    sample = io_memory_sample(actual)
                except BaseException as e:
                    if getattr(e, 'code', None) == 'IO_RESOURCE_UNKNOWN' and win.k.WaitForSingleObject(actual_handle, 0) == win.WAIT_OBJECT_0:
                        samples.append(dict(monotonic=now, observation='RACED_CONFIRMED_PROCESS_EXIT'))
                        continue
                    raise
                samples.append(dict(monotonic=now, **sample))
                atomic_json(session / 'IO-memory.json', dict(samples=samples))
                last_sample = now
            reason = life.stopping(session, spec)
            if (session / 'host-ready.json').is_file():
                life.checkpoint(session.parent, 'owner_after_ready')
            if reason:
                if stop_since is None:
                    stop_since = now
                    atomic_json(session / 'stop.signal', dict(reason=reason))
                if now-stop_since >= grace:
                    forced = True
                    job.terminate()
            time.sleep(.02)
        return 0 if job.poll() == 0 else 9
    except BaseException as e:
        failure = repr(e)
        host_startup_pending = getattr(e, "startup_pending", False)
        early_host_exit = getattr(e, "startup_exit_proof", None)
        life.diagnostic(session, e)
        if job and job.poll() is None:
            atomic_json(session / 'stop.signal', dict(reason='OWNER_EXCEPTION'))
            until = time.monotonic()+grace
            while job.poll() is None and time.monotonic() < until:
                time.sleep(.02)
            if job.poll() is None:
                forced = True
                job.terminate()
        raise
    finally:
        try:
            if job:
                waited = runtime.wait(job, session / 'host', timeout=30)
                ack_observed = actual_handle is not None
                actual_wait = bool(actual_handle and win.k.WaitForSingleObject(actual_handle, 30000) == win.WAIT_OBJECT_0)
                if not ack_observed and broker_child_retained:
                    actual = host
                    actual_wait = waited["actual_wait"]
                atomic_json(session / 'owner-cleanup.json', dict(owner=owner, host=host,
                    actual_host_identity=actual, actual_host_handle_wait=actual_wait, host_ack_observed=ack_observed,
                    actual_wait=waited['actual_wait'], owned_tree_empty=waited['tree_empty'],
                    session_id=spec['session_id'], host_exit_code=waited['exit_code'], forced=forced,
                    host_normal_tail=(session / 'host/normal-tail.json').is_file(), failure=failure,
                    peak_tree_rss_bytes=job.peak_tree_rss_bytes, peak_job_memory_bytes=job.memory_peak()))
            elif early_host_exit is not None:
                atomic_json(session / "owner-cleanup.json", dict(owner=owner,
                    host=early_host_exit["identity"], actual_host_identity=early_host_exit["identity"],
                    actual_host_handle_wait=True, actual_wait=True, owned_tree_empty=True,
                    session_id=spec["session_id"], forced=True, host_normal_tail=False,
                    host_ack_observed=False, host_exit_code=early_host_exit["exit_code"],
                    failure=failure, startup_exit_proof=early_host_exit))
            elif host_startup_pending:
                atomic_json(session / "owner-cleanup.json", dict(owner=owner, host_never_started=None,
                    actual_wait=None, owned_tree_empty=None, session_id=spec["session_id"],
                    forced=False, host_normal_tail=False, failure=failure, startup_settlement="UNKNOWN"))
            else:
                atomic_json(session / 'owner-cleanup.json', dict(owner=owner, host_never_started=True,
                    actual_wait=False, owned_tree_empty=True, session_id=spec['session_id'],
                    forced=False, host_normal_tail=False, failure=failure))
            runtime.observation(session, 'owner', 'FAILED' if failure else 'EXITED')
        finally:
            if actual_handle:
                win.close(actual_handle)
            if job:
                job.close()
