"""Windows-only owned process container. Job handles never inherit into children."""

from __future__ import annotations

import ctypes as c
import os
import subprocess
import time
from ctypes import wintypes as w

from .common import GMError


if os.name != "nt":
    raise RuntimeError("GM-001 Windows process owner requires Windows")

k = c.WinDLL("kernel32", use_last_error=True)
psapi = c.WinDLL("psapi", use_last_error=True)
HANDLE = w.HANDLE
DWORD = w.DWORD
SIZE_T = c.c_size_t
k.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
k.CreateJobObjectW.restype = HANDLE
k.OpenProcess.argtypes = [DWORD, w.BOOL, DWORD]
k.OpenProcess.restype = HANDLE
k.CloseHandle.argtypes = [HANDLE]
k.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL,
                             DWORD, c.c_void_p, w.LPCWSTR, c.c_void_p, c.c_void_p]
k.CreateProcessW.restype = w.BOOL
k.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
k.SetInformationJobObject.argtypes = [HANDLE, c.c_int, c.c_void_p, DWORD]
k.QueryInformationJobObject.argtypes = [HANDLE, c.c_int, c.c_void_p, DWORD, c.c_void_p]
k.IsProcessInJob.argtypes = [HANDLE, HANDLE, c.c_void_p]
k.ResumeThread.argtypes = [HANDLE]
k.TerminateJobObject.argtypes = [HANDLE, c.c_uint]
k.TerminateProcess.argtypes = [HANDLE, c.c_uint]
k.ResumeThread.restype = DWORD
k.WaitForSingleObject.restype = DWORD
k.WaitForSingleObject.argtypes = [HANDLE, DWORD]
k.QueryFullProcessImageNameW.argtypes = [HANDLE, DWORD, w.LPWSTR, c.POINTER(DWORD)]
k.GetProcessTimes.argtypes = [HANDLE, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p]
k.GetExitCodeProcess.argtypes = [HANDLE, c.POINTER(DWORD)]
psapi.GetProcessMemoryInfo.argtypes = [HANDLE, c.c_void_p, DWORD]
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_UNICODE_ENVIRONMENT = 0x00000400


def creation_flags(launch_profile='default', *, unicode_environment=False):
    """Explicit console policy; default callers retain their original flags."""
    if type(launch_profile) is not str or launch_profile not in ('default', 'detached'):
        raise ValueError('OWNED_JOB_LAUNCH_PROFILE')
    flags = CREATE_SUSPENDED | (DETACHED_PROCESS if launch_profile == 'detached' else CREATE_NO_WINDOW)
    return flags | (CREATE_UNICODE_ENVIRONMENT if unicode_environment else 0)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
JobObjectExtendedLimitInformation = 9


class FILETIME(c.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class STARTUPINFO(c.Structure):
    _fields_ = [("cb", DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", DWORD), ("dwY", DWORD),
                ("dwXSize", DWORD), ("dwYSize", DWORD), ("dwXCountChars", DWORD),
                ("dwYCountChars", DWORD), ("dwFillAttribute", DWORD), ("dwFlags", DWORD),
                ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                ("lpReserved2", c.POINTER(c.c_byte)), ("hStdInput", HANDLE),
                ("hStdOutput", HANDLE), ("hStdError", HANDLE)]


class STARTUPINFOEX(c.Structure):
    _fields_ = [("info", STARTUPINFO), ("attributes", c.c_void_p)]


def _job_startup(job_handle):
    init = k.InitializeProcThreadAttributeList
    init.argtypes = [c.c_void_p, DWORD, DWORD, c.POINTER(SIZE_T)]
    init.restype = w.BOOL
    update = k.UpdateProcThreadAttribute
    update.argtypes = [c.c_void_p, DWORD, SIZE_T, c.c_void_p, SIZE_T, c.c_void_p, c.c_void_p]
    update.restype = w.BOOL
    delete = k.DeleteProcThreadAttributeList
    delete.argtypes = [c.c_void_p]
    delete.restype = None
    needed = SIZE_T()
    init(None, 1, 0, c.byref(needed))
    if not needed.value:
        _err("InitializeProcThreadAttributeList size")
    storage = c.create_string_buffer(needed.value)
    if not init(storage, 1, 0, c.byref(needed)):
        _err("InitializeProcThreadAttributeList")
    handles = (HANDLE * 1)(job_handle)
    if not update(storage, 0, 0x0002000D, handles, c.sizeof(handles), None, None):
        error = c.get_last_error()
        delete(storage)
        raise c.WinError(error)
    startup = STARTUPINFOEX()
    startup.info.cb = c.sizeof(startup)
    startup.attributes = c.cast(storage, c.c_void_p)
    return startup, storage, handles, delete


class PROCESS_INFORMATION(c.Structure):
    _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE),
                ("dwProcessId", DWORD), ("dwThreadId", DWORD)]


