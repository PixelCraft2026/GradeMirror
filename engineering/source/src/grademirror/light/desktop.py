"""Original GradeMirror workbench with a local environment page."""
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFormLayout, QLabel, QMessageBox, QPushButton
from ..common import GMError, atomic_json
from ..desktop.branding import configure_application
from ..desktop.language import set_text
from ..desktop.i18n import tr
from ..desktop.window import MainWindow
from .backend import Backend
from . import portable, runtime as rt
from .system import manifest, check_loaded


def gib(value):
    return f"{value/1024**3:.2f} GiB" if type(value) is int else tr("未知")


def detection_text(row):
    state=row["state"]
    if state=="NVIDIA_DEVICE_SELECTED":
        device=row["device"]
        return tr("GPU 候选 · {0} · 驱动 {1} · 可用显存 {2}", device["name"],device["driver_version"],gib(device["free_mib"]*1024**2))
    if state=="CPU_EDITION":
        return tr("CPU 离线版 · 内置 CPU 路线")
    if state=="NO_NVIDIA":
        return tr("系统未检测到 NVIDIA 显卡 · 可按图片条件使用 CPU")
    if state=="LIGHT_NVIDIA_NO_SUPPORTED_DEVICE":
        return tr("NVIDIA 驱动或架构不符合随包 GPU 运行时要求")
    return tr("GPU 检测失败 · {0}",state)


