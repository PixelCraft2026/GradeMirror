"""Windows file selection in a helper and path-only Explorer drops."""
import ctypes
from ctypes import wintypes
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from PySide6.QtCore import QEvent, QObject, QPoint
from PySide6.QtWidgets import QWidget

from ..common import atomic_json, strict_load
from ..cpu_replay.contract import need
from ..winproc import OwnedJob
from . import runtime, portable
from .system import packaged


class ExplorerFileDrop(QObject):
    """Receive filenames without enabling Qt's OLE drag-image helper."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.shell.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        self.shell.DragAcceptFiles.restype = None
        self.shell.DragQueryPoint.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.POINT)]
        self.shell.DragQueryPoint.restype = wintypes.BOOL
        self.shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        self.shell.DragQueryFileW.restype = wintypes.UINT
        self.shell.DragFinish.argtypes = [wintypes.HANDLE]
        self.shell.DragFinish.restype = None
        self.ole = ctypes.WinDLL("ole32", use_last_error=True)
        self.ole.RevokeDragDrop.argtypes = [wintypes.HWND]
        self.ole.RevokeDragDrop.restype = ctypes.c_long

        for widget in [window, *window.findChildren(QWidget)]:
            if widget.acceptDrops():
                widget.setAcceptDrops(False)
        self._register(window.winId())
        window.installEventFilter(self)

    def _register(self, win_id):
        self.hwnd = int(win_id or 0)
        if not self.hwnd:
            return
        # Qt's Windows plugin registers top-level HWNDs independently of acceptDrops.
        status = self.ole.RevokeDragDrop(self.hwnd) & 0xffffffff
        if status not in (0, 0x80040100):  # DRAGDROP_E_NOTREGISTERED
            raise OSError(f"Cannot disable the Windows OLE drop target (0x{status:08x})")
        self.shell.DragAcceptFiles(self.hwnd, True)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.WinIdChange:
            self._register(watched.effectiveWinId())
        return super().eventFilter(watched, event)

    def handle_message(self, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != 0x0233 or int(msg.hWnd or 0) != self.hwnd:  # WM_DROPFILES
            return False
        handle = wintypes.HANDLE(msg.wParam)
        if not handle:
            return True
        try:
            path = self._single_path(handle)
        finally:
            self.shell.DragFinish(handle)
        if path is not None:
            role, filename = path
            self.window._accept_dropped_image(role, filename)
        return True

    def _single_path(self, handle):
        query = self.shell.DragQueryFileW
        point = wintypes.POINT()
        if query(handle, 0xffffffff, None, 0) != 1 or not self.shell.DragQueryPoint(handle, ctypes.byref(point)):
            return None
        target = self._target(point)
        if target is None:
            return None
        length = query(handle, 0, None, 0)
        if not 0 < length < 32768:
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        if query(handle, 0, buffer, len(buffer)) != length:
            return None
        path = Path(buffer.value)
        if path.is_absolute() and path.suffix.lower() in self.window._image_suffixes:
            return target, buffer.value

    def _target(self, point):
        window = self.window
        if window._closing or window._close_after_release:
            return None
        dpr = window.devicePixelRatioF()
        child = window.childAt(QPoint(int(point.x / dpr), int(point.y / dpr)))
        if child is None:
            return None
        for role in ("source", "reference"):
            for target in (window._pane(role), window.materials[role]):
                if target.drop_enabled and (child is target or target.isAncestorOf(child)):
                    return role


def file_path(parent, kind, title, initial="", file_filter=""):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    modal=QDialog(parent)
    modal.setWindowTitle(title)
    modal.resize(1,1)
    home=runtime.data_root()/"pickers"
    home.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=home) as scratch:
        directory=Path(scratch)
        request=directory/"request.json"
        result=directory/"result.json"
        atomic_json(request,dict(kind=kind,title=title,initial=initial,filter=file_filter))
        exe=packaged(runtime.bundle_exe("desktop"))
        entry=packaged(runtime.ENTRY)
        packaged(Path(__file__))
        job=OwnedJob(0)
        timer=QTimer(modal)
        timer.setInterval(80)
        def poll():
            if job.poll() is not None:
                modal.accept()
        timer.timeout.connect(poll)
        try:
            who=job.spawn(portable.argv(exe, entry, "pick-file", request),
                          str(runtime.ROOT),runtime.launch_env(exe,directory))
            parent._file_picker_pid=who["pid"]
            timer.start()
            if modal.exec()!=QDialog.DialogCode.Accepted:
                return ""
            need(job.wait(1000)==0 and job.wait_empty(2000),"LIGHT_IMAGE_PICKER_EXIT")
            selected=strict_load(result)["path"]
            need(isinstance(selected,str) and (not selected or Path(selected).is_absolute()),
                 "LIGHT_IMAGE_PICKER_RESPONSE")
            return selected
        finally:
            timer.stop()
            parent._file_picker_pid=None
            if job.child and not job.wait_empty(100):
                job.terminate()
                job.wait(5000)
                job.wait_empty(5000)
            job.close()


def main(request):
    need(request.is_absolute() and request.name=="request.json"
         and request.parent.parent==runtime.data_root()/"pickers","LIGHT_IMAGE_PICKER_SCOPE")
    from ..common import reject_reparse
    reject_reparse(request)
    options=strict_load(request)
    if portable.enabled():
        from .native_policy import qt_root
        plugins=qt_root()
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.setLibraryPaths([str(plugins)])
    from PySide6.QtWidgets import QApplication, QFileDialog
    app=QApplication(sys.argv[:1])
    if portable.enabled():
        QCoreApplication.setLibraryPaths([str(plugins)])
    arguments=(None,options["title"],options["initial"])
    kind=options["kind"]
    if kind=="directory":
        selected=QFileDialog.getExistingDirectory(*arguments)
    elif kind=="open":
        selected,_=QFileDialog.getOpenFileName(*arguments,options["filter"])
    elif kind in ("save","export"):
        flags=QFileDialog.Option.DontConfirmOverwrite if kind=="export" else QFileDialog.Option(0)
        selected,_=QFileDialog.getSaveFileName(*arguments,options["filter"],options=flags)
    else:
        need(False,"LIGHT_FILE_PICKER_KIND")
    atomic_json(request.with_name("result.json"),dict(path=selected))
    return 0
