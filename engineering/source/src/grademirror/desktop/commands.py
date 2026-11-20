"""One action table for menus, visible tools and shortcuts; controller owns work."""

from .i18n import tr, render, source_text
from .language import set_text, text_widget, manager, unbind_text
from .language import bound_text
import weakref
from PySide6.QtCore import QSignalBlocker, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QGridLayout,
    QLabel, QLineEdit, QPlainTextEdit, QTextEdit, QVBoxLayout)
from .branding import action_icon, app_icon, desktop_version

class Commands:
    def __init__(self, window):
        self.window = window
        self.actions = {}
        self._editor = None
        # Methods are looked up on dispatch so a replaced/injected controller remains observable.
        definitions = [
            ('new',tr('新建仿色'),'Ctrl+N','new','_toggle_new_project'),
            ('open',tr('打开工程…'),'Ctrl+O','open','_choose_open_project'),
            ('source',tr('选择原图…'),None,'image',lambda:window._browse_image('source')),
            ('reference',tr('选择 AI 参考图…'),None,'image',lambda:window._browse_image('reference')),
            ('save',tr('保存工程'),'Ctrl+S','save','_save_clicked'),
            ('export',tr('导出图片…'),'Ctrl+E','export','_choose_export_image'),
            ('exit',tr('退出'),None,'cancel','close'),
            ('cancel',tr('取消当前任务'),None,'cancel','_cancel_clicked'),
            ('clear',tr('清除预览'),None,'clear','_release_clicked'),
            ('fit',tr('适合窗口'),'Ctrl+0','fit','_fit_clicked'),
            ('one-to-one','100%','Ctrl+1','one-to-one','_roi_clicked'),
            ('diagnostics',tr('显示诊断'),None,'diagnostics',lambda:window.diag_toggle.setChecked(self.actions['diagnostics'].isChecked())),
            ('export-log',tr('导出诊断日志…'),None,'diagnostics','_choose_export_log'),
            ('shortcuts',tr('快捷键'),None,'help',self.show_shortcuts),
            ('about',tr('关于 GradeMirror'),None,'info',self.show_about)]
        definitions += [
            ('destination',tr('工程位置…'),None,'open','_browse_destination'),
            ('create',tr('仅保存工程'),None,'save','_create_clicked'),
            ('process',tr('开始仿色'),None,'process','_primary_clicked'),
            ('replay',tr('恢复仿色结果'),None,'replay','_load_clicked'),
            ('refine',tr('继续精细处理…'),None,'process','_refine_clicked'),
            ('export-location',tr('另存…'),None,'export','_browse_export')]
        definitions.append(('system-menu',tr('系统菜单'),'Alt+Space',None,self.show_system_menu))
        definitions += [
            ('result-view',tr('结果'),None,None,lambda:window._select_display('result')),
            ('source-result-view',tr('原图 ↔ 结果'),None,None,lambda:window._select_display('source_result')),
            ('reference-result-view',tr('参考 ↔ 结果'),None,None,lambda:window._select_display('reference_result')),
            ('layout-horizontal',tr('左右'),None,None,lambda:window._arrange_display('horizontal')),
            ('layout-vertical',tr('上下'),None,None,lambda:window._arrange_display('vertical')),
            ('sidebar',tr('设置面板'),None,'sidebar','_toggle_sidebar'),
            ('return-view',tr('返回比较／结果'),None,None,'_return_display'),
            ('inspect-source',tr('查看原图'),None,None,lambda:window._inspect_material('source')),
            ('inspect-reference',tr('查看调色参考'),None,None,lambda:window._inspect_material('reference'))]
        for key,text,shortcut,icon,handler in definitions:
            action = QAction(action_icon(icon) if icon else QIcon(), text, window)
            set_text(action,text)
            action.setObjectName('gm.action.' + key)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda checked=False,k=key,h=handler:self._dispatch(k,h))
            window.addAction(action)
            self.actions[key] = action
        for key,shortcut,label in [('cut','Ctrl+X',tr('剪切')),('copy','Ctrl+C',tr('复制')),
                ('paste','Ctrl+V',tr('粘贴')),('select-all','Ctrl+A',tr('全选'))]:
            a=QAction(action_icon(key),label,window);a.setObjectName('gm.action.'+key)
            set_text(a,label)
            a.setShortcut(QKeySequence(shortcut));a.triggered.connect(lambda checked=False,k=key:self._text(k))
            window.addAction(a);self.actions[key]=a
        group=QActionGroup(window);group.setExclusive(True)
        for key in ('fit','one-to-one'):
            self.actions[key].setCheckable(True);group.addAction(self.actions[key])
        for keys in (('result-view','source-result-view','reference-result-view'),
                     ('layout-horizontal','layout-vertical')):
            group=QActionGroup(window);group.setExclusive(True)
            for key in keys:self.actions[key].setCheckable(True);group.addAction(self.actions[key])
        self.actions['sidebar'].setCheckable(True)
        self.actions['diagnostics'].setCheckable(True)
        QApplication.instance().focusChanged.connect(self._focus_changed)
        QApplication.clipboard().dataChanged.connect(self.sync_text)
        window.diag_toggle.toggled.connect(lambda _:self.sync_diagnostics())

    def detach(self):
        # QApplication outlives a window; do not retain closed controllers/editors.
        for signal,slot in ((QApplication.instance().focusChanged,self._focus_changed),
                (QApplication.clipboard().dataChanged,self.sync_text)):
            try:signal.disconnect(slot)
            except RuntimeError:pass
        self._editor=None

    def _dispatch(self, key, handler):
        if not self.actions[key].isEnabled():
            return
        if key not in ('exit','shortcuts','about','diagnostics') and (
                self.window._closing or self.window._close_after_release):
            return
        (getattr(self.window,handler) if isinstance(handler,str) else handler)()
        self.sync()

    def bind_button(self, button, key):
        action=self.actions[key]
        button.setIcon(action.icon())
        set_text(button, bound_text(action), 'setAccessibleName')
        button.clicked.connect(action.trigger)
        set_text(button, bound_text(action) + ('  '+action.shortcut().toString() if not action.shortcut().isEmpty() else ''), 'setToolTip')

    def _focus_changed(self, old, now):
        if (isinstance(now,(QLineEdit,QTextEdit,QPlainTextEdit)) or
                isinstance(now,QLabel) and now.textInteractionFlags() & Qt.TextSelectableByKeyboard) and now.window() is self.window:
            self._editor=weakref.ref(now)
        elif now is not None and now.window() is self.window:
            # Menu opening retains the text widget as the editing target.
            from PySide6.QtWidgets import QMenuBar,QMenu
            if not isinstance(now,(QMenuBar,QMenu)):
                self._editor=None
        self.sync_text()

    def text_target(self):
        editor=self._editor() if self._editor else None
        try:
            return editor if editor is not None and editor.isEnabled() else None
        except RuntimeError:
            self._editor=None
            return None

    def sync_text(self):
        t=self.text_target()
        text = t.text() if isinstance(t,(QLineEdit,QLabel)) else t.toPlainText() if t else ''
        selected = t.hasSelectedText() if isinstance(t,(QLineEdit,QLabel)) else t.textCursor().hasSelection() if t else False
        editable=t is not None and not isinstance(t,QLabel) and not t.isReadOnly()
        mime=QApplication.clipboard().mimeData() if editable else None
        paste=editable and mime is not None and mime.hasText()
        states={'cut':editable and selected,'copy':selected,'paste':paste,'select-all':bool(text)}
        for key,enabled in states.items():self.actions[key].setEnabled(bool(enabled))

    def _text(self,key):
        self.sync_text();target=self.text_target()
        if target is not None and self.actions[key].isEnabled():
            if isinstance(target,QLabel):
                if key=='copy':set_text(QApplication.clipboard(), target.selectedText())
                elif key=='select-all':target.setSelection(0,len(target.text()))
            else:getattr(target,{'select-all':'selectAll'}.get(key,key))()
            self.sync_text()

    def sync_diagnostics(self):
        a=self.actions['diagnostics']
        with QSignalBlocker(a):a.setChecked(self.window.diag_toggle.isChecked())

    def sync(self):
        w=self.window
        pairs={'source':w.source_browse,'reference':w.reference_browse,'export':w.export_button,
            'fit':w.fit_button,'one-to-one':w.roi_button,'cancel':w.cancel_button,'clear':w.release_button,
            'destination':w.destination_browse,'process':w.process_button,
            'replay':w.load_button,'refine':w.refine_button,'export-location':w.export_browse}
        closing=w._closing or w._close_after_release
        for key,enabled in w._command_states.items():
            self.actions[key].setEnabled(enabled and not closing)
        for key,b in pairs.items():
            enabled=b.isEnabled() and not closing
            if key=='save' and (w.project or {}).get('read_only',False):enabled=False
            self.actions[key].setEnabled(enabled)
            b.setEnabled(enabled)
        for key,checked in [('fit',w.roi is None),('one-to-one',w.roi is not None)]:
            with QSignalBlocker(self.actions[key]):self.actions[key].setChecked(checked)
        for key,selection in (('result-view','result'),('source-result-view','source_result'),
                              ('reference-result-view','reference_result')):
            self.actions[key].setVisible(w.binding is not None)
            with QSignalBlocker(self.actions[key]):self.actions[key].setChecked(w.display.object==selection)
        self.actions['return-view'].setVisible(w.display.temporary)
        for key,layout in (('layout-horizontal','horizontal'),('layout-vertical','vertical')):
            with QSignalBlocker(self.actions[key]):self.actions[key].setChecked(w.display.layout==layout)
        with QSignalBlocker(self.actions['sidebar']):self.actions['sidebar'].setChecked(w.display.sidebar)
        set_text(self.actions['process'], bound_text(w.process_button))
        set_text(self.actions['replay'], bound_text(w.load_button))
        self.sync_diagnostics();self.sync_text()
        for key in ('shortcuts','about'):self.actions[key].setEnabled(not closing)
        self.actions['export-log'].setEnabled(
            not closing and not any(kind == 'support_log' for kind, *_ in w._tasks))
        self.actions['system-menu'].setEnabled(bool(getattr(getattr(w,'chrome',None),'adapter',None)
            and w.chrome.adapter.active))
        if hasattr(w,'chrome'):w.chrome.update_document()

    def show_system_menu(self):
        if self.window.chrome.adapter.active:self.window.chrome.adapter.system_menu()

    def _dialog(self,title):
        dialog=QDialog(self.window);set_text(dialog, title, 'setWindowTitle');dialog.setWindowIcon(app_icon())
        dialog.setAttribute(Qt.WA_DeleteOnClose);dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumWidth(400);layout=QVBoxLayout(dialog);layout.setContentsMargins(24,22,24,20);layout.setSpacing(16)
        # QWidget's transparent workstation base must not leave top-level dialogs unpainted.
        dialog.setAutoFillBackground(True)
        if __import__('sys').platform=='win32':
            import ctypes
            dark=ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(int(dialog.winId())),20,ctypes.byref(dark),4)
        return dialog,layout

    def show_shortcuts(self):
        dialog,layout=self._dialog(tr('快捷键 · GradeMirror'));grid=QGridLayout();grid.setHorizontalSpacing(48)
        for row,a in enumerate(x for x in self.actions.values() if not x.shortcut().isEmpty()):
            grid.addWidget(text_widget(QLabel, bound_text(a)),row,0);grid.addWidget(text_widget(QLabel, a.shortcut().toString(QKeySequence.NativeText)),row,1)
        row=grid.rowCount();grid.addWidget(text_widget(QLabel, tr('退出')),row,0);grid.addWidget(text_widget(QLabel, 'Alt+F4'),row,1)
        layout.addLayout(grid);layout.addWidget(text_widget(QLabel, tr('文本快捷键只操作当前文本框；Esc 关闭菜单或对话框。')))
        buttons=QDialogButtonBox(QDialogButtonBox.Close);set_text(buttons.button(QDialogButtonBox.Close), tr('关闭'));buttons.rejected.connect(dialog.close);layout.addWidget(buttons)
        self.shortcut_dialog=dialog;dialog.show()

    def show_about(self):
        dialog,layout=self._dialog(tr('关于 GradeMirror'));logo=QLabel();logo.setPixmap(app_icon().pixmap(QSize(80,80),dialog.devicePixelRatioF()));logo.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo);title=text_widget(QLabel, 'GradeMirror');title.setAlignment(Qt.AlignCenter);title.setStyleSheet('font-size:22px;font-weight:600;')
        layout.addWidget(title);version=text_widget(QLabel, desktop_version());version.setAlignment(Qt.AlignCenter);layout.addWidget(version)
        text=text_widget(QLabel, tr('原图的细节与尺寸 · 参考图的色彩'));text.setAlignment(Qt.AlignCenter);layout.addWidget(text)
        buttons=QDialogButtonBox(QDialogButtonBox.Close);set_text(buttons.button(QDialogButtonBox.Close), tr('关闭'));buttons.rejected.connect(dialog.close);layout.addWidget(buttons)
        self.about_dialog=dialog;dialog.show()
