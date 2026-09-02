# quicktoggle.py
# Compact always-on-top system toggle widget
# Buttons: Monitor Off | Internet Off | Mouse Off | Keyboard Off | Audio Off

import sys
import os
import io
import time
import math
import ctypes
import ctypes.wintypes
import subprocess
import threading
import json

# Force stdout/stderr to UTF-8 to prevent charmap encoding errors, or dummy writer if None (pythonw.exe)
class DummyWriter:
    def write(self, *args, **kwargs): pass
    def flush(self, *args, **kwargs): pass

if sys.stdout is None:
    sys.stdout = DummyWriter()
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

if sys.stderr is None:
    sys.stderr = DummyWriter()
else:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from PyQt5.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu, QAction
from PyQt5.QtCore import Qt, QPoint, QPointF, QRectF, QTimer
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QLinearGradient,
    QRadialGradient, QPainterPath, QFont, QIcon, QPixmap
)

# ── Win32 setup ───────────────────────────────────────────────────────────────
user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32  = ctypes.windll.shell32

WM_SYSCOMMAND  = 0x0112
SC_MONITORPOWER = 0xF170
HWND_BROADCAST  = 0xFFFF
WH_KEYBOARD_LL  = 13
WH_MOUSE_LL     = 14
GWL_EXSTYLE     = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

shell32.IsUserAnAdmin.argtypes = []
shell32.IsUserAnAdmin.restype = ctypes.wintypes.BOOL

def is_admin():
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


# ── Hook structs ──────────────────────────────────────────────────────────────
class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('pt', POINT), ('mouseData', ctypes.c_ulong),
                ('flags', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', ctypes.c_ulong), ('scanCode', ctypes.c_ulong),
                ('flags', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int,
                               ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)

user32.GetAncestor.restype  = ctypes.wintypes.HWND
user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
GA_ROOT = 2

user32.WindowFromPoint.restype = ctypes.wintypes.HWND
user32.WindowFromPoint.argtypes = [POINT]

user32.GetDesktopWindow.argtypes = []
user32.GetDesktopWindow.restype = ctypes.wintypes.HWND

user32.PostMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.PostMessageW.restype = ctypes.wintypes.BOOL

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.wintypes.HINSTANCE, ctypes.wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK

user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL

user32.CallNextHookEx.argtypes = [ctypes.wintypes.HHOOK, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.wintypes.LPARAM

kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel32.GetModuleHandleW.restype = ctypes.wintypes.HMODULE

if hasattr(user32, 'GetWindowLongPtrW'):
    user32.GetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    _GetWindowLong = user32.GetWindowLongPtrW
else:
    user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    _GetWindowLong = user32.GetWindowLongW

if hasattr(user32, 'SetWindowLongPtrW'):
    user32.SetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    _SetWindowLong = user32.SetWindowLongPtrW
else:
    user32.SetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    _SetWindowLong = user32.SetWindowLongW

# ── COM/Audio setup ───────────────────────────────────────────────────────────
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8)
    ]
    def __init__(self, guid_str):
        parts = guid_str.strip("{}").split("-")
        self.Data1 = int(parts[0], 16)
        self.Data2 = int(parts[1], 16)
        self.Data3 = int(parts[2], 16)
        self.Data4 = (ctypes.c_ubyte * 8)(
            int(parts[3][:2], 16), int(parts[3][2:], 16),
            int(parts[4][:2], 16), int(parts[4][2:4], 16),
            int(parts[4][4:6], 16), int(parts[4][6:8], 16),
            int(parts[4][8:10], 16), int(parts[4][10:12], 16)
        )

def call_vfunc(obj, index, argtypes, *args):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))
    vtable_array = ctypes.cast(vtable.contents, ctypes.POINTER(ctypes.c_void_p))
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    func = proto(vtable_array[index])
    return func(obj, *args)

# ── Global hook state ─────────────────────────────────────────────────────────
_kb_hook_id    = None
_mouse_hook_id = None
_kb_blocked    = False
_mouse_blocked = False
_app_hwnd      = None   # set once widget is created


@HOOKPROC
def _kb_proc(nCode, wParam, lParam):
    if nCode >= 0 and _kb_blocked:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if kb.vkCode != 0x1B:   # ESC always passes through (safety)
            return 1
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