class Workbench(MainWindow):
    def __init__(self, backend):
        super().__init__(root=rt.data_root(),backend=backend)
        self._explorer_drop=None
        if portable.enabled() and QApplication.platformName()=="windows":
            from .native_picker import ExplorerFileDrop
            self._explorer_drop=ExplorerFileDrop(self)
        self._offline=manifest().get("bundled_roles") is not None
        self._device_summary=None
        self._cpu_once=False
        self._retry_once=None
        self.chrome.menus.removeAction(self._adaptive_action)
        help_menu=self.chrome.menu_map["帮助"]
        export_log=self.commands.actions["export-log"]
        action=QAction(self)
        set_text(action,tr("设备与运行状态…") if self._offline else tr("计算环境…"))
        action.setShortcut("Ctrl+Alt+R")
        action.triggered.connect(self.device_page if self._offline else self.environment_page)
        help_menu.insertAction(export_log,action)
        relink=QAction(self)
        set_text(relink,tr("重新定位原图…"))
        relink.triggered.connect(self.relink_source)
        help_menu.insertAction(export_log,relink)
        help_menu.aboutToShow.connect(lambda: relink.setEnabled(
            not self.busy and self.binding is None and self.project_path is not None))
        self._environment_action=action
        self._preview_eta=None
        self._preview_eta_timer=QTimer(self)
        self._preview_eta_timer.setInterval(250)
        self._preview_eta_timer.timeout.connect(self._update_preview_eta)
        self._preview_eta_timer.start()

    def nativeEvent(self, event_type, message):
        drop=getattr(self,"_explorer_drop",None)
        if drop is not None and drop.handle_message(message):
            return True,0
        return super().nativeEvent(event_type,message)

    def _support_log_info(self):
        package = manifest()
        return {"version": package.get("version") or super()._support_log_info()["version"],
                "edition": package.get("edition")}

    def detect_devices(self, callback=None):
        if self._closing or any(kind=="detect_devices" for kind, *_ in self._tasks):
            return

        def work():
            row=self.backend.device_overview()
            check_loaded()
            return row

        def finished(row,error):
            if self._closing:
                return
            self.backend.runtime_problem=str(error) if error else None
            self._device_summary=row
            self._log("light_runtime_checked",trust="UNKNOWN" if error else "VERIFIED",science=0,
                      device_status=row["device"]["state"] if row else None)
            if not self.busy and self.project is None:
                set_text(self.status_label,str(error) if error else detection_text(row["device"]))
            if callback:
                callback()

        self._run("detect_devices",work,finished)

    def _file_dialog(self, kind, title, initial="", file_filter=""):
        from .native_picker import file_path
        try:
            return file_path(self, kind, str(title), str(initial), str(file_filter))
        except (GMError, OSError, ValueError, KeyError) as error:
            self._error(title, error)
            return ""

    def device_page(self):
        dialog=QDialog(self)
        set_text(dialog,tr("设备与运行状态…"),"setWindowTitle")
        form=QFormLayout(dialog)
        form.addRow(QLabel("GradeMirror " + rt.VERSION))
        overview=QLabel(); overview.setWordWrap(True)
        mode=QComboBox()
        options=[(tr("自动"),"auto"),("CPU","cpu")]
        if manifest()["edition"]=="gpu":
            options.append(("NVIDIA","gpu"))
        preference=self.backend.preference()
        if preference not in {value for _,value in options}:
            options.append((tr("现有偏好 · {0}",preference),preference))
        for label,value in options:
            mode.addItem(str(label),value)
        mode.setCurrentIndex(mode.findData(preference))
        mode.setEnabled(not self.busy and self.binding is None)
        mode.currentIndexChanged.connect(lambda:atomic_json(rt.data_root()/"preview-preference.json",dict(backend=mode.currentData())))
        refresh=QPushButton()
        set_text(refresh,tr("重新检测"))

        def show():
            row=self._device_summary
            if self.backend.runtime_problem:
                set_text(overview,self.backend.runtime_problem)
                return
            if row is None:
                set_text(overview,tr("正在检测设备…"))
                return
            memory=row["memory"]
            text=tr("{0} · Windows {1} · {2}\n可用 RAM {3} · 提交余量 {4}\n{5}",row["version"],row["windows_build"],row["cpu"],
                    gib(memory["physical"]["available_bytes"]),gib(memory["commit"]["headroom_bytes"]),detection_text(row["device"]))
            last=self.backend.last_plan
            if last:
                selected=last["selection"]
                text += "\n" + tr("最近一次处理 · {0}", selected["backend"].upper() if selected else last["selection_basis"])
            set_text(overview,text)

        refresh.clicked.connect(lambda:self.detect_devices(lambda:show() if dialog.isVisible() else None))
        form.addRow(mode)
        form.addRow(overview)
        form.addRow(refresh)
        show()
        dialog.resize(560,230)
        dialog.exec()

    def _begin_active(self, kind, key):
        if self._cpu_once:
            self._cpu_once=False
            self.backend.choose_cpu_once(key)
        if self._retry_once:
            self.backend.choose_retry(key,self._retry_once)
            self._retry_once=None
        return super()._begin_active(kind,key)

    def _end_active(self, active):
        self.backend.clear_choice(active.request_key)
        return super()._end_active(active)

    def _error(self, action, exc):
        if getattr(exc, "code", None) == "MIXED_RESULT_PENDING_PUBLICATION":
            set_text(self.status_label,tr("结果待发布。再次点击处理按钮可重试发布。"))
            self._log("light_publication_pending",request_key=exc.request_key)
            return
        if not hasattr(exc,"routing") or self._closing:
            inner=getattr(exc,"inner_resource",None)
            if inner and inner["science"]!="NOT_STARTED":
                set_text(self.status_label,tr("处理可能已开始。请检查工程状态及已保存结果。"))
                self._log("light_resource_restart_blocked",science=inner["science"],cleanup=inner["cleanup"])
                return
            return super()._error(action,exc)
        routing=exc.routing
        epoch,project=self._epoch,self.project_path
        shortages = [dict(backend=row["backend"], **item) for row in routing["candidates"]
                     for item in row["shortages"]]
        inner = getattr(exc, "inner_resource", None)
        if inner:
            shortages.extend(dict(backend=inner["backend"], **item) for item in inner["shortages"])
        self._log("light_resource_choice",reason=routing["reason"],can_choose_cpu=routing["can_choose_cpu"],
                  operation=routing["operation"], shortages=shortages)
        set_text(self.status_label,tr("处理尚未启动 · 请查看路线详情"))

        def offer():
            if self._closing or self._epoch!=epoch or self.project_path!=project or self.busy:
                return
            prompt=QMessageBox(self)
            prompt.setWindowTitle(str(tr("请选择本次处理路线")))
            reason=routing["reason"]
            if reason=="INNER_RESOURCE_CHANGED":
                prompt.setText(str(tr("资源已变化，尚未开始处理。释放资源后可重新检测。")))
            elif reason=="GPU_DETECTION_FAILED":
                prompt.setText(str(tr("GPU 检测失败，可重检或选择合格的 CPU 路线。")))
            elif reason=="RECIPE_BACKEND_MISMATCH":
                prompt.setText(str(tr("此配方需要原处理后端。请在设置中选择该后端或自动。")))
            elif reason=="RESOURCE_DETECTION_UNKNOWN":
                prompt.setText(str(tr("无法确认当前可用资源。请重新检测。")))
            elif reason in ("GPU_RESOURCES","ROUTE_RESOURCES"):
                prompt.setText(str(tr("当前可用资源不足。可释放资源后重新检测。")))
            else:
                prompt.setText(str(tr("当前硬件或随包组件不支持所选路线。")))
            lines=[]
            inner=getattr(exc,"inner_resource",None)
            if inner:
                for row in inner["shortages"]:
                    lines.append(str(tr("{0} · {1} · {2}：可用 {3}，需要 {4}，差额 {5}",inner["backend"].upper(),
                                        tr(row["stage"]),tr(row["resource"]),gib(row["available"]),gib(row["required"]),gib(row["gap"]))))
            for candidate in routing["candidates"]:
                if candidate["backend"]=="mixed":
                    continue
                for row in candidate["shortages"]:
                    lines.append(str(tr("{0} · {1} · {2}：可用 {3}，需要 {4}，差额 {5}",candidate["backend"].upper(),
                                        tr(row["stage"]),tr(row["resource"]),gib(row["available"]),gib(row["required"]),gib(row["gap"]))))
            prompt.setInformativeText("\n".join(lines) if lines else str(detection_text(routing["device"])))
            prompt.setDetailedText("\n".join([routing["device"].get("message","")] +
                [f"{row['backend'].upper()}  {', '.join(row['reasons'])}" for row in routing["candidates"]]))
            retry=None
            if reason in ("GPU_RESOURCES","ROUTE_RESOURCES","INNER_RESOURCE_CHANGED","GPU_DETECTION_FAILED","RESOURCE_DETECTION_UNKNOWN"):
                retry=prompt.addButton(str(tr("重新检测")),QMessageBox.ButtonRole.ActionRole)
            cpu=prompt.addButton(str(tr("本次使用 CPU（较慢）")),QMessageBox.ButtonRole.ActionRole) if routing["can_choose_cpu"] else None
            cancel=prompt.addButton(QMessageBox.StandardButton.Cancel)
            prompt.setDefaultButton(cancel)
            prompt.exec()
            selected=prompt.clickedButton()
            if ((retry is not None and selected is retry) or (cpu is not None and selected is cpu)) and not self._closing and self._epoch==epoch and self.project_path==project and not self.busy:
                self._cpu_once=selected is cpu
                self._retry_once=routing.get("retry",{}).get("path")
                try:
                    (self._load_clicked if routing["operation"]=="replay" else self._process_new_clicked)()
                finally:
                    self._cpu_once=False
                    self._retry_once=None

        QTimer.singleShot(0,offer)

    def _update_preview_eta(self):
        from .eta import Estimate,phase_chain
        if self._closing or not self.preview_inflight or not self.binding or self.busy:
            self._preview_eta=None
            return
        if self._preview_eta is None:
            self._preview_eta=Estimate(phases=phase_chain(None,"preview"))
            self._preview_eta.observe("preview",None,None,None)
            self._preview_eta_since=time.monotonic()-.25
        try:
            row=self.backend.preview_progress(self.store_root,self.job_id,self.attempt_id)
            stamp=row.get("stamp")
            if type(stamp) in (int,float) and stamp>=self._preview_eta_since:
                self._preview_eta.observe(row.get("stage"),row.get("done"),row.get("total"),stamp)
            set_text(self.status_label,self._preview_eta.text())
        except (GMError,OSError,ValueError,KeyError,TypeError):
            pass

    def relink_source(self):
        if self.busy or self.binding or self.project_path is None:
            return
        path=self._file_dialog("open",tr("选择字节一致的原图"))
        if not path:
            return
        from ..project import relink_project
        from ..engine.profiles import runtimes
        self.busy="relink"
        def done(value,error):
            self.busy=None
            if error:
                self._error(tr("重新定位原图…"),error)
            else:
                self._open_project_path(self.project_path)
        self._run("relink",lambda:relink_project(self.project_path,Path(path),available_runtimes=runtimes()),done)

    def environment_page(self):
        dialog=QDialog(self)
        set_text(dialog,tr("计算环境…"),"setWindowTitle")
        form=QFormLayout(dialog)
        result=QLabel();result.setWordWrap(True)
        status=QLabel();status.setWordWrap(True)
        profiles=QComboBox()
        for label,profile in (("CPU · Torch 2.5.1+cpu","cpu"),("NVIDIA · Torch 2.5.1+cu124","cu124"),
                              ("P3 solver · SciPy 1.18.1","solver")):
            profiles.addItem(label,profile)
        select=QPushButton()
        set_text(select,tr("选择 Python 并验证…"))
        mode=QComboBox()
        for label,val in (("Auto","auto"),("CPU","cpu"),("NVIDIA","gpu"),("Hybrid","mixed")):
            mode.addItem(label,val)
        mode.setCurrentIndex(max(0,mode.findData(self.backend.preference())))
        def overview():
            try:
                roles=rt.snapshot()
                lines=[]
                for profile in rt.EXTERNAL:
                    row=roles.get(profile)
                    status="ENGINEERING_READY · "+row["python"] if row else "MISSING"
                    if row and profile=="cu124" and not row["gpu"]["available"]:
                        status+=" · NVIDIA_DEVICE_OR_COMPILED_ARCH_UNQUALIFIED"
                    lines.append(f"{profile}: "+status)
                result.setText("\n".join(lines)+"\n"+(self.backend.runtime_problem or ""))
            except Exception as error:
                result.setText(str(error))
        overview()
        def save_mode(index):
            if not self.busy and not self.binding:
                atomic_json(rt.data_root()/"preview-preference.json",dict(backend=mode.itemData(index)))
        mode.currentIndexChanged.connect(save_mode)
        mode.setEnabled(not self.busy and not self.binding)
        def choose():
            if self.busy or self.binding:
                return
            filename=self._file_dialog("open",tr("选择 Python 并验证…"),file_filter="python.exe (python.exe)")
            if not filename:
                return
            profile=profiles.currentData()
            self.busy="environment"
            select.setEnabled(False)
            mode.setEnabled(False)
            set_text(status,tr("正在验证外部环境…"))
            def finished(row,error):
                self.busy=None
                if not self._closing:
                    select.setEnabled(True);mode.setEnabled(True)
                    set_text(status,str(error) if error else tr("已绑定计算环境。数值资格单列。"))
                    overview()
            self._run("environment",lambda:rt.register(Path(filename),profile),finished)
        select.clicked.connect(choose)
        select.setEnabled(not self.busy and not self.binding and not self.backend.runtime_problem)
        form.addRow(profiles,select)
        form.addRow(mode)
        form.addRow(result)
        form.addRow(status)
        dialog.resize(550,260)
        dialog.exec()


def main():
    from ..preview.contract import architecture
    from ..cpu_replay.contract import need
    need(sys.platform=="win32" and architecture()=="AMD64" and sys.getwindowsversion().build>=17763,
         "LIGHT_WINDOWS_PLATFORM")
    from . import portable
    from PySide6.QtCore import QCoreApplication
    if portable.enabled():
        from .native_policy import qt_root
        qt_plugins = qt_root()
        QCoreApplication.setLibraryPaths([str(qt_plugins)])
    app=QApplication([])
    if portable.enabled():
        QCoreApplication.setLibraryPaths([str(qt_plugins)])
    configure_application(app)
    rt.data_root().mkdir(parents=True,exist_ok=True)
    backend=Backend()
    window=Workbench(backend)
    window.show()
    QTimer.singleShot(0,window.detect_devices)
    return app.exec()
