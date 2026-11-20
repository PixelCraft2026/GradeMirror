"""Dark photographic workspace; the controller owns all operation state."""

from .i18n import tr, render, source_text
from .language import set_text, text_widget, manager, unbind_text
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontDatabase
from pathlib import Path
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox,
    QSizePolicy, QSplitter, QTextEdit, QToolBar, QToolButton, QVBoxLayout, QWidget)

STYLE = """
QMainWindow, QWidget#workspace { background:#191B1E; }
QWidget { background:transparent; color:#E5E7EB; font-family:'Segoe UI','Microsoft YaHei UI'; font-size:14px; }
QLabel#document { color:#C1C7D1; font-size:13px; }
QLabel#eyebrow, QLabel#section { color:#E5E7EB; font-size:14px; font-weight:600; }
QLabel#muted { color:#C1C7D1; font-size:12px; }
QLabel#warning { color:#EFB1A7; font-size:12px; }
QFrame#pane { background:#191B1E; border:0; }
QFrame#pane[dropActive="true"] { border:2px solid #A9BDE8; }
QLabel#photo { background:#111315; color:#C1C7D1; font-size:16px; }
QFrame#rail { background:#23262B; border-left:1px solid #343840; }
QFrame#material { border:0; border-bottom:1px solid #343840; padding-bottom:6px; }
QFrame#workspaceFooter { background:#202327; }
QLabel#currentStatus { color:#E5E7EB; font-size:13px; }
QPushButton { background:#30353D; color:#E5E7EB; border:1px solid #7D8DA9; border-radius:5px; padding:6px 10px; min-height:20px; }
QPushButton:hover { background:#3B4350; }
QPushButton:pressed { background:#252B35; }
QPushButton:focus, QToolButton:focus { border:1px solid #B6C8EF; }
QPushButton:disabled { background:#272B31; color:#78818F; border-color:#343840; }
QPushButton#primary { background:#A9BDE8; border-color:#A9BDE8; color:#182337; min-height:28px; font-weight:600; }
QPushButton#primary:hover { background:#B6C8EF; }
QPushButton#primary:disabled { background:#343C48; color:#A0AABA; border-color:#434F62; }
QFrame#modeSwitch { background:#191B1E; border:1px solid #343840; border-radius:7px; }
QPushButton#modeOption { background:transparent; border:1px solid transparent; border-radius:4px; padding:2px 10px; color:#C1C7D1; }
QPushButton#modeOption:checked { background:#303B50; color:#E5E7EB; border-color:#52678D; }
QPushButton#modeOption:hover { background:#30353D; }
QPushButton#modeOption:focus { border-color:#B6C8EF; }
QPushButton#modeOption:disabled { color:#78818F; }
QPushButton#quiet { background:transparent; border-color:transparent; color:#C1C7D1; padding:3px 6px; }
QPushButton#quiet:hover { background:#30353D; }
QPushButton#quiet:focus { border-color:#B6C8EF; }
QToolBar#photoTools { background:transparent; border:0; spacing:4px; padding:0; }
QToolBar#photoTools QToolButton { background:#23262B; border:1px solid transparent; border-radius:5px; color:#C1C7D1; padding:0 9px; }
QToolBar#photoTools QToolButton:hover { background:#30353D; color:#E5E7EB; }
QToolBar#photoTools QToolButton:checked { background:#303B50; color:#E5E7EB; }
QToolBar#photoTools QToolButton:pressed { background:#38465F; }
QToolBar#photoTools QToolButton:focus { border-color:#B6C8EF; }
QToolBar#photoTools QToolButton:disabled { background:transparent; color:#78818F; }
QToolBar#photoTools::separator { background:#343840; width:1px; margin:9px 6px; }
QToolBar#photoTools QToolButton#layoutTool { padding-right:20px; }
QFrame#actionDock { border:0; border-top:1px solid #343840; }
QToolButton#materialView { text-align:left; background:#1D2024; border:1px solid #343840; border-radius:4px; color:#E5E7EB; padding:2px 6px; }
QToolButton#materialView:hover { background:#30353D; }
QToolButton#materialView:focus { border-color:#B6C8EF; }
QToolButton#materialView:disabled { color:#A0AABA; }
QToolButton#disclosure { background:transparent; border:1px solid transparent; border-radius:4px; padding:3px 2px; color:#C1C7D1; }
QToolButton#disclosure:hover { background:#30353D; }
QToolButton#disclosure:focus { border-color:#B6C8EF; }
QLineEdit, QSpinBox, QComboBox { background:#191B1E; color:#E5E7EB; border:1px solid #7D8DA9; border-radius:4px; padding:5px; min-height:20px; selection-background-color:#52678D; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color:#B6C8EF; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color:#78818F; border-color:#343840; }
QComboBox QAbstractItemView { background:#23262B; color:#E5E7EB; selection-background-color:#303B50; }
QSlider::groove:horizontal { height:5px; background:#7D8DA9; border-radius:2px; }
QSlider::sub-page:horizontal { background:#A9BDE8; border-radius:2px; }
QSlider::handle:horizontal { background:#E5E7EB; border:2px solid #A9BDE8; width:14px; margin:-6px 0; border-radius:8px; }
QSlider::handle:horizontal:disabled { background:#6B7483; border-color:#515C70; }
QProgressBar { background:#343840; border:0; min-height:3px; max-height:3px; }
QProgressBar::chunk { background:#A9BDE8; }
QTextEdit { background:#111315; border:1px solid #7D8DA9; color:#C1C7D1; font-family:Consolas; font-size:12px; }
QScrollArea { background:#23262B; border:0; }
QScrollBar:vertical { background:transparent; width:8px; margin:0; }
QScrollBar::handle:vertical { background:#7D8DA9; border-radius:3px; min-height:28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QSplitter::handle { background:#343840; }
QCheckBox { color:#C1C7D1; spacing:8px; }
QToolTip { background:#30353D; color:#E5E7EB; border:1px solid #7D8DA9; padding:6px; }
"""