@HOOKPROC
def _mouse_proc(nCode, wParam, lParam):
    if nCode >= 0 and _mouse_blocked:
        ms  = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        pt  = POINT(ms.pt.x, ms.pt.y)
        hwnd = user32.WindowFromPoint(pt)
        root = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else 0
        if _app_hwnd and root == _app_hwnd:
            pass           # allow clicks on our own window
        else:
            return 1       # block everything else
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def _install_kb_hook():
    global _kb_hook_id
    if not _kb_hook_id:
        _kb_hook_id = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, _kb_proc, kernel32.GetModuleHandleW(None), 0)

def _uninstall_kb_hook():
    global _kb_hook_id
    if _kb_hook_id:
        user32.UnhookWindowsHookEx(_kb_hook_id)
        _kb_hook_id = None

def _install_mouse_hook():
    global _mouse_hook_id
    if not _mouse_hook_id:
        _mouse_hook_id = user32.SetWindowsHookExW(
            WH_MOUSE_LL, _mouse_proc, kernel32.GetModuleHandleW(None), 0)

def _uninstall_mouse_hook():
    global _mouse_hook_id
    if _mouse_hook_id:
        user32.UnhookWindowsHookEx(_mouse_hook_id)
        _mouse_hook_id = None


# ── Feature actions ───────────────────────────────────────────────────────────
_disabled_adapters: list = []

def action_monitor(_active):
    """Turn monitor off via PostMessage to the desktop window."""
    time.sleep(0.4)   # let button-release clear before blackout
    hwnd = user32.GetDesktopWindow()
    user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def action_internet(off: bool):
    global _disabled_adapters
    if off:
        ps = ("Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} "
              "| Select-Object -ExpandProperty Name | ConvertTo-Json")
        r = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-NonInteractive', '-Command', ps],
            capture_output=True, text=True, timeout=12)
        try:
            data = json.loads(r.stdout.strip())
            _disabled_adapters = [data] if isinstance(data, str) else list(data)
        except Exception:
            _disabled_adapters = []

        if _disabled_adapters:
            names = ', '.join(f'"{n}"' for n in _disabled_adapters)
            subprocess.Popen(['powershell', '-ExecutionPolicy', 'Bypass',
                '-NonInteractive', '-Command',
                f'Disable-NetAdapter -Name @({names}) -Confirm:$false'])
            return True
        return False
    else:
        if _disabled_adapters:
            names = ', '.join(f'"{n}"' for n in _disabled_adapters)
            subprocess.Popen(['powershell', '-ExecutionPolicy', 'Bypass',
                '-NonInteractive', '-Command',
                f'Enable-NetAdapter -Name @({names}) -Confirm:$false'])
            _disabled_adapters.clear()


def action_mouse(off: bool):
    global _mouse_blocked
    _mouse_blocked = off
    if off:
        _install_mouse_hook()
    else:
        _mouse_blocked = False
        _uninstall_mouse_hook()


def action_keyboard(off: bool):
    global _kb_blocked
    _kb_blocked = off
    if off:
        _install_kb_hook()
    else:
        _kb_blocked = False
        _uninstall_kb_hook()


def action_audio(off: bool):
    try:
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        
        CLSID_MMDeviceEnumerator = GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")
        IID_IMMDeviceEnumerator = GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")
        IID_IAudioEndpointVolume = GUID("5CDF2C82-841E-4546-9722-0CF74078229A")
        
        enum = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDeviceEnumerator),
            None,
            1,
            ctypes.byref(IID_IMMDeviceEnumerator),
            ctypes.byref(enum)
        )
        if hr == 0:
            device = ctypes.c_void_p()
            hr = call_vfunc(enum, 4, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)], 0, 1, ctypes.byref(device))
            if hr == 0:
                volume = ctypes.c_void_p()
                hr = call_vfunc(device, 3, [ctypes.POINTER(GUID), ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], 
                                ctypes.byref(IID_IAudioEndpointVolume), 23, None, ctypes.byref(volume))
                if hr == 0:
                    call_vfunc(volume, 14, [ctypes.c_bool, ctypes.c_void_p], off, None)
        ole32.CoUninitialize()
    except Exception as e:
        print(f"[QuickToggle] Audio action error: {e}", flush=True)


