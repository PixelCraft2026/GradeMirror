"""A material's existing preview and path; viewing never replaces its input."""

from .i18n import tr, render, source_text
from .language import set_text, text_widget, manager, unbind_text
from pathlib import Path
from PySide6.QtCore import Qt, QSize, Signal, QEvent
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton, QVBoxLayout, QSizePolicy
from .sidebar_widgets import DisclosureButton


class MaterialCard(QFrame):
    viewRequested = Signal(str)
    imageDropped = Signal(str)
    THUMBNAIL_WIDTH = 76

    def __init__(self, role, suffixes):
        super().__init__()
        self.role = role
        self.suffixes = frozenset(suffixes)
        self.drop_enabled = True
        self._filename = tr('尚未选择')
        self._thumbnail_key = None
        self._thumbnail_image = None
        self.thumbnail_height = 64
        self.setObjectName('material')
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        label = text_widget(QLabel, tr('原图') if role == 'source' else tr('调色参考'))
        label.setObjectName('section')
        self.replace_button = text_widget(QPushButton, tr('更换…'))
        self.replace_button.setObjectName('quiet')
        set_text(self.replace_button, tr('更换原图') if role == 'source' else tr('更换调色参考'), 'setAccessibleName')
        row.addWidget(label)
        row.addStretch()
        row.addWidget(self.replace_button)
        layout.addLayout(row)
        self.view_button = QToolButton()
        self.view_button.setObjectName('materialView')
        self.view_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.view_button.setFixedHeight(self.thumbnail_height)
        self.view_button.setMinimumWidth(0)
        self.view_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.view_button.installEventFilter(self)
        self.view_button.clicked.connect(lambda: self.viewRequested.emit(self.role))
        set_text(self.view_button, tr('查看原图') if role == 'source' else tr('查看调色参考'), 'setAccessibleName')
        set_text(self.view_button, tr('单看这份素材；不更换输入，不重新计算'), 'setToolTip')
        layout.addWidget(self.view_button)
        self.dimensions = text_widget(QLabel, tr('未读取尺寸'))
        self.dimensions.setObjectName('muted')
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setObjectName('warning')
        self.error.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.error.hide()
        self.details_toggle = DisclosureButton(tr('完整路径'))
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        set_text(self.path_edit, tr('原图完整路径') if role == 'source' else tr('参考完整路径'), 'setAccessibleName')
        set_text(self.path_edit, tr('尚未选择图片'), 'setPlaceholderText')
        self.path_edit.hide()
        self.path_edit.textChanged.connect(self._path_changed)
        self.details_toggle.toggled.connect(self._details)
        metadata = QHBoxLayout()
        metadata.addWidget(self.dimensions)
        metadata.addStretch()
        metadata.addWidget(self.details_toggle)
        layout.addLayout(metadata)
        layout.addWidget(self.error)
        layout.addWidget(self.path_edit)
        self._elide()
        manager().changed.connect(self._elide)

    def _details(self, checked):
        self.path_edit.setVisible(checked)

    def _path_changed(self, path):
        self._filename = Path(path).name if path else tr('尚未选择')
        set_text(self.path_edit, path, 'setToolTip')
        set_text(self.view_button, (path + tr('\n单看素材；不更换输入')) if path else tr('先使用「更换…」或拖入图片'), 'setToolTip')
        self._elide()

    def _elide(self):
        inset = 20 if self.view_button.icon().isNull() else self.THUMBNAIL_WIDTH + 26
        text = self.view_button.fontMetrics().elidedText(render(self._filename), Qt.ElideMiddle,
            max(0, self.view_button.width() - inset))
        set_text(self.view_button, text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()
        self._refresh_thumbnail()

    def eventFilter(self, watched, event):
        if watched is self.view_button and event.type() == QEvent.Resize:
            self._elide()
            self._refresh_thumbnail()
        return super().eventFilter(watched, event)

    def set_preview(self, image, grid=None):
        self._thumbnail_image = image
        self._refresh_thumbnail()
        self._elide()
        if grid and type(grid.get('width')) is int and type(grid.get('height')) is int:
            set_text(self.dimensions, f"{grid['width']} × {grid['height']}")
        else:
            set_text(self.dimensions, tr('未读取尺寸'))

    def set_thumbnail_height(self, height):
        if height != self.thumbnail_height:
            self.thumbnail_height = height
            self.view_button.setFixedHeight(height)
            self._refresh_thumbnail()

    def _refresh_thumbnail(self):
        image = self._thumbnail_image
        if image is None or image.isNull():
            self._thumbnail_key = None
            self.view_button.setIcon(QIcon())
            return
        width, height = self.THUMBNAIL_WIDTH, self.thumbnail_height - 12
        dpr = self.devicePixelRatioF()
        key = image.cacheKey(), width, height, dpr
        if key != self._thumbnail_key:
            self._thumbnail_key = key
            self.view_button.setIconSize(QSize(width, height))
            thumb = QPixmap.fromImage(image.scaled(round(width*dpr), round(height*dpr),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
            thumb.setDevicePixelRatio(dpr)
            self.view_button.setIcon(QIcon(thumb))

    def set_error(self, text):
        set_text(self.error, text)
        self.error.setVisible(bool(text))

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if (self.drop_enabled and len(urls) == 1 and urls[0].isLocalFile()
                and Path(urls[0].toLocalFile()).suffix.lower() in self.suffixes):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if (self.drop_enabled and len(urls) == 1 and urls[0].isLocalFile()
                and Path(urls[0].toLocalFile()).suffix.lower() in self.suffixes):
            self.imageDropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()
        else:
            event.ignore()
