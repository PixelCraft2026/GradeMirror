"""Explicit zero-science target check. Never submits an application request."""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel

from . import contract as k


def main():
    app = QApplication([])
    label = QLabel("GradeMirror 便携测试版 · 仅检查 Qt 和本地运行时")
    label.resize(430, 100)
    label.show()
    result = 1

    def finish():
        nonlocal result
        try:
            checked = k.runtime_check("desktop")
            print("Windows build:", checked["windows_build"], checked["architecture"])
            print("Package/OS members matched:", len(checked["checked"]))
            print("Qt platform:", app.platformName())
            print("Scientific operations submitted: 0")
            result = 0
        except Exception as error:
            print(error, file=sys.stderr)
        label.close()

    QTimer.singleShot(350, finish)
    app.exec()
    return result
