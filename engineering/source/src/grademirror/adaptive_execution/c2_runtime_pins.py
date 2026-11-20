"""Continuous per-worker byte proof for the GM064 C2 terminal observation."""
import ctypes as ct
from ctypes import wintypes as w
import hashlib
import msvcrt
import os
import struct
import time
from pathlib import Path

from ..cpu_replay.contract import need
from .portable_renewal import Pins


class RuntimePins(Pins):
    def __init__(self, known, build, inventory):
        super().__init__()
        self.known = dict(known)
        self.known[os.path.normcase(inventory['path'])] = inventory['sha256']
        self.build = build
        self.files = {}
        self.observed = {}
        self.sealed = False
        self.stage = None
        k = ct.WinDLL('kernel32', use_last_error=True)
        self.file_info = k.GetFileInformationByHandle
        self.file_info.argtypes = [w.HANDLE, ct.c_void_p]
        self.file_info.restype = w.BOOL
        self.final_path = k.GetFinalPathNameByHandleW
        self.final_path.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
        self.final_path.restype = w.DWORD

    def _identity(self, handle, path, *, directory=False):
        raw = ct.create_string_buffer(52)
        need(self.file_info(handle, raw), 'C2_PIN_HANDLE_INVALID', str(path))
        attrs, _, _, _, _, _, _, volume, hi, lo, _, idhi, idlo = struct.unpack('<13I', raw.raw)
        need(attrs & 0x410 == (0x10 if directory else 0), 'C2_PIN_FILE_TYPE', str(path))
        final = ct.create_unicode_buffer(32768)
        length = self.final_path(handle, final, len(final), 0)
        need(0 < length < len(final), 'C2_PIN_FINAL_PATH', str(path))
        actual = final.value.removeprefix('\\\\?\\')
        need(os.path.normcase(actual) == os.path.normcase(str(path)), 'C2_PIN_FINAL_PATH', str(path))
        return dict(volume_serial=volume, file_id=(idhi << 32) | idlo, bytes=(hi << 32) | lo,
                    final_path=final.value)

    def _held_identity(self, path):
        stream = self.handles[path]
        need(not stream.closed, 'C2_PIN_CLOSED', str(path))
        return self._identity(msvcrt.get_osfhandle(stream.fileno()), path)

    def begin(self, build, stage):
        need(not self.sealed and build == self.build, 'C2_PIN_BUILD_OR_LIFETIME')
        need((self.stage, stage) in ((None, 'entry'), ('entry', 'terminal')), 'C2_PIN_STAGE')
        self.stage = stage
        self.observed.clear()
        self.started = time.monotonic()

    def _fresh(self, path, expected):
        # Each ancestor stays open with delete sharing denied. Check a newly
        # opened directory once instead of walking its entire prefix per file.
        for parent in reversed(path.parents):
            if parent == Path(path.anchor) or parent in self.handles:
                continue
            handle = self._open(parent, 0x80, 3, 0x02200000)
            self.handles[parent] = handle
            self._identity(handle, parent, directory=True)
        handle = self._open(path, 0x80000000, 1, 0x00200000)
        try:
            info = self._identity(handle, path)
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            self.close_handle(handle)
            raise
        try:
            stream = os.fdopen(fd, 'rb')
        except BaseException:
            os.close(fd)
            raise
        self.handles[path] = stream
        need(hashlib.file_digest(stream, 'sha256', _bufsize=1024*1024).hexdigest() == expected,
             'GM064_PIN_BYTES_CHANGED', str(path))
        self.rows[path] = expected
        self.hashed_bytes += info['bytes']
        return info

    def bind(self, raw_path, expected=None):
        need(not self.sealed and self.stage is not None, 'C2_PIN_CLOSED')
        path = Path(os.path.abspath(raw_path))
        known = self.known.get(os.path.normcase(str(path)))
        need(known is not None and (expected is None or known == expected), 'C2_PIN_UNKNOWN_OR_CHANGED', str(path))
        old = self.files.get(path)
        if old is None:
            info = self._fresh(path, known)
            row = dict(path=str(path), bytes=info['bytes'], sha256=known)
            old = dict(binding=row, identity=info, validated_at=time.monotonic(), validated_stage=self.stage)
            self.files[path] = old
            method = 'fresh_hash'
        else:
            info = self._held_identity(path)
            # Resolve the name from a new metadata-only handle, without lifting
            # the original deny-write/delete share on the protected file.
            peer = self._open(path, 0x80, 7, 0x00200000)
            try:
                need(info == old['identity'] == self._identity(peer, path)
                     and known == old['binding']['sha256'], 'C2_PIN_IDENTITY_CHANGED', str(path))
            finally:
                self.close_handle(peer)
            method = 'protected_reuse'
        self.observed.setdefault(path, dict(old, method=method))
        return dict(old['binding'])

    def sha(self, path):
        return self.bind(path)['sha256']

    def proof(self):
        records = list(self.observed.values())
        return dict(stage=self.stage, started_at=self.started, content_finished_at=time.monotonic(),
                    executor_build=self.build, records=records,
                    fresh_bytes=sum(x['binding']['bytes'] for x in records if x['method'] == 'fresh_hash'),
                    reused_bytes=sum(x['binding']['bytes'] for x in records if x['method'] == 'protected_reuse'))

    def close(self):
        self.sealed = True
        failures = []
        for value in reversed(tuple(self.handles.values())):
            try:
                if isinstance(value, int):
                    if not self.close_handle(value):
                        raise ct.WinError(ct.get_last_error())
                else:
                    value.close()
            except OSError as error:
                failures.append(error)
        self.handles.clear()
        self.rows.clear()
        if failures:
            raise ExceptionGroup('CPU runtime pin cleanup', failures)
