"""Small photo-first Qt Widgets controller over the shared GM-004A API."""

from __future__ import annotations

from .i18n import tr, render, source_text
from .i18n import geometry_text, external_text
from .language import set_text, text_widget, manager, unbind_text, bound_text

import base64
import hashlib
import json
import math
import os
import time
import uuid
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal, QPointF, QRectF, QSize, QSignalBlocker
from PySide6.QtGui import QColor, QColorSpace, QImage, QPixmap, QPainter
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QLabel, QMainWindow, QVBoxLayout, QSizePolicy,
)

from .backend import EngineBackend
from .active_operation import ActiveOperation
from .native_preview import NativePreviewCache
from .preview_cache import ExactPreviewCache
from .diagnostics import DiagnosticWriter
from .tile_frame import TileFrame
from grademirror.preview_limits import MAX_FIT_SIDE, PreviewSuperseded


class PhotoCanvas(QLabel):
    def paintEvent(self, event):
        pane = self.parentWidget()
        if not pane._native or pane.native_view is None:
            return super().paintEvent(event)
        cx, cy, width, height = pane.native_view
        dpr = self.devicePixelRatioF()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111215"))
        painter.translate(self.width() / 2 - cx / dpr, self.height() / 2 - cy / dpr)
        painter.scale(1 / dpr, 1 / dpr)
        if pane._proxy is not None:
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(QRectF(0, 0, width, height), pane._proxy)
        if pane._image is not None and pane.image_roi is not None:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            if isinstance(pane._image, TileFrame):
                # Round the complete patch origin once, like the old single-image
                # blit. Independent half-pixel rounding of adjacent tiles can gap.
                anchor = painter.deviceTransform().map(QPointF(*pane.image_roi[:2]))
                rounded = lambda n: math.floor(n+.5) if n >= 0 else math.ceil(n-.5)
                painter.translate(rounded(anchor.x())-anchor.x(), rounded(anchor.y())-anchor.y())
                for (bounds, _), image in pane._image.patches.items():
                    painter.drawImage(QPointF(bounds[0],bounds[1]), image)
            else:
                painter.drawImage(QPointF(pane.image_roi[0], pane.image_roi[1]), pane._image)
        painter.end()