class BASIC_LIMIT(c.Structure):
    _fields_ = [("PerProcessUserTimeLimit", c.c_int64), ("PerJobUserTimeLimit", c.c_int64),
                ("LimitFlags", DWORD), ("MinimumWorkingSetSize", SIZE_T),
                ("MaximumWorkingSetSize", SIZE_T), ("ActiveProcessLimit", DWORD),
                ("Affinity", SIZE_T), ("PriorityClass", DWORD),
                ("SchedulingClass", DWORD)]


class IO_COUNTERS(c.Structure):
    _fields_ = [(name, c.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class EXTENDED_LIMIT(c.Structure):
    _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T),
                ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]


class PROCESS_MEMORY_COUNTERS(c.Structure):
    _fields_ = [("cb", DWORD), ("PageFaultCount", DWORD), ("PeakWorkingSetSize", SIZE_T),
                ("WorkingSetSize", SIZE_T), ("QuotaPeakPagedPoolUsage", SIZE_T),
                ("QuotaPagedPoolUsage", SIZE_T), ("QuotaPeakNonPagedPoolUsage", SIZE_T),
                ("QuotaNonPagedPoolUsage", SIZE_T), ("PagefileUsage", SIZE_T),
                ("PeakPagefileUsage", SIZE_T)]


class BASIC_ACCOUNTING(c.Structure):
    _fields_ = [(name, c.c_int64) for name in ("TotalUserTime", "TotalKernelTime",
                "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [
                (name, DWORD) for name in ("TotalPageFaultCount", "TotalProcesses",
                "ActiveProcesses", "TotalTerminatedProcesses")]


class PROCESS_MEMORY_COUNTERS_EX(c.Structure):
    _fields_ = PROCESS_MEMORY_COUNTERS._fields_ + [("PrivateUsage", SIZE_T)]


def identity_key(identity):
    """Canonical exact identity; extra receipt metadata does not change it."""
    if (not isinstance(identity,dict)or identity.get('unknown')or
            type(identity.get('pid'))is not int or type(identity.get('creation_time'))is not int or
            not isinstance(identity.get('image_path'),str)or not identity['image_path']):return None
    return identity['pid'],identity['creation_time'],os.path.normcase(os.path.normpath(identity['image_path']))


def process_memory(identity):
    """Current counters from one retained handle; never historical peaks."""
    unknown = dict(rss_bytes=None, private_commit_bytes=None)
    if identity_key(identity)is None:return unknown
    h = k.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | SYNCHRONIZE, False, identity['pid'])
    if not h:return unknown
    try:
        if identity_key(_identity_from_handle(h, identity['pid'])) != identity_key(identity):return unknown
        info = PROCESS_MEMORY_COUNTERS_EX();info.cb = c.sizeof(info)
        if not psapi.GetProcessMemoryInfo(h,c.byref(info),info.cb):return unknown
        if identity_key(_identity_from_handle(h, identity['pid'])) != identity_key(identity):return unknown
        return dict(rss_bytes=int(info.WorkingSetSize),private_commit_bytes=int(info.PrivateUsage))
    finally:close(h)


def process_peak_memory(identity):
    """Read OS lifetime peaks from an exact live process; no limit changes.

    PeakPagefileUsage is the OS pagefile/commit accounting peak, not physical
    RAM, current PrivateUsage or a same-time tree peak. Unknown stays None.
    The original process_memory API above deliberately remains unchanged.
    """
    unknown=dict(peak_working_set_bytes=None,peak_pagefile_usage_bytes=None,
                 peak_source='GetProcessMemoryInfo/lifetime',known=False)
    if identity_key(identity)is None:return unknown
    h=k.OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ|SYNCHRONIZE,False,identity['pid'])
    if not h:return unknown
    try:
        if identity_key(_identity_from_handle(h,identity['pid']))!=identity_key(identity):return unknown
        info=PROCESS_MEMORY_COUNTERS_EX();info.cb=c.sizeof(info)
        if not psapi.GetProcessMemoryInfo(h,c.byref(info),info.cb):return unknown
        if identity_key(_identity_from_handle(h,identity['pid']))!=identity_key(identity):return unknown
        return dict(peak_working_set_bytes=int(info.PeakWorkingSetSize),
            peak_pagefile_usage_bytes=int(info.PeakPagefileUsage),
            peak_source='GetProcessMemoryInfo/lifetime',known=True)
    finally:close(h)


