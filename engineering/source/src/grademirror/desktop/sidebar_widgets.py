"""Small disclosures and height-aware material previews for the photo sidebar."""

from .i18n import tr, render, source_text
from .language import set_text, text_widget, manager, unbind_text
from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QToolButton
from .branding import action_icon


class DisclosureButton(QToolButton):
    def __init__(self, text):
        super().__init__()
        self.setObjectName('disclosure')
        set_text(self, text)
        self.setCheckable(True)
        self.setArrowType(Qt.NoArrow)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(12, 12))
        self.toggled.connect(self._update_icon)
        self._update_icon(False)

    def _update_icon(self, expanded):
        self.setIcon(action_icon('chevron-down' if expanded else 'chevron-right'))


class WorkspaceRail(QFrame):
    MIN_PREVIEW = 64
    MAX_PREVIEW = 84

    def __init__(self):
        super().__init__()
        self.cards = []
        self.viewport = None
        self._pending = False
        self._stopped = False

    def stop_updates(self):
        self._stopped = True

    def attach_viewport(self, viewport):
        self.viewport = viewport
        viewport.installEventFilter(self)
        self._schedule()

    def eventFilter(self, watched, event):
        if watched is self.viewport and event.type() in (QEvent.Resize, QEvent.Show):
            self._schedule()
        return super().eventFilter(watched, event)

    def event(self, event):
        if event.type() == QEvent.LayoutRequest:
            self._schedule()
        return super().event(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule()

    def _schedule(self):
        if getattr(self, '_stopped', False):
            return
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._fit_previews)

    def _fit_previews(self):
        self._pending = False
        if self._stopped or not self.cards or self.viewport is None or self.layout() is None:
            return
        layout = self.layout()
        natural = layout.totalHeightForWidth(self.width())
        if natural < 0:
            natural = layout.minimumSize().height()
        # Remove current preview growth from the content's natural height. Path
        # details, wrapped labels and hidden controls still contribute normally.
        compact = natural - sum(card.thumbnail_height - self.MIN_PREVIEW for card in self.cards)
        extra = max(0, self.viewport.height() - compact - 24)
        height = min(self.MAX_PREVIEW, self.MIN_PREVIEW + extra // len(self.cards))
        for card in self.cards:
            card.set_thumbnail_height(height)