class PhotoPane(QFrame):
    imageDropped = Signal(str)
    imageRequested = Signal()
    detailRequested = Signal(float, float)
    panStarted = Signal()
    panMoved = Signal(float, float)
    panFinished = Signal()
    viewportResized = Signal()

    def __init__(self, title: str, detail: str, *, drop_suffixes=()):
        super().__init__()
        self.setObjectName("pane")
        layout = QVBoxLayout(self)
        title_label = text_widget(QLabel, title)
        self.title_label = title_label
        self.full_title = title
        title_label.setObjectName("eyebrow")
        self._native = False
        self._image = None
        self.native_view = None
        self._proxy = None
        self.fit_key = None
        self._proxy_detail = ""
        self._draw_key = None
        self.image_roi = None
        self.image_grid = None
        self.pan_enabled = False
        self._drag_origin = None
        self._dragged = False
        self.photo = text_widget(PhotoCanvas, tr("＋\n\n点击选择照片\n或将图片拖入这里"), self)
        self.photo.setObjectName("photo")
        self.photo.setAlignment(Qt.AlignCenter)
        self.photo.setWordWrap(True)
        self.photo.setMinimumSize(80, 80)
        self.photo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.detail = text_widget(QLabel, detail)
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(20)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(title_label)
        layout.addWidget(self.photo, 1)
        layout.addWidget(self.detail)
        self._image = None
        self._native = False
        self.drop_suffixes = frozenset(drop_suffixes)
        self.inspect_enabled = False
        self.selection_enabled = True
        self.drop_enabled = True
        self.setAcceptDrops(bool(self.drop_suffixes))
        for child in (title_label, self.photo, self.detail):
            child.setAcceptDrops(bool(self.drop_suffixes))
            child.installEventFilter(self)
        self.setCursor(Qt.PointingHandCursor if self.drop_suffixes else Qt.ArrowCursor)

    def _drag_highlight(self, active):
        self.setProperty("dropActive", active)
        self.style().unpolish(self); self.style().polish(self)

    def eventFilter(self, watched, event):
        if watched is self.photo:
            if event.type() == QEvent.Resize:
                self._draw()
                self.viewportResized.emit()
            if (event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton
                    and self.pan_enabled and self._native):
                self._drag_origin = event.position()
                self._dragged = False
                self.photo.setCursor(Qt.ClosedHandCursor)
                self.panStarted.emit()
                return True
            if event.type() == QEvent.MouseMove and self._drag_origin is not None:
                delta = event.position() - self._drag_origin
                self._dragged = self._dragged or abs(delta.x()) + abs(delta.y()) >= 3
                if self._dragged:
                    self.panMoved.emit(delta.x(), delta.y())
                return True
            if event.type() == QEvent.MouseButtonRelease and self._drag_origin is not None:
                moved = self._dragged
                self._drag_origin = None
                self.photo.setCursor(Qt.OpenHandCursor)
                self.panFinished.emit()
                if moved:
                    return True
        if (event.type() == QEvent.MouseButtonRelease and watched is self.photo
                and event.button() == Qt.LeftButton and self.inspect_enabled
                and self._image is not None):
            pixmap = self.photo.pixmap()
            if self._native and self.native_view is not None:
                dpr = self.photo.devicePixelRatioF()
                cx, cy, _, _ = self.native_view
                x0, y0, x1, y1 = self.image_roi or (cx, cy, cx+1, cy+1)
                x = cx+(event.position().x()-self.photo.width()/2)*dpr
                y = cy+(event.position().y()-self.photo.height()/2)*dpr
                if x0 <= x <= x1 and y0 <= y <= y1:
                    self.detailRequested.emit((x-x0)/(x1-x0), (y-y0)/(y1-y0))
            elif pixmap is not None:
                size = pixmap.deviceIndependentSize()
                x0 = (self.photo.width() - size.width()) / 2
                y0 = (self.photo.height() - size.height()) / 2
                u = (event.position().x() - x0) / max(1., size.width())
                v = (event.position().y() - y0) / max(1., size.height())
                if 0 <= u <= 1 and 0 <= v <= 1:
                    self.detailRequested.emit(u, v)
            return True
        if (event.type() == QEvent.MouseButtonRelease and self.drop_suffixes
                and self.selection_enabled and self._image is None and event.button() == Qt.LeftButton):
            self.imageRequested.emit()
            return True
        if event.type() == QEvent.DragLeave:
            self._drag_highlight(False)
        if watched in (self.photo, self.detail) or isinstance(watched, QLabel):
            if event.type() == QEvent.DragEnter:
                self.dragEnterEvent(event)
                return True
            if event.type() == QEvent.Drop:
                self.dropEvent(event)
                return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if (self.drop_enabled and len(urls) == 1 and urls[0].isLocalFile() and
                Path(urls[0].toLocalFile()).suffix.lower() in self.drop_suffixes):
            event.acceptProposedAction()
            self._drag_highlight(True)
        else:
            event.ignore()

    def dropEvent(self, event):
        self._drag_highlight(False)
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if (not self.drop_enabled or len(urls) != 1 or not urls[0].isLocalFile() or
                Path(urls[0].toLocalFile()).suffix.lower() not in self.drop_suffixes):
            event.ignore()
            return
        self.imageDropped.emit(urls[0].toLocalFile())
        event.acceptProposedAction()

    def set_image(self, image: QImage, *, native: bool, detail: str, roi=None, fit_key=None):
        unbind_text(self.photo)
        self._image = image
        self._native = native
        self.image_roi = roi
        if not native:
            self._proxy = image
            self.fit_key = fit_key
            self._proxy_detail = detail
            self.native_view = None
        set_text(self.detail, detail)
        self._draw()

    def restore_fit(self, key):
        if self._proxy is None or self.fit_key != key:
            return False
        self._image = self._proxy
        self._native = False
        self.image_roi = self.native_view = None
        set_text(self.detail, self._proxy_detail)
        self._draw()
        return True

    def clear(self):
        self._image = None
        self.image_grid = None
        self._proxy = None
        self.fit_key = self._draw_key = None
        self.native_view = self.image_roi = None
        self._native = False
        self._drag_origin = None
        self.photo.clear()
        set_text(self.photo, tr("＋\n\n点击选择照片\n或将图片拖入这里") if self.drop_suffixes else tr("等待仿色结果"))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        set_text(self.title_label, self.title_label.fontMetrics().elidedText(self.full_title, Qt.ElideMiddle, max(1, self.width())))
        self._draw()

    def _draw(self):
        if self._image is None:
            return
        if isinstance(self._image, TileFrame):
            self._draw_key = None
            self.photo.update()
            return
        key = (self._image.cacheKey(), self._native, self.photo.devicePixelRatioF(),
               self.photo.width(), self.photo.height())
        if key == self._draw_key:
            return
        self._draw_key = key
        pixmap = QPixmap.fromImage(self._image)
        if self._native:
            # One source pixel per physical display pixel, without scaling.
            pixmap.setDevicePixelRatio(self.photo.devicePixelRatioF())
        else:
            dpr = self.photo.devicePixelRatioF()
            physical = QSize(math.ceil(self.photo.width() * dpr), math.ceil(self.photo.height() * dpr))
            pixmap = pixmap.scaled(physical, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
            pixmap.setDevicePixelRatio(dpr)
        self.photo.setPixmap(pixmap)


class MainWindow(QMainWindow):
    CLOSE_GRACE_SECONDS = 30
    def __init__(self, *, root: Path, backend=None, receipt_path: Path | None = None):
        super().__init__()
        self.language = manager()
        self.root = Path(root)
        self.backend = backend if backend is not None else EngineBackend()
        self.receipt_path = receipt_path or (self.root / "jobs/desktop-logs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.jsonl")
        self.events = deque(maxlen=2048)
        self._diagnostic_writer = DiagnosticWriter(self.receipt_path)
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="gm004b-ui")
        self._tasks = []
        self._epoch = 0
        self.project_path: Path | None = None
        self.project = None
        self.store_root: Path | None = None
        self.output_root: Path | None = None
        self.replay_key: str | None = None
        self.active_operation: ActiveOperation | None = None
        self._export_requests = {}
        self.job_id: str | None = None
        self.attempt_id: str | None = None
        self.binding: dict | None = None
        self.preview_inflight = False
        self.pending_alpha: float | None = None
        self.last_result_alpha: float | None = None
        self.roi: tuple[int, int, int, int] | None = None
        self._native_cache = NativePreviewCache()
        self._reference_center = None
        self._fit_cache = ExactPreviewCache()
        self._preview_superseded = threading.Event()
        self._native_refresh_pending = False
        self._pan_anchor = None
        self.export_pending = False
        self.busy: str | None = None
        self._closing = False
        self._closed = False
        self._close_after_release = False
        self._release_pending = False
        self._stage_inflight = False
        self._source_grid = None
        self._source_image_metadata = {}
        self._image_suffixes = EngineBackend.IMAGE_SUFFIXES
        self._after_release = None
        self._import_tokens = {"source": 0, "reference": 0}
        self._import_errors = {}
        self._import_geometry = {}
        self._geometry_status = None
        self._error_status = None
        self._keepalive_inflight = False
        self._cancel_pending = False
        from .view_state import DisplayState
        self.display = DisplayState()
        self._display_generation = 0
        self._display_refresh_pending = False
        self._restored_object = None
        self._initial_native_center = None
        self._import_pending = {}
        self._command_states = {}
        self._active_started = None
        self._build()
        self._apply_display_layout()
        self.diagnostics.document().setMaximumBlockCount(2048)
        self._poll = QTimer(self)
        self._poll.setInterval(30)
        self._poll.timeout.connect(self._poll_tasks)
        self._poll.start()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(160)
        self._debounce.timeout.connect(self._request_result)
        self._native_debounce = QTimer(self)
        self._native_debounce.setSingleShot(True)
        self._native_debounce.setInterval(30)
        self._native_debounce.timeout.connect(self._request_result)
        self._viewport_debounce = QTimer(self)
        self._viewport_debounce.setSingleShot(True)
        self._viewport_debounce.setInterval(80)
        self._viewport_debounce.timeout.connect(self._resize_detail_view)
        self._stage_timer = QTimer(self)
        self._stage_timer.setInterval(500)
        self._stage_timer.timeout.connect(self._poll_stage)
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(45000)
        self._heartbeat.timeout.connect(self._keep_alive)
        self._heartbeat.start()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_ui_state)
        self._sync_controls()
        from PySide6.QtGui import QAction
        adaptive_action=QAction('实验性资源适配…',self)
        adaptive_action.setShortcut('Ctrl+Alt+R')
        adaptive_action.triggered.connect(self._open_adaptive_panel)
        self.chrome.menus.addAction(adaptive_action);self._adaptive_action=adaptive_action;self._adaptive_dock=None
        self.language.changed.connect(self._language_changed)

    def _open_adaptive_panel(self):
        if self._adaptive_dock is None:
            from PySide6.QtWidgets import QDockWidget
            from .adaptive_panel import AdaptivePanel
            self._adaptive_dock=QDockWidget('实验性资源适配',self)
            self._adaptive_dock.setWidget(AdaptivePanel(self.backend,self._adaptive_dock))
            self.addDockWidget(Qt.RightDockWidgetArea,self._adaptive_dock)
        self._adaptive_dock.show();self._adaptive_dock.raise_()

    def _language_changed(self):
        if not self._closing:
            self._sync_controls()

    def _build(self):
        from .workspace_ui import build_workspace
        build_workspace(self, PhotoPane)

    def nativeEvent(self, event_type, message):
        adapter = getattr(getattr(self, "chrome", None), "adapter", None)
        if adapter is not None:
            handled, result = adapter.native_event(event_type, message)
            if handled:
                return True, result
        return super().nativeEvent(event_type, message)

    def _visible_roles(self):
        roles = self.display.roles
        if self.binding is None and self.display.object in ('result', 'source_result', 'reference_result'):
            return ('source', 'reference')
        return roles

    def _pane(self, role):
        return {'source': self.source_pane, 'reference': self.reference_pane, 'result': self.result_pane}[role]

    def _primary_clicked(self):
        if self.project is None:
            if not self.source_edit.text().strip():
                return self._browse_image('source')
            if not self.reference_edit.text().strip():
                return self._browse_image('reference')
        self._process_new_clicked()

    def _select_display(self, selection):
        if self.binding is None:
            return
        self._remember_detail()
        self.display.select(selection)
        self._display_changed()

    def _inspect_material(self, role):
        pane = self._pane(role)
        if pane._image is None and pane._proxy is None:
            set_text(self.status_label, tr('这份素材尚未完成读取。'))
            return
        self._remember_detail()
        self.display.inspect(role)
        self._display_changed()

    def _return_display(self):
        self._remember_detail()
        self.display.return_to_workspace()
        self._display_changed()

    def _arrange_display(self, layout):
        self._remember_detail()
        self.display.arrange(layout)
        self._display_changed()

    def _toggle_sidebar(self):
        self._remember_detail()
        self.display.sidebar = not self.display.sidebar
        self._display_changed()

    def _reference_detail(self):
        return self.display.object=='material_reference' and getattr(self.backend,"REFERENCE_ROI",False)

    def _detail_capable(self):
        return self.display.detail_capable or self._reference_detail()

    def _detail_grid(self):
        return self.reference_pane.image_grid if self._reference_detail() else self._source_grid

    def _detail_center(self):
        return self._reference_center if self._reference_detail() else self.display.native_center

    def _remember_detail(self):
        if self.roi is not None:
            x0, y0, x1, y1 = self.roi
            center=((x0+x1)/2,(y0+y1)/2)
            if self._reference_detail():
                self._reference_center=center
            else:
                self.display.remember_center(*center)
            self.display.native_preferred = True

    def _display_changed(self):
        self._display_generation += 1
        self._preview_superseded.set()
        self.roi = None
        self._apply_display_layout()
        self._display_refresh_pending = self.binding is not None
        grid=self._detail_grid()
        if self.binding is not None and self.display.native_preferred and self._detail_capable() and grid:
            center = self._detail_center() or (grid['width']/2, grid['height']/2)
            QTimer.singleShot(0, lambda: self._set_detail_roi(*center))
        elif self.binding is not None and self.busy is None:
            QTimer.singleShot(0, self._refresh_source_result)
        self._save_timer.start()
        self._sync_controls()

    def _apply_display_layout(self):
        roles = self._visible_roles()
        orientation = Qt.Horizontal if self.display.layout == 'horizontal' else Qt.Vertical
        self.photos.setOrientation(orientation)
        for role in ('source', 'reference', 'result'):
            self._pane(role).setVisible(role in roles)
        self.sidebar_scroll.setVisible(self.display.sidebar)
        self.photos.setSizes([1000 if role in roles else 0 for role in ('source','reference','result')])
        self.layout_button.setEnabled(len(roles) == 2)
        set_text(self.layout_button, tr('双图可以左右或上下排列') if len(roles) == 2 else tr('单图不需要排列；切换到比较后可用'), 'setToolTip')
        for role in roles:
            pane = self._pane(role)
            if not self.display.native or self.binding is None:
                if pane._proxy is not None and pane._native:
                    pane.restore_fit(pane.fit_key)
                pane._native = False
                pane.native_view = None
                pane._draw()
        self._update_photo_info()

    def _update_photo_info(self):
        if not hasattr(self, 'zoom_label'):
            return
        names = {'source':tr('原图'), 'reference':tr('调色参考'), 'result':tr('结果')}
        project = self.project or {}
        grid = self._source_grid
        zooms = []
        for role in ('source', 'reference', 'result'):
            pane = self._pane(role)
            record = project.get('source' if role == 'result' else role, {})
            field = self.source_edit if role in ('source','result') else self.reference_edit
            filename = record.get('name') or (Path(field.text()).name if field.text() else '')
            title = names[role] + (' · ' + filename if filename else '')
            if role == 'result' and self.binding:
                from grademirror.engine.profiles import FINE
                title += ' · ' + (tr('精细') if project.get('profile_id') == FINE else tr('快速')) + tr(' · 强度 {0}%', self.slider.value())
            pane.full_title = title
            set_text(pane.title_label, pane.title_label.fontMetrics().elidedText(title, Qt.ElideMiddle, max(1,pane.width())))
            set_text(pane.title_label, title, 'setToolTip')
            if role in self.materials:
                card = self.materials[role]
                info = self._import_geometry.get(role, {})
                material_grid = pane.image_grid or (info if 'width' in info else None)
                card.set_preview(pane._proxy, material_grid)
                card.set_error(self._import_errors.get(role, ''))
            if role not in self._visible_roles():
                continue
            view_grid = pane.image_grid or (grid if role != 'reference' else None)
            if not view_grid or pane._image is None:
                zooms.append(names[role] + ' —')
                continue
            native = pane._native and self.roi is not None and tuple(pane.image_roi or ()) == tuple(self.roi)
            pixmap = pane.photo.pixmap()
            if native:
                scale_text = '100%'
            elif pane._native or pixmap is None:
                scale_text = tr('— · 更新中')
            else:
                scale = pixmap.width() / view_grid['width']
                proxy = pane._proxy is not None and pane._proxy.width() < view_grid['width'] and pixmap.width() > pane._proxy.width()
                scale_text = f'{scale*100:.1f}%' + (tr(' · 代理放大') if proxy else '')
            zooms.append(names[role] + ' ' + scale_text)
            set_text(pane.detail, f"{view_grid['width']} × {view_grid['height']} · " + scale_text)
        if 'reference' in self._visible_roles() and not self._reference_detail():
            zooms.append(tr('参考用于全图外观比较'))
        set_text(self.zoom_label, '   ·   '.join(zooms))
        source = project.get('source', {})
        metadata = self._source_image_metadata or source.get('image_metadata') or source
        fmt = metadata.get('format') if metadata.get('format') in ('JPEG','PNG','TIFF','WEBP','BMP') else tr('原格式（未读取）')
        bits = metadata.get('bits', metadata.get('bit_depth'))
        depth = tr('{0} 位', bits) if type(bits) is int and bits in (8,16) else tr('位深未读取')
        color = metadata.get('color', {})
        icc = (tr('保留原 ICC') if color.get('icc_present') is True else
               tr('无嵌入 ICC · 按 sRGB 处理') if color.get('icc_present') is False else tr('ICC 未读取'))
        dimensions = grid or metadata
        size = f"{dimensions['width']} × {dimensions['height']}" if dimensions and 'width' in dimensions and 'height' in dimensions else tr('尺寸未读取')
        set_text(self.export_summary, f'{fmt} · {size} · {depth}\n{icc}')
        set_text(self.export_summary, tr('导出保留原图格式、尺寸、位深和已有 ICC。') +
            (tr('\nICC：') + color['icc_description'] if isinstance(color.get('icc_description'), str) else ''), 'setToolTip')

    def _continue_display_preview(self):
        if self._initial_native_center is not None and self._source_grid and self._detail_capable():
            center, self._initial_native_center = self._initial_native_center, None
            self._set_detail_roi(*center)
        elif self._display_refresh_pending or (self.pending_alpha is not None and 'result' in self._visible_roles()):
            self._request_result()
        elif self.export_pending and self.last_result_alpha == self.slider.value()/100:
            self.export_pending = False
            self._export_clicked()


    def _sync_controls(self):
        if not hasattr(self, 'progress'):
            return
        idle = self.busy is None
        input_idle = idle and not self.preview_inflight
        resident = self.binding is not None
        pair = bool(self.source_edit.text().strip() and self.reference_edit.text().strip())
        portable = bool(self.project and not self.project.get('metadata_only', False))
        writable = not (self.project or {}).get('read_only', False)
        geometry_message = self._fine_geometry_message()
        valid = not self._import_errors and not geometry_message and not self._import_pending
        self._sync_status_alert(geometry_message)
        missing = self.project is None and not pair
        primary = tr('导入原图…') if not self.source_edit.text().strip() else tr('导入参考…') if not self.reference_edit.text().strip() else tr('开始仿色')
        set_text(self.process_button, primary if self.project is None else tr('开始仿色'))
        self.process_button.setVisible(not portable and not resident)
        self.process_button.setEnabled(idle and not resident and writable and (missing or valid and (bool(self.project) or pair)))
        set_text(self.process_button, geometry_message or tr('选齐原图和参考后开始；工程会自动保存'), 'setToolTip')
        self.load_button.setVisible(portable and not resident); self.load_button.setEnabled(idle and not resident and portable and writable)
        self.adjustments.setVisible(resident)
        self.export_button.setVisible(resident)
        from grademirror.engine.profiles import R0
        can_refine=(resident or portable and getattr(self.backend,"COLD_REFINEMENT",False)) and writable and (self.project or {}).get('profile_id')==R0
        self.refine_button.setVisible(can_refine);self.refine_button.setEnabled(can_refine and idle and not self.preview_inflight)
        for button in (self.source_browse,self.reference_browse): button.setEnabled(input_idle)
        for role,pane in (('source',self.source_pane),('reference',self.reference_pane)):
            pane.setEnabled(True)
            pane.selection_enabled = input_idle and not resident and pane._image is None
            pane.drop_enabled = input_idle
            self.materials[role].drop_enabled = input_idle
            self.materials[role].view_button.setEnabled(pane._image is not None or pane._proxy is not None)
        self.result_pane.setEnabled(True)
        self.new_panel.setVisible(self.project is None)
        self.project_info.setVisible(True)
        self.destination_edit.setVisible(self.project is None); self.destination_browse.setVisible(self.project is None)
        self.project_edit.setVisible(self.project is not None)
        self.mode_selector.setEnabled(idle and self.project is None)
        self.destination_edit.setEnabled(idle); self.destination_browse.setEnabled(idle)
        ready = resident and idle and not self.preview_inflight
        strength_ready = resident and self.busy not in {'export','release','previews'}
        self.slider.setEnabled(strength_ready); self.strength_spin.setEnabled(strength_ready); self.strength_reset.setEnabled(strength_ready)
        self.fit_button.setEnabled((idle and not self.preview_inflight) or resident)
        self.roi_button.setEnabled(ready and self._detail_grid() is not None and self._detail_capable())
        set_text(self.roi_button, tr('1 个参考像素对应 1 个物理显示像素 · Ctrl+1') if self._reference_detail()
                 else tr('1 个源像素对应 1 个物理显示像素 · Ctrl+1') if self._detail_capable() else tr('参考用于全图外观比较；100%仅用于原图和结果'), 'setToolTip')
        for pane in (self.source_pane,self.result_pane):
            pane.inspect_enabled = ready and self._detail_capable() and not pane.isHidden()
            pane.pan_enabled = resident and idle and self.roi is not None and not pane.isHidden()
            pane.photo.setCursor((Qt.ClosedHandCursor if pane._drag_origin is not None else Qt.OpenHandCursor) if pane.pan_enabled else Qt.CrossCursor if pane.inspect_enabled else Qt.ArrowCursor)
            set_text(pane.photo, tr('100% 时拖动；单击定位细节。更换图片请使用对应素材条目。') if resident else tr('空白区域点击选图；载入后单击不会替换素材'), 'setToolTip')
        self.export_button.setEnabled(ready and self.pending_alpha is None and self.last_result_alpha == self.slider.value()/100)
        self.export_browse.setEnabled(resident and idle); self.export_edit.setEnabled(resident and idle)
        self.release_button.setVisible(resident); self.release_button.setEnabled(resident and self.busy != 'release')
        running = self.active_operation is not None and self.busy in {'fit','replay','export'}
        self.cancel_button.setVisible(running); self.cancel_button.setEnabled(running and not self._cancel_pending)
        set_text(self.elapsed_label, tr('已用 {0} 秒', int(time.monotonic()-self._active_started)) if running and self._active_started is not None else '')
        working = not idle or self.preview_inflight or any(not f.done() for _,f,_,_ in self._tasks)
        self.progress.setRange(0,0 if working else 1)
        if not working: self.progress.setValue(0)
        if self.project_path and self.project:
            from grademirror.engine.profiles import FINE
            mode = tr('历史算法') if self.project.get('algorithm_retired') else tr('精细') if self.project.get('profile_id') == FINE else tr('快速')
            set_text(self.project_info, tr('工程已保存 · {0} · {1}', self.project_path.name, mode))
            set_text(self.project_info, str(self.project_path), 'setToolTip')
            fine = self.project.get('profile_id') == FINE
            set_text(self.load_button, tr('重建精细预览') if fine else tr('恢复仿色结果'))
            set_text(self.load_button, tr('重新渲染与精细后处理，不重新拟合；耗时较长') if fine else tr('读取已保存参数，重新渲染结果'), 'setToolTip')
        elif self.project is None:
            set_text(self.project_info, tr('开始仿色时自动保存工程。'))
        set_text(self.action_note, tr('检查图片中…') if self._import_pending else (tr('请更换无法导入的素材。') if self._import_errors else
            tr('先选择缺少的素材；可点击空白画布或拖入。') if missing else tr('工程会自动保存。') if not resident else tr('0% 为原图；100% 为本次完整仿色。')))
        self._command_states = dict(new=idle or self.active_operation is not None, open=idle or self.active_operation is not None,
            save=idle and writable and (self.project is not None or pair and valid), create=idle and pair and valid and not resident,
            **{'result-view':resident, 'source-result-view':resident, 'reference-result-view':resident,
               'layout-horizontal':len(self._visible_roles())==2, 'layout-vertical':len(self._visible_roles())==2,
               'return-view':self.display.temporary, 'sidebar':True,
               'inspect-source':self.materials['source'].view_button.isEnabled(),
               'inspect-reference':self.materials['reference'].view_button.isEnabled()})
        self._update_photo_info()
        if hasattr(self,'commands'): self.commands.sync()

    def _input_changed(self):
        self._sync_controls()

    def _sync_status_alert(self, geometry_message):
        # Track ownership by the live text binding, including across language changes.
        # A control refresh must not replace an unrelated error with geometry feedback.
        current = bound_text(self.status_label)
        if (geometry_message and self.busy is None and not self._import_errors
                and current is not self._error_status):
            self._geometry_status = geometry_message
            set_text(self.status_label, geometry_message)
        elif self._geometry_status is not None and current is self._geometry_status and not geometry_message:
            set_text(self.status_label, tr('选择原图与调色参考，开始一组新的仿色。'))
        current = bound_text(self.status_label)
        alert = ((self._geometry_status is not None and current is self._geometry_status)
                 or (self._error_status is not None and current is self._error_status))
        self.status_label.setStyleSheet('color: #efb1a7;' if alert else '')

    def _fine_geometry_message(self):
        if self.project is not None or self.mode_selector.currentData() != "fine":
            return ""
        from grademirror.fine_contract import geometry_issues
        known = {}
        for role, field in (("source", self.source_edit), ("reference", self.reference_edit)):
            info = self._import_geometry.get(role)
            if info and field.text().strip() and Path(info["path"]) == Path(field.text().strip()):
                known[role] = info
        issues = geometry_issues(**known)
        return tr('；').join(geometry_text(issue) for issue in issues) + tr("。请更换尺寸符合限制的图片。") if issues else ""

    def _save_clicked(self):
        if self.busy is not None:
            return
        if self.project is None:
            self._create_clicked()
        else:
            if self._save_ui_state():
                set_text(self.status_label, tr("工程与当前强度已保存。"))

    def _file_dialog(self, kind, title, initial="", file_filter=""):
        args=(self,title,initial)
        if kind=="directory":
            return QFileDialog.getExistingDirectory(*args)
        if kind=="open":
            return QFileDialog.getOpenFileName(*args,file_filter)[0]
        flags=QFileDialog.Option.DontConfirmOverwrite if kind=="export" else QFileDialog.Option(0)
        return QFileDialog.getSaveFileName(*args,file_filter,options=flags)[0]

    def _choose_open_project(self):
        if self.busy is not None and self.active_operation is None:
            return
        path = self._file_dialog("directory", tr("打开 GradeMirror 工程"), str(self.root / "projects"))
        if path:
            selected = Path(path).resolve()
            if self.active_operation is not None:
                self._request_switch(lambda: self._open_project_path(selected))
            elif self.binding is not None:
                self._after_release = lambda: self._open_project_path(selected)
                self._release_clicked()
            else:
                self._open_project_path(selected)

    def _keep_alive(self):
        if (self.binding is None or self.busy is not None or self.preview_inflight or
                self._tasks or self._keepalive_inflight or not hasattr(self.backend, "keep_alive")):
            return
        self._keepalive_inflight = True
        store, job, attempt, binding = self.store_root, self.job_id, self.attempt_id, dict(self.binding)
        def complete(reply, error):
            self._keepalive_inflight = False
            if error:
                self._error(tr("保持预览"), error)
        self._run("keep_alive", lambda: self.backend.keep_alive(store, job, attempt, binding), complete)

    def _choose_export_image(self):
        if self.binding is not None and self.busy is None and self._browse_export():
            self._export_clicked()

    def _browse_export(self):
        if self.binding is None or self.busy is not None:
            return
        suffix = self._source_suffix()
        path = self._file_dialog("export", tr("导出原尺寸仿色结果"),
            self.export_edit.text(), tr('{0} 图片 (*{1})', suffix.lstrip('.').upper(), suffix))
        if path:
            destination = Path(path)
            if not destination.suffix:
                destination = destination.with_suffix(suffix)
            if destination.exists():
                self._error(tr("导出"), ValueError(tr("目标文件已存在，请使用新的文件名。")))
                return
            set_text(self.export_edit, str(destination))
            set_text(self.export_edit, str(destination), 'setToolTip')
            return True
        return False

    def _source_suffix(self):
        source = (self.project or {}).get("source", {})
        suffix = Path(source.get("name", self.source_edit.text() or "source.tiff")).suffix.lower()
        return suffix if suffix in self._image_suffixes else ".tiff"

    def _support_log_info(self):
        from .branding import desktop_version
        return {"version": str(desktop_version())}

    def _choose_export_log(self):
        from .support_log import write_support_log

        default = Path.home() / f"GradeMirror-logs-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        path = self._file_dialog("save", tr("导出诊断日志…"), str(default), tr("诊断日志 ZIP (*.zip)"))
        if not path:
            return
        destination = Path(path)
        if destination.suffix.lower() != ".zip":
            destination = destination.with_name(destination.name + ".zip")
            if destination.exists():
                self._error(tr("导出诊断日志"), FileExistsError(tr("目标日志 ZIP 已存在。请选择新的文件名。")))
                return
        store, receipt, events = self.store_root, self.receipt_path, list(self.events)
        info = self._support_log_info()

        def complete(_, error):
            if error:
                self._error(tr("导出诊断日志"), error)
            else:
                set_text(self.status_label, tr("诊断日志已保存。"))

        self._run("support_log", lambda: write_support_log(
            destination, self.root, store=store, receipt=receipt, events=events, **info), complete)

    def _log(self, event: str, **fields):
        row = {"at": time.time(), "event": event, **fields}
        self.events.append(row)
        line = json.dumps(row, ensure_ascii=False, sort_keys=True)
        self.diagnostics.append(line)
        self._diagnostic_writer.append(line)

    def _run(self, kind: str, fn, done, *, epoch=None):
        current_epoch = self._epoch if epoch is None else epoch
        future = self.executor.submit(fn)
        self._tasks.append((kind, future, done, current_epoch))
        self._log("worker_started", kind=kind)
        self._sync_controls()

    def _poll_tasks(self):
        previous, self._tasks = self._tasks, []
        for kind, future, done, epoch in previous:
            if not future.done():
                self._tasks.append((kind, future, done, epoch))
                continue
            error = None
            try:
                result = future.result()
            except Exception as exc:
                result, error = None, exc
            if epoch == self._epoch:
                try:
                    done(result, error)
                except Exception as exc:
                    self.busy = None
                    self._error(tr("更新界面"), exc)
        self._sync_controls()
        if (self._release_pending and self.binding is not None and self.busy is None
                and not self.preview_inflight and not self._tasks):
            self._release_clicked()
        if (self._release_pending and self.binding is None and self.busy is None and not self._tasks):
            self._release_pending = False
            continuation, self._after_release = self._after_release, None
            if continuation:
                continuation()
        self._check_close_deadline()
        if (self._display_refresh_pending and self.binding is not None and self.busy is None
                and not self.preview_inflight):
            self._display_refresh_pending = False
            self._request_result()
        if self._closing and self.busy is None and not self._tasks and not self.preview_inflight:
            self.close()

    def _error(self, action: str, exc):
        code = getattr(exc, "code", type(exc).__name__)
        messages = {
            "COLOR_ICC_UNSUPPORTED": tr("这张图片使用非 sRGB 色彩配置。请先导出为 sRGB 后重试。"),
            "COLOR_ICC_MALFORMED": tr("图片的 ICC 色彩配置损坏，请重新导出图片。"),
            "PNG_RGB_SCHEMA": tr("当前支持 RGB PNG；请先将透明、灰度或索引色图片转换为 RGB。"),
            "IMAGE_RGB_SCHEMA": tr("当前处理需要 RGB 图片；请先转换为 RGB。"),
            "TIFF_RGB_SCHEMA": tr("这张 TIFF 的通道或方向未受支持，请导出标准 RGB TIFF 后重试。"),
            "IMAGE_FORMAT_MISMATCH_OR_ANIMATED": tr("图片格式与扩展名不符，或文件包含动画。请使用单张静态图片。"),
            "OUTPUT_EXISTS": tr("目标文件已存在，请选择新的文件名。"),
            "OUTPUT_PATH_INVALID": tr("保存路径无效，请通过「另存」选择一个可写入的位置。"),
            "RESEARCH_OUTPUT_FORBIDDEN": tr("这个目录是只读研究资料，请选择其他工程或导出位置。"),
            "RESIDENT_POOL_BUSY": tr("另一个仿色会话仍占用内存。请在对应窗口清除预览或关闭窗口后重试。"),
            "RAM_POOL_LIMIT": tr("处理时的内存占用超过本次任务上限。可在「帮助」中导出诊断日志。"),
            "CUDA_DEVICE_UNAVAILABLE": tr("未找到可用的 NVIDIA CUDA 显卡。"),
            "CANCELLED": tr("任务已取消；已保存的工程可继续使用。"),
            "CANCELLED_WAITING": tr("任务已取消。"),
            "PROJECT_NOT_NEW_PAIR": tr("工程已有仿色参数，请使用「恢复仿色结果」。"),
            "EXPORT_SOURCE_FORMAT_MISMATCH": tr("导出格式必须与原图保持一致。"),
            "EXPORT_DESTINATION_EXTENSION_MISMATCH": tr("请使用与原图相同格式的文件扩展名。"),
            "PREVIEW_STALE": tr("预览请求已更新，请重试一次强度调整。"),
            "ProjectExistsError": tr("这个工程位置已存在。请选择新的工程位置，或打开已有工程。"),
            "ProjectFormatError": tr("所选目录不是有效的 GradeMirror 工程。请选择以 .grademirror 结尾的工程目录。"),
            "ProjectIdentityMismatch": tr("原图或工程内参考文件发生变化，文件身份与保存记录不一致。请重新选择原图与参考图创建工程。"),
            "IMAGE_INSPECTION_FAILED": tr("图片未能读取，请检查文件是否存在、能否正常打开，或更换一张图片。"),
        }
        original = exc.args[0] if getattr(exc,'args',()) else str(exc) or code
        message = messages.get(code, external_text(original) if isinstance(original,str) else str(original))
        status = tr('{0}：{1}', action, message)
        if code == 'FINE_GEOMETRY_UNSUPPORTED' and self._fine_geometry_message():
            self._geometry_status = status
        else:
            self._error_status = status
        set_text(self.status_label, status)
        detail = getattr(exc, "diagnostics", None)
        self._log("error", action=action, code=code, message=str(exc), **({"failure": detail} if detail else {}))
        if code in {"RESULT_NOT_RESIDENT", "RESULT_LEASE_INVALID", "RESULT_UNAVAILABLE"}:
            continuation, self._after_release = self._after_release, None
            self._clear_result()
            set_text(self.status_label, tr("结果已释放，需要重新载入。"))
            if continuation:
                continuation()
        self._sync_controls()

    def _ui_state_path(self, path: Path):
        return self.root / "jobs/gm004b-ui-state" / (hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest() + ".json")

    def _save_ui_state(self):
        if self.project_path is None or self.project is None:
            return
        path = self._ui_state_path(self.project_path)
        from grademirror.common import atomic_json
        self._remember_detail()
        record = {"schema_version": "gm004b-ui-v2", "project_path": str(self.project_path),
                  "project_revision": self.project.get("revision_id", self.project.get("current_revision")),
                  "alpha": self.slider.value() / 100, "roi": list(self.roi) if self.roi else None,
                  "export_directory": str(Path(self.export_edit.text()).parent) if self.export_edit.text() else None,
                  "display": self.display.to_record()}
        try:
            atomic_json(path, record)
        except OSError as exc:
            self._error(tr("保存界面设置"), exc)
            return False
        return True

    def _restore_ui_state(self):
        self.slider.setValue(100)
        path = self._ui_state_path(self.project_path)
        if not path.is_file():
            return
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(row, dict) or row.get('schema_version') not in ('gm004b-ui-v1','gm004b-ui-v2')
                    or (row.get('project_path'),row.get('project_revision')) !=
                    (str(self.project_path),self.project['revision_id'])):
                return
            from .view_state import DisplayState
            self.display = DisplayState.from_record(row.get('display'))
            self._restored_object = self.display.object if self.display.object != 'prepare' else None
            alpha = row.get("alpha")
            if type(alpha) in (float, int) and 0 <= alpha <= 1:
                self.slider.setValue(round(alpha * 100))
            roi = row.get("roi")
            if (isinstance(roi, list) and len(roi) == 4 and all(type(v) is int for v in roi)
                    and 0 <= roi[0] < roi[2] <= 210_000_000 and 0 <= roi[1] < roi[3] <= 210_000_000):
                self.roi = tuple(roi)
                self.display.remember_center((roi[0]+roi[2])/2,(roi[1]+roi[3])/2)
                self.display.native_preferred = True
            self._apply_display_layout()
            self._log("ui_state_restored", alpha=self.slider.value() / 100, roi=self.roi)
        except (ValueError, OSError, KeyError):
            self._log("ui_state_invalid")

    def _clear_result(self):
        self._preview_superseded.set()
        self._debounce.stop()
        self._native_debounce.stop()
        self._viewport_debounce.stop()
        self._native_cache = NativePreviewCache()
        self._reference_center = None
        self._fit_cache = ExactPreviewCache()
        self._native_refresh_pending = False
        self._pan_anchor = None
        self.binding = None
        self.job_id = self.attempt_id = None
        self.last_result_alpha = None
        self.pending_alpha = None
        self.preview_inflight = False
        self.export_pending = False
        self._source_grid = None
        self._source_image_metadata = {}
        self._keepalive_inflight = False
        self.slider.setEnabled(False)
        self.export_button.setEnabled(False)
        self.roi_button.setEnabled(False)
        self.release_button.setEnabled(False)
        self.result_pane.clear()
        self.roi = None
        self.display.clear_document()
        self._display_generation += 1
        self._display_refresh_pending = False
        self._restored_object = self._initial_native_center = None
        self._apply_display_layout()
        self._sync_controls()

    def _toggle_new_project(self):
        if self.active_operation is not None:
            self._request_switch(self._reset_workspace)
            return
        if self.busy is not None:
            return
        if self.binding is not None:
            self._after_release = self._reset_workspace
            self._release_clicked()
            return
        self._reset_workspace()

    def _reset_workspace(self):
        self._epoch += 1
        self._clear_result()
        self.project = self.project_path = None
        self.mode_selector.setCurrentIndex(0)
        self.roi = None
        self._import_errors.clear()
        self._import_geometry.clear()
        self._import_pending.clear()
        for role in self._import_tokens:
            self._import_tokens[role] += 1
        for edit in (self.source_edit, self.reference_edit, self.destination_edit,
                     self.project_edit, self.export_edit):
            edit.clear()
        for pane in (self.source_pane, self.reference_pane):
            pane.clear()
        set_text(self.source_pane.detail, tr("点击选原图，或拖入这里"))
        set_text(self.reference_pane.detail, tr("点击选图，或拖入同一照片的调色参考"))
        set_text(self.project_title, tr("新的仿色"))
        self.slider.setValue(100)
        set_text(self.status_label, tr("选择原图与 AI 参考图，点击「开始仿色」。工程会自动保存。"))
        self._log("new_workspace")
        self._sync_controls()

    def _suggest_destination(self, source: Path):
        if not self.destination_edit.text().strip():
            folder = self.root / "projects"
            destination = folder / f"{source.stem}.grademirror"
            index = 2
            while destination.exists():
                destination = folder / f"{source.stem}-{index}.grademirror"
                index += 1
            set_text(self.destination_edit, str(destination))

    def _set_import_image(self, role: str, path: str):
        if self.busy is not None:
            set_text(self.status_label, tr("当前任务正在进行，请完成或取消后再更换图片。"))
            return
        if self.binding is not None:
            self._after_release = lambda: self._set_import_image(role, path)
            self._release_clicked()
            return
        suffixes = EngineBackend.IMAGE_SUFFIXES
        if Path(path).suffix.lower() not in suffixes:
            self._error(tr("导入图片"), ValueError(tr("支持 RGB JPEG、PNG、TIFF、WebP、BMP")))
            return
        if self.project is not None:
            self._epoch += 1
            self.project = self.project_path = None
            self.project_edit.clear()
            self.destination_edit.clear()
            self.roi = None
            self._clear_result()
            other_role = "reference" if role == "source" else "source"
            other_field = self.reference_edit if role == "source" else self.source_edit
            if other_field.text().strip():
                self._request_import_preview(other_role, Path(other_field.text().strip()))
        self._import_errors.pop(role, None)
        if role == "source":
            self.source_pane.clear()
            set_text(self.source_edit, path)
            self._suggest_destination(Path(path))
            set_text(self.source_pane.photo, tr('原图已选择\n{0}', Path(path).name))
            set_text(self.source_pane.detail, tr("原文件保持只读；保存工程后记录链接"))
        else:
            self.reference_pane.clear()
            set_text(self.reference_edit, path)
            set_text(self.reference_pane.photo, tr('参考图已选择\n{0}', Path(path).name))
            set_text(self.reference_pane.detail, tr("保存工程时嵌入参考图原始字节"))
        set_text(self.project_title, tr("新的仿色"))
        self.new_panel.show()
        self._log("image_selected", role=role, path=path)
        self._request_import_preview(role, Path(path))
        set_text(self.status_label, tr("检查导入图片…"))
        self._sync_controls()

    def _request_import_preview(self, role: str, path: Path):
        pane = self.source_pane if role == "source" else self.reference_pane
        label = tr("原图") if role == "source" else tr("参考图")
        self._import_tokens[role] += 1
        token = self._import_tokens[role]
        self._import_pending[role] = token
        self._import_geometry.pop(role, None)
        side = self._fit_preview_side(role)
        def complete(value, error):
            if self._import_pending.get(role) == token:
                self._import_pending.pop(role, None)
            if self.binding is not None or token != self._import_tokens[role]:
                return
            if error:
                self._import_errors[role] = external_text(error.args[0]) if getattr(error,'args',()) and isinstance(error.args[0],str) else str(error)
                pane.clear()
                set_text(pane.detail, tr('{0}无法导入，请更换文件', label))
                self._log("import_preview_unavailable", role=role, path=str(path),
                          message=str(error))
                self._error(tr('导入{0}', label), error)
                return
            self._import_errors.pop(role, None)
            image, (width, height) = value
            self._import_geometry[role] = {"path": str(path), "width": width, "height": height}
            pane.set_image(image, native=False,
                           detail=tr('{0} × {1} · 导入预览', width, height))
            pane.image_grid = {"width": width, "height": height}
            self._update_photo_info()
            self._log("import_preview_ready", role=role, path=str(path),
                      width=width, height=height)
            if (self.busy is None and not self._import_errors
                    and bound_text(self.status_label) is not self._error_status):
                set_text(self.status_label, tr("图片已导入。选齐原图与参考图后，点击「开始仿色」。") if self.project is None
                    else tr("工程已打开，可继续仿色或恢复结果。"))
            self._sync_controls()
        def work():
            options = {"max_side": side} if getattr(self.backend, "SIZED_PREVIEWS", False) else {}
            return self.backend.import_preview(path, label, **options)
        self._run("import_preview", work, complete)

    def _accept_dropped_image(self, role: str, path: str):
        if Path(path).is_file():
            self._set_import_image(role, path)
        else:
            self._error(tr("导入图片"), ValueError(tr("拖入的文件不存在")))

    def _browse_image(self, role: str):
        caption = tr("选择原图") if role == "source" else tr("选择 AI 参考图")
        file_filter = tr("图片 (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp)")
        path = self._file_dialog("open", caption, "", file_filter)
        if path:
            self._set_import_image(role, path)

    def _browse_destination(self):
        initial = self.destination_edit.text().strip() or str(self.root / "projects" / "new.grademirror")
        path = self._file_dialog("save", tr("选择新工程位置"), initial,
                                 tr("GradeMirror 工程 (*.grademirror)"))
        if path:
            destination = Path(path)
            if destination.suffix.lower() != ".grademirror":
                destination = destination.with_name(destination.name + ".grademirror")
            set_text(self.destination_edit, str(destination))

    def _show_metadata_project(self, view):
        source = view["source"]["name"]
        reference = view["reference"]["name"]
        set_text(self.source_pane.photo, tr('原图已关联\n{0}', source))
        set_text(self.source_pane.detail, tr("原图保持链接，身份已核对"))
        set_text(self.reference_pane.photo, tr('参考图已导入\n{0}', reference))
        set_text(self.reference_pane.detail, tr("参考图已嵌入工程"))
        set_text(self.result_pane.photo, tr("尚无计算结果"))
        set_text(self.result_pane.detail, tr("点击「开始仿色」生成结果"))
        set_text(self.source_edit, view["source"]["canonical_path"])
        set_text(self.reference_edit, view["reference"]["local_path"])
        self._request_import_preview("source", Path(view["source"]["canonical_path"]))
        self._request_import_preview("reference", Path(view["reference"]["local_path"]))

    def _create_clicked(self, checked=False, *, start_after=False):
        if self.busy is not None or self.binding is not None:
            return
        source_text = self.source_edit.text().strip()
        reference_text = self.reference_edit.text().strip()
        destination_text = self.destination_edit.text().strip()
        if not destination_text and source_text:
            self._suggest_destination(Path(source_text))
            destination_text = self.destination_edit.text().strip()
        if not all((source_text, reference_text, destination_text)):
            self._error(tr("新建工程"), ValueError(tr("请选择原图、AI 参考图和工程位置")))
            return
        selected_mode=self.mode_selector.currentData()
        geometry_message = self._fine_geometry_message()
        if geometry_message:
            from grademirror.common import GMError
            self._error(tr("新建工程"), GMError("FINE_GEOMETRY_UNSUPPORTED", geometry_message, 3))
            return
        source, reference, destination = map(Path,
            (source_text, reference_text, destination_text))
        self.busy = "create"
        self.commands.actions['save'].setEnabled(False)
        self.commands.actions['open'].setEnabled(False)
        set_text(self.status_label, tr("正在检查图片并保存新工程…"))
        self._log("create_clicked", source=str(source), reference=str(reference),
                  destination=str(destination), mode=selected_mode)

        def complete(view, error):
            self.busy = None
            self.commands.actions['save'].setEnabled(True)
            self.commands.actions['open'].setEnabled(True)
            if error:
                self._error(tr("新建工程"), error)
                return
            self._epoch += 1
            self._clear_result()
            self.project_path = destination.resolve()
            self.project = view
            from grademirror.engine.profiles import get_profile, R0
            label=tr("历史算法（已停用）") if view.get("algorithm_retired") else get_profile(view.get("profile_id") or R0)["label"]
            set_text(self.project_edit, tr("算法：") + label + (" / C_REVERT_XY_DECAY / XY decay=0.01" if not view.get("algorithm_retired") else tr("；请另建 R0 工程重新仿色")), 'setToolTip')
            set_text(self.project_edit, str(self.project_path))
            self.new_panel.hide()
            self.load_button.setEnabled(False)
            self.process_button.setEnabled(True)
            self._show_metadata_project(view)
            set_text(self.status_label, tr("工程已保存；点击「开始仿色」生成结果。"))
            self._log("project_created", project=str(self.project_path),
                      revision=view["revision_id"], source_verified=True)
            if start_after:
                self._process_new_clicked()
        self._run("create", lambda: self.backend.create_project(source, reference,
            destination,mode=selected_mode) if selected_mode=="fine" else self.backend.create_project(source,reference,destination), complete)

    def _open_clicked(self):
        if self.busy is not None and self.active_operation is None:
            return
        raw = self.project_edit.text().strip()
        if not raw or (self.project is not None and raw == str(self.project_path)):
            raw = self._file_dialog("directory", tr("选择 .grademirror 工程"))
        if not raw:
            return
        path = Path(raw).resolve()
        if self.active_operation is not None:
            self._request_switch(lambda: self._open_project_path(path))
            return
        if self.binding is not None:
            self._after_release = lambda: self._open_project_path(path)
            self._release_clicked()
            return
        self._open_project_path(path)

    def _open_project_path(self, path):
        previous_project, previous_path = self.project, self.project_path
        self._epoch += 1
        self._clear_result()
        self.project = None
        self._import_errors.clear()
        self.project_path = path
        set_text(self.project_edit, str(path))
        self.busy = "open"
        self.load_button.setEnabled(False)
        self.process_button.setEnabled(False)
        set_text(self.status_label, tr("正在检查工程与源文件身份…"))
        self._log("open_clicked", project=str(path))

        def complete(view, error):
            self.busy = None
            if error:
                self.project, self.project_path = previous_project, previous_path
                set_text(self.project_edit, str(previous_path) if previous_path else "")
                self._error(tr("打开工程"), error)
                return
            self.project = view
            self.new_panel.hide()
            metadata_only = view.get("metadata_only", False)
            from grademirror.engine.profiles import get_profile, R0
            label=tr("历史算法（已停用）") if view.get("algorithm_retired") else get_profile(view.get("profile_id") or R0)["label"]
            set_text(self.project_edit, tr("算法：") + label + (" / C_REVERT_XY_DECAY / XY decay=0.01" if not view.get("algorithm_retired") else tr("；请另建 R0 工程重新仿色")), 'setToolTip')
            self.load_button.setEnabled(not view.get("read_only", True) and not metadata_only)
            self.process_button.setEnabled(metadata_only and not view.get("read_only", True))
            self._restore_ui_state()
            self.roi = None
            if view.get("algorithm_retired"):
                set_text(self.source_pane.photo, tr("旧工程只读：仿色算法已停用"))
                set_text(self.reference_pane.photo, tr("原参考文件保留"))
                set_text(self.result_pane.photo, tr("请使用原图与参考图另建 R0 工程"))
                set_text(self.status_label, tr("旧算法已停用；原工程和已有导出保留。请新建 R0 工程重新仿色。"))
            elif metadata_only:
                self._show_metadata_project(view)
                set_text(self.status_label, tr("工程已打开；点击「开始仿色」继续。"))
            elif view.get("read_only"):
                set_text(self.status_label, tr("工程只读：所需运行时不可用。"))
            else:
                set_text(self.status_label, tr("工程已打开；点击「恢复仿色结果」继续调整与导出。"))
                if (view.get("source", {}).get("canonical_path") and
                        view.get("reference", {}).get("local_path")):
                    self._show_metadata_project(view)
                    set_text(self.result_pane.photo, tr("已保存仿色参数\n\n点击「恢复仿色结果」继续"))
            self._log("project_opened", revision=view.get("revision_id"), read_only=view.get("read_only"),
                      source_verified=view.get("source", {}).get("identity_verified"))
        self._run("open", lambda: self.backend.open_project(path), complete)

    def _process_new_clicked(self):
        if self.busy is None and self.project is None:
            self._create_clicked(start_after=True)
            return
        if (self.busy is not None or self.project is None or
                self.project.get("read_only") or not self.project.get("metadata_only") or self.binding is not None):
            return
        run = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        pending = getattr(self, "_pending_project_publication", None)
        if pending and pending[0] == self.project_path:
            self.store_root, self.output_root, self.replay_key = pending[1:]
        else:
            self.store_root, self.output_root = self._new_pair_paths(run)
            self.replay_key = f"gm-new-fit-{run}"
        self.busy = "fit"
        active = self._begin_active("fit", self.replay_key)
        self._cancel_pending = False
        self.process_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        set_text(self.status_label, tr("正在复用快速参数，继续精细处理…") if self.project.get("refinement_pending") else tr("正在学习参考图的色彩并生成原尺寸结果…"))
        self._log("fit_clicked", store=str(self.store_root), output=str(self.output_root),
                  request_key=self.replay_key)
        self._stage_timer.start()

        def complete(value, error):
            if active is not self.active_operation:
                return
            if value and not error:
                try:
                    if not active.address(value["result"]):
                        return
                except ValueError as issue:
                    error = issue
            self._end_active(active)
            self.busy = None
            self._cancel_pending = False
            self.cancel_button.setEnabled(False)
            if error:
                self.process_button.setEnabled(True)
                if getattr(error, "code", None) == "MIXED_RESULT_PENDING_PUBLICATION":
                    self._pending_project_publication = (
                        self.project_path, self.store_root, self.output_root, self.replay_key)
                self._error(tr("处理新图"), error)
                def recovered(view, failure):
                    if not failure and view:
                        self.project = view
                        if not view.get("metadata_only", True):
                            set_text(self.status_label, tr("仿色参数已保存，点击「恢复仿色结果」继续。"))
                self._run("refresh_project", lambda: self.backend.open_project(self.project_path), recovered)
                if self._closing:
                    self.close()
                return
            reply = value["result"]
            self._pending_project_publication = None
            self.project = value["project"]
            if reply.get("state") in ("CANCELLED","FAILED"):
                self.process_button.setEnabled(bool(self.project.get("metadata_only")))
                self.load_button.setEnabled(not self.project.get("metadata_only"))
                set_text(self.status_label, tr("处理已取消") if reply["state"]=="CANCELLED" else tr("处理失败，请查看诊断。"))
                if self._closing:
                    self.close()
                return
            self.slider.setValue(100)
            self.process_button.setEnabled(False)
            self.load_button.setEnabled(True)
            self.job_id, self.attempt_id = reply["job_id"], reply["attempt_id"]
            if not reply.get("binding"):
                set_text(self.status_label, tr("仿色参数已保存；点击「恢复仿色结果」继续。") if reply.get("state") == "SUCCEEDED"
                                          else tr("同一任务已受理，请稍后查看工程状态。"))
                return
            self.binding = dict(reply["binding"])
            self._set_source_metadata(reply)
            self.output_root = Path(reply["output_root"])
            self._set_export_default()
            self.slider.setEnabled(True)
            self.roi_button.setEnabled(True)
            self.release_button.setEnabled(True)
            set_text(self.status_label, tr("新图处理完成；正在载入预览…"))
            self._log("fit_committed", job_id=self.job_id, attempt_id=self.attempt_id,
                      binding=self.binding, receipt_path=reply.get("receipt_path"))
            if self._closing or self._after_release is not None:
                self._release_pending = True
            else:
                self._start_initial_previews()
        self._run("fit", lambda: self.backend.fit_new_pair(self.project_path,
            self.store_root, self.output_root, self.replay_key), complete)

    def _refine_clicked(self):
        from grademirror.engine.profiles import R0
        if (self.busy is not None or self.preview_inflight or not self.project or
                self.project.get('read_only') or self.project.get('profile_id')!=R0 or
                self.binding is None and (self.project.get('metadata_only') or
                                          not getattr(self.backend,"COLD_REFINEMENT",False))):
            return
        source_project=self.project_path
        base=source_project.with_name(source_project.stem+'-fine.grademirror');destination=base;number=2
        while destination.exists():
            destination=base.with_name(base.stem+'-'+str(number)+base.suffix);number+=1
        self.busy='create_refinement'
        set_text(self.status_label,tr('正在保存精细工程并复用快速参数…'))
        def complete(view,error):
            self.busy=None
            if error:
                self._error(tr('继续精细处理'),error);return
            if self._closing or self._close_after_release:
                self.close();return
            def start():
                self._epoch+=1;self.project_path=destination.resolve();self.project=view
                set_text(self.project_edit,str(self.project_path));self.mode_selector.setCurrentIndex(1)
                self._show_metadata_project(view);self._process_new_clicked()
            self._request_switch(start)
        self._run('create_refinement',lambda:self.backend.create_refinement(source_project,destination),complete)

    def _new_pair_paths(self, run):
        return self.root / "jobs" / f"gm-new-{run}", self.root / "outputs" / f"gm-new-{run}"

    def _set_export_default(self):
        suffix = self._source_suffix()
        stem = Path((self.project or {}).get("source", {}).get("name", "GradeMirror")).stem
        set_text(self.export_edit, str(self.output_root / f"{stem}-GradeMirror{suffix}"))
        set_text(self.export_edit, self.export_edit.text(), 'setToolTip')
        set_text(self.export_button, tr('导出图片…'))

    def _load_clicked(self):
        if (self.busy is not None or self.project is None or self.project.get("read_only")
                or self.project.get("metadata_only") or self.binding is not None):
            return
        run = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.store_root = self.root / "jobs" / f"gm004b-{run}"
        self.output_root = self.root / "outputs" / f"gm004b-{run}"
        self.replay_key = f"gm004b-replay-{run}"
        self.busy = "replay"
        active = self._begin_active("replay", self.replay_key)
        self._cancel_pending = False
        self.load_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        set_text(self.status_label, tr("正在载入已有结果 · 不定进度"))
        self._log("load_clicked", store=str(self.store_root), output=str(self.output_root),
                  request_key=self.replay_key)
        self._stage_timer.start()

        def complete(reply, error):
            self._end_active(active)
            self.busy = None
            self._cancel_pending = False
            self.cancel_button.setEnabled(False)
            self.load_button.setEnabled(True)
            if error:
                self._error(tr("载入结果"), error)
                if self._closing:
                    self.close()
                return
            self.job_id, self.attempt_id = reply["job_id"], reply["attempt_id"]
            if not reply.get("binding"):
                set_text(self.status_label, tr("仿色参数已保存；点击「恢复仿色结果」继续。") if reply.get("state") == "SUCCEEDED"
                                          else tr("同一任务已受理，请稍后查看工程状态。"))
                return
            self.binding = dict(reply["binding"])
            self._set_source_metadata(reply)
            self.output_root = Path(reply["output_root"])
            self._set_export_default()
            self.slider.setEnabled(True)
            self.roi_button.setEnabled(True)
            self.release_button.setEnabled(True)
            self._log("replay_committed", job_id=self.job_id, attempt_id=self.attempt_id,
                      binding=self.binding, receipt_path=reply.get("receipt_path"))
            if self._closing or self._after_release is not None:
                self._release_pending = True
            else:
                self._start_initial_previews()
        self._run("replay", lambda: self.backend.replay(self.project_path, self.store_root,
            self.output_root, self.replay_key), complete)

    def _begin_active(self, kind, key):
        self._active_started = time.monotonic()
        self.active_operation = ActiveOperation(kind, key, self.store_root)
        return self.active_operation

    def _set_source_metadata(self, reply):
        self._source_image_metadata = reply.get('source_image_metadata') or {}
        if reply.get('source_metadata_error'):
            self._log('source_metadata_unavailable', message=reply['source_metadata_error'])

    def _end_active(self, active):
        finish=getattr(self.backend,"end_eta",None)
        if finish:
            finish(active)
        if self.active_operation is active:
            self._active_started = None
            self.active_operation = None
            self._stage_timer.stop()
            self._cancel_pending = False

    def _request_switch(self, continuation):
        self._after_release = continuation
        self._release_pending = True
        if self.active_operation is not None:
            self.active_operation.request_close()
            self._cancel_clicked()
        elif self.binding is not None:
            self._release_clicked()
        elif self.busy is None:
            self._release_pending = False
            self._after_release = None
            continuation()

    def _check_close_deadline(self):
        active = self.active_operation
        if (active is None or active.close_requested_at is None or active.forced_cleanup
                or time.monotonic() - active.close_requested_at < self.CLOSE_GRACE_SECONDS
                or not hasattr(self.backend, "abort_operation")):
            return
        active.forced_cleanup = True
        set_text(self.status_label, tr("取消等待超时，正在回收本次任务资源…"))
        def complete(reply, error):
            self._log("close_cleanup", request_key=active.request_key, result=reply,
                      error=str(error) if error else None)
            if active is not self.active_operation:
                return
            if reply:
                try:
                    if not active.address(reply):
                        active.forced_cleanup = False
                        return
                except ValueError as issue:
                    self._log("cleanup_address_rejected", message=str(issue))
                    return
            if reply and reply.get("cleanup_state") == "COMPLETE":
                if self.store_root == active.store:
                    self._clear_result()
            elif reply and reply.get("cleanup_state") == "STARTUP_PENDING":
                active.forced_cleanup = False
            elif error:
                self._error(tr("清理任务"), error)
        self._run("force_cleanup", lambda: self.backend.abort_operation(active.store,
            active.request_key, active.job_id, active.attempt_id), complete)

    def _poll_stage(self):
        active = self.active_operation
        if active is None or self._stage_inflight:
            return
        self._stage_inflight = True
        def complete(row, error):
            self._stage_inflight = False
            if active is not self.active_operation:
                return
            if row and not error:
                try:
                    if not active.address(row):
                        return
                except ValueError as issue:
                    self._log("progress_address_rejected", message=str(issue))
                    return
            if self._cancel_pending:
                return
            if error:
                set_text(self.status_label, tr('暂时无法读取任务进度，可取消等待；详见诊断。'))
                issue = (type(error).__name__, str(error))
                if getattr(self, '_last_progress_issue', None) != issue:
                    self._log('progress_read_error', error=issue[0], message=issue[1])
                self._last_progress_issue = issue
            elif row:
                self._last_progress_issue = None
                from .progress_text import task_progress_text
                update=getattr(self.backend,"eta_text",None)
                estimate=update(active,row) if update else None
                text=(tr("快速参数已保存，正在交接精细处理…")
                      if row.get("state")=="SUCCEEDED" and row.get("light_segment")=="fast"
                      else estimate or task_progress_text(active.kind,row))
                set_text(self.status_label,text)
        self._run("stage", lambda: self.backend.status_key(active.store, active.request_key), complete)

    def _cancel_clicked(self):
        active = self.active_operation
        if active is None or self.busy not in {"fit", "replay", "export"} or active.cancel_requested:
            return
        active.cancel_requested = True
        self.cancel_button.setEnabled(False)
        self._cancel_pending = True
        set_text(self.status_label, tr("正在取消导出…") if active.kind == "export" else tr("正在取消任务并释放资源…"))
        self._log("cancel_clicked", kind=active.kind, request_key=active.request_key,
                  job_id=active.job_id, attempt_id=active.attempt_id)
        self._send_cancel(active)

    def _send_cancel(self, active=None):
        active = active or self.active_operation
        if active is None or active is not self.active_operation or not self._cancel_pending:
            return
        def complete(reply, error):
            if active is not self.active_operation:
                return
            self._log("cancel_response", response=reply, error=str(error) if error else None)
            if reply:
                try:
                    if not active.address(reply):
                        QTimer.singleShot(200, lambda: self._send_cancel(active))
                        return
                except ValueError as issue:
                    self._log("cancel_address_rejected", message=str(issue))
                    return
                if reply.get("cancel_disposition") == "NOT_ACCEPTED_YET":
                    QTimer.singleShot(200, lambda: self._send_cancel(active))
                elif reply.get("cancel_disposition") == "ALREADY_COMMITTED":
                    set_text(self.status_label, tr("导出已完成，正在确认保存结果…") if active.kind == "export" else tr("仿色参数已保存，正在结束任务…"))
            elif error:
                self._error(tr("取消任务"), error)
                self._cancel_pending = False
                active.cancel_requested = False
        def work():
            if hasattr(self.backend, "cancel_operation"):
                return self.backend.cancel_operation(active.store, active.request_key, active.job_id, active.attempt_id)
            return self.backend.cancel_key(active.store, active.request_key)
        self._run("cancel", work, complete)

    def _start_initial_previews(self):
        if self.binding is None:
            return
        self._display_generation += 1
        self.display.select(self._restored_object or 'result')
        self._restored_object = None
        self._remember_detail()
        self._initial_native_center = self.display.native_center if self.display.native else None
        self.roi = None
        self.busy = None
        self._apply_display_layout()
        self.pending_alpha = self.slider.value()/100
        self._refresh_source_result()

    def _slider_changed(self, value):
        set_text(self.strength_label, f"{value}%")
        with QSignalBlocker(self.strength_spin):
            self.strength_spin.setValue(value)
        self._log("slider_changed", alpha=value / 100)
        if self.binding is None:
            return
        self._preview_superseded.set()
        self.pending_alpha = value / 100
        set_text(self.status_label, tr('正在更新 {0}% 强度预览…', value))
        self.export_button.setEnabled(False)
        self._debounce.start()
        self._save_timer.start()

    def _request_result(self):
        if self.binding is None:
            return
        if self.busy is not None or self.preview_inflight:
            self._display_refresh_pending = True
            return
        if self.roi is not None:
            self._request_native_previews()
        else:
            self._refresh_source_result()

    def _accept_preview(self, view, request_id, binding, reply, alpha):
        if "tiles" in reply:
            return self._accept_native_preview(view, request_id, binding, reply, alpha)
        metadata = reply["preview"]
        raw = reply["pixels"]
        shape = metadata["shape"]
        if (metadata.get("request_id") != request_id or metadata.get("binding") != binding
                or self.binding != binding or not isinstance(shape, list) or len(shape) != 3
                or shape[2] != 3 or len(raw) != shape[0] * shape[1] * 3
                or hashlib.sha256(raw).hexdigest() != metadata.get("pixel_sha256")
                or (alpha is not None and metadata.get("alpha") != alpha)
                or (view != "reference" and tuple(metadata.get("roi") or ()) != tuple(self.roi or ()))):
            self._log("preview_rejected", view=view, request_id=request_id)
            return
        if view == "result" and self.slider.value() / 100 != alpha:
            self._log("preview_superseded", request_id=request_id, alpha=alpha)
            self.pending_alpha = self.slider.value() / 100
            return
        image = QImage(raw, shape[1], shape[0], shape[1] * 3, QImage.Format_RGB888).copy()
        icc = base64.b64decode(metadata.get("icc_profile_base64", ""))
        if icc:
            color = QColorSpace.fromIccProfile(icc)
            if color.isValid():
                image.setColorSpace(color)
        elif metadata.get("color_interpretation") in {"assumed_sRGB", "marked_sRGB"}:
            image.setColorSpace(QColorSpace(QColorSpace.SRgb))
        pane = {"source": self.source_pane, "reference": self.reference_pane,
                "result": self.result_pane}[view]
        pane.image_grid = metadata.get("view_grid") if view == "reference" else metadata.get("source_grid")
        native = metadata.get("native_pixel_roi") is True
        if view in ("source", "result"):
            self._source_grid = metadata.get("source_grid")
        detail = (tr('{0} × {1} · 1:1 源像素', shape[1], shape[0]) if native else
                  tr('{0} × {1} · 适合窗口', shape[1], shape[0]))
        if view == "reference":
            reference_grid = metadata.get("view_grid") or {"width": shape[1], "height": shape[0]}
            detail += tr(' · 参考原始 {0} × {1}', reference_grid['width'], reference_grid['height'])
        pane.set_image(image, native=native, detail=detail, roi=metadata.get("roi"),
                       fit_key=reply.get("fit_key"))
        if view == "result":
            if self.slider.value() / 100 == alpha:
                self.last_result_alpha = alpha
                self.export_button.setEnabled(True)
                set_text(self.status_label, tr('结果已就绪 · 强度 {0}%', round(alpha * 100)))
            else:
                self._log("preview_superseded", request_id=request_id, alpha=alpha)
                self.pending_alpha = self.slider.value() / 100
        self._log("preview_accepted", view=view, alpha=metadata.get("alpha"),
                  request_id=request_id, binding=binding, shape=shape,
                  pixel_sha256=metadata["pixel_sha256"], roi=metadata.get("roi"),
                  fit_count=reply.get("fit_count"), render_count=reply.get("render_count"))
        self._update_photo_info()

    def _result_ready(self, alpha):
        self.last_result_alpha = alpha
        set_text(self.status_label, tr('结果已就绪 · 强度 {0}%', round(alpha * 100)))
        self._sync_controls()

    def _accept_native_preview(self, view, request_id, binding, reply, alpha):
        meta = reply["preview"]
        if (self.binding != binding or meta.get("binding") != binding
                or meta.get("request_id") != request_id
                or tuple(meta.get("roi") or ()) != tuple(self.roi or ())
                or (view == "result" and (meta.get("alpha") != alpha or self.slider.value()/100 != alpha))):
            self._log("preview_rejected", view=view, request_id=request_id)
            return
        pane = self._pane(view)
        frame = TileFrame(reply, pane._image)
        pane.image_grid = meta["view_grid"] if view=="reference" else meta["source_grid"]
        pane.set_image(frame, native=True, roi=meta["roi"],
            detail=tr('{0} × {1} · 1:1 源像素', frame.width(), frame.height()))
        if view == "result":
            self._result_ready(alpha)
        self._log("preview_accepted", view=view, alpha=meta.get("alpha"),
            request_id=request_id, binding=binding, shape=meta["shape"], roi=meta["roi"],
            tile_manifest_sha256=meta["tile_manifest_sha256"],
            fit_count=reply.get("fit_count"), render_count=reply.get("render_count"))

    def _fit_clicked(self):
        self._remember_detail()
        self.display.native_preferred = False
        self._display_generation += 1
        self._preview_superseded.set()
        self._native_debounce.stop()
        self._native_refresh_pending = False
        self.roi = None
        self._apply_display_layout()
        self._save_ui_state()
        if self.binding is not None and self.busy is None:
            self._refresh_source_result()

    def _roi_clicked(self):
        if (self.binding is None or self.project is None or self.busy is not None or self.preview_inflight
                or not self._detail_capable()):
            return
        grid = self._detail_grid()
        if not isinstance(grid, dict) or not all(type(grid.get(v)) is int for v in ('width','height')):
            self._error(tr('100% 预览'), ValueError(tr('源图网格尚未验证')))
            return
        self.display.native_preferred = True
        center = self._detail_center() or (grid['width']//2, grid['height']//2)
        self._set_detail_roi(*center)

    def _inspect_at(self, u, v):
        if self.binding is None or self.busy is not None or self.preview_inflight or not self._detail_grid():
            return
        if self.roi is None:
            x, y = u * self._detail_grid()["width"], v * self._detail_grid()["height"]
        else:
            x = self.roi[0] + u * (self.roi[2] - self.roi[0])
            y = self.roi[1] + v * (self.roi[3] - self.roi[1])
        self._set_detail_roi(round(x), round(y))

    def _set_detail_roi(self, center_x, center_y):
        grid=self._detail_grid()
        if not grid:
            return
        height, width = grid["height"], grid["width"]
        panes = tuple(self._pane(role) for role in self._visible_roles())
        if not panes or not self._detail_capable():
            return
        physical_w = max(math.ceil(p.photo.width() * p.photo.devicePixelRatioF()) for p in panes)
        physical_h = max(math.ceil(p.photo.height() * p.photo.devicePixelRatioF()) for p in panes)
        side_w, side_h = min(width, max(1, physical_w)), min(height, max(1, physical_h))
        x0 = max(0, min(width - side_w, round(center_x - side_w / 2)))
        y0 = max(0, min(height - side_h, round(center_y - side_h / 2)))
        roi = (x0, y0, x0 + side_w, y0 + side_h)
        if self.roi == roi and all(p._native for p in panes):
            return
        self.roi = roi
        self.display.native_preferred = True
        self._remember_detail()
        self._show_native_view()
        self._save_ui_state()
        self._log("roi_clicked", roi=self.roi, device_pixel_ratio=self.devicePixelRatioF())
        self._refresh_source_result()

    def _show_native_view(self):
        if self.roi is None or self._detail_grid() is None:
            return
        x0, y0, x1, y1 = self.roi
        view = ((x0 + x1) / 2, (y0 + y1) / 2,
                self._detail_grid()["width"], self._detail_grid()["height"])
        for pane in (self._pane(role) for role in self._visible_roles()):
            pane._native = True
            pane.native_view = view
            pane.photo.update()

    def _begin_pan(self):
        self._pan_anchor = self.roi

    def _pan_detail(self, pane, dx, dy):
        if self._pan_anchor is None or self.binding is None or self._detail_grid() is None:
            return
        x0, y0, x1, y1 = self._pan_anchor
        dpr = pane.photo.devicePixelRatioF()
        left = max(0, min(self._detail_grid()["width"] - (x1-x0), x0 - round(dx * dpr)))
        top = max(0, min(self._detail_grid()["height"] - (y1-y0), y0 - round(dy * dpr)))
        self.roi = (left, top, left + x1-x0, top + y1-y0)
        self._remember_detail()
        self._show_native_view()  # GUI-thread paint moves before any backend work.
        self._queue_native_refresh()

    def _finish_pan(self):
        self._pan_anchor = None
        self._save_timer.start()
        self._queue_native_refresh()

    def _native_view_resized(self):
        if self._closing:
            return
        sender = self.sender()
        if isinstance(sender, PhotoPane) and sender.isHidden():
            return
        if hasattr(self, "_viewport_debounce"):
            self._viewport_debounce.start()

    def _resize_detail_view(self):
        if self._closing:
            return
        roles = self._visible_roles()
        if self.binding is None:
            for role in roles:
                pane = self._pane(role)
                info = self._import_geometry.get(role)
                if (info and role not in self._import_pending and pane._image is not None
                        and max(pane._image.width(),pane._image.height()) < min(self._fit_preview_side(role), max(info['width'],info['height']))):
                    self._request_import_preview(role, Path(info['path']))
            self._update_photo_info()
            return
        if self.busy is not None or self.preview_inflight:
            self._display_refresh_pending = True
            return
        if self.roi is not None and self._detail_grid() is not None:
            x0,y0,x1,y1 = self.roi
            self._set_detail_roi((x0+x1)/2, (y0+y1)/2)
        elif getattr(self.backend,'SIZED_PREVIEWS',False) and any(
                self._pane(role)._image is not None and max(self._pane(role)._image.width(),self._pane(role)._image.height()) <
                min(self._fit_preview_side(role),max((self._pane(role).image_grid or {'width':MAX_FIT_SIDE}).values())) for role in roles):
            self._refresh_source_result()
        self._update_photo_info()

    def _queue_native_refresh(self):
        self._preview_superseded.set()
        self._native_refresh_pending = True
        if 'result' in self._visible_roles():
            self.last_result_alpha = None
            self.export_button.setEnabled(False)
        if not self._native_debounce.isActive():
            self._native_debounce.start()

    def _request_native_previews(self):
        if self.binding is None or self.roi is None or self.busy is not None or self.preview_inflight:
            return
        roi, grid, alpha = self.roi, dict(self._detail_grid()), self.slider.value() / 100
        binding = dict(self.binding)
        store, job, attempt = self.store_root, self.job_id, self.attempt_id
        cache = self._native_cache
        superseded = self._preview_superseded = threading.Event()
        self.pending_alpha = None
        self._native_refresh_pending = False
        # Same viewport + binding: strength changes do not touch the source frame.
        source_ready = (isinstance(self.source_pane._image, TileFrame)
                        and self.source_pane._image.binding == binding
                        and self.source_pane.image_roi == list(roi))
        views = [view for view in self._visible_roles()
                 if not (view == 'source' and source_ready)]
        if not views:
            self._display_refresh_pending = False
            self._sync_controls()
            return
        self.preview_inflight = True
        generation = self._display_generation
        self._display_refresh_pending = False
        rid = str(uuid.uuid4())
        def work():
            deadline = time.monotonic() + 30
            frames = cache.frames(self.backend, store, job, attempt, binding,
                views=views, alpha=alpha, roi=roi, grid=grid, request_id=rid,
                deadline=deadline, superseded=superseded.is_set)
            return [(view, rid+"-"+view, frame) for view, frame in zip(views, frames)]
        def complete(rows, error):
            self.preview_inflight = False
            if generation != self._display_generation:
                self._display_refresh_pending = True
                self._log('preview_rejected', reason='superseded_display', generation=generation)
            elif isinstance(error, PreviewSuperseded):
                self._log("preview_rejected", reason="superseded", request_id=rid)
            elif error:
                self._error(tr("局部预览"), error)
            else:
                for view, row_id, reply in rows:
                    self._accept_preview(view, row_id, binding, reply, alpha if view == "result" else None)
            if (self._native_refresh_pending or self._display_refresh_pending
                    or self.pending_alpha is not None and 'result' in self._visible_roles()):
                self._request_result()
            elif self.export_pending and self.last_result_alpha == self.slider.value() / 100:
                self.export_pending = False
                self._export_clicked()
        self._run("native_viewport", work, complete)

    def _refresh_source_result(self):
        if self.binding is None:
            return
        if self.busy is not None or self.preview_inflight:
            self._display_refresh_pending = True
            return
        if self.roi is not None:
            self._queue_native_refresh()
            return
        self._display_refresh_pending = False
        roles = self._visible_roles()
        alpha = self.slider.value()/100
        requests = []
        for view in roles:
            pane = self._pane(view)
            value = alpha if view == 'result' else 0.
            side = self._fit_preview_side(view)
            key = self._fit_key(view, value, side)
            if pane.restore_fit(key):
                if view == 'result':
                    self.pending_alpha = None
                    self._result_ready(alpha)
            else:
                requests.append((view, str(uuid.uuid4()), side, value))
        if not requests:
            self._continue_display_preview()
            self._update_photo_info()
            return
        generation = self._display_generation
        binding = dict(self.binding)
        store, job, attempt = self.store_root, self.job_id, self.attempt_id
        if 'result' in roles:
            self.pending_alpha = None
        self.preview_inflight = True
        def work():
            return [(view, request, value, self._fit_preview(store, job, attempt, binding,
                view=view, alpha=value, request_id=request, side=side))
                for view, request, side, value in requests]
        def complete(rows, error):
            self.preview_inflight = False
            if generation != self._display_generation:
                self._display_refresh_pending = True
                self._log('preview_rejected', reason='superseded_display', generation=generation)
            elif error:
                self._error(tr('预览'), error)
            else:
                for view, request, value, reply in rows:
                    self._accept_preview(view, request, binding, reply, value if view == 'result' else None)
                    requested_side = next(side for v, rid, side, a in requests if rid == request)
                    if (getattr(self.backend, 'SIZED_PREVIEWS', False)
                            and self._fit_preview_side(view) != requested_side):
                        # A first reply can supply the previously unknown grid.
                        # Finish at the actual physical viewport size, as before.
                        self._display_refresh_pending = True
            self._continue_display_preview()
            self._update_photo_info()
        self._run('visible_previews', work, complete)

    def _fit_preview_side(self, view):
        pane = self._pane(view)
        if view not in self._visible_roles():
            # A genuinely visible material thumbnail may need an initial bounded
            # import preview. Never use a hidden full pane's previous dimensions.
            return 512 if view in self.materials and self.display.sidebar else 128
        dpr = pane.photo.devicePixelRatioF()
        width, height = pane.photo.width()*dpr, pane.photo.height()*dpr
        grid = pane.image_grid or (self._source_grid if view != 'reference' else None)
        if grid is None:
            role = 'source' if view == 'result' else view
            info = self._import_geometry.get(role)
            field = self.source_edit if role == 'source' else self.reference_edit
            if info and Path(info['path']) == Path(field.text()):
                grid = {'width':info['width'], 'height':info['height']}
        if grid:
            side = max(grid.values())*min(1., width/grid['width'], height/grid['height'])
        else:
            side = max(width,height)
        return min(MAX_FIT_SIDE,max(1,math.ceil(side)))
    def _fit_preview(self, store, job, attempt, binding, *, view, alpha, request_id, side):
        key = ExactPreviewCache.key(store, job, attempt, binding, view, alpha,
            side if getattr(self.backend, "SIZED_PREVIEWS", False) else None)
        cache = self._fit_cache
        reply = cache.get(key, request_id)
        if reply is not None:
            return {**reply, "fit_key":key}
        options = {"max_side": side} if getattr(self.backend, "SIZED_PREVIEWS", False) else {}
        reply = self.backend.preview(store, job, attempt, binding,
            view=view, alpha=alpha, request_id=request_id, **options)
        cache.put(key, reply, request_id, binding, alpha)
        return {**reply, "fit_key":key}

    def _fit_key(self, view, alpha, side):
        return ExactPreviewCache.key(self.store_root, self.job_id, self.attempt_id,
            self.binding, view, alpha,
            side if getattr(self.backend, "SIZED_PREVIEWS", False) else None)

    def _export_clicked(self):
        if self.binding is None or self.busy is not None:
            return
        alpha = self.slider.value() / 100
        if self.preview_inflight or self.pending_alpha is not None or self.last_result_alpha != alpha:
            self.export_pending = True
            self.pending_alpha = alpha
            self._request_result()
            return
        destination = Path(self.export_edit.text().strip())
        if not self.export_edit.text().strip():
            self._browse_export()
            if not self.export_edit.text().strip():
                return
            destination = Path(self.export_edit.text().strip())
        suffix = self._source_suffix()
        same_family = ({".tif", ".tiff"} if suffix in {".tif", ".tiff"} else
                       {".jpg", ".jpeg"} if suffix in {".jpg", ".jpeg"} else {suffix})
        if not self.export_edit.text().strip() or destination.suffix.lower() not in same_family:
            self._error(tr("导出"), ValueError(tr("目标图片扩展名必须与原图格式一致")))
            return
        key_hash = hashlib.sha256(json.dumps({"binding": self.binding, "alpha": alpha,
            "destination": str(destination.resolve())}, sort_keys=True).encode("utf-8")).hexdigest()
        key = self._export_requests.setdefault(key_hash, "gm004b-export-" + key_hash[:48] + "-" + uuid.uuid4().hex[:12])
        self.busy = "export"
        active = self._begin_active("export", key)
        self._stage_timer.start()
        store, job, attempt, binding = self.store_root, self.job_id, self.attempt_id, dict(self.binding)
        self.export_button.setEnabled(False)
        set_text(self.status_label, tr("正在按原图格式导出原尺寸结果 · 不定进度"))
        self._log("export_clicked", alpha=alpha, destination=str(destination), request_key=key)
        def complete(reply, error):
            self._end_active(active)
            self.busy = None
            self.export_button.setEnabled(self.binding is not None)
            if error:
                self._export_requests.pop(key_hash, None)
                if getattr(error, "resident_lost", False):
                    self._clear_result()
                    set_text(self.status_label, tr("结果已释放，请点击「恢复仿色结果」后重试导出。"))
                    self._log("export_result_lost", message=str(error))
                elif getattr(error, "code", str(error)) == "CANCELLED":
                    set_text(self.status_label, tr("导出已取消，可以继续调整强度或重新导出。"))
                    self._log("export_cancelled", request_key=key)
                else:
                    self._error(tr("导出"), error)
                return
            set_text(self.status_label, tr("原图格式结果已导出"))
            self._log("export_committed", alpha=alpha, destination=str(destination),
                      result=reply)
            self._save_ui_state()
        self._run("export", lambda: self.backend.export(store, job, attempt, binding, destination, alpha, key), complete)

    def _release_clicked(self):
        if self.binding is None or self.busy == "release":
            return
        if self.busy is not None or self.preview_inflight or self._tasks:
            self._preview_superseded.set()
            self._native_refresh_pending = False
            self.pending_alpha = None
            self._release_pending = True
            set_text(self.status_label, tr("当前操作完成后释放驻留结果…"))
            return
        # Consume the queued release before its continuation can bind a new result.
        self._release_pending = False
        self.busy = "release"
        self.release_button.setEnabled(False)
        set_text(self.status_label, tr("正在释放驻留结果…"))
        self._log("release_clicked", job_id=self.job_id, attempt_id=self.attempt_id)
        binding = dict(self.binding)
        self._remember_detail()
        saved_display = self.display.to_record()
        def complete(reply, error):
            self.busy = None
            if error:
                self._error(tr("释放结果"), error)
                self._close_after_release = False
            else:
                self._log("released", result=reply)
                self._clear_result()
                if self._after_release is None and self.project is not None:
                    # Releasing RAM keeps this document's viewing preference.
                    # Input replacement/new/open still clear it before reuse.
                    from .view_state import DisplayState
                    self.display = DisplayState.from_record(saved_display)
                    self._restored_object = self.display.object
                    self._apply_display_layout()
                set_text(self.status_label, tr("结果已释放；需要重新载入。"))
                if self._after_release is not None:
                    continuation, self._after_release = self._after_release, None
                    continuation()
            if self._close_after_release and not error:
                self.close()
        self._run("release", lambda: self.backend.release(self.store_root,
            self.job_id, self.attempt_id, binding), complete)

    def closeEvent(self, event):
        dock=getattr(self,'_adaptive_dock',None)
        if dock is not None:
            panel=dock.widget();controller=panel.controller
            if controller.futures or controller.busy or (controller.result and controller.result.get('resident')):
                event.ignore()
                if not panel._closing:panel.close()
                QTimer.singleShot(250,self.close);return
            controller.shutdown()
        if self._closed:
            event.accept()
            return
        if self.active_operation is not None:
            self._closing = True
            self._close_after_release = True
            self._release_pending = True
            self.active_operation.request_close()
            self._cancel_clicked()
            event.ignore()
            return
        if self.binding is not None:
            self._close_after_release = True
            self._release_clicked()
            event.ignore()
            return
        if self.busy in {"fit", "replay"}:
            self._closing = True
            self._cancel_clicked()
            event.ignore()
            return
        if self._tasks or self.busy is not None:
            self._closing = True
            event.ignore()
            return
        self._save_ui_state()
        self._closing = self._closed = True
        for timer in (self._heartbeat, self._save_timer, self._poll, self._stage_timer,
                      self._viewport_debounce, self._native_debounce, self._debounce):
            timer.stop()
        self.sidebar_scroll.widget().stop_updates()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self._log("window_closed")
        self._diagnostic_writer.close()
        self.commands.detach()
        self.language.changed.disconnect(self._language_changed)
        self.chrome.close_shell()
        event.accept()