def _err(label: str):
    raise OSError(c.get_last_error(), label)


def close(handle):
    if handle:
        k.CloseHandle(handle)


def _identity_from_handle(h, pid):
    state = k.WaitForSingleObject(h, 0)
    if state == WAIT_OBJECT_0:
        return None
    if state != WAIT_TIMEOUT:
        return {"unknown": True, "pid": pid}
    creation, exit_time, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    if not k.GetProcessTimes(h, c.byref(creation), c.byref(exit_time), c.byref(kernel), c.byref(user)):
        return {"unknown": True, "pid": pid}
    size = DWORD(32768)
    buf = c.create_unicode_buffer(size.value)
    if not k.QueryFullProcessImageNameW(h, 0, buf, c.byref(size)):
        return {"unknown": True, "pid": pid}
    return {"pid": pid, "creation_time": (creation.high << 32) | creation.low,
            "image_path": buf.value}


def process_identity(pid: int):
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
    if not h:
        error = c.get_last_error()
        if error == 87 or error == 1168:
            return None
        return {"unknown": True, "pid": pid, "winerror": error}
    try:
        return _identity_from_handle(h, pid)
    finally:
        close(h)


def identity_alive(identity: dict):
    actual = process_identity(identity["pid"])
    if actual is None:
        return False
    if actual.get("unknown"):
        return None
    return (actual["creation_time"] == identity["creation_time"] and
            os.path.normcase(actual["image_path"]) == os.path.normcase(identity["image_path"]))


def current_identity():
    return process_identity(os.getpid())


def process_rss(pid: int):
    h = k.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return None
    try:
        info = PROCESS_MEMORY_COUNTERS()
        info.cb = c.sizeof(info)
        if not psapi.GetProcessMemoryInfo(h, c.byref(info), info.cb):
            return None
        return int(info.WorkingSetSize)
    finally:
        close(h)


def terminate_verified(identity: dict, code: int = 99):
    h = k.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                      False, identity["pid"])
    if not h:
        _err("OpenProcess terminate")
    try:
        actual = _identity_from_handle(h, identity["pid"])
        if (not actual or actual.get("unknown") or
            actual["creation_time"] != identity.get("creation_time") or
            os.path.normcase(actual["image_path"]) != os.path.normcase(identity.get("image_path", ""))):
            raise GMError("PROCESS_IDENTITY_MISMATCH", "Refusing to terminate process", 9)
        if not k.TerminateProcess(h, code):
            _err("TerminateProcess")
        if k.WaitForSingleObject(h, 10000) != WAIT_OBJECT_0:
            _err("WaitForSingleObject terminate")
    finally:
        close(h)