# ── Button config ─────────────────────────────────────────────────────────────
BUTTONS = [
    {'id': 'monitor',  'label': 'MONITOR OFF',  'icon': '🖥',
     'action': action_monitor,  'trigger': True},
    {'id': 'internet', 'label': 'INTERNET OFF', 'icon': '📡',
     'action': action_internet, 'trigger': False},
    {'id': 'mouse',    'label': 'MOUSE OFF',    'icon': '🖱',
     'action': action_mouse,    'trigger': False},
    {'id': 'keyboard', 'label': 'KEYBOARD OFF', 'icon': '⌨',
     'action': action_keyboard, 'trigger': False},
    {'id': 'audio',    'label': 'AUDIO OFF',    'icon': '🔊',
     'action': action_audio,    'trigger': False},
]

# ── Main widget ───────────────────────────────────────────────────────────────
class QuickToggleWidget(QWidget):
    MARGIN   = 8
    BTN_W    = 132
    BTN_H    = 40
    GAP      = 5
    HEADER_H = 0   # no header text — pure buttons

    def __init__(self):
        super().__init__()
        n = len(BUTTONS)
        W = self.BTN_W + 2 * self.MARGIN
        H = (self.HEADER_H + self.MARGIN
             + n * (self.BTN_H + self.GAP) - self.GAP
             + self.MARGIN)
        self.setFixedSize(W, H)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
            Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("QuickToggle")

        # State
        self._states:     dict[str, bool] = {b['id']: False for b in BUTTONS}
        self._hover_idx  = -1
        self._press_idx  = -1
        self._drag_pos   = QPoint()
        self._press_global = QPoint()   # where the press started (global coords)
        self._dragging   = False
        self._did_move   = False        # True only if window actually moved

        # Pulse animation
        self._pt = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(28)

        self.setMouseTracking(True)

        # Apply WS_EX_NOACTIVATE so we don't steal focus
        hwnd = int(self.winId())
        style = _GetWindowLong(hwnd, GWL_EXSTYLE)
        _SetWindowLong(hwnd, GWL_EXSTYLE,
                       style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        global _app_hwnd
        _app_hwnd = hwnd

        self._setup_tray()

    # ── Animation ─────────────────────────────────────────────────────────────
    def _tick(self):
        self._pt = (self._pt + 0.07) % (2 * math.pi)
        self.update()

    # ── Geometry helpers ──────────────────────────────────────────────────────
    def _btn_rect(self, idx) -> QRectF:
        x = float(self.MARGIN)
        y = float(self.HEADER_H + self.MARGIN + idx * (self.BTN_H + self.GAP))
        return QRectF(x, y, self.BTN_W, self.BTN_H)

    def _idx_at(self, pos) -> int:
        for i in range(len(BUTTONS)):
            if self._btn_rect(i).contains(pos.x(), pos.y()):
                return i
        return -1

    # ── Paint ─────────────────────────────────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        pulse = 0.5 + 0.5 * math.sin(self._pt)

        # Background
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(0, 0, W, H), 13, 13)
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0.0, QColor(9, 12, 26, 235))
        grad.setColorAt(1.0, QColor(5, 8, 17, 245))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawPath(bg)

        # Outer border glow
        border_c = QColor(0, 140, 220, int(55 + 25 * pulse))
        p.setPen(QPen(border_c, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(bg)

        # Buttons
        for i, btn in enumerate(BUTTONS):
            r   = self._btn_rect(i)
            off = self._states[btn['id']]
            hov = (i == self._hover_idx)
            prs = (i == self._press_idx)
            trg = btn['trigger']

            # Button body
            bp = QPainterPath()
            bp.addRoundedRect(r, 9, 9)

            if prs:
                body_c = QColor(28, 55, 100, 210)
            elif hov:
                body_c = QColor(18, 38, 70, 190)
            else:
                body_c = QColor(11, 16, 34, 165)
            p.setBrush(QBrush(body_c))
            p.setPen(Qt.NoPen)
            p.drawPath(bp)

            # Button border
            if off and not trg:
                brd_c = QColor(255, 55, 85, 160)
            elif hov or prs:
                brd_c = QColor(0, 175, 255, 130)
            else:
                brd_c = QColor(30, 55, 95, 75)
            p.setPen(QPen(brd_c, 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawPath(bp)

            # Indicator dot
            dx = r.x() + 13.0
            dy = r.y() + r.height() / 2.0
            dr = 4.5

            if trg:
                dot_c = QColor(60, 130, 255, int(170 + 60 * pulse))
            elif off:
                dot_c = QColor(255, 55, 85, int(195 + 45 * pulse))
            else:
                dot_c = QColor(0, 215, 120, int(175 + 50 * pulse))

            # Glow halo
            gl = QRadialGradient(dx, dy, dr * 3.5)
            gc = QColor(dot_c)
            gc.setAlpha(50)
            gl.setColorAt(0.0, gc)
            gl.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(QBrush(gl))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(dx, dy), dr * 3.5, dr * 3.5)

            # Solid dot
            p.setBrush(QBrush(dot_c))
            p.drawEllipse(QPointF(dx, dy), dr, dr)

            # Label
            if trg:
                lbl_c = QColor(140, 185, 255, 200)
            elif off:
                lbl_c = QColor(255, 110, 135, 230)
            else:
                lbl_c = QColor(175, 210, 255, 205)

            p.setFont(QFont("Segoe UI", 7, QFont.Bold))
            p.setPen(lbl_c)
            lbl_r = QRectF(r.x() + 24, r.y(), r.width() - 28, r.height())
            p.drawText(lbl_r, Qt.AlignVCenter | Qt.AlignLeft, btn['label'])

        p.end()

    # ── Mouse events ──────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos     = event.globalPos() - self.frameGeometry().topLeft()
            self._press_global = event.globalPos()
            self._dragging     = False
            self._did_move     = False
            self._press_idx    = self._idx_at(event.pos())
            self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        idx = self._idx_at(event.pos())
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()

        if event.buttons() & Qt.LeftButton:
            # Use distance from original press, not from window corner,
            # so jitter near press point doesn't falsely flag as drag.
            dist = (event.globalPos() - self._press_global).manhattanLength()
            if dist > 8:
                self._dragging = True
            if self._dragging:
                self.move(event.globalPos() - self._drag_pos)
                self._did_move = True

        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Allow toggle even after small jitter — only block if window was dragged
            if not self._dragging and self._press_idx >= 0:
                self._toggle(self._press_idx)
            self._press_idx = -1
            self._dragging  = False
            self._did_move  = False
            self.update()
        event.accept()

    def leaveEvent(self, _event):
        self._hover_idx = -1
        self.update()

    def _toggle(self, idx):
        btn = BUTTONS[idx]
        bid = btn['id']
        if bid in ('mouse', 'keyboard'):
            # Run hook functions synchronously on the main thread (required by Win32 hooks)
            if btn['trigger']:
                btn['action'](True)
            else:
                new = not self._states[bid]
                self._states[bid] = new
                btn['action'](new)
        else:
            # Run other actions in background thread to avoid UI freeze
            if btn['trigger']:
                threading.Thread(target=lambda: btn['action'](True), daemon=True).start()
            else:
                new = not self._states[bid]
                self._states[bid] = new
                threading.Thread(target=lambda: btn['action'](new), daemon=True).start()
        self.update()

    # ── Tray ──────────────────────────────────────────────────────────────────
    def _setup_tray(self):
        pix = QPixmap(16, 16)
        pix.fill(Qt.transparent)
        pp = QPainter(pix)
        pp.setRenderHint(QPainter.Antialiasing)
        pp.setBrush(QBrush(QColor(0, 160, 255)))
        pp.setPen(Qt.NoPen)
        pp.drawEllipse(2, 2, 12, 12)
        pp.end()

        self._tray = QSystemTrayIcon(QIcon(pix), self)
        self._tray.setToolTip("QuickToggle")
        menu = QMenu()
        q = QAction("Quit QuickToggle", self)
        q.triggered.connect(self._quit)
        menu.addAction(q)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda r: (self.hide() if self.isVisible() else self.show())
            if r == QSystemTrayIcon.Trigger else None)
        self._tray.show()

    def _quit(self):
        action_mouse(False)
        action_keyboard(False)
        if self._states.get('internet'):
            action_internet(False)
        if self._states.get('audio'):
            action_audio(False)
        QApplication.quit()

    def closeEvent(self, _e):
        self._quit()


# ── Entry ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps,    True)
    app.setQuitOnLastWindowClosed(False)

    w = QuickToggleWidget()
    scr = QApplication.primaryScreen().geometry()
    w.move(scr.width() - w.width() - 18, scr.height() - w.height() - 55)
    w.show()
    sys.exit(app.exec_())
