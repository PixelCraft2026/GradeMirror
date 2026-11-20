"""Log every uncaught UI exception without nesting modal event loops."""

from .i18n import tr, render, source_text
from .language import set_text, text_widget, manager, unbind_text
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox


class DesktopErrorReporter:
    def __init__(self, window):
        self.host_window = window
        self.dialog = None
        self._reporting = False

    def __call__(self, kind, value, trace):
        # Reporting itself can change focus and deliver another Qt exception.
        if self._reporting:
            sys.__excepthook__(kind, value, trace)
            return
        self._reporting = True
        try:
            window = self.host_window
            window._log("unhandled_exception", traceback="".join(
                traceback.format_exception(kind, value, trace)))
            set_text(window.status_label, tr("操作未完成，请重试；详细信息已写入诊断日志。"))
            if self.dialog is not None:
                return
            dialog = QMessageBox(QMessageBox.Warning, tr("操作未完成"),
                tr('{0}\n\n诊断日志：{1}', value, window.receipt_path), QMessageBox.Ok, window)
            set_text(dialog,tr('操作未完成'),'setWindowTitle')
            set_text(dialog,tr('{0}\n\n诊断日志：{1}', value, window.receipt_path))
            dialog.setObjectName("gm.error-dialog")
            dialog.setTextFormat(Qt.PlainText)
            dialog.setWindowIcon(window.windowIcon())
            dialog.setWindowModality(Qt.WindowModal)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.setDefaultButton(QMessageBox.Ok)
            dialog.setEscapeButton(QMessageBox.Ok)
            self.dialog = dialog
            dialog.finished.connect(lambda _: self._finished(dialog))
            # open() returns immediately; warning()/exec() would nest the event loop.
            dialog.open()
        except Exception:
            # Never recurse through sys.excepthook if error reporting fails.
            sys.__excepthook__(*sys.exc_info())
        finally:
            self._reporting = False

    def _finished(self, dialog):
        if self.dialog is dialog:
            self.dialog = None
