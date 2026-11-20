"""Compact photographic workstation chrome; no photo or operation logic."""

from .i18n import tr, render, source_text
from .language import set_text, text_widget, manager, unbind_text, bound_text
from PySide6.QtCore import Qt,QSize,QEvent
from PySide6.QtGui import QPainter,QPen,QColor,QAction,QActionGroup
from PySide6.QtWidgets import QFrame,QHBoxLayout,QLabel,QMenu,QMenuBar,QMessageBox,QSizePolicy,QToolButton
from .branding import app_icon,mark_icon,configure_taskbar

CHROME_STYLE='''
QFrame#gmChrome { background:#292A2D; border-bottom:1px solid #45484E; }
QMenuBar {background:#292A2D; color:#E3E5E8; padding:0; spacing:0; font-size:13px;}
QMenuBar::item {background:transparent; padding:7px 9px; margin:0; border:1px solid transparent;}
QMenuBar::item:selected {background:#41444A;}
QMenuBar::item:pressed {background:#303236; border-color:#45484E;}
QMenu {background:#303236; color:#E3E5E8; border:1px solid #45484E; padding:5px 0;}
QMenu::item {padding:5px 30px 5px 12px; min-width:225px; border:1px solid transparent;}
QMenu::item:selected {background:#41444A; border-color:#A9BDE8;}
QMenu::item:disabled {color:#858990;}
QMenu::separator {height:1px; background:#45484E; margin:5px 10px;}
QMenu::icon {padding-left:8px;}
QDialog {background:#292A2D;}
QToolButton#gmTool {background:transparent; border:1px solid transparent; border-radius:4px; padding:3px 7px; color:#C9CED7;}
QToolButton#gmTool:hover {background:#30353D;}
QToolButton#gmTool:focus {border-color:#A9BDE8;}
QToolButton#gmTool:disabled {color:#858990;}
QToolButton#gmCaption {background:transparent; border:none; border-radius:0; padding:0;}
QToolButton#gmCaption:hover,QToolButton#gmCaption[nativeHover="true"] {background:#41444A;}
QToolButton#gmCaption:pressed {background:#50535B;}
QToolButton#gmClose:hover {background:#C42B1C;}
QToolButton#gmClose:pressed {background:#A92216;}
QToolButton#gmCaption:focus,QToolButton#gmClose:focus {border:1px solid #A9BDE8;}
'''

class DocumentLabel(QLabel):
    def __init__(self):
        super().__init__();self.full_text='';self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.setAlignment(Qt.AlignCenter);self.setStyleSheet('color:#C5C8CE;font-size:12px;')
    def setText(self,text):
        self.full_text=text
        self._elide()
    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self.full_text,Qt.ElideMiddle,max(0,self.width()-12)))
    def resizeEvent(self,event):
        self._elide();super().resizeEvent(event)

class CaptionButton(QToolButton):
    def __init__(self,window,kind):
        super().__init__();self.host_window=window;self.kind=kind
        self.setObjectName('gmClose' if kind=='close' else 'gmCaption');self.setFixedSize(46,36)
        self.setProperty('nativeHover',False);self.setFocusPolicy(Qt.StrongFocus)
        set_text(self, {'minimize':tr('最小化'),'maximize':tr('最大化或还原'),'close':tr('关闭')}[kind], 'setAccessibleName')
        set_text(self, bound_text(self,'setAccessibleName'), 'setToolTip')
    def paintEvent(self,event):
        super().paintEvent(event);p=QPainter(self);p.setPen(QPen(QColor('#E3E5E8'),1.0))
        x,y=self.width()//2,self.height()//2
        if self.kind=='minimize':p.drawLine(x-5,y,x+5,y)
        elif self.kind=='close':p.drawLine(x-5,y-5,x+5,y+5);p.drawLine(x+5,y-5,x-5,y+5)
        elif self.host_window.isMaximized():
            p.drawRect(x-3,y-5,8,8);p.fillRect(x-5,y-3,8,8,QColor('#41444A') if self.underMouse() else QColor('#292A2D'));p.drawRect(x-5,y-3,8,8)
        else:p.drawRect(x-5,y-5,10,10)
        p.end()