def label(text, name='muted'):
    item = text_widget(QLabel, text); item.setObjectName(name); item.setWordWrap(True)
    return item


def disclosure(layout, text):
    from .sidebar_widgets import DisclosureButton
    toggle = DisclosureButton(text)
    widget = QFrame(); widget.hide()
    toggle.toggled.connect(widget.setVisible)
    layout.addWidget(toggle); layout.addWidget(widget)
    return toggle, widget


def build_workspace(w, PhotoPane):
    from .chrome import TitleBar, CHROME_STYLE
    from .commands import Commands
    from .material_card import MaterialCard
    from .sidebar_widgets import WorkspaceRail
    from .branding import action_icon, ASSETS
    # Windows offscreen Qt may not enumerate system fonts. Reuse the installed
    # system faces, as the previous workspace did; no font package is installed.
    if not QFontDatabase.families():
        for name in ('segoeui.ttf','msyh.ttc'):
            path = Path('C:/Windows/Fonts') / name
            if path.is_file():QFontDatabase.addApplicationFont(str(path))
    set_text(w, tr('新的仿色 · GradeMirror'), 'setWindowTitle'); w.setMinimumSize(960, 600)
    screen = w.screen().availableGeometry()
    w.resize(min(1420, max(960, screen.width())), min(900, max(600, screen.height())))
    chevron = (ASSETS / 'icons/actions/chevron-down.svg').as_posix()
    w.setStyleSheet(STYLE + CHROME_STYLE + '''
        QToolBar#photoTools QToolButton::menu-indicator {
            image:url("''' + chevron + '''"); width:10px; height:10px;
            subcontrol-position:right center; right:6px;
        }
    ''')
    content = QWidget(); content.setObjectName('workspace'); w.setCentralWidget(content)
    outer = QVBoxLayout(content); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
    w.workspace_splitter = QSplitter(Qt.Horizontal); w.workspace_splitter.setChildrenCollapsible(False)
    w.workspace_splitter.setHandleWidth(1)
    w.canvas = QWidget(); w.canvas.setMinimumWidth(0)
    canvas = QVBoxLayout(w.canvas); canvas.setContentsMargins(16, 8, 16, 8); canvas.setSpacing(8)
    w.toolbar = QToolBar(tr('照片视图')); w.toolbar.setObjectName('photoTools'); w.toolbar.setMovable(False)
    set_text(w.toolbar,tr('照片视图'),'setWindowTitle')
    w.toolbar.setIconSize(QSize(16,16))
    w.toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon); w.toolbar.setMinimumHeight(40)
    canvas.addWidget(w.toolbar)
    w.photos = QSplitter(Qt.Horizontal); w.photos.setChildrenCollapsible(False); w.photos.setHandleWidth(1)
    w.source_pane = PhotoPane(tr('原图'), tr('选择照片，或拖入这里'), drop_suffixes=w._image_suffixes)
    w.reference_pane = PhotoPane(tr('调色参考'), tr('选择调色参考，或拖入这里'), drop_suffixes=w._image_suffixes)
    w.result_pane = PhotoPane(tr('仿色结果'), tr('完成后可调整强度并导出'))
    set_text(w.result_pane.photo, tr('结果将在这里显示'))
    for pane in (w.source_pane, w.reference_pane, w.result_pane):
        w.photos.addWidget(pane); pane.viewportResized.connect(w._native_view_resized)
    for pane in (w.source_pane, w.result_pane):
        pane.detailRequested.connect(w._inspect_at); pane.panStarted.connect(w._begin_pan)
        pane.panMoved.connect(lambda dx, dy, pane=pane: w._pan_detail(pane, dx, dy))
        pane.panFinished.connect(w._finish_pan)
    canvas.addWidget(w.photos, 1)
    # Keep the diagnostic value without duplicating each pane's size/zoom caption.
    w.zoom_label = label(tr('显示倍率 —')); w.zoom_label.setParent(w.canvas); w.zoom_label.hide()
    w.workspace_splitter.addWidget(w.canvas)
    rail = WorkspaceRail(); rail.setObjectName('rail')
    rail_layout = QVBoxLayout(rail); rail_layout.setContentsMargins(16, 14, 16, 12); rail_layout.setSpacing(10)
    w.materials = {}
    for role in ('source', 'reference'):
        card = MaterialCard(role, w._image_suffixes); w.materials[role] = card; rail_layout.addWidget(card)
        rail.cards.append(card)
    w.source_edit, w.reference_edit = (w.materials[role].path_edit for role in ('source', 'reference'))
    w.source_browse, w.reference_browse = (w.materials[role].replace_button for role in ('source', 'reference'))
    for field in (w.source_edit, w.reference_edit): field.textChanged.connect(w._input_changed)
    w.reference_note = label(tr('参考图需与原图构图一致，分辨率可不同。'))
    rail_layout.addWidget(w.reference_note)
    rail_layout.addStretch(1)
    # All stages share the same bottom action anchor. Their settings grow above
    # the button; opening details or a small viewport can still use normal scroll.
    w.action_dock = QFrame(); w.action_dock.setObjectName('actionDock')
    actions = QVBoxLayout(w.action_dock); actions.setContentsMargins(0,12,0,0); actions.setSpacing(9)
    w.new_panel = QFrame(); form = QVBoxLayout(w.new_panel)
    form.setContentsMargins(0, 0, 0, 0); form.setSpacing(8)
    from .mode_switch import ModeSwitch
    w.mode_selector = ModeSwitch()
    set_text(w.mode_selector,tr('快速'),'setItemText',0);set_text(w.mode_selector,tr('精细'),'setItemText',1)
    set_text(w.mode_selector, tr('仿色模式'), 'setAccessibleName')
    form.addWidget(label(tr('仿色模式'), 'section'))
    form.addWidget(w.mode_selector)
    w.mode_description = label(tr('快速完成颜色迁移')); form.addWidget(w.mode_description)
    w.mode_selector.currentIndexChanged.connect(lambda: set_text(w.mode_description, tr('增加局部颜色校正，处理时间更长') if w.mode_selector.currentData() == 'fine' else tr('快速完成颜色迁移')))
    w.mode_selector.currentIndexChanged.connect(w._input_changed); actions.addWidget(w.new_panel)
    w.process_button = text_widget(QPushButton, tr('导入原图…')); w.process_button.setObjectName('primary')
    w.load_button = text_widget(QPushButton, tr('恢复仿色结果')); w.load_button.setObjectName('primary')
    w.adjustments = QWidget(); adjust = QVBoxLayout(w.adjustments)
    adjust.setContentsMargins(0, 0, 0, 0); adjust.setSpacing(8)
    row = QHBoxLayout(); row.addWidget(label(tr('仿色强度'), 'section')); row.addStretch()
    w.strength_spin = QSpinBox(); w.strength_spin.setRange(0, 100); w.strength_spin.setSuffix('%'); w.strength_spin.setValue(100)
    set_text(w.strength_spin, tr('仿色强度百分比'), 'setAccessibleName'); row.addWidget(w.strength_spin); adjust.addLayout(row)
    w.slider = QSlider(Qt.Horizontal); set_text(w.slider, tr('仿色强度'), 'setAccessibleName'); w.slider.setRange(0, 100); w.slider.setValue(100)
    w.strength_spin.valueChanged.connect(w.slider.setValue); w.slider.valueChanged.connect(w._slider_changed); adjust.addWidget(w.slider)
    w.strength_label = label(tr('100% · 完整仿色')); w.strength_reset = text_widget(QPushButton, tr('恢复 100%')); w.strength_reset.setObjectName('quiet')
    w.strength_reset.clicked.connect(lambda: w.slider.setValue(100))
    row = QHBoxLayout(); row.addWidget(w.strength_label); row.addStretch(); row.addWidget(w.strength_reset); adjust.addLayout(row)
    w.export_button = text_widget(QPushButton, tr('导出图片…')); w.export_button.setObjectName('primary')
    w.refine_button = text_widget(QPushButton, tr('继续精细处理…'))
    set_text(w.refine_button,tr('复用快速仿色参数，继续局部校正与细节处理；另存为精细工程。'),'setToolTip')
    w.export_summary = label(tr('格式／位深／ICC：未读取')); adjust.addWidget(w.export_summary)
    w.export_details_toggle, export_details = disclosure(adjust, tr('导出位置'))
    detail = QVBoxLayout(export_details); detail.setContentsMargins(0, 0, 0, 0)
    w.export_edit = QLineEdit(); set_text(w.export_edit, tr('导出图片完整路径'), 'setAccessibleName'); set_text(w.export_edit, tr('结果保存位置'), 'setPlaceholderText')
    w.export_browse = text_widget(QPushButton, tr('另存…')); detail.addWidget(w.export_edit); detail.addWidget(w.export_browse); actions.addWidget(w.adjustments)
    actions.addWidget(w.refine_button)
    for button in (w.process_button,w.load_button,w.export_button): actions.addWidget(button)
    w.action_note = label(tr('选齐两张图片后开始；工程会自动保存。'))
    w.action_note.setMinimumHeight(22); w.action_note.setAlignment(Qt.AlignLeft | Qt.AlignTop)
    actions.addWidget(w.action_note); rail_layout.addWidget(w.action_dock)
    w.project_details_toggle, project_details = disclosure(rail_layout, tr('工程详情'))
    details = QVBoxLayout(project_details); details.setContentsMargins(0, 0, 0, 0); details.setSpacing(8)
    w.project_info = label(tr('工程尚未保存')); details.addWidget(w.project_info)
    w.destination_edit = QLineEdit(); set_text(w.destination_edit, tr('自动创建工程，或选择位置'), 'setPlaceholderText'); set_text(w.destination_edit, tr('新工程保存位置'), 'setAccessibleName')
    w.destination_browse = text_widget(QPushButton, tr('工程位置…')); details.addWidget(w.destination_edit); details.addWidget(w.destination_browse)
    w.project_edit = QLineEdit(); w.project_edit.setReadOnly(True); set_text(w.project_edit, tr('已保存工程完整路径'), 'setAccessibleName'); details.addWidget(w.project_edit)
    w.release_button = text_widget(QPushButton, tr('释放结果预览')); w.release_button.setObjectName('quiet'); details.addWidget(w.release_button)
    w.sidebar_scroll = QScrollArea(); w.sidebar_scroll.setWidgetResizable(True)
    w.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); w.sidebar_scroll.setWidget(rail)
    rail.attach_viewport(w.sidebar_scroll.viewport())
    w.sidebar_scroll.setMinimumWidth(300); w.sidebar_scroll.setMaximumWidth(360)
    w.workspace_splitter.addWidget(w.sidebar_scroll); w.workspace_splitter.setStretchFactor(0, 1)
    w.workspace_splitter.setStretchFactor(1, 0); w.workspace_splitter.setSizes([1100, 320]); outer.addWidget(w.workspace_splitter, 1)
    footer_frame = QFrame(); footer_frame.setObjectName('workspaceFooter')
    footer = QVBoxLayout(footer_frame); footer.setContentsMargins(16, 0, 16, 6); footer.setSpacing(4)
    w.progress = QProgressBar(); w.progress.setTextVisible(False); w.progress.setRange(0, 1); w.progress.setValue(0); footer.addWidget(w.progress)
    status_row = QWidget()
    row = QHBoxLayout(status_row); row.setContentsMargins(0,0,0,0); row.setSpacing(10)
    w.status_label = label(tr('选择原图与调色参考，开始一组新的仿色。'), 'currentStatus')
    w.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    w.status_label.setFocusPolicy(Qt.StrongFocus); set_text(w.status_label, tr('当前任务状态与错误详情'), 'setAccessibleName')
    row.addWidget(w.status_label, 1); w.elapsed_label = label(''); w.elapsed_label.setWordWrap(False)
    w.elapsed_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter); row.addWidget(w.elapsed_label)
    w.cancel_button = text_widget(QPushButton, tr('取消任务')); row.addWidget(w.cancel_button)
    w.diag_toggle = text_widget(QCheckBox, tr('诊断')); row.addWidget(w.diag_toggle); footer.addWidget(status_row)
    cancel_policy = w.cancel_button.sizePolicy()
    cancel_policy.setRetainSizeWhenHidden(True)
    w.cancel_button.setSizePolicy(cancel_policy)
    def size_status_row():
        w.elapsed_label.setMinimumWidth(w.elapsed_label.fontMetrics().horizontalAdvance(tr('已用 {0} 秒', '00000')))
    language = manager()
    language.changed.connect(size_status_row)
    status_row.destroyed.connect(lambda: language.changed.disconnect(size_status_row))
    size_status_row()
    w.diagnostics = QTextEdit(); w.diagnostics.setReadOnly(True); w.diagnostics.setMaximumHeight(120); w.diagnostics.hide()
    w.diag_toggle.toggled.connect(w.diagnostics.setVisible); footer.addWidget(w.diagnostics); outer.addWidget(footer_frame)
    w.commands = Commands(w)
    for button, key in ((w.source_browse, 'source'), (w.reference_browse, 'reference'),
            (w.cancel_button, 'cancel'), (w.release_button, 'clear'), (w.export_button, 'export'),
            (w.destination_browse, 'destination'), (w.process_button, 'process'),
            (w.load_button, 'replay'), (w.refine_button, 'refine'), (w.export_browse, 'export-location')):
        w.commands.bind_button(button, key)
    set_text(w.refine_button,tr('复用快速仿色参数，继续局部校正与细节处理；另存为精细工程。'),'setToolTip')
    for key in ('result-view', 'source-result-view', 'reference-result-view', 'return-view'):
        w.toolbar.addAction(w.commands.actions[key])
    w.layout_button = QToolButton(); set_text(w.layout_button, tr('布局')); w.layout_button.setObjectName('layoutTool')
    w.layout_button.setIcon(action_icon('layout')); w.layout_button.setIconSize(QSize(16,16))
    w.layout_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    set_text(w.layout_button, tr('双图排列'), 'setAccessibleName'); menu = QMenu(w.layout_button)
    for key in ('layout-horizontal', 'layout-vertical'): menu.addAction(w.commands.actions[key])
    w.layout_button.setMenu(menu); w.layout_button.setPopupMode(QToolButton.InstantPopup); w.toolbar.addWidget(w.layout_button)
    w.toolbar.addSeparator(); w.toolbar.addAction(w.commands.actions['fit']); w.toolbar.addAction(w.commands.actions['one-to-one'])
    spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred)
    w.toolbar.addWidget(spacer); w.toolbar.addAction(w.commands.actions['sidebar'])
    w.fit_button = w.toolbar.widgetForAction(w.commands.actions['fit']); w.roi_button = w.toolbar.widgetForAction(w.commands.actions['one-to-one'])
    w.roi_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    w.toolbar.widgetForAction(w.commands.actions['sidebar']).setToolButtonStyle(Qt.ToolButtonIconOnly)
    for button in w.toolbar.findChildren(QToolButton):
        button.setFixedHeight(32)
        button.setIconSize(QSize(16,16))
    w.toolbar.widgetForAction(w.commands.actions['sidebar']).setFixedWidth(34)
    for role, pane in (('source', w.source_pane), ('reference', w.reference_pane)):
        pane.imageDropped.connect(lambda path, r=role: w._accept_dropped_image(r, path)); pane.imageRequested.connect(lambda r=role: w._browse_image(r))
        card = w.materials[role]; card.imageDropped.connect(lambda path, r=role: w._accept_dropped_image(r, path))
        card.viewRequested.connect(lambda r: w.commands.actions['inspect-'+r].trigger())
    w.chrome = TitleBar(w, w.commands); w.project_title = w.chrome.document; w.chrome.install_native()
