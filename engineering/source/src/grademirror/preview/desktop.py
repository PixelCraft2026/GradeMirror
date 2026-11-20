"""Portable user workspace. Background work uses the same service as CLI."""
import functools
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from ..common import strict_load
from . import contract as k, service
from .eta import ETA, read_history, save_history

PHOTO_FILTER = "RGB 图片 (*.jpg *.jpeg *.png *.tiff *.tif *.webp *.bmp)"


class PhotoPath(QLineEdit):
    def dragEnterEvent(self, event):
        if self.isEnabled() and len(event.mimeData().urls()) == 1 and event.mimeData().urls()[0].isLocalFile():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self.setText(urls[0].toLocalFile())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class Window(QWidget):
    def __init__(self, runtime_problem=None):
        super().__init__()
        self.runtime_problem = runtime_problem
        self.key = self.result = self.future = self.eta = self.group = None
        self.closing = False
        self.display_file = None
        self.cancel_requested = False
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gm065")
        self.history = k.user_root() / "eta.json"
        self.setWindowTitle("GradeMirror · 便携测试版 " + k.VERSION)
        self.resize(780, 630)
        layout = QVBoxLayout(self)
        title = QLabel("GradeMirror")
        title.setStyleSheet("font-size: 27px; font-weight: 600")
        layout.addWidget(title)
        layout.addWidget(QLabel("快速 · CPU  ·  精细首次、补精细和精细重放待验证"))
        form = QFormLayout()
        self.source = self.file_row(form, "原图", lambda: self.pick_photo(self.source))
        self.reference = self.file_row(form, "参考图", lambda: self.pick_photo(self.reference))
        self.project = self.file_row(form, "工程 / 配方", self.open_recipe)
        layout.addLayout(form)
        commands = QHBoxLayout()
        self.start = QPushButton("开始快速处理…")
        self.replay = QPushButton("显式重放")
        self.release = QPushButton("释放结果")
        self.cancel = QPushButton("取消")
        for button, handler in ((self.start, self.start_fit), (self.replay, self.start_replay),
                                (self.release, self.release_result), (self.cancel, self.cancel_current)):
            commands.addWidget(button)
            button.clicked.connect(handler)
        layout.addLayout(commands)
        self.preview = QLabel("选择原图和参考图，或打开已保存的快速配方")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(320)
        self.preview.setStyleSheet("background: #202328; color: #e5e9f0; border-radius: 5px")
        layout.addWidget(self.preview, 1)
        adjustment = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        self.strength = QLabel("强度 100%")
        self.slider.valueChanged.connect(lambda v: self.strength.setText(f"强度 {v}%"))
        self.slider.sliderReleased.connect(self.refresh)
        self.render = QPushButton("更新预览")
        self.render.clicked.connect(self.refresh)
        self.export = QPushButton("按原图格式导出…")
        self.export.clicked.connect(self.export_image)
        for widget in (self.strength, self.slider, self.render, self.export):
            adjustment.addWidget(widget)
        layout.addLayout(adjustment)
        self.note = QLabel(runtime_problem or "请选择文件。运行记录在本机 GradeMirror 用户数据目录。")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.buttons()

    def file_row(self, form, label, action):
        row = QHBoxLayout()
        edit = PhotoPath() if label != "工程 / 配方" else QLineEdit()
        if label != "工程 / 配方":
            edit.editingFinished.connect(lambda: edit.setText(edit.text().strip().strip('"')))
        else:
            edit.setReadOnly(True)
        button = QPushButton("打开…" if label == "工程 / 配方" else "选择…")
        button.clicked.connect(action)
        row.addWidget(edit)
        row.addWidget(button)
        form.addRow(label, row)
        return edit

    def buttons(self):
        idle = self.future is None
        ready = idle and not self.runtime_problem and not self.closing
        self.start.setEnabled(ready and self.result is None)
        self.replay.setEnabled(ready and self.result is None)
        for widget in (self.release, self.slider, self.render, self.export):
            widget.setEnabled(ready and self.result is not None)
        self.source.setEnabled(idle and self.result is None)
        self.reference.setEnabled(idle and self.result is None)
        self.cancel.setEnabled(not idle and self.action in ("fit", "replay", "export") and not self.cancel_requested)

    def pick_photo(self, edit):
        if self.future is not None or self.result is not None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 RGB 图片", "", PHOTO_FILTER)
        if path:
            edit.setText(path)

    def open_recipe(self):
        if self.future is not None or self.result is not None:
            self.note.setText("打开其他工程前请先释放结果。")
            return
        path = QFileDialog.getExistingDirectory(self, "选择 .grademirror 工程目录")
        if not path:
            return
        try:
            from ..cpu_recipe.validation import inspect_cpu_recipe
            row = inspect_cpu_recipe(Path(path))
            self.project.setText(path)
            mode = row.get("mode", "待完成")
            if mode == "fine":
                self.note.setText("精细配方可查看。当前候选的精细重放资格尚待验证。")
                return
            self.note.setText(f"已打开配方（{mode}）。请选择字节一致的原图，再点击显式重放。"
                              + (f"\n{self.runtime_problem}" if self.runtime_problem else ""))
        except Exception as error:
            self.note.setText(str(error))

    def start_fit(self):
        if not self.source.text() or not self.reference.text():
            self.note.setText("请先选择原图和参考图。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "指定新工程保存位置", "", "GradeMirror 工程 (*.grademirror)")
        if not path:
            return
        if not path.lower().endswith(".grademirror"):
            path += ".grademirror"
        self.project.setText(path)
        self.begin("fit", self.reference.text())

    def start_replay(self):
        if not self.source.text() or not self.project.text():
            self.note.setText("请先打开工程，并选择字节一致的原图。")
            return
        self.begin("replay", None)

    def begin(self, operation, reference):
        self.key = str(uuid.uuid4())
        self.cancel_requested = False
        self.group = None
        self.eta = ETA(operation)
        self.launch(operation, service.submit, operation, self.source.text(), reference,
                    self.project.text(), self.key)

    def launch(self, action, function, *args, **kwargs):
        self.action = action
        self.future = self.executor.submit(functools.partial(function, *args, **kwargs))
        self.buttons()

    def tick(self):
        if self.future is None:
            return
        if self.eta and self.action in ("fit", "replay", "initial_preview", "export"):
            try:
                p = k.user_root() / "runs" / self.key / "request.json"
                if self.group is None and p.is_file():
                    self.group = self.eta.configure(strict_load(p), read_history(self.history, self.eta_diagnostic))
                self.eta.update(service.status(self.key))
            except (OSError, ValueError):
                pass
            self.note.setText(self.eta.text())
        if not self.future.done():
            return
        future, action = self.future, self.action
        self.future = None
        try:
            row = future.result()
            if action in ("fit", "replay"):
                if row.get("binding"):
                    self.result = row
                    if self.closing or self.cancel_requested:
                        self.release_result()
                    else:
                        self.refresh(initial=True)
                else:
                    self.note.setText(f"{row.get('state', 'UNKNOWN')} · {row.get('availability', '')}")
            elif action in ("preview", "initial_preview"):
                old = self.display_file
                self.display_file = Path(row["file"]["path"])
                self.preview.setPixmap(QPixmap(str(self.display_file)).scaled(
                    self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                if old:
                    try:
                        old.unlink(missing_ok=True)
                    except OSError:
                        pass
                if action == "initial_preview":
                    self.success("工程已保存 · ")
            elif action == "export":
                self.success("图片已保存并校验 · ")
            elif action == "release":
                if not row.get("released"):
                    self.note.setText("释放待完成。请再次点击释放，或关闭后查看本地记录。")
                    if self.closing:
                        self.closing = False
                else:
                    self.result = None
                    self.preview.clear()
                    self.preview.setText("已释放驻留结果")
                    self.note.setText("工程可显式重放。")
        except Exception as error:
            if self.eta:
                self.eta.finish("failed")
            if action == "release":
                self.closing = False
            self.note.setText(str(error))
        self.buttons()
        if self.closing and self.future is None:
            QTimer.singleShot(0, self.close)

    def eta_diagnostic(self, message):
        try:
            path = k.user_root() / "eta-warning.txt"
            from ..common import atomic_json
            atomic_json(path.with_suffix(".json"), dict(message=message[:1000]))
        except OSError:
            pass

    def success(self, prefix):
        if self.eta:
            self.eta.finish("complete")
            save_history(self.history, self.group, self.eta.operation, self.eta.completed, self.eta_diagnostic)
            prefix += self.eta.text()
            self.eta = None
        self.note.setText(prefix)

    def refresh(self, initial=False):
        if not self.result or self.future is not None:
            return
        request = str(uuid.uuid4())
        path = k.user_root() / "runs" / self.key / "previews" / (request + ".jpg")
        self.launch("initial_preview" if initial else "preview", service.consume, self.key, self.result,
                    "preview", destination=path, request_id=request, alpha=self.slider.value()/100)

    def export_image(self):
        if not self.result or self.future is not None:
            return
        request = strict_load(k.user_root() / "runs" / self.key / "request.json")
        source = Path(request["source"]["path"])
        path, _ = QFileDialog.getSaveFileName(self, "保存图片", str(source.with_stem(source.stem+"-GradeMirror")),
                                              f"原图格式 (*{source.suffix})")
        if path:
            if not Path(path).suffix:
                path += source.suffix
            self.eta = ETA("export")
            self.group = None
            self.export_request_key = str(uuid.uuid4())
            self.launch("export", service.consume, self.key, self.result, "export",
                        destination=path, alpha=self.slider.value()/100, request_key=self.export_request_key)

    def cancel_current(self):
        if self.cancel_requested:
            return
        self.cancel_requested = True
        if self.eta:
            self.eta.finish("cancel")
        if self.action == "export":
            self.executor.submit(service.cancel_export,self.key,self.result,self.export_request_key)
        else:
            self.executor.submit(service.cancel,self.key)
        self.buttons()

    def release_result(self):
        if self.result and self.future is None:
            self.launch("release", service.consume, self.key, self.result, "release")

    def closeEvent(self, event):
        if self.future is not None:
            self.closing = True
            if self.action in ("fit", "replay", "export"):
                self.cancel_current()
            event.ignore()
        elif self.result is not None:
            self.closing = True
            self.release_result()
            event.ignore()
        else:
            self.executor.shutdown(wait=True)
            event.accept()


def main(*, close_after=None):
    app = QApplication([])
    app.setApplicationName("GradeMirror Preview")
    k.user_root().mkdir(parents=True, exist_ok=True)
    problem = None
    try:
        k.runtime_check("desktop")
    except Exception as error:
        problem = f"运行时检查未通过，当前可查看工程信息。{error}"
    window = Window(problem)
    window.show()
    if close_after is not None:
        QTimer.singleShot(close_after, window.close)
    status = app.exec()
    return status, problem