class TitleBar(QFrame):
    def __init__(self,window,commands):
        super().__init__();self.host_window=window;self.setObjectName('gmChrome');self.setFixedHeight(36)
        layout=QHBoxLayout(self);layout.setContentsMargins(10,0,0,0);layout.setSpacing(3)
        self.brand=QLabel();self.brand.setFixedSize(22,22);set_text(self.brand, 'GradeMirror', 'setToolTip')
        self.brand.setPixmap(mark_icon().pixmap(QSize(22,22),window.devicePixelRatioF()))
        layout.addWidget(self.brand);layout.addSpacing(5)
        self.menus=QMenuBar();self.menus.setNativeMenuBar(False);self.menus.setSizePolicy(QSizePolicy.Maximum,QSizePolicy.Expanding)
        self.menus.setObjectName('gm.menubar');layout.addWidget(self.menus)
        table=[(tr('文件'),['new','open',None,'source','reference',None,'save','export',None,'exit']),
            (tr('编辑'),['cancel','clear',None,'cut','copy','paste','select-all']),
            (tr('视图'),['result-view','source-result-view','reference-result-view',None,
                'layout-horizontal','layout-vertical',None,'fit','one-to-one',None,
                'inspect-source','inspect-reference','return-view','sidebar','diagnostics']),(tr('帮助'),['export-log',None,'shortcuts','about'])]
        self.menu_map={}
        for label,keys in table:
            stable=source_text(label)
            menu=self.menus.addMenu(str(label));set_text(menu,label,'setTitle')
            menu.setObjectName('gm.menu.'+stable);menu.aboutToShow.connect(commands.sync)
            for key in keys:
                if key is None:menu.addSeparator()
                else:menu.addAction(commands.actions[key])
            self.menu_map[stable]=menu
        self.language_menu=QMenu(self.menus);self.language_menu.setObjectName('gm.menu.language')
        set_text(self.language_menu,tr('语言'),'setTitle')
        self.menu_map[source_text(tr('视图'))].addSeparator()
        self.menu_map[source_text(tr('视图'))].addMenu(self.language_menu)
        self.language_actions={};language_group=QActionGroup(self);language_group.setExclusive(True)
        for preference,label in (('system',tr('跟随系统')),('zh_CN',tr('简体中文')),('en','English')):
            action=QAction(self);set_text(action,label);action.setObjectName('gm.language.'+preference)
            action.setCheckable(True);language_group.addAction(action);self.language_menu.addAction(action)
            action.triggered.connect(lambda checked=False,p=preference:self.change_language(p))
            self.language_actions[preference]=action
        manager().changed.connect(self.sync_language);self.sync_language()
        layout.addSpacing(16)
        self.document=DocumentLabel();layout.addWidget(self.document,1);layout.addSpacing(16)
        self.minimize=CaptionButton(window,'minimize');self.maximize=CaptionButton(window,'maximize');self.close_button=CaptionButton(window,'close')
        for button in (self.minimize,self.maximize,self.close_button):layout.addWidget(button)
        self.minimize.clicked.connect(window.showMinimized);self.maximize.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close)
        self.adapter=None
        window.setWindowIcon(app_icon());window.setMenuWidget(self)

    def sync_language(self):
        for preference,action in self.language_actions.items():action.setChecked(preference==manager().preference)

    def change_language(self, preference):
        try:manager().set_preference(preference)
        except OSError:
            self.sync_language()
            QMessageBox.warning(self.host_window,str(tr('语言')),
                str(tr('无法保存语言设置，请检查用户配置目录的写入权限。')))

    def update_document(self):
        w=self.host_window
        name=w.project_path.stem if w.project_path and w.project else tr('新的仿色')
        title=name+' · GradeMirror';set_text(self.document, title)
        set_text(self.document, str(w.project_path) if w.project_path and w.project else title, 'setToolTip')
        if w.windowTitle()!=title:set_text(w, title, 'setWindowTitle')
        maximized=w.isMaximized()
        if getattr(self,'_last_maximized',None)!=maximized:
            self._last_maximized=maximized;self.maximize.update()

    def event(self,event):
        if event.type()==QEvent.DevicePixelRatioChange and hasattr(self,'brand'):
            self.brand.setPixmap(mark_icon().pixmap(QSize(22,22),self.devicePixelRatioF()))
        return super().event(event)

    def toggle_maximized(self):
        (self.host_window.showNormal if self.host_window.isMaximized() else self.host_window.showMaximized)()
        self.maximize.update()

    def drag_point(self,point):
        local=self.mapFrom(self.host_window,point)
        if not self.rect().contains(local):return False
        child=self.childAt(local)
        return child is None or child is self.document

    def install_native(self):
        from .windows_chrome import WindowsChrome
        self.adapter=WindowsChrome(self.host_window,self)
        self.adapter.install()
        self.taskbar_icon=configure_taskbar(self.host_window)
        self.update_document()

    def close_shell(self):
        try:manager().changed.disconnect(self.sync_language)
        except RuntimeError:pass
        if self.taskbar_icon is not None:
            try:self.taskbar_icon.close()
            except OSError as exc:
                self.host_window.setProperty('gradeMirrorTaskbarCleanupError',str(exc))
            finally:self.taskbar_icon=None

def tool_button(commands,key):
    button=QToolButton();button.setObjectName('gmTool');button.setDefaultAction(commands.actions[key])
    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon);button.setIconSize(QSize(16,16));return button
