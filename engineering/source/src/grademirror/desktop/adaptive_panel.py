"""Experimental resource panel; normal CPU/auto submission remains disabled."""
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QKeySequence,QShortcut,QPixmap
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QSlider,QFileDialog,QPlainTextEdit,QProgressBar,QLineEdit
from .adaptive_controller import AdaptiveController

class AdaptivePanel(QWidget):
    def __init__(self,backend,parent=None):
        super().__init__(parent);self.controller=AdaptiveController(backend,self);self._closing=False;self.recipe_path=None;self.preview_loads=0;self._plan_valid=False;self._last_stage='尚未提交'
        self.training_total_updates=None;self.select_export_path=QFileDialog.getSaveFileName
        self.setObjectName('gmAdaptivePanel');self.setAttribute(Qt.WA_StyledBackground,True)
        self.setStyleSheet('''
QWidget#gmAdaptivePanel {background:#24272C;color:#E3E5E8;}
QWidget#gmAdaptivePanel QLabel {color:#D2D7E0;background:transparent;font-weight:normal;}
QWidget#gmAdaptivePanel QPushButton,QWidget#gmAdaptivePanel QComboBox,QWidget#gmAdaptivePanel QLineEdit,QWidget#gmAdaptivePanel QPlainTextEdit {background:#30343B;color:#E3E5E8;border:1px solid #505763;border-radius:4px;padding:5px;}
QWidget#gmAdaptivePanel QPushButton:disabled {color:#8A909B;border-color:#3B414A;}
QWidget#gmAdaptivePanel QPushButton:hover {background:#3C4450;}
QWidget#gmAdaptivePanel QPlainTextEdit {background:#1E2126;color:#BBC6D7;font-size:11px;}
QWidget#gmAdaptivePanel QComboBox QAbstractItemView {background:#30343B;color:#E3E5E8;selection-background-color:#4B5D7B;}
''')
        layout=QVBoxLayout(self);layout.addWidget(QLabel('实验性资源适配'))
        self.notice=QLabel('真实 CPU / 自动执行尚未完成资格；原默认处理方式保持。');self.notice.setWordWrap(True);layout.addWidget(self.notice)
        row=QHBoxLayout();self.quality=QComboBox();self.quality.addItems(['快速','精细']);self.preference=QComboBox();self.preference.addItems(['当前执行','自动选择','CPU']);row.addWidget(self.quality);row.addWidget(self.preference);layout.addLayout(row)
        row=QHBoxLayout();self.ram_limit=QLineEdit();self.commit_limit=QLineEdit();self.vram_limit=QLineEdit()
        for w,label in ((self.ram_limit,'总 RAM MiB'),(self.commit_limit,'总 commit MiB'),(self.vram_limit,'总 VRAM MiB')):
            w.setPlaceholderText(label);w.setToolTip(label+'；留空不设用户上限，0为有效上限。');row.addWidget(w);w.editingFinished.connect(self.refresh_plan)
        layout.addLayout(row);self.plan_text=QLabel('尚无输入意图；资源未知。');self.plan_text.setWordWrap(True);layout.addWidget(self.plan_text)
        self.quality.currentIndexChanged.connect(self.refresh_plan);self.preference.currentIndexChanged.connect(self.refresh_plan)
        actions=QHBoxLayout();self.open_button=QPushButton('打开 CPU 配方');self.submit_button=QPushButton('执行');self.cancel_button=QPushButton('取消');self.release_button=QPushButton('释放结果')
        for w in (self.open_button,self.submit_button,self.cancel_button,self.release_button):actions.addWidget(w)
        layout.addLayout(actions);self.submit_button.setEnabled(False);self.submit_button.setToolTip('EXECUTION_NOT_QUALIFIED')
        self.source_status=QLabel('配方未打开');self.relink_button=QPushButton('重新关联原图');self.relink_button.setEnabled(False)
        self.relink_button.clicked.connect(self.relink);layout.addWidget(self.source_status);layout.addWidget(self.relink_button)
        self.preview_label=QLabel('驻留结果可用后调整强度');self.preview_label.setAlignment(Qt.AlignCenter);self.preview_label.setMinimumHeight(70);layout.addWidget(self.preview_label)
        self.slider=QSlider(Qt.Horizontal);self.slider.setRange(0,100);self.slider.setValue(100);self.slider.setEnabled(False);layout.addWidget(self.slider)
        self.export_button=QPushButton('导出 JPEG');self.export_button.setEnabled(False);layout.addWidget(self.export_button)
        self.status=QLabel('尚未提交');self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.hide();layout.addWidget(self.status);layout.addWidget(self.progress)
        self.diagnostics=QPlainTextEdit();self.diagnostics.setReadOnly(True);self.diagnostics.document().setMaximumBlockCount(512);layout.addWidget(self.diagnostics)
        self.diagnostics.setMaximumHeight(160);self.diagnostics.hide()
        self.diagnostics_toggle=QPushButton('诊断信息');self.diagnostics_toggle.setCheckable(True)
        self.diagnostics_toggle.toggled.connect(self.diagnostics.setVisible);layout.insertWidget(layout.count()-1,self.diagnostics_toggle)
        layout.addStretch(1)
        self.submit_button.clicked.connect(self.controller.submit);self.cancel_button.clicked.connect(self.controller.cancel);self.release_button.clicked.connect(self.controller.release)
        self.open_button.clicked.connect(self.open_recipe);self.export_button.clicked.connect(self.export)
        self.debounce=QTimer(self);self.debounce.setSingleShot(True);self.debounce.setInterval(160);self.debounce.timeout.connect(lambda:self.controller.preview(self.slider.value()/100))
        self.slider.valueChanged.connect(lambda:self.debounce.start());self.controller.updated.connect(self.update_state);self.controller.failed.connect(self.show_error);self.controller.completed.connect(self.received)
        self.cancel_shortcut=QShortcut(QKeySequence('Escape'),self);self.cancel_shortcut.activated.connect(self.controller.cancel)
        self._close_poll=QTimer(self);self._close_poll.setInterval(50);self._close_poll.timeout.connect(self._finish_close)
    def configure_qualification(self,request,workspace,qualification):
        # The service independently authenticates the frozen factory; this UI
        # injection cannot authorize an ordinary request or a native worker.
        self.configure_intent(request,workspace,qualification)
        from ..common import strict_load
        q=strict_load(Path(qualification));self.training_total_updates=q.get('settings',{}).get('updates') if q.get('schema')=='adaptive-first-qualification-v1' else None
        self.notice.setText('独立CPU兼容运行目录：仅限本次固定资格，非生产启用。' if q.get('schema')=='adaptive-independent-qualification-v3' else '恢复已保存的快速配方驻留结果：不重新拟合，仅限当前资格。' if q.get('schema')=='adaptive-first-recovery-qualification-v1' else '固定照片 CPU 首次处理／精细追加：仅限当前精确资格，非生产启用。' if q.get('schema')=='adaptive-first-qualification-v1' else '固定配方 CPU 实验重放：仅限当前精确资格，非生产启用。' if q.get('schema')=='adaptive-native-qualification-v1' else 'TEST_ONLY 小帧资格：不运行真实算法或照片。')
        self.submit_button.setEnabled(self._plan_valid and not self.controller.futures)
    def configure_intent(self,request,workspace,qualification=None):
        self.training_total_updates=None;self.controller.configure(request,workspace,qualification)
        self._last_stage='等待提交';self.preview_label.clear();self.preview_label.setText('驻留结果可用后调整强度')
        self.quality.blockSignals(True);self.preference.blockSignals(True)
        self.quality.setCurrentIndex(0 if request['quality']=='fast' else 1);self.preference.setCurrentIndex(('current','auto','cpu').index(request['execution_preference']))
        self.quality.blockSignals(False);self.preference.blockSignals(False);self.refresh_plan()
        self.submit_button.setEnabled(qualification is not None and self._plan_valid and not self.controller.futures)
    def refresh_plan(self,*unused):
        if self.controller.request is None:return
        import copy
        request=copy.deepcopy(self.controller.request)
        try:
            request.update(quality=('fast','fine')[self.quality.currentIndex()],execution_preference=('current','auto','cpu')[self.preference.currentIndex()])
            request['limits']={k:None if not w.text().strip() else int(w.text())*1024**2 for k,w in
                (('ram_bytes',self.ram_limit),('commit_bytes',self.commit_limit),('vram_bytes',self.vram_limit))}
            value=self.controller.backend.adaptive_plan(request,qualification=self.controller.qualification,workspace=self.controller.workspace);self.controller.request=request;self._plan_valid=value['execution_authorized']
            cpu=value['candidates'][0];saved=any(x['request_metadata_matches_saved_input'] is True for x in value['capabilities']);self.plan_text.setText(value.get('feedback','读数未知，尚未开始')+'\n阶段：'+' → '.join(cpu['stages'])+('\n独立运行固定配方实验资格；预计时间：未知' if value['schema']=='adaptive-plan-v3' else '\n当前构建：ENGINEERING_ONLY / NATIVE_REQUALIFICATION_PENDING；预计时间：未知'))
            self.submit_button.setEnabled(self.controller.qualification is not None and self._plan_valid and not self.controller.futures)
        except Exception as e:self._plan_valid=False;self.plan_text.setText('计划无法生成：'+str(e));self.submit_button.setEnabled(False)
    def received(self,row):
        value=row['value']
        if row['action'] in ('open','relink'):
            source=value.get('source_status',{});self.source_status.setText('源文件：'+source.get('status','UNKNOWN')+' · 已保存，需显式重放')
            self.relink_button.setEnabled(self.recipe_path is not None)
        if row['action']=='preview':
            # Only an accepted, current epoch/generation may reach this slot.
            target=value.get('path') or value.get('destination') or value.get('file',{}).get('path')
            if target:
                from .backend import EngineBackend
                path=Path(target)
                if path.suffix.lower() not in (".jpg", ".jpeg"):
                    return
                try:
                    reader,device=EngineBackend._image_reader(path,"预览")
                except (OSError,ValueError):
                    return
                try:
                    size=reader.size()
                    if not size.isValid() or max(size.width(),size.height())>1024:
                        return
                    image=reader.read()
                finally:
                    device.close()
                if not image.isNull():
                    self.preview_label.setPixmap(QPixmap.fromImage(image).scaled(240,160,Qt.KeepAspectRatio))
                    self.preview_loads+=1
    def update_state(self,value):
        if 'state' in value:self.status.setText(str(value['state']));self.diagnostics.appendPlainText(str(value))
        elif self.controller.busy:self.status.setText(self._last_stage+' · 已用 %.1fs · 已等待 %.1fs（非worker心跳）'%(value['ui_elapsed_seconds'],value['ui_waited_seconds']))
        resident=bool(self.controller.result and self.controller.result.get('resident'))
        self.slider.setEnabled(resident);self.export_button.setEnabled(resident);self.release_button.setEnabled(resident)
        self.cancel_button.setEnabled(self.controller.busy);self.progress.setVisible(self.controller.busy)
        detail=value.get('result')
        if isinstance(detail,dict) and 'stage' in detail:
            current=detail.get('progress') or detail.get('detail') or detail
            done=current.get('done');total=current.get('total')
            if done is None:done=current.get('completed_updates',current.get('completed_tiles',current.get('update')))
            if total is None:total=current.get('total_updates',current.get('total_tiles'))
            if total is None and current.get('stage') in ('training','sampling'):total=self.training_total_updates
            self._last_stage=str(current.get('detail_stage') or current.get('stage'))
            suffix=(' · 已用 %.1fs · 已等待 %.1fs'%(value['ui_elapsed_seconds'],value['ui_waited_seconds'])) if 'ui_elapsed_seconds' in value else ''
            self.status.setText(self._last_stage+(' · '+str(done)+' / '+str(total) if total is not None and done is not None else ' · 总量未知')+suffix+(' · 取消中' if self.controller.cancel_requested and self.controller.busy else ''))
            if isinstance(done,int) and isinstance(total,int) and total>0:self.progress.setRange(0,total);self.progress.setValue(done)
            else:self.progress.setRange(0,0)
        if self.controller.qualification:self.submit_button.setEnabled(self._plan_valid and not self.controller.busy and not self.controller.futures)
    def show_error(self,value):
        saved=value.get('preserved_recipes',[])
        self.status.setText(value['stage']+'：'+value['message']+(' · 已保存配方，需显式重放。' if saved else ''));self.diagnostics.appendPlainText(str(value));self.progress.hide()
    def open_recipe(self):
        path=QFileDialog.getExistingDirectory(self,'打开自包含 CPU 配方')
        if not path:return
        self.open_recipe_path(Path(path))
    def open_recipe_path(self,path):
        self.recipe_path=Path(path);self.controller._call('open',lambda:self.controller.backend.adaptive_open_recipe(self.recipe_path))
    def relink(self):
        path,_=QFileDialog.getOpenFileName(self,'重新关联原图')
        if path:self.relink_path(Path(path))
    def relink_path(self,path):
        recipe=self.recipe_path;self.controller._call('relink',lambda:self.controller.backend.adaptive_relink_recipe(recipe,Path(path)))
    def export(self):
        path,_=self.select_export_path(self,'导出 JPEG','','JPEG (*.jpg)')
        if path:self.controller.export(path,self.slider.value()/100)
    def closeEvent(self,event):
        if self.controller.futures or self.controller.busy or (self.controller.result and self.controller.result.get('resident')):
            event.ignore();self._closing=True
            if self.controller.busy:self.controller.cancel()
            elif self.controller.result:self.controller.release()
            self._close_poll.start();return
        self.controller.shutdown();event.accept()
    def _finish_close(self):
        if self.controller.futures or self.controller.busy:return
        if self.controller.result and self.controller.result.get('resident'):self.controller.release();return
        self._close_poll.stop();self.close()
