"""Window-only Windows taskbar branding, separate from file shortcut artwork."""
import ctypes as c
from ctypes import wintypes as t
import subprocess
import uuid


class GUID(c.Structure):
    _fields_=[('a',c.c_uint32),('b',c.c_uint16),('d',c.c_uint16),('e',c.c_ubyte*8)]
    @classmethod
    def parse(cls,text):return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class KEY(c.Structure):
    _fields_=[('format',GUID),('pid',t.DWORD)]


class VALUE(c.Union):
    _fields_=[('text',c.c_wchar_p),('storage',c.c_ubyte*16)]


class VARIANT(c.Structure):
    _fields_=[('vt',c.c_ushort),('reserved',c.c_ushort*3),('value',VALUE)]


def _key(pid):return KEY(GUID.parse('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3'),pid)


def _call(ptr,index,args=(),types=()):
    address=c.cast(ptr,c.POINTER(c.POINTER(c.c_void_p))).contents[index]
    hr=c.WINFUNCTYPE(c.c_long,c.c_void_p,*types)(address)(ptr,*args)
    if hr<0:raise OSError(f'Taskbar property HRESULT {hr & 0xffffffff:08x}')


class TaskbarIcon:
    def __init__(self,hwnd,root,app_id):
        self.hwnd=hwnd
        from ..light.runtime import application, ROOT
        launch=([str(ROOT/'GradeMirror.cmd')] if application() else
                [str(root/'.venv-desktop/Scripts/pythonw.exe'),'-B',str(root/'tools/grademirror_desktop.py')])
        self.values={2:subprocess.list2cmdline(launch),
            3:str(root/'assets/brand/app/grademirror-taskbar-v4.ico')+',0',
            4:'GradeMirror',5:app_id}
        self.active=False
        try:
            self._write(self.values)
            if self.read()!=self.values:raise OSError('Taskbar property readback differs')
            self.active=True
        except Exception:
            self.close()
            raise

    def _store(self):
        shell=c.WinDLL('shell32',use_last_error=True)
        shell.SHGetPropertyStoreForWindow.argtypes=[t.HWND,c.POINTER(GUID),c.POINTER(c.c_void_p)]
        shell.SHGetPropertyStoreForWindow.restype=c.c_long
        iid=GUID.parse('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99');ptr=c.c_void_p()
        hr=shell.SHGetPropertyStoreForWindow(self.hwnd,c.byref(iid),c.byref(ptr))
        if hr<0:raise OSError(f'Taskbar store HRESULT {hr & 0xffffffff:08x}')
        return ptr

    def _write(self,values):
        ptr=self._store()
        try:
            for pid,text in values.items():
                key=_key(pid);value=VARIANT()
                if text is not None:value.vt=31;value.value.text=text
                _call(ptr,6,(c.byref(key),c.byref(value)),(c.POINTER(KEY),c.POINTER(VARIANT)))
        finally:_call(ptr,2)

    def read(self):
        ptr=self._store();result={};ole=c.WinDLL('ole32')
        ole.PropVariantClear.argtypes=[c.POINTER(VARIANT)]
        try:
            for pid in self.values:
                key=_key(pid);value=VARIANT()
                try:
                    _call(ptr,5,(c.byref(key),c.byref(value)),(c.POINTER(KEY),c.POINTER(VARIANT)))
                    result[pid]=value.value.text if value.vt==31 else None
                finally:ole.PropVariantClear(c.byref(value))
        finally:_call(ptr,2)
        return result

    def close(self):
        # Window properties must be emptied before the HWND is destroyed.
        self._write({pid:None for pid in self.values})
        self.active=False
