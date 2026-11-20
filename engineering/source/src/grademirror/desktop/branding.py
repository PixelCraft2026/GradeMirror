"""Product-relative branding and stable Windows shell identity; no engine imports."""

from .i18n import tr, render, source_text
import ctypes
import importlib.metadata
import sys
from pathlib import Path
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon

APP_ID = 'GradeMirror.Desktop'
# A shortcut registered under APP_ID supplies its black tile to the Shell's
# application group, even when WM_GETICON and RelaunchIconResource are transparent.
# Keep the running workspace in its own stable group; its relaunch properties
# supply the transparent artwork and correct launch command for taskbar pinning.
TASKBAR_APP_ID = 'GradeMirror.Desktop.Workspace'
ASSETS = Path(__file__).resolve().parents[3] / 'assets'

def _icon_source(path):
    from ..light import portable
    if portable.enabled():
        from ..light.system import packaged
        return str(packaged(path))
    return str(path)


def app_icon():
    icon = QIcon(_icon_source(ASSETS / 'brand/app/grademirror-desktop-v3.ico'))
    if icon.isNull():
        icon = QIcon(_icon_source(ASSETS / 'brand/app/grademirror-tile.svg'))
    return icon

def mark_icon():
    path = ASSETS / 'brand/app/grademirror-mark-compact.svg'
    path = path if path.is_file() else ASSETS / 'brand/grademirror-mark.svg'
    from ..light import portable
    if not portable.enabled():
        return QIcon(str(path))
    from ..light.system import packaged
    from ..light.native_policy import QSVG, fixed_component, qt_loaded_receipt
    from ..light.runtime import data_root
    from ..common import atomic_json
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import Qt
    # This existing shipped mark is a verified application resource, never a user image.
    image = _BrandMark(str(packaged(path)), fixed_component(QSVG, "qsvg"))
    return QIcon(image)

from PySide6.QtGui import QIconEngine
class _BrandMark(QIconEngine):
    def __init__(self, path, plugin):
        super().__init__()
        self.path = path
        self.plugin = plugin

    def clone(self):
        return _BrandMark(self.path, self.plugin)

    def pixmap(self, size, mode, state):
        from PySide6.QtGui import QImageReader, QPixmap
        from ..light.native_policy import qt_loaded_receipt
        from ..light.runtime import data_root
        from ..common import atomic_json
        reader = QImageReader(self.path, b"svg")
        reader.setAutoDetectImageFormat(False)
        reader.setScaledSize(QSize(min(512, size.width()), min(512, size.height())))
        rendered = reader.read()
        if rendered.isNull():
            raise ValueError(reader.errorString())
        identity = qt_loaded_receipt(self.plugin)
        path = data_root() / "native-resource.json"
        if not path.exists():
            atomic_json(path, dict(identity, asset=Path(self.path).name,
                                   width=rendered.width(), height=rendered.height()))
        return QPixmap.fromImage(rendered)

    def paint(self, painter, rect, mode, state):
        painter.drawPixmap(rect, self.pixmap(rect.size(), mode, state))

def runtime_icon():
    icon = QIcon(_icon_source(ASSETS / 'brand/app/grademirror-taskbar-v4.ico'))
    return icon if not icon.isNull() else QIcon(_icon_source(ASSETS / 'brand/app/grademirror-app.svg'))

def configure_taskbar(window):
    from PySide6.QtWidgets import QApplication
    if sys.platform!='win32' or QApplication.platformName()=='offscreen':
        return None
    # RelaunchIconResource does not replace WM_GETICON for a running window.
    # Give Qt the transparent icon so all native HICON sizes stay correct,
    # including after DPI changes. File shortcuts and dialogs use app_icon().
    window.setWindowIcon(runtime_icon())
    from .taskbar_icon import TaskbarIcon
    try:
        owner=TaskbarIcon(int(window.winId()),ASSETS.parent,TASKBAR_APP_ID)
        window.setProperty('gradeMirrorTaskbarIcon',dict(status='configured',values=owner.values,
            runtime_window_icon='transparent'))
        return owner
    except OSError as exc:
        window.setProperty('gradeMirrorTaskbarIcon',dict(status='unavailable',error=str(exc)))
        return None

def action_icon(name):
    folder = ASSETS / 'icons/actions'
    icon = QIcon(_icon_source(folder / (name + '.svg')))
    disabled = folder / 'disabled' / (name + '.svg')
    hover = folder / 'hover' / (name + '.svg')
    if disabled.is_file():
        icon.addFile(_icon_source(disabled), QSize(), QIcon.Mode.Disabled)
    if hover.is_file():
        icon.addFile(_icon_source(hover), QSize(), QIcon.Mode.Active)
    return icon

def desktop_version():
    try:
        return importlib.metadata.version('grademirror')
    except importlib.metadata.PackageNotFoundError:
        return tr('开发版')

def configure_application(app):
    # Must run before any window/file dialog is presented.
    identity = dict(app_id=APP_ID, platform=sys.platform, explicit=False)
    if sys.platform == 'win32':
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        hr = shell.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        identity.update(explicit=hr >= 0, hresult=hr)
    app.setApplicationName('GradeMirror')
    app.setApplicationDisplayName('GradeMirror')
    app.setWindowIcon(app_icon())
    app.setProperty('gradeMirrorShellIdentity', identity)
    return identity