class OwnedJob:
    def __init__(self, ram_limit_bytes: int):
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            _err("CreateJobObjectW")
        info = EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if ram_limit_bytes:
            info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = int(ram_limit_bytes)
        if not k.SetInformationJobObject(self.handle, JobObjectExtendedLimitInformation,
                                        c.byref(info), c.sizeof(info)):
            close(self.handle)
            _err("SetInformationJobObject")
        self.child = None
        self.peak_job_memory_bytes = 0
        self.peak_tree_rss_bytes = 0
        self.rss_sample_complete = True

    def spawn(self, argv: list[str], cwd: str, env: dict[str, str] | None = None, *, launch_profile='default', launch_trace=None,
              atomic_job=False, cancel_before_resume=None):
        flags = creation_flags(launch_profile, unicode_environment=env is not None)
        if self.child:
            raise RuntimeError("One root worker per job")
        if os.environ.get('GM063_LAUNCH_CONFIG'):
            from .adaptive_execution.calibration_launch import declare
            declare(argv,cwd)
        si = STARTUPINFO()
        si.cb = c.sizeof(si)
        pi = PROCESS_INFORMATION()
        command = c.create_unicode_buffer(subprocess.list2cmdline(argv))
        environment = None
        if env is not None:
            environment = c.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0")
        def trace(stage, **fields):
            if launch_trace is not None:
                launch_trace(stage, fields)

        attributes = None
        if atomic_job:
            si, storage, handles, delete = _job_startup(self.handle)
            attributes = storage
            flags |= 0x00080000  # EXTENDED_STARTUPINFO_PRESENT
        try:
            trace("create_before", flags=flags, atomic_job=atomic_job)
            created = k.CreateProcessW(argv[0], command, None, None, False,
                                       flags, c.cast(environment, c.c_void_p) if environment is not None else None, cwd,
                                       c.byref(si), c.byref(pi))
            create_error = c.get_last_error()
        finally:
            if attributes is not None:
                delete(attributes)
        if not created:
            trace("create_failed", winerror=create_error)
            raise c.WinError(create_error)
        birth = None
        try:
            birth = _identity_from_handle(pi.hProcess, pi.dwProcessId)
            trace("create_returned", pid=pi.dwProcessId, tid=pi.dwThreadId, identity=birth)
            if not atomic_job:
                trace("assign_before")
                assigned = k.AssignProcessToJobObject(self.handle, pi.hProcess)
                if not assigned:
                    assign_error = c.get_last_error()
                    trace("assign_failed", winerror=assign_error)
                    raise c.WinError(assign_error)
                trace("assign_returned")
            member = w.BOOL()
            if not k.IsProcessInJob(pi.hProcess, self.handle, c.byref(member)) or not member.value:
                _err("IsProcessInJob")
            trace("member_verified")
            identity = _identity_from_handle(pi.hProcess, pi.dwProcessId)
            if not identity or identity.get("unknown"):
                _err("worker identity")
            trace("identity_verified", identity=identity)
            if cancel_before_resume is not None and cancel_before_resume():
                raise InterruptedError("OWNED_R0_CANCEL_BEFORE_RESUME")
            trace("resume_before")
            previous = k.ResumeThread(pi.hThread)
            if previous == 0xffffffff:
                resume_error = c.get_last_error()
                trace("resume_failed", winerror=resume_error)
                raise c.WinError(resume_error)
            trace("resume_returned", previous_suspend_count=previous)
            self.child = pi.hProcess
            return {**identity, "job_member": True}
        except BaseException:
            try:
                terminated = bool(k.TerminateProcess(pi.hProcess, 9))
                waited = k.WaitForSingleObject(pi.hProcess, 10000) == WAIT_OBJECT_0
                code = DWORD()
                exit_code = code.value if waited and k.GetExitCodeProcess(pi.hProcess, c.byref(code)) else None
                try:
                    trace("launch_failure_cleanup", terminated=terminated, actual_wait=waited,
                          exit_code=exit_code, identity=birth)
                except Exception:
                    pass
            finally:
                close(pi.hProcess)
            raise
        finally:
            close(pi.hThread)

    def poll(self):
        if not self.child:
            raise RuntimeError("Worker not started")
        state = k.WaitForSingleObject(self.child, 0)
        if state == WAIT_TIMEOUT:
            return None
        if state != WAIT_OBJECT_0:
            _err("WaitForSingleObject poll")
        exit_code = DWORD()
        if not k.GetExitCodeProcess(self.child, c.byref(exit_code)):
            _err("GetExitCodeProcess")
        self.memory_peak()
        return int(exit_code.value)

    def memory_peak(self):
        info = EXTENDED_LIMIT()
        length = DWORD()
        if k.QueryInformationJobObject(self.handle, JobObjectExtendedLimitInformation,
                                       c.byref(info), c.sizeof(info), c.byref(length)):
            self.peak_job_memory_bytes = max(self.peak_job_memory_bytes, int(info.PeakJobMemoryUsed))
        buf = c.create_string_buffer(65536)
        length = DWORD()
        if k.QueryInformationJobObject(self.handle, 3, buf, c.sizeof(buf), c.byref(length)):
            count = DWORD.from_buffer_copy(buf.raw[4:8]).value
            count = min(count, (c.sizeof(buf) - 8) // c.sizeof(SIZE_T))
            rss = process_rss(os.getpid())
            if rss is None:
                self.rss_sample_complete = False
                rss = 0
            for i in range(count):
                offset = 8 + i * c.sizeof(SIZE_T)
                pid = SIZE_T.from_buffer_copy(buf.raw[offset:offset + c.sizeof(SIZE_T)]).value
                value = process_rss(pid)
                if value is None:
                    self.rss_sample_complete = False
                else:
                    rss += value
            self.peak_tree_rss_bytes = max(self.peak_tree_rss_bytes, rss)
        else:
            self.rss_sample_complete = False
        return self.peak_job_memory_bytes

    def _member_pids(self):
        """One Job query; a failed/short enumeration never becomes an empty Job."""
        buf=c.create_string_buffer(65536)
        if not k.QueryInformationJobObject(self.handle,3,buf,c.sizeof(buf),None):
            error=c.get_last_error()
            return dict(query_success=False,enumeration_complete=False,reported_count=None,returned_count=None,pids=[],reason='buffer_insufficient'if error==234 else'query_failed',winerror=error)
        reported=DWORD.from_buffer_copy(buf.raw[:4]).value
        count=DWORD.from_buffer_copy(buf.raw[4:8]).value
        if count>(c.sizeof(buf)-8)//c.sizeof(SIZE_T):
            return dict(query_success=True,enumeration_complete=False,reported_count=reported,returned_count=count,pids=[],reason='buffer_insufficient')
        pids=[]
        for i in range(count):
            offset=8+i*c.sizeof(SIZE_T)
            pid=SIZE_T.from_buffer_copy(buf.raw[offset:offset+c.sizeof(SIZE_T)]).value
            pids.append(pid)
        return dict(query_success=True,enumeration_complete=reported==count,reported_count=reported,returned_count=count,pids=pids,reason=None if reported==count else'partial_enumeration')

    def _verify_member(self,identity):
        h=k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,False,identity['pid'])
        if not h:return False,'member_handle_unavailable'
        try:
            if identity_key(_identity_from_handle(h,identity['pid']))!=identity_key(identity):return False,'identity_changed_or_exited'
            member=w.BOOL()
            if not k.IsProcessInJob(h,self.handle,c.byref(member)):return False,'membership_query_failed'
            if not member.value:return False,'not_current_job_member'
            if identity_key(_identity_from_handle(h,identity['pid']))!=identity_key(identity):return False,'identity_changed_or_exited'
            return True,None
        finally:close(h)

    def member_snapshot(self):
        query=self._member_pids();known=[];unresolved=[];seen=set()
        for pid in query['pids']:
            if pid in seen:unresolved.append(dict(pid=pid,reason='duplicate_enumerated_pid'));continue
            seen.add(pid);who=process_identity(pid)
            if identity_key(who)is None:
                unresolved.append(dict(pid=pid,reason='identity_unavailable_or_exited'if who is None else'identity_unknown',detail=who));continue
            valid,reason=self._verify_member(who)
            if valid:known.append(who)
            else:unresolved.append(dict(pid=pid,reason=reason))
        if not query['enumeration_complete']:
            missing=None if query['reported_count']is None else max(1,query['reported_count']-len(query['pids']))
            unresolved.append(dict(pid=None,reason=query['reason'],count=missing))
        return dict(schema='gm010-members-v1',query_success=query['query_success'],enumeration_complete=query['enumeration_complete'],reported_count=query['reported_count'],returned_count=query['returned_count'],known_identities=known,unresolved=unresolved,complete=bool(query['query_success']and query['enumeration_complete']and not unresolved),query_reason=query['reason'])

    def identities(self):
        """Legacy interface: incomplete membership is unknown, never a short list."""
        snapshot=self.member_snapshot()
        return snapshot['known_identities']if snapshot['complete']else None

    def terminate(self):
        if not k.TerminateJobObject(self.handle, 8):
            _err("TerminateJobObject")

    def wait(self, ms: int = 10000):
        if self.child and k.WaitForSingleObject(self.child, ms) != WAIT_OBJECT_0:
            raise GMError("OWNED_PROCESS_WAIT_TIMEOUT", "Worker did not exit", 9)
        return self.poll() if self.child else None

    def member(self, pid: int):
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
        if not h:
            return False
        try:
            member = w.BOOL()
            return bool(k.IsProcessInJob(h, self.handle, c.byref(member)) and member.value)
        finally:
            close(h)

    def wait_empty(self, ms: int = 10000):
        # A Job handle's signaled state is not the general normal-exit contract.
        deadline = time.monotonic() + ms / 1000
        while True:
            info = BASIC_ACCOUNTING()
            if not k.QueryInformationJobObject(self.handle, 1, c.byref(info), c.sizeof(info), None):
                _err("QueryInformationJobObject active processes")
            if info.ActiveProcesses == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(.01)

    def close(self):
        if self.child:
            close(self.child)
            self.child = None
        if self.handle:
            close(self.handle)
            self.handle = None
