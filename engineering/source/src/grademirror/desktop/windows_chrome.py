"""Local Windows frame adapter. Native hit tests; business close always stays in Qt."""
import ctypes as c
from ctypes import wintypes as t
import os,sys
from PySide6.QtCore import QPoint,Qt

class WindowsChrome:
    def __init__(self,window,bar):
        self.window,self.bar=window,bar;self.active=False;self.hwnd=None;self.max_pressed=False
        self.status='native-frame-fallback'
    def install(self):
        if sys.platform!='win32' or os.environ.get('GRADEMIRROR_NATIVE_FRAME')=='1' or os.environ.get('QT_QPA_PLATFORM')=='offscreen':
            self._fallback();return
        try:
            self.u=c.WinDLL('user32',use_last_error=True);self.dwm=c.WinDLL('dwmapi',use_last_error=True)
            u=self.u
            u.GetWindowLongPtrW.argtypes=[t.HWND,c.c_int];u.GetWindowLongPtrW.restype=c.c_ssize_t
            u.SetWindowLongPtrW.argtypes=[t.HWND,c.c_int,c.c_ssize_t];u.SetWindowLongPtrW.restype=c.c_ssize_t
            u.SetWindowPos.argtypes=[t.HWND,t.HWND,c.c_int,c.c_int,c.c_int,c.c_int,t.UINT]
            u.GetWindowRect.argtypes=[t.HWND,c.POINTER(t.RECT)]
            u.ScreenToClient.argtypes=[t.HWND,c.POINTER(t.POINT)]
            u.MonitorFromWindow.argtypes=[t.HWND,t.DWORD];u.MonitorFromWindow.restype=t.HMONITOR
            u.GetMonitorInfoW.argtypes=[t.HMONITOR,c.c_void_p]
            u.SetCapture.argtypes=[t.HWND];u.SetCapture.restype=t.HWND
            u.DefWindowProcW.argtypes=[t.HWND,t.UINT,t.WPARAM,t.LPARAM];u.DefWindowProcW.restype=c.c_ssize_t
            u.GetSystemMenu.argtypes=[t.HWND,t.BOOL];u.GetSystemMenu.restype=t.HMENU
            u.TrackPopupMenu.argtypes=[t.HMENU,t.UINT,c.c_int,c.c_int,c.c_int,t.HWND,c.c_void_p];u.TrackPopupMenu.restype=t.UINT
            u.PostMessageW.argtypes=[t.HWND,t.UINT,t.WPARAM,t.LPARAM]
            self.dwm.DwmSetWindowAttribute.argtypes=[t.HWND,t.DWORD,c.c_void_p,t.DWORD]
            self.window.setWindowFlag(Qt.FramelessWindowHint,True)
            self.hwnd=int(self.window.winId())
            style=u.GetWindowLongPtrW(self.hwnd,-16)
            u.SetWindowLongPtrW(self.hwnd,-16,style|0x00C00000|0x00040000|0x00080000|0x00010000|0x00020000)
            self.active=True
            for attr,value in ((20,1),(33,2),(34,0x002D2A29)):
                v=t.DWORD(value);self.dwm.DwmSetWindowAttribute(self.hwnd,attr,c.byref(v),4)
            u.SetWindowPos(self.hwnd,None,0,0,0,0,0x0037)
            self.status='windows-custom-client-native-hit-test'
        except (OSError,AttributeError) as exc:
            self.active=False;self.status='native-frame-fallback: '+str(exc);self._fallback()

    def _fallback(self):
        self.window.setWindowFlag(Qt.FramelessWindowHint,False)
        for b in (self.bar.minimize,self.bar.maximize,self.bar.close_button):b.hide()
        if sys.platform=='win32' and os.environ.get('QT_QPA_PLATFORM')!='offscreen':
            try:
                hwnd=int(self.window.winId());dark=c.c_int(1)
                c.windll.dwmapi.DwmSetWindowAttribute(t.HWND(hwnd),20,c.byref(dark),4)
            except (OSError,AttributeError):pass

    def work_area(self):
        class MONITORINFO(c.Structure):_fields_=[('cbSize',t.DWORD),('rcMonitor',t.RECT),('rcWork',t.RECT),('dwFlags',t.DWORD)]
        info=MONITORINFO();info.cbSize=c.sizeof(info)
        if not self.u.GetMonitorInfoW(self.u.MonitorFromWindow(self.hwnd,2),c.byref(info)):return None
        return info

    def client_point(self,x,y):
        pt=t.POINT(x,y);self.u.ScreenToClient(self.hwnd,c.byref(pt));dpr=self.window.devicePixelRatioF()
        return QPoint(round(pt.x/dpr),round(pt.y/dpr))

    def hit_test(self,x,y):
        rect=t.RECT();self.u.GetWindowRect(self.hwnd,c.byref(rect));point=self.client_point(x,y)
        border=max(5,round(6*self.window.devicePixelRatioF()))
        if not self.window.isMaximized():
            left=x<rect.left+border;right=x>=rect.right-border;top=y<rect.top+border;bottom=y>=rect.bottom-border
            if top and left:return 13
            if top and right:return 14
            if bottom and left:return 16
            if bottom and right:return 17
            if left:return 10
            if right:return 11
            if top:return 12
            if bottom:return 15
        if self.bar.maximize.rect().contains(self.bar.maximize.mapFrom(self.window,point)):return 9
        if self.bar.drag_point(point):return 2
        return 1

    def system_menu(self,x=None,y=None):
        if x is None:
            rect=t.RECT();self.u.GetWindowRect(self.hwnd,c.byref(rect));x,y=rect.left+8,rect.top+36
        menu=self.u.GetSystemMenu(self.hwnd,False)
        command=self.u.TrackPopupMenu(menu,0x0100|0x0002,x,y,0,self.hwnd,None)
        if command:self.u.PostMessageW(self.hwnd,0x0112,command,0)

    def native_event(self,event_type,message):
        if not self.active:return False,0
        msg=t.MSG.from_address(int(message))
        if int(msg.hWnd or 0)!=self.hwnd:return False,0
        kind=msg.message
        if kind==0x0083:  # WM_NCCALCSIZE: one client row, retain Win32 resize styles.
            if self.window.isMaximized():
                info=self.work_area()
                if info:
                    rect=t.RECT.from_address(msg.lParam)
                    rect.left,rect.top,rect.right,rect.bottom=info.rcWork.left,info.rcWork.top,info.rcWork.right,info.rcWork.bottom
            return True,0
        if kind==0x0084:
            x=c.c_short(msg.lParam&0xffff).value;y=c.c_short((msg.lParam>>16)&0xffff).value
            return True,self.hit_test(x,y)
        if kind==0x00A0:  # NC mouse movement: let Windows offer Snap for HTMAXBUTTON.
            hover=msg.wParam==9
            if self.bar.maximize.property('nativeHover')!=hover:
                self.bar.maximize.setProperty('nativeHover',hover);self.bar.maximize.style().unpolish(self.bar.maximize);self.bar.maximize.style().polish(self.bar.maximize)
            return True,int(self.u.DefWindowProcW(self.hwnd,kind,msg.wParam,msg.lParam))
        if kind in (0x0200,0x02A2) and self.bar.maximize.property('nativeHover'):
            self.bar.maximize.setProperty('nativeHover',False)
            self.bar.maximize.style().unpolish(self.bar.maximize);self.bar.maximize.style().polish(self.bar.maximize)
        if kind==0x00A1 and msg.wParam==9:
            self.max_pressed=True;self.bar.maximize.setDown(True);self.u.SetCapture(self.hwnd);return True,0
        if kind in (0x0202,0x00A2) and self.max_pressed:
            self.max_pressed=False;self.u.ReleaseCapture();self.bar.maximize.setDown(False)
            pt=t.POINT();self.u.GetCursorPos(c.byref(pt));p=self.client_point(pt.x,pt.y)
            if self.bar.maximize.rect().contains(self.bar.maximize.mapFrom(self.window,p)):self.bar.toggle_maximized()
            return True,0
        if kind==0x0215 and self.max_pressed:
            self.max_pressed=False;self.bar.maximize.setDown(False)
        if kind==0x0104 and msg.wParam==0x20:
            self.system_menu();return True,0
        if kind==0x00A5 and msg.wParam==2:
            self.system_menu(c.c_short(msg.lParam&0xffff).value,c.c_short((msg.lParam>>16)&0xffff).value);return True,0
        return False,0
