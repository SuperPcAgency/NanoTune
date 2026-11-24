import os
import sys
import re
import json
import time
import math
import zlib
import shutil
import hashlib
import random
import ctypes
import colorsys
import importlib.util
import threading
import traceback
import subprocess
import unicodedata
from collections import Counter
from io import BytesIO
from pathlib import Path


# ---------------------------------------------------------------------------
# Pre-Launch Dependency Check (Windows 7/8/8.1 & Windows 10/11 Aware)
# ---------------------------------------------------------------------------
def is_legacy_windows() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().major < 10
    except Exception:
        return False


IS_LEGACY_WIN = is_legacy_windows()


def check_and_prompt_dependencies():
    if getattr(sys, "frozen", False) or "--export-icon" in sys.argv:
        return

    if IS_LEGACY_WIN:
        vital = [("PySide6", "PySide6<6.5"), ("mutagen", "mutagen"), ("PIL", "Pillow<10.3")]
        optional_pkgs = [("numpy", "numpy<1.25"), ("av", "av<11.0")]
    else:
        vital = [("PySide6", "PySide6"), ("mutagen", "mutagen"), ("PIL", "Pillow")]
        optional_pkgs = [
            ("numpy", "numpy"),
            ("av", "av"),
            ("winrt.runtime", "winrt-runtime"),
            ("winrt.windows.foundation", "winrt-Windows.Foundation"),
            ("winrt.windows.media", "winrt-Windows.Media"),
            ("winrt.windows.media.interop", "winrt-Windows.Media.Interop"),
            ("winrt.windows.media.playback", "winrt-Windows.Media.Playback"),
            ("winrt.windows.storage", "winrt-Windows.Storage"),
            ("winrt.windows.storage.streams", "winrt-Windows.Storage.Streams"),
        ]

    missing_vital = [pip for mod, pip in vital if importlib.util.find_spec(mod) is None]
    missing_opt = []
    for mod, pip in optional_pkgs:
        if importlib.util.find_spec(mod) is None and pip not in missing_opt:
            missing_opt.append(pip)

    if missing_vital or missing_opt:
        all_missing = missing_vital + missing_opt
        print("[NanoTune] Missing packages:", ", ".join(all_missing))
        try:
            ans = input(f"Download and install {', '.join(all_missing)} now? [Y/n]: ").strip().lower()
        except Exception:
            ans = "y"
        if ans in ("", "y", "yes"):
            subprocess.check_call([sys.executable, "-m", "pip", "install", *all_missing])
        elif missing_vital:
            sys.exit(1)


check_and_prompt_dependencies()

from PIL import Image, ImageFilter, ImageEnhance, ImageDraw
import mutagen
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import USLT
from mutagen.mp4 import MP4

try:
    import numpy as np
    import av
    HAS_FX_DSP = True
except Exception:
    np = None
    av = None
    HAS_FX_DSP = False

HAS_WINRT_SMTC = False
winrt_smtc_for_window = None
WinRtMediaPlayer = None
WinRtUri = None
StorageFile = None

if not IS_LEGACY_WIN:
    try:
        from winrt.windows.media import (
            MediaPlaybackStatus,
            MediaPlaybackType,
            SystemMediaTransportControlsButton,
        )
        from winrt.windows.storage.streams import (
            DataWriter,
            InMemoryRandomAccessStream,
            RandomAccessStreamReference,
        )
        try:
            from winrt.windows.foundation import Uri as WinRtUri
        except Exception:
            WinRtUri = None
        try:
            from winrt.windows.storage import StorageFile
        except Exception:
            StorageFile = None
        try:
            from winrt.windows.media.interop import get_for_window as winrt_smtc_for_window
        except Exception:
            winrt_smtc_for_window = None
        try:
            from winrt.windows.media.playback import MediaPlayer as WinRtMediaPlayer
        except Exception:
            WinRtMediaPlayer = None
        if winrt_smtc_for_window is not None or WinRtMediaPlayer is not None:
            HAS_WINRT_SMTC = True
    except Exception:
        HAS_WINRT_SMTC = False


def get_config_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    cfg_dir = Path(appdata) / "NanoTune"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir


def get_covers_cache_dir() -> Path:
    d = get_config_dir() / "covers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_imported_lyrics_dir() -> Path:
    d = get_config_dir() / "imported_lyrics"
    d.mkdir(parents=True, exist_ok=True)
    return d


def register_pc_appdata_registry(cfg_dir: Path, folders: list[str]):
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\NanoTune") as key:
            winreg.SetValueEx(key, "DataPath", 0, winreg.REG_SZ, str(cfg_dir))
            winreg.SetValueEx(key, "MusicFolders", 0, winreg.REG_SZ, ";".join(folders))
    except Exception:
        pass


def _app_launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    py_exe = Path(sys.executable)
    pyw = py_exe.with_name("pythonw.exe")
    runner = str(pyw if pyw.exists() else py_exe)
    script = str(Path(__file__).resolve())
    return f'"{runner}" "{script}" "%1"'


def register_file_associations():
    if sys.platform != "win32":
        return
    try:
        import winreg

        prog_id = "NanoTune.Audio"
        cmd = _app_launch_command()
        if getattr(sys, "frozen", False):
            icon_val = f'"{sys.executable}",0'
        else:
            ico_p = resource_path("assets/nanotune.ico")
            if not ico_p.exists():
                ico_p = get_config_dir() / "nanotune.ico"
                if not ico_p.exists():
                    try:
                        export_hd_star_ico(str(ico_p))
                    except Exception:
                        pass
            icon_val = f'"{ico_p}",0' if ico_p.exists() else f'"{sys.executable}",0'

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "NanoTune Audio File")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, icon_val)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)

        cap_path = r"Software\NanoTune\Capabilities"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cap_path) as k:
            winreg.SetValueEx(k, "ApplicationName", 0, winreg.REG_SZ, "NanoTune")
            winreg.SetValueEx(
                k, "ApplicationDescription", 0, winreg.REG_SZ, "Lightweight Native Windows Music & Lyrics Player"
            )

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{cap_path}\FileAssociations") as fa_key:
            for ext in AUDIO_EXTENSIONS:
                winreg.SetValueEx(fa_key, ext, 0, winreg.REG_SZ, prog_id)
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}\OpenWithProgids") as ow_key:
                    winreg.SetValueEx(ow_key, prog_id, 0, winreg.REG_NONE, b"")

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications") as reg_apps:
            winreg.SetValueEx(reg_apps, "NanoTune", 0, winreg.REG_SZ, cap_path)

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass


def open_windows_default_apps():
    register_file_associations()
    if sys.platform != "win32":
        return
    try:
        if IS_LEGACY_WIN:
            subprocess.Popen(["control.exe", "/name", "Microsoft.DefaultPrograms", "/page", "pageDefaultProgram"])
        else:
            try:
                os.startfile("ms-settings:defaultapps?registeredAppUser=NanoTune")
            except Exception:
                os.startfile("ms-settings:defaultapps")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Hardware DirectX 10.1/10.0 & OpenGL 2.1/2.0 Capability Probes
# ---------------------------------------------------------------------------
def detect_dx_hardware() -> tuple[str, str]:
    if sys.platform != "win32":
        return "", "Non-Windows Platform"
    try:
        d3d11 = ctypes.windll.d3d11
        levels = (ctypes.c_uint * 2)(0xA100, 0xA000)
        device = ctypes.c_void_p()
        actual_level = ctypes.c_uint(0)
        hr = d3d11.D3D11CreateDevice(
            None, 1, None, 0, levels, 2, 7,
            ctypes.byref(device), ctypes.byref(actual_level), None,
        )
        if hr == 0 and device.value:
            vtable = ctypes.cast(device, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            release_fn = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
            release_fn(device)
            if actual_level.value >= 0xA100:
                return "10.1", "DirectX 10.1 Hardware Detected (FL 10_1)"
            if actual_level.value >= 0xA000:
                return "10.0", "DirectX 10.0 Hardware Detected (FL 10_0)"
    except Exception:
        pass
    return "", "DirectX 10.0+ Not Detected"


def detect_opengl_hardware() -> tuple[tuple[int, int], str]:
    if sys.platform != "win32":
        return (2, 1), "OpenGL 2.1"
    hwnd = None
    hdc = None
    hglrc = None
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        opengl32 = ctypes.windll.opengl32

        class PIXELFORMATDESCRIPTOR(ctypes.Structure):
            _fields_ = [
                ("nSize", ctypes.c_ushort), ("nVersion", ctypes.c_ushort), ("dwFlags", ctypes.c_uint),
                ("iPixelType", ctypes.c_ubyte), ("cColorBits", ctypes.c_ubyte), ("cRedBits", ctypes.c_ubyte),
                ("cRedShift", ctypes.c_ubyte), ("cGreenBits", ctypes.c_ubyte), ("cGreenShift", ctypes.c_ubyte),
                ("cBlueBits", ctypes.c_ubyte), ("cBlueShift", ctypes.c_ubyte), ("cAlphaBits", ctypes.c_ubyte),
                ("cAlphaShift", ctypes.c_ubyte), ("cAccumBits", ctypes.c_ubyte), ("cAccumRedBits", ctypes.c_ubyte),
                ("cAccumGreenBits", ctypes.c_ubyte), ("cAccumBlueBits", ctypes.c_ubyte), ("cAccumAlphaBits", ctypes.c_ubyte),
                ("cDepthBits", ctypes.c_ubyte), ("cStencilBits", ctypes.c_ubyte), ("cAuxBuffers", ctypes.c_ubyte),
                ("iLayerType", ctypes.c_ubyte), ("bReserved", ctypes.c_ubyte), ("dwLayerMask", ctypes.c_uint),
                ("dwVisibleMask", ctypes.c_uint), ("dwDamageMask", ctypes.c_uint),
            ]

        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.GetDC.restype = ctypes.c_void_p
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        gdi32.ChoosePixelFormat.argtypes = [ctypes.c_void_p, ctypes.POINTER(PIXELFORMATDESCRIPTOR)]
        gdi32.SetPixelFormat.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(PIXELFORMATDESCRIPTOR)]
        opengl32.wglCreateContext.argtypes = [ctypes.c_void_p]
        opengl32.wglCreateContext.restype = ctypes.c_void_p
        opengl32.wglMakeCurrent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        opengl32.wglDeleteContext.argtypes = [ctypes.c_void_p]
        opengl32.glGetString.argtypes = [ctypes.c_uint]
        opengl32.glGetString.restype = ctypes.c_char_p

        hwnd = user32.CreateWindowExW(0, "STATIC", "NanoGLProbe", 0, 0, 0, 16, 16, None, None, None, None)
        if hwnd:
            hdc = user32.GetDC(hwnd)
            pfd = PIXELFORMATDESCRIPTOR()
            pfd.nSize = ctypes.sizeof(PIXELFORMATDESCRIPTOR)
            pfd.nVersion = 1
            pfd.dwFlags = 0x25  # PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER
            pfd.cColorBits = 24
            pf = gdi32.ChoosePixelFormat(hdc, ctypes.byref(pfd))
            if pf and gdi32.SetPixelFormat(hdc, pf, ctypes.byref(pfd)):
                hglrc = opengl32.wglCreateContext(hdc)
                if hglrc and opengl32.wglMakeCurrent(hdc, hglrc):
                    raw_ver = opengl32.glGetString(0x1F02)
                    if raw_ver:
                        ver_str = raw_ver.decode("ascii", errors="ignore").strip()
                        m = re.match(r"(\d+)\.(\d+)", ver_str)
                        if m:
                            maj, min_v = int(m.group(1)), int(m.group(2))
                            return (maj, min_v), f"OpenGL {maj}.{min_v} Detected ({ver_str})"
    except Exception:
        pass
    finally:
        try:
            if hglrc:
                ctypes.windll.opengl32.wglMakeCurrent(None, None)
                ctypes.windll.opengl32.wglDeleteContext(hglrc)
            if hdc and hwnd:
                ctypes.windll.user32.ReleaseDC(hwnd, hdc)
            if hwnd:
                ctypes.windll.user32.DestroyWindow(hwnd)
        except Exception:
            pass
    return (0, 0), "OpenGL 2.0+ Not Detected"


DX_LEVEL, DX_DETECT_LABEL = detect_dx_hardware()
GL_VER_TUPLE, GL_DETECT_LABEL = detect_opengl_hardware()
GPU_INCOMPATIBLE_NEED_PROMPT = False


def read_startup_graphics_backend() -> str:
    global GPU_INCOMPATIBLE_NEED_PROMPT
    cfg_file = get_config_dir() / "config.json"
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            val = data.get("graphics_backend")
            if val in ("software", "dx10_1", "opengl21"):
                return val
        except Exception:
            pass
    if DX_LEVEL in ("10.1", "10.0"):
        return "dx10_1"
    if GL_VER_TUPLE >= (2, 0):
        return "opengl21"
    GPU_INCOMPATIBLE_NEED_PROMPT = True
    return "software"


SELECTED_BACKEND = read_startup_graphics_backend()

if "--export-icon" not in sys.argv:
    if SELECTED_BACKEND == "software":
        os.environ["QT_QUICK_BACKEND"] = "software"
        os.environ.pop("QT_OPENGL", None)
        os.environ.pop("QSG_RHI_BACKEND", None)
    elif SELECTED_BACKEND == "dx10_1":
        os.environ["QSG_RHI_BACKEND"] = "d3d11"
        os.environ.pop("QT_QUICK_BACKEND", None)
        os.environ.pop("QT_OPENGL", None)
    elif SELECTED_BACKEND == "opengl21":
        os.environ["QT_OPENGL"] = "desktop"
        os.environ["QSG_RHI_BACKEND"] = "opengl"
        os.environ.pop("QT_QUICK_BACKEND", None)

from PySide6.QtCore import (
    Qt, QUrl, QTimer, QSize, Signal, QRect, QRectF, QPointF,
    QBuffer, QCoreApplication, QEventLoop, QIODevice, QObject, QEvent,
    QVariantAnimation, QEasingCurve,
)
from PySide6.QtGui import (
    QAction, QColor, QCursor, QFont, QFontDatabase, QFontMetrics, QIcon, QImage,
    QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF, QShortcut, QSurfaceFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget,
    QStyle, QStyledItemDelegate, QStyleOptionSlider, QSystemTrayIcon,
    QTextEdit, QToolButton, QVBoxLayout, QWidget,
)
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioOutput, QAudioSink, QMediaDevices, QMediaPlayer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# ---------------------------------------------------------------------------
# App Metadata & Built-In Windows Fonts
# ---------------------------------------------------------------------------
APP_ID = "nanotune.player.1.1"
IPC_SERVER_NAME = "NanoTune_SingleInstance_IPC"
AUDIO_EXTENSIONS = {".flac", ".mp3", ".wav", ".m4a", ".ogg", ".opus", ".aac"}
LYRIC_EXTENSIONS = {".lrc", ".elrc"}
IMPORTED_LYRICS_MAP: dict[str, str] = {}

STAR_DEFAULT_YELLOW = "#FFD83D"
STAR_DEFAULT_YELLOW_2 = "#FFE885"

FONT_PRESETS = [
    ("Georgia Editorial (Default)", ["Georgia"]),
    ("Segoe UI (Clean Native)", ["Segoe UI"]),
    ("Segoe UI Semibold", ["Segoe UI Semibold", "Segoe UI"]),
    ("Bahnschrift (Modern Geometric)", ["Bahnschrift"]),
    ("Calibri (Soft Sans)", ["Calibri"]),
    ("Cambria (Classic Serif)", ["Cambria"]),
    ("Candara (Humanist Sans)", ["Candara"]),
    ("Century Gothic (Wide Geometric)", ["Century Gothic", "Bahnschrift"]),
    ("Corbel (Clean Modern)", ["Corbel"]),
    ("Franklin Gothic Medium", ["Franklin Gothic Medium", "Arial"]),
    ("Lucida Sans Unicode", ["Lucida Sans Unicode"]),
    ("Tahoma (Crisp UI)", ["Tahoma"]),
    ("Trebuchet MS", ["Trebuchet MS"]),
    ("Verdana (Wide Legible)", ["Verdana"]),
    ("Consolas (Monospace Tech)", ["Consolas"]),
]


def resolve_font_family(candidates: list[str]) -> str:
    available = set(QFontDatabase.families())
    for fam in candidates:
        if fam in available:
            return fam
    return "Segoe UI"


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = re.sub(r"^\d{1,2}\s*[-._)]\s*", "", text)
    text = re.sub(r"^\d{1,2}\s+(?=[a-z])", "", text)
    text = re.sub(r"[\(\[][^)\]]*(feat|ft\.|explicit|clean|prod)[^)\]]*[\)\]]", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def format_ms(ms: int) -> str:
    if ms <= 0:
        return "0:00"
    total_sec = int(ms // 1000)
    mins, secs = divmod(total_sec, 60)
    return f"{mins}:{secs:02d}"


def format_bytes(size: int) -> str:
    if size <= 0:
        return "—"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def color_luminance(hex_c: str) -> float:
    c = QColor(hex_c)
    return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0


def clean_artist_str(raw: str) -> str:
    return re.sub(r"\s*;\s*", ", ", raw.strip()) if raw else "Unknown Artist"


def primary_artist_token(raw: str) -> str:
    cleaned = clean_artist_str(raw)
    first = re.split(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b)\s*", cleaned, maxsplit=1, flags=re.I)[0].strip()
    return first or cleaned


def album_cache_key(album_name: str, folder_path: str = "") -> str:
    norm_alb = album_name.strip().lower()
    return hashlib.md5(norm_alb.encode("utf-8", errors="ignore")).hexdigest()


def track_cover_cache_key(path_str: str, mtime: float) -> str:
    return hashlib.md5(f"{path_str}|{mtime:.3f}".encode("utf-8", errors="ignore")).hexdigest()


def enable_windows_dark_titlebar(hwnd: int):
    if sys.platform != "win32" or IS_LEGACY_WIN:
        return
    try:
        dwmapi = ctypes.windll.dwmapi
        value = ctypes.c_int(1)
        for attr in (20, 19):
            res = dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), ctypes.c_uint(attr),
                ctypes.byref(value), ctypes.sizeof(value),
            )
            if res == 0:
                break
    except Exception:
        pass


def describe_graphics_backend(mode: str) -> str:
    if mode == "dx10_1":
        if DX_LEVEL == "10.0":
            return f"DirectX 10.0 Fallback Active  •  {DX_DETECT_LABEL}"
        return f"DirectX 10.1 Active  •  {DX_DETECT_LABEL}"
    if mode == "opengl21":
        if GL_VER_TUPLE == (2, 0):
            return f"OpenGL 2.0 Fallback Active  •  {GL_DETECT_LABEL}"
        return f"OpenGL 2.1 Active  •  {GL_DETECT_LABEL}"
    return f"Software Rasterizer Active  •  {DX_DETECT_LABEL}  •  {GL_DETECT_LABEL}"


# ---------------------------------------------------------------------------
# Mario SNES Star Icon + Monochrome Vector Icon Factory
# ---------------------------------------------------------------------------
def make_mario_star_icon(color_hex: str = STAR_DEFAULT_YELLOW, size: int = 64) -> QIcon:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    s = float(size)
    cx, cy = s * 0.5, s * 0.52
    outer_r = s * 0.43
    inner_r = s * 0.24

    pts = []
    for i in range(10):
        angle = -math.pi / 2 + i * (math.pi / 5)
        rad = outer_r if i % 2 == 0 else inner_r
        pts.append(QPointF(cx + rad * math.cos(angle), cy + rad * math.sin(angle)))

    path = QPainterPath()
    n = len(pts)
    for i in range(n):
        prev_pt = pts[(i - 1) % n]
        cur_pt = pts[i]
        next_pt = pts[(i + 1) % n]
        p_in = QPointF(cur_pt.x() * 0.72 + prev_pt.x() * 0.28, cur_pt.y() * 0.72 + prev_pt.y() * 0.28)
        p_out = QPointF(cur_pt.x() * 0.72 + next_pt.x() * 0.28, cur_pt.y() * 0.72 + next_pt.y() * 0.28)
        if i == 0:
            path.moveTo(p_in)
        else:
            path.lineTo(p_in)
        path.quadTo(cur_pt, p_out)
    path.closeSubpath()

    base = QColor(color_hex)
    darker = base.darker(150)
    grad = QLinearGradient(0, s * 0.1, 0, s * 0.9)
    grad.setColorAt(0.0, base.lighter(115))
    grad.setColorAt(1.0, base.darker(112))

    p.setBrush(grad)
    p.setPen(QPen(darker, max(1.5, s * 0.055), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#111315"))
    eye_w = s * 0.075
    eye_h = s * 0.19
    eye_y = cy - s * 0.09
    p.drawRoundedRect(QRectF(cx - s * 0.115, eye_y, eye_w, eye_h), eye_w * 0.5, eye_w * 0.5)
    p.drawRoundedRect(QRectF(cx + s * 0.040, eye_y, eye_w, eye_h), eye_w * 0.5, eye_w * 0.5)

    p.setBrush(QColor(255, 255, 255, 225))
    sh_w = eye_w * 0.45
    p.drawEllipse(QRectF(cx - s * 0.105, eye_y + eye_h * 0.16, sh_w, sh_w))
    p.drawEllipse(QRectF(cx + s * 0.050, eye_y + eye_h * 0.16, sh_w, sh_w))

    p.end()
    return QIcon(px)


def export_hd_star_ico(out_path: str):
    pm = make_mario_star_icon(STAR_DEFAULT_YELLOW, 500).pixmap(500, 500)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    img = Image.open(BytesIO(bytes(buf.data()))).convert("RGBA")
    img.save(
        out_path,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )


def _pt_polar(cx: float, cy: float, r: float, angle: float) -> QPointF:
    return QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle))


def make_vector_icon(name: str, color: str = "#FFFFFF", size: int = 24) -> QIcon:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    c = QColor(color)
    pen = QPen(c, max(1.6, size * 0.085), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = float(size)

    if name == "play":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(s * 0.28, s * 0.18), QPointF(s * 0.80, s * 0.50), QPointF(s * 0.28, s * 0.82)]))
    elif name == "pause":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(s * 0.26, s * 0.22, s * 0.17, s * 0.56), 1.5, 1.5)
        p.drawRoundedRect(QRectF(s * 0.57, s * 0.22, s * 0.17, s * 0.56), 1.5, 1.5)
    elif name == "prev":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(s * 0.20, s * 0.24, s * 0.10, s * 0.52), 1.0, 1.0)
        p.drawPolygon(QPolygonF([QPointF(s * 0.78, s * 0.23), QPointF(s * 0.34, s * 0.50), QPointF(s * 0.78, s * 0.77)]))
    elif name == "next":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(s * 0.70, s * 0.24, s * 0.10, s * 0.52), 1.0, 1.0)
        p.drawPolygon(QPolygonF([QPointF(s * 0.22, s * 0.23), QPointF(s * 0.66, s * 0.50), QPointF(s * 0.22, s * 0.77)]))
    elif name == "shuffle":
        path1 = QPainterPath(QPointF(s * 0.16, s * 0.30))
        path1.lineTo(s * 0.38, s * 0.30)
        path1.lineTo(s * 0.64, s * 0.70)
        path1.lineTo(s * 0.82, s * 0.70)
        p.drawPath(path1)
        path2 = QPainterPath(QPointF(s * 0.16, s * 0.70))
        path2.lineTo(s * 0.38, s * 0.70)
        path2.lineTo(s * 0.64, s * 0.30)
        path2.lineTo(s * 0.82, s * 0.30)
        p.drawPath(path2)
        p.drawPolyline(QPolygonF([QPointF(s * 0.73, s * 0.21), QPointF(s * 0.84, s * 0.30), QPointF(s * 0.73, s * 0.39)]))
        p.drawPolyline(QPolygonF([QPointF(s * 0.73, s * 0.61), QPointF(s * 0.84, s * 0.70), QPointF(s * 0.73, s * 0.79)]))
    elif name in ("repeat", "repeat_one"):
        path = QPainterPath(QPointF(s * 0.22, s * 0.45))
        path.lineTo(s * 0.22, s * 0.30)
        path.lineTo(s * 0.76, s * 0.30)
        p.drawPath(path)
        p.drawPolyline(QPolygonF([QPointF(s * 0.68, s * 0.21), QPointF(s * 0.78, s * 0.30), QPointF(s * 0.68, s * 0.39)]))
        path_b = QPainterPath(QPointF(s * 0.78, s * 0.55))
        path_b.lineTo(s * 0.78, s * 0.70)
        path_b.lineTo(s * 0.24, s * 0.70)
        p.drawPath(path_b)
        p.drawPolyline(QPolygonF([QPointF(s * 0.32, s * 0.61), QPointF(s * 0.22, s * 0.70), QPointF(s * 0.32, s * 0.79)]))
        if name == "repeat_one":
            p.setFont(QFont("Segoe UI", max(7, int(s * 0.28)), QFont.Weight.Bold))
            p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "1")
    elif name == "volume":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([
            QPointF(s * 0.16, s * 0.40), QPointF(s * 0.32, s * 0.40), QPointF(s * 0.50, s * 0.24),
            QPointF(s * 0.50, s * 0.76), QPointF(s * 0.32, s * 0.60), QPointF(s * 0.16, s * 0.60),
        ]))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(s * 0.46, s * 0.33, s * 0.22, s * 0.34), -55 * 16, 110 * 16)
        p.drawArc(QRectF(s * 0.50, s * 0.22, s * 0.34, s * 0.56), -55 * 16, 110 * 16)
    elif name == "mute":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([
            QPointF(s * 0.16, s * 0.40), QPointF(s * 0.32, s * 0.40), QPointF(s * 0.50, s * 0.24),
            QPointF(s * 0.50, s * 0.76), QPointF(s * 0.32, s * 0.60), QPointF(s * 0.16, s * 0.60),
        ]))
        p.setPen(pen)
        p.drawLine(QPointF(s * 0.62, s * 0.38), QPointF(s * 0.82, s * 0.62))
        p.drawLine(QPointF(s * 0.82, s * 0.38), QPointF(s * 0.62, s * 0.62))
    elif name == "lyrics":
        p.drawRoundedRect(QRectF(s * 0.37, s * 0.16, s * 0.26, s * 0.42), s * 0.13, s * 0.13)
        p.drawArc(QRectF(s * 0.26, s * 0.30, s * 0.48, s * 0.40), -180 * 16, 180 * 16)
        p.drawLine(QPointF(s * 0.50, s * 0.70), QPointF(s * 0.50, s * 0.84))
        p.drawLine(QPointF(s * 0.35, s * 0.84), QPointF(s * 0.65, s * 0.84))
    elif name == "fx":
        for x_ratio, y_dot in ((0.26, 0.35), (0.50, 0.66), (0.74, 0.42)):
            p.drawLine(QPointF(s * x_ratio, s * 0.20), QPointF(s * x_ratio, s * 0.80))
            p.setBrush(c)
            p.drawEllipse(QPointF(s * x_ratio, s * y_dot), s * 0.08, s * 0.08)
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "edit":
        p.drawLine(QPointF(s * 0.24, s * 0.76), QPointF(s * 0.72, s * 0.24))
        p.drawLine(QPointF(s * 0.20, s * 0.80), QPointF(s * 0.34, s * 0.77))
        p.drawLine(QPointF(s * 0.65, s * 0.20), QPointF(s * 0.79, s * 0.34))
    elif name == "refresh":
        p.drawArc(QRectF(s * 0.20, s * 0.20, s * 0.60, s * 0.60), 35 * 16, 285 * 16)
        p.drawPolyline(QPolygonF([QPointF(s * 0.68, s * 0.18), QPointF(s * 0.80, s * 0.32), QPointF(s * 0.64, s * 0.36)]))
    elif name == "clock":
        p.drawEllipse(QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64))
        p.drawLine(QPointF(s * 0.50, s * 0.32), QPointF(s * 0.50, s * 0.52))
        p.drawLine(QPointF(s * 0.50, s * 0.52), QPointF(s * 0.65, s * 0.60))
    elif name == "save":
        p.drawRoundedRect(QRectF(s * 0.20, s * 0.20, s * 0.60, s * 0.60), 2.5, 2.5)
        p.drawRect(QRectF(s * 0.34, s * 0.20, s * 0.32, s * 0.20))
        p.drawRect(QRectF(s * 0.32, s * 0.54, s * 0.36, s * 0.26))
    elif name == "music":
        p.drawLine(QPointF(s * 0.38, s * 0.72), QPointF(s * 0.38, s * 0.26))
        p.drawLine(QPointF(s * 0.38, s * 0.26), QPointF(s * 0.76, s * 0.20))
        p.drawLine(QPointF(s * 0.76, s * 0.20), QPointF(s * 0.76, s * 0.64))
        p.setBrush(c)
        p.drawEllipse(QPointF(s * 0.30, s * 0.74), s * 0.10, s * 0.08)
        p.drawEllipse(QPointF(s * 0.68, s * 0.66), s * 0.10, s * 0.08)
    elif name == "albums":
        for rx, ry in ((0.18, 0.18), (0.54, 0.18), (0.18, 0.54), (0.54, 0.54)):
            p.drawRoundedRect(QRectF(s * rx, s * ry, s * 0.28, s * 0.28), 2.5, 2.5)
    elif name == "settings":
        cx = cy = s * 0.50
        r_root = s * 0.305
        r_tip = s * 0.425
        r_hole = s * 0.172
        da_base = 0.215
        da_tip = 0.118
        fillet_a = 0.042
        dr = s * 0.028

        gear = QPainterPath()
        gear.setFillRule(Qt.FillRule.OddEvenFill)

        for i in range(8):
            a = -math.pi / 2.0 + i * (math.pi / 4.0)
            p_v_in = _pt_polar(cx, cy, r_root, a - da_base - fillet_a)
            p_b1_ctrl = _pt_polar(cx, cy, r_root, a - da_base)
            p_b1_out = _pt_polar(cx, cy, r_root + dr, a - da_base + 0.022)
            p_t1_in = _pt_polar(cx, cy, r_tip - dr * 0.8, a - da_tip - 0.022)
            p_t1_ctrl = _pt_polar(cx, cy, r_tip, a - da_tip)
            p_t1_top = _pt_polar(cx, cy, r_tip, a - da_tip * 0.48)
            p_t2_top = _pt_polar(cx, cy, r_tip, a + da_tip * 0.48)
            p_t2_ctrl = _pt_polar(cx, cy, r_tip, a + da_tip)
            p_t2_out = _pt_polar(cx, cy, r_tip - dr * 0.8, a + da_tip + 0.022)
            p_b2_in = _pt_polar(cx, cy, r_root + dr, a + da_base - 0.022)
            p_b2_ctrl = _pt_polar(cx, cy, r_root, a + da_base)
            p_v_out = _pt_polar(cx, cy, r_root, a + da_base + fillet_a)

            if i == 0:
                gear.moveTo(p_v_in)
            else:
                a_prev = -math.pi / 2.0 + (i - 1) * (math.pi / 4.0)
                a_mid = 0.5 * ((a_prev + da_base + fillet_a) + (a - da_base - fillet_a))
                p_arc_mid = _pt_polar(cx, cy, r_root, a_mid)
                gear.quadTo(p_arc_mid, p_v_in)

            gear.quadTo(p_b1_ctrl, p_b1_out)
            gear.lineTo(p_t1_in)
            gear.quadTo(p_t1_ctrl, p_t1_top)
            gear.lineTo(p_t2_top)
            gear.quadTo(p_t2_ctrl, p_t2_out)
            gear.lineTo(p_b2_in)
            gear.quadTo(p_b2_ctrl, p_v_out)

        a_last = -math.pi / 2.0 + 7 * (math.pi / 4.0)
        a_first = -math.pi / 2.0 + 2 * math.pi
        a_mid_close = 0.5 * ((a_last + da_base + fillet_a) + (a_first - da_base - fillet_a))
        gear.quadTo(_pt_polar(cx, cy, r_root, a_mid_close), _pt_polar(cx, cy, r_root, a_first - da_base - fillet_a))
        gear.closeSubpath()
        gear.addEllipse(QPointF(cx, cy), r_hole, r_hole)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPath(gear)
    elif name == "info":
        ring_pen = QPen(c, max(1.5, s * 0.080), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(ring_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.34, s * 0.34)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(s * 0.50, s * 0.315), s * 0.046, s * 0.046)

        stem_pen = QPen(c, max(1.6, s * 0.084), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(stem_pen)
        p.drawLine(QPointF(s * 0.50, s * 0.485), QPointF(s * 0.50, s * 0.685))
    elif name == "sidebar":
        p.drawRoundedRect(QRectF(s * 0.16, s * 0.20, s * 0.68, s * 0.60), 3.0, 3.0)
        p.drawLine(QPointF(s * 0.38, s * 0.20), QPointF(s * 0.38, s * 0.80))
    elif name == "fullscreen":
        p.drawPolyline(QPolygonF([QPointF(s * 0.18, s * 0.38), QPointF(s * 0.18, s * 0.18), QPointF(s * 0.38, s * 0.18)]))
        p.drawPolyline(QPolygonF([QPointF(s * 0.62, s * 0.18), QPointF(s * 0.82, s * 0.18), QPointF(s * 0.82, s * 0.38)]))
        p.drawPolyline(QPolygonF([QPointF(s * 0.82, s * 0.62), QPointF(s * 0.82, s * 0.82), QPointF(s * 0.62, s * 0.82)]))
        p.drawPolyline(QPolygonF([QPointF(s * 0.38, s * 0.82), QPointF(s * 0.18, s * 0.82), QPointF(s * 0.18, s * 0.62)]))
    elif name == "close":
        p.drawLine(QPointF(s * 0.26, s * 0.26), QPointF(s * 0.74, s * 0.74))
        p.drawLine(QPointF(s * 0.74, s * 0.26), QPointF(s * 0.26, s * 0.74))

    p.end()
    return QIcon(px)


# ---------------------------------------------------------------------------
# Color Extraction (Monochrome B&W Puffed White-Greyish + Vivid Covers)
# ---------------------------------------------------------------------------
def extract_theme_colors(img: Image.Image) -> tuple[str, str]:
    try:
        small = img.convert("RGB").resize((48, 48), Image.Resampling.LANCZOS)
        pixels = list(small.getdata())
        total_px = len(pixels)
        buckets: dict[int, list[tuple[float, float, float]]] = {}
        chromatic_count = 0

        for r, g, b in pixels:
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if s > 0.22 and v > 0.16:
                chromatic_count += 1
                if v < 0.96:
                    b_idx = int(h * 24) % 24
                    buckets.setdefault(b_idx, []).append((h, s, v))

        if chromatic_count < total_px * 0.08 or not buckets:
            return "#E4E8EC", "#B4BCC4"

        top_bucket = max(buckets.values(), key=len)
        avg_h = sum(x[0] for x in top_bucket) / len(top_bucket)
        avg_s = min(0.88, max(0.50, sum(x[1] for x in top_bucket) / len(top_bucket)))
        avg_v = min(0.94, max(0.74, sum(x[2] for x in top_bucket) / len(top_bucket)))

        r1, g1, b1 = colorsys.hsv_to_rgb(avg_h, avg_s, avg_v)
        r2, g2, b2 = colorsys.hsv_to_rgb((avg_h + 0.08) % 1.0, max(0.45, avg_s * 0.8), min(0.96, avg_v * 1.05))
        return f"#{int(r1*255):02X}{int(g1*255):02X}{int(b1*255):02X}", f"#{int(r2*255):02X}{int(g2*255):02X}{int(b2*255):02X}"
    except Exception:
        return STAR_DEFAULT_YELLOW, STAR_DEFAULT_YELLOW_2


# ---------------------------------------------------------------------------
# Metadata, Cover Art, Perceptual Similarity, Multi-Cover Collage & Preloader
# ---------------------------------------------------------------------------
def extract_metadata(file_path: Path) -> dict:
    title = file_path.stem
    artist = "Unknown Artist"
    album_artist = ""
    album = ""
    year_str = ""
    track_no = 0
    duration_ms = 0
    sample_rate = 0
    bits_per_sample = 0
    bitrate = 0
    channels = 2
    ext = file_path.suffix.lstrip(".").upper()
    fmt_label = ext
    codec_detail = ext

    try:
        st = file_path.stat()
        file_size = st.st_size
        mtime = st.st_mtime
    except Exception:
        file_size = 0
        mtime = 0.0

    try:
        audio = mutagen.File(str(file_path), easy=True)
        if audio is not None:
            if audio.tags:
                title = (audio.tags.get("title") or [title])[0]
                artist = (audio.tags.get("artist") or [artist])[0]
                album_artist = (audio.tags.get("albumartist") or [""])[0]
                album = (audio.tags.get("album") or [""])[0]
                raw_tn = (audio.tags.get("tracknumber") or ["0"])[0]
                m = re.match(r"(\d+)", str(raw_tn))
                if m:
                    track_no = int(m.group(1))
                for dk in ("date", "year", "originaldate"):
                    raw_d = (audio.tags.get(dk) or [""])[0]
                    if raw_d:
                        ym = re.search(r"\b(19\d{2}|20\d{2})\b", str(raw_d))
                        if ym:
                            year_str = ym.group(1)
                            break
            if hasattr(audio, "info") and audio.info:
                info = audio.info
                duration_ms = int((getattr(info, "length", 0) or 0) * 1000)
                sample_rate = int(getattr(info, "sample_rate", 0) or 0)
                bits_per_sample = int(getattr(info, "bits_per_sample", 0) or 0)
                bitrate = int(getattr(info, "bitrate", 0) or 0)
                channels = int(getattr(info, "channels", 2) or 2)

                if isinstance(audio, FLAC) or ext == "FLAC":
                    codec_detail = "FLAC (Lossless)"
                    bps = bits_per_sample or 16
                    sr = sample_rate or 44100
                    fmt_label = f"FLAC {bps}-bit/{sr/1000:.1f}kHz"
                elif ext == "MP3":
                    codec_detail = "MP3 (MPEG Audio)"
                    fmt_label = f"MP3 {bitrate // 1000}kbps" if bitrate else "MP3"
                elif ext == "M4A":
                    codec_detail = "M4A (AAC / ALAC)"
                    fmt_label = f"M4A {bitrate // 1000}kbps" if bitrate else "M4A"
                elif ext == "WAV":
                    codec_detail = "WAV (Uncompressed PCM)"
                    fmt_label = f"WAV {bits_per_sample or 16}-bit"
    except Exception:
        pass

    return {
        "path": str(file_path),
        "filename": file_path.name,
        "title": str(title),
        "artist": clean_artist_str(str(artist)),
        "album_artist": clean_artist_str(str(album_artist)) if album_artist else "",
        "album": str(album) or file_path.parent.name,
        "year": year_str,
        "track_no": track_no,
        "duration_ms": duration_ms,
        "sample_rate": sample_rate,
        "bits_per_sample": bits_per_sample,
        "bitrate": bitrate,
        "channels": channels,
        "file_size": file_size,
        "mtime": mtime,
        "codec_detail": codec_detail,
        "format": fmt_label,
    }


def resolve_album_artist(album_tracks: list[dict]) -> str:
    explicit = [t["album_artist"] for t in album_tracks if t.get("album_artist")]
    if explicit:
        return Counter(explicit).most_common(1)[0][0]
    primaries = [primary_artist_token(t.get("artist", "")) for t in album_tracks if t.get("artist")]
    if primaries:
        return Counter(primaries).most_common(1)[0][0]
    return "Unknown Artist"


def resolve_album_year(album_tracks: list[dict]) -> str:
    years = [t.get("year", "") for t in album_tracks if t.get("year")]
    if years:
        return Counter(years).most_common(1)[0][0]
    return ""


def extract_embedded_cover_from_file(file_path: Path) -> Image.Image | None:
    try:
        ext = file_path.suffix.lower()
        if ext == ".flac":
            fl = FLAC(str(file_path))
            if fl.pictures:
                return Image.open(BytesIO(fl.pictures[0].data)).convert("RGB")
        elif ext == ".mp3":
            mp = MP3(str(file_path))
            if mp.tags:
                for tag in mp.tags.values():
                    if getattr(tag, "FrameID", "") == "APIC":
                        return Image.open(BytesIO(tag.data)).convert("RGB")
        elif ext == ".m4a":
            m4 = MP4(str(file_path))
            if m4.tags and "covr" in m4.tags:
                return Image.open(BytesIO(bytes(m4.tags["covr"][0]))).convert("RGB")
        else:
            generic = mutagen.File(str(file_path))
            if generic and hasattr(generic, "pictures") and generic.pictures:
                return Image.open(BytesIO(generic.pictures[0].data)).convert("RGB")
    except Exception:
        pass

    for name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg"):
        p = file_path.parent / name
        if p.exists():
            try:
                return Image.open(p).convert("RGB")
            except Exception:
                pass
    return None


def compute_cover_visual_vec(img: Image.Image) -> bytes:
    w, h = img.size
    mx = max(1, int(w * 0.06))
    my = max(1, int(h * 0.06))
    cropped = img.crop((mx, my, max(mx + 4, w - mx), max(my + 4, h - my)))
    return cropped.convert("RGB").resize((16, 16), Image.Resampling.BILINEAR).tobytes()


def covers_look_similar(vec_a: bytes, vec_b: bytes) -> bool:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return False
    if vec_a == vec_b:
        return True
    n = len(vec_a)
    abs_sum = 0
    sum_a = 0
    sum_b = 0
    for a, b in zip(vec_a, vec_b):
        abs_sum += abs(a - b)
        sum_a += a
        sum_b += b
    mae = abs_sum / float(n)
    if mae < 20.0:
        return True
    if mae > 38.0:
        return False
    mean_a = sum_a / float(n)
    mean_b = sum_b / float(n)
    num = 0.0
    da2 = 0.0
    db2 = 0.0
    for a, b in zip(vec_a, vec_b):
        da = a - mean_a
        db = b - mean_b
        num += da * db
        da2 += da * da
        db2 += db * db
    denom = math.sqrt(da2 * db2)
    zncc = (num / denom) if denom > 1e-6 else 1.0
    return zncc > 0.88 and mae < 34.0


def build_fallback_cover(accent_hex: str = STAR_DEFAULT_YELLOW) -> Image.Image:
    w, h = 600, 600
    img = Image.new("RGB", (w, h), (14, 17, 19))
    draw = ImageDraw.Draw(img)
    c = QColor(accent_hex)
    for y in range(h):
        t = y / h
        r = int(12 + c.red() * 0.16 * (1 - t))
        g = int(14 + c.green() * 0.16 * (1 - t * 0.5))
        b = int(16 + c.blue() * 0.16 * (1 - t * 0.3))
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    draw.ellipse((140, 140, 460, 460), outline=(c.red(), c.green(), c.blue()), width=6)
    return img


def pil_to_qimage(img: Image.Image) -> QImage:
    rgb = img.convert("RGBA")
    data = rgb.tobytes("raw", "RGBA")
    return QImage(data, rgb.width, rgb.height, rgb.width * 4, QImage.Format.Format_RGBA8888).copy()


def _draw_cover_cropped(p: QPainter, target: QRectF, img: QImage):
    if img.isNull() or target.width() <= 0 or target.height() <= 0:
        return
    src_w, src_h = float(img.width()), float(img.height())
    scale = max(target.width() / src_w, target.height() / src_h)
    draw_w, draw_h = src_w * scale, src_h * scale
    dx = target.x() + (target.width() - draw_w) * 0.5
    dy = target.y() + (target.height() - draw_h) * 0.5
    p.drawImage(QRectF(dx, dy, draw_w, draw_h), img)


def compose_album_cover_qimage(qimages: list[QImage], size: int = 220) -> QImage:
    if not qimages:
        return QImage()
    imgs = qimages[:4]
    n = len(imgs)
    if n == 1:
        return imgs[0].scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)

    out = QImage(size, size, QImage.Format.Format_RGBA8888)
    out.fill(QColor(15, 19, 21))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    s = float(size)
    div_pen = QPen(QColor(12, 16, 18, 220), 1.5)

    if n == 2:
        tri1 = QPainterPath()
        tri1.moveTo(0.0, 0.0)
        tri1.lineTo(s, 0.0)
        tri1.lineTo(0.0, s)
        tri1.closeSubpath()
        p.save()
        p.setClipPath(tri1)
        _draw_cover_cropped(p, QRectF(0.0, 0.0, s, s), imgs[0])
        p.restore()

        tri2 = QPainterPath()
        tri2.moveTo(s, 0.0)
        tri2.lineTo(s, s)
        tri2.lineTo(0.0, s)
        tri2.closeSubpath()
        p.save()
        p.setClipPath(tri2)
        _draw_cover_cropped(p, QRectF(0.0, 0.0, s, s), imgs[1])
        p.restore()

        p.setPen(div_pen)
        p.drawLine(QPointF(s, 0.0), QPointF(0.0, s))

    elif n == 3:
        w1 = s / 3.0
        w2 = (2.0 * s) / 3.0
        rects = [
            QRectF(0.0, 0.0, w1, s),
            QRectF(w1, 0.0, w2 - w1, s),
            QRectF(w2, 0.0, s - w2, s),
        ]
        for idx, r in enumerate(rects):
            p.save()
            p.setClipRect(r)
            _draw_cover_cropped(p, r, imgs[idx])
            p.restore()
        p.setPen(div_pen)
        p.drawLine(QPointF(w1, 0.0), QPointF(w1, s))
        p.drawLine(QPointF(w2, 0.0), QPointF(w2, s))

    else:
        half = s * 0.5
        cells = [
            QRectF(0.0, 0.0, half, half),
            QRectF(half, 0.0, s - half, half),
            QRectF(0.0, half, half, s - half),
            QRectF(half, half, s - half, s - half),
        ]
        for idx, r in enumerate(cells):
            p.save()
            p.setClipRect(r)
            _draw_cover_cropped(p, r, imgs[idx])
            p.restore()
        p.setPen(div_pen)
        p.drawLine(QPointF(half, 0.0), QPointF(half, s))
        p.drawLine(QPointF(0.0, half), QPointF(s, half))

    p.end()
    return out


class CoverPreloader(threading.Thread):
    def __init__(self, jobs: list[tuple[str, str, str]], cache_dir: Path, stop_event: threading.Event, callback):
        super().__init__(daemon=True)
        self.jobs = jobs
        self.cache_dir = cache_dir
        self.stop_event = stop_event
        self.callback = callback

    def run(self):
        for track_key, alb_key, track_path in self.jobs:
            if self.stop_event.is_set():
                return
            try:
                cached_jpg = self.cache_dir / f"{track_key}.jpg"
                cached_vec = self.cache_dir / f"{track_key}.vec"
                img = None
                cov_vec = b""
                if cached_jpg.exists():
                    try:
                        img = Image.open(cached_jpg).convert("RGB")
                        if cached_vec.exists():
                            raw_vec = cached_vec.read_bytes()
                            if len(raw_vec) == 768:
                                cov_vec = raw_vec
                    except Exception:
                        img = None
                        cov_vec = b""

                if img is None:
                    raw = extract_embedded_cover_from_file(Path(track_path))
                    if raw is not None:
                        img = raw.resize((220, 220), Image.Resampling.LANCZOS)
                        try:
                            img.save(cached_jpg, format="JPEG", quality=88)
                        except Exception:
                            pass

                if img is not None:
                    if not cov_vec:
                        cov_vec = compute_cover_visual_vec(img)
                        try:
                            cached_vec.write_bytes(cov_vec)
                        except Exception:
                            pass
                    q220 = pil_to_qimage(img)
                    q64 = pil_to_qimage(img.resize((64, 64), Image.Resampling.LANCZOS))
                    self.callback(track_key, alb_key, cov_vec, q220, q64)
            except Exception:
                pass
            time.sleep(0.004)


# ---------------------------------------------------------------------------
# Lyrics Engine: .ELRC (Word-by-Word), .LRC & Embedded Tags
# ---------------------------------------------------------------------------
def find_lrc_path(audio_path: Path) -> Path | None:
    for key in (str(audio_path), str(audio_path.resolve())):
        mapped = IMPORTED_LYRICS_MAP.get(key)
        if mapped:
            mp = Path(mapped)
            if mp.exists():
                return mp

    for ext in (".elrc", ".lrc"):
        direct = audio_path.with_suffix(ext)
        if direct.exists():
            return direct
        sub = audio_path.parent / "lyrics" / f"{audio_path.stem}{ext}"
        if sub.exists():
            return sub
    return None


def read_lyrics_for_track(audio_path_str: str) -> tuple[str, str]:
    audio_path = Path(audio_path_str)

    lrc_file = find_lrc_path(audio_path)
    if lrc_file is not None:
        tag_lbl = ".ELRC" if lrc_file.suffix.lower() == ".elrc" else ".LRC"
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = lrc_file.read_text(encoding=enc).strip()
                if text:
                    return text, f"{tag_lbl} ({lrc_file.name})"
            except Exception:
                continue

    ext = audio_path.suffix.lower()
    try:
        if ext == ".flac":
            fl = FLAC(str(audio_path))
            if fl.tags:
                for key in ("SYNCEDLYRICS", "LYRICS", "UNSYNCEDLYRICS", "LYRICS:ENG"):
                    vals = fl.tags.get(key)
                    if vals and str(vals[0]).strip():
                        return str(vals[0]).strip(), f"FLAC Embedded ({key})"
                for k, vals in fl.tags.items():
                    if "lyric" in k.lower() and vals and str(vals[0]).strip():
                        return str(vals[0]).strip(), f"FLAC Embedded ({k})"
        elif ext == ".mp3":
            mp = MP3(str(audio_path))
            if mp.tags:
                for tag in mp.tags.values():
                    if isinstance(tag, USLT) and str(tag.text).strip():
                        return str(tag.text).strip(), "MP3 Embedded (USLT)"
        elif ext == ".m4a":
            m4 = MP4(str(audio_path))
            if m4.tags and "\xa9lyr" in m4.tags:
                txt = str(m4.tags["\xa9lyr"][0]).strip()
                if txt:
                    return txt, "M4A Embedded (©lyr)"
        else:
            generic = mutagen.File(str(audio_path))
            if generic and generic.tags:
                for k, vals in generic.tags.items():
                    if "lyric" in str(k).lower() and vals:
                        txt = str(vals[0] if isinstance(vals, list) else vals).strip()
                        if txt:
                            return txt, f"Embedded ({k})"
    except Exception:
        pass

    return "", "No Lyrics Found"


LRC_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
ELRC_WORD_RE = re.compile(r"<(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?>")
VOICE_PREFIX_RE = re.compile(r"^\s*v\d+\s*:\s*", re.I)


def _ts_to_ms(mins: str, secs: str, frac: str | None) -> int:
    frac_s = frac or "0"
    ms_part = int(frac_s.ljust(3, "0")[:3])
    return (int(mins) * 60 + int(secs)) * 1000 + ms_part


def _parse_elrc_words(body: str, fallback_start_ms: int) -> tuple[list[dict], str, int]:
    """Parse inline <mm:ss.xxx> tags into word segments with clean start_ms/end_ms."""
    body_clean = VOICE_PREFIX_RE.sub("", body).strip()
    matches = list(ELRC_WORD_RE.finditer(body_clean))
    if not matches:
        return [], body_clean, fallback_start_ms

    words: list[dict] = []
    first_ts = _ts_to_ms(matches[0].group(1), matches[0].group(2), matches[0].group(3))
    line_start = fallback_start_ms if fallback_start_ms >= 0 else first_ts

    if matches[0].start() > 0:
        lead_txt = VOICE_PREFIX_RE.sub("", body_clean[: matches[0].start()])
        if lead_txt.strip():
            words.append({"text": lead_txt, "start_ms": line_start, "end_ms": first_ts})

    for idx, m in enumerate(matches):
        t_ms = _ts_to_ms(m.group(1), m.group(2), m.group(3))
        seg_start = m.end()
        seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body_clean)
        seg_txt = body_clean[seg_start:seg_end]

        if not seg_txt.strip():
            if words and words[-1]["end_ms"] is None:
                words[-1]["end_ms"] = t_ms
            continue

        if words and words[-1]["end_ms"] is None:
            words[-1]["end_ms"] = t_ms
        words.append({"text": seg_txt, "start_ms": t_ms, "end_ms": None})

    plain_joined = "".join(w["text"] for w in words).strip() or "♪"
    return words, plain_joined, (line_start if fallback_start_ms >= 0 else first_ts)


def parse_lrc_text(raw_text: str) -> tuple[bool, list[dict]]:
    """
    Parse .ELRC / .LRC / plain lyrics.
    Returns (is_synced, [{"time_ms": int, "text": str, "words": list[dict]}, ...]).
    """
    if not raw_text.strip():
        return False, []

    synced_entries: list[dict] = []
    plain_entries: list[dict] = []

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if re.match(r"^\[(ar|ti|al|by|offset|length|re|ve):.*\]$", line, re.I):
            continue

        line_matches = list(LRC_LINE_RE.finditer(raw_line))
        has_word_tags = bool(ELRC_WORD_RE.search(raw_line))

        if line_matches:
            lyric_body = LRC_LINE_RE.sub("", raw_line).strip()
            for m in line_matches:
                t_ms = _ts_to_ms(m.group(1), m.group(2), m.group(3))
                words, clean_txt, _ = _parse_elrc_words(lyric_body, t_ms)
                synced_entries.append({"time_ms": t_ms, "text": clean_txt or "♪", "words": words})
        elif has_word_tags:
            words, clean_txt, inferred_ms = _parse_elrc_words(raw_line, -1)
            if words:
                synced_entries.append({"time_ms": inferred_ms, "text": clean_txt or "♪", "words": words})
        else:
            plain_clean = VOICE_PREFIX_RE.sub("", raw_line)
            plain_entries.append({"time_ms": -1, "text": plain_clean, "words": []})

    if synced_entries:
        synced_entries.sort(key=lambda x: x["time_ms"])
        for i, entry in enumerate(synced_entries):
            words = entry["words"]
            if not words:
                continue
            next_line_ms = synced_entries[i + 1]["time_ms"] if i + 1 < len(synced_entries) else (words[-1]["start_ms"] + 1200)
            for j, w in enumerate(words):
                if w["end_ms"] is None:
                    w["end_ms"] = words[j + 1]["start_ms"] if j + 1 < len(words) else max(w["start_ms"] + 250, next_line_ms)
                w["end_ms"] = max(w["start_ms"] + 40, w["end_ms"])
        return True, synced_entries

    return False, plain_entries


# ---------------------------------------------------------------------------
# Crackle-Free Real-Time Audio FX Engine (Threaded Pre-Buffer + Lo-Fi + Bass + Reverb)
# ---------------------------------------------------------------------------
class FXStreamDevice(QIODevice):
    BLOCK = 1024
    N_FFT = 2048
    SAMPLE_RATE = 44100
    TARGET_BUF_BYTES = 32768

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cond = threading.Condition()
        self.pcm = None
        self.cursor_frame: float = 0.0
        self._cursor_played_frames: float = 0.0
        self.ended: bool = False

        self.speed: float = 1.0
        self.pitch_semitones: float = 0.0
        self.link_pitch: bool = True
        self.bass_boost: float = 0.0
        self.reverb_mix: float = 0.0
        self.lofi_enabled: bool = False

        self._s_speed: float = 1.0
        self._s_pitch: float = 0.0
        self._s_bass: float = 0.0
        self._s_reverb: float = 0.0
        self._s_lofi: float = 0.0
        self._prev_step: float = 1.0
        self._prev_link_pitch: bool = True
        self._grain_tail = np.zeros((self.BLOCK // 2, 2), dtype=np.float32) if HAS_FX_DSP else None
        self._prev_dry_tail = np.zeros((64, 2), dtype=np.float32) if HAS_FX_DSP else None

        self._seek_fade_len: int = 512
        self._seek_fade_pos: int = 512

        self._pcm_bytes_buf = bytearray()
        self._producer_stop: bool = True
        self._producer_thread: threading.Thread | None = None

        self._dry_ola_buf = np.zeros((self.BLOCK, 2), dtype=np.float32) if HAS_FX_DSP else None
        self._wet_ola_buf = np.zeros((self.BLOCK, 2), dtype=np.float32) if HAS_FX_DSP else None
        self._ir_parts = None
        self._fdl = None
        self._fdl_idx = 0
        self._lp_fft = None

        self._lofi_coeffs = (1.0, 0.0, 0.0, 0.0, 0.0)
        self._lofi_z1 = np.zeros(2, dtype=np.float32) if HAS_FX_DSP else None
        self._lofi_z2 = np.zeros(2, dtype=np.float32) if HAS_FX_DSP else None

        if HAS_FX_DSP:
            self._init_dsp_tables()

    def isSequential(self) -> bool:
        return True

    def _init_dsp_tables(self):
        taps = 255
        n = np.arange(taps, dtype=np.float32)
        fc = 155.0 / self.SAMPLE_RATE
        h_lp = 2.0 * fc * np.sinc(2.0 * fc * (n - (taps - 1) / 2.0)) * np.hamming(taps).astype(np.float32)
        h_lp /= np.sum(h_lp)
        lp_pad = np.zeros((self.N_FFT, 2), dtype=np.float32)
        lp_pad[:taps, 0] = h_lp
        lp_pad[:taps, 1] = h_lp
        self._lp_fft = np.fft.rfft(lp_pad, axis=0)

        rng = np.random.default_rng(42)
        n_parts = 36
        ir_len = n_parts * self.BLOCK
        t = np.arange(ir_len, dtype=np.float32) / self.SAMPLE_RATE
        env = np.exp(-t * 3.8).astype(np.float32)[:, None]
        fade_in = np.minimum(1.0, t / 0.025).astype(np.float32)[:, None]
        noise = rng.standard_normal((ir_len, 2)).astype(np.float32)
        for _ in range(3):
            noise[1:] = 0.55 * noise[1:] + 0.45 * noise[:-1]
        ir = noise * env * fade_in * 0.068

        self._ir_parts = []
        for k in range(n_parts):
            seg = ir[k * self.BLOCK : (k + 1) * self.BLOCK]
            padded = np.zeros((self.N_FFT, 2), dtype=np.float32)
            padded[: self.BLOCK] = seg
            self._ir_parts.append(np.fft.rfft(padded, axis=0))

        self._fdl = np.zeros((n_parts, self.N_FFT // 2 + 1, 2), dtype=np.complex64)
        self._fdl_idx = 0

        w0 = 2.0 * math.pi * 1400.0 / float(self.SAMPLE_RATE)
        cos_w0 = math.cos(w0)
        alpha = math.sin(w0) / (2.0 * 0.7071)
        a0 = 1.0 + alpha
        b0 = ((1.0 - cos_w0) * 0.5) / a0
        b1 = (1.0 - cos_w0) / a0
        b2 = b0
        a1 = (-2.0 * cos_w0) / a0
        a2 = (1.0 - alpha) / a0
        self._lofi_coeffs = (b0, b1, b2, a1, a2)

    def open(self, mode):
        ok = super().open(mode)
        if ok and HAS_FX_DSP:
            with self._cond:
                self._producer_stop = False
            self._producer_thread = threading.Thread(target=self._producer_loop, daemon=True)
            self._producer_thread.start()
        return ok

    def close(self):
        with self._cond:
            self._producer_stop = True
            self._cond.notify_all()
        if self._producer_thread is not None and self._producer_thread.is_alive():
            self._producer_thread.join(timeout=0.35)
        self._producer_thread = None
        super().close()

    def _producer_loop(self):
        while True:
            with self._cond:
                if self._producer_stop:
                    return
                if self.pcm is None or self.ended or len(self._pcm_bytes_buf) >= self.TARGET_BUF_BYTES:
                    self._cond.wait(timeout=0.020)
                    continue
                blk = self._process_next_block()
                self._pcm_bytes_buf.extend(np.ascontiguousarray(blk, dtype=np.float32).tobytes())
                self._cond.notify_all()

    def set_pcm(self, pcm_array, start_ms: int = 0):
        with self._cond:
            self.pcm = pcm_array
            self.cursor_frame = max(0.0, (start_ms / 1000.0) * self.SAMPLE_RATE)
            self._cursor_played_frames = self.cursor_frame
            self.ended = False
            self._s_speed = self.speed
            self._s_pitch = 0.0 if self.link_pitch else self.pitch_semitones
            self._s_bass = self.bass_boost
            self._s_reverb = self.reverb_mix
            self._s_lofi = 1.0 if self.lofi_enabled else 0.0
            self._prev_step = self.speed * (2.0 ** (self._s_pitch / 12.0))
            self._prev_link_pitch = self.link_pitch
            self._reset_buffers()
            self._seek_fade_pos = 0
            self._cond.notify_all()

    def seek_ms(self, ms: int):
        with self._cond:
            if self.pcm is not None:
                self.cursor_frame = max(0.0, min(float(len(self.pcm)), (ms / 1000.0) * self.SAMPLE_RATE))
                self._cursor_played_frames = self.cursor_frame
                self.ended = False
                self._s_lofi = 1.0 if self.lofi_enabled else 0.0
                self._s_bass = self.bass_boost
                self._s_reverb = self.reverb_mix
                self._reset_buffers()
                self._seek_fade_pos = 0
                self._cond.notify_all()

    def _reset_buffers(self):
        self._pcm_bytes_buf.clear()
        if HAS_FX_DSP:
            self._dry_ola_buf.fill(0)
            self._wet_ola_buf.fill(0)
            self._grain_tail.fill(0)
            self._prev_dry_tail.fill(0)
            self._lofi_z1.fill(0)
            self._lofi_z2.fill(0)
            if self._fdl is not None:
                self._fdl.fill(0)
            self._fdl_idx = 0

    def update_params(self, speed: float, pitch_st: float, link_pitch: bool, bass: float, reverb: float, lofi: bool):
        with self._cond:
            self.speed = speed
            self.pitch_semitones = pitch_st
            self.link_pitch = link_pitch
            self.bass_boost = bass
            self.reverb_mix = reverb
            self.lofi_enabled = lofi

    def get_position_ms(self) -> int:
        with self._cond:
            return int((self._cursor_played_frames / self.SAMPLE_RATE) * 1000.0)

    def is_finished(self) -> bool:
        with self._cond:
            return self.ended and len(self._pcm_bytes_buf) == 0

    def bytesAvailable(self) -> int:
        return 16384 + super().bytesAvailable()

    def _sample_interp(self, idx: "np.ndarray", total: int):
        valid = idx < (total - 1)
        idx_c = np.clip(idx, 0, total - 1.001)
        i0 = idx_c.astype(np.int64)
        frac = (idx_c - i0).astype(np.float32)[:, None]
        out = self.pcm[i0] * (1.0 - frac) + self.pcm[i0 + 1] * frac
        out[~valid] = 0.0
        return out

    def _process_next_block(self):
        if self.pcm is None or len(self.pcm) == 0:
            return np.zeros((self.BLOCK, 2), dtype=np.float32)

        total = len(self.pcm)
        if self.cursor_frame >= total:
            self.ended = True
            return np.zeros((self.BLOCK, 2), dtype=np.float32)

        self._s_speed += (self.speed - self._s_speed) * 0.55
        target_pitch = 0.0 if self.link_pitch else self.pitch_semitones
        self._s_pitch += (target_pitch - self._s_pitch) * 0.55
        self._s_bass += (self.bass_boost - self._s_bass) * 0.55
        self._s_reverb += (self.reverb_mix - self._s_reverb) * 0.55

        pitch_factor = 2.0 ** (self._s_pitch / 12.0)
        link_switched = self.link_pitch != self._prev_link_pitch
        self._prev_link_pitch = self.link_pitch

        if self.link_pitch or (abs(pitch_factor - 1.0) < 0.01 and abs(self._s_speed - 1.0) < 0.01):
            next_step = max(0.45, min(1.85, self._s_speed * pitch_factor))
            steps = np.linspace(self._prev_step, next_step, self.BLOCK, dtype=np.float64)
            idx = self.cursor_frame + np.cumsum(steps) - steps[0]
            self.cursor_frame += float(np.sum(steps))
            self._prev_step = next_step
            dry = self._sample_interp(idx, total)
            if link_switched:
                self._grain_tail.fill(0)
        else:
            half = self.BLOCK // 2
            win = np.hanning(self.BLOCK).astype(np.float32)[:, None]
            hop_in = half * self._s_speed
            dry = np.zeros((self.BLOCK, 2), dtype=np.float32)

            g0_idx = self.cursor_frame + np.arange(self.BLOCK, dtype=np.float64) * pitch_factor
            g0 = self._sample_interp(g0_idx, total) * win
            if link_switched:
                self._grain_tail = g0[:half].copy()
            dry[:half] = self._grain_tail + g0[:half]

            g1_idx = (self.cursor_frame + hop_in) + np.arange(self.BLOCK, dtype=np.float64) * pitch_factor
            g1 = self._sample_interp(g1_idx, total) * win
            dry[half:] = g0[half:] + g1[:half]
            self._grain_tail = g1[half:].copy()

            self.cursor_frame += hop_in * 2.0
            self._prev_step = self._s_speed * pitch_factor

        if link_switched:
            cf = np.linspace(0.0, 1.0, 64, dtype=np.float32)[:, None]
            dry[:64] = self._prev_dry_tail * (1.0 - cf) + dry[:64] * cf

        if self._seek_fade_pos < self._seek_fade_len:
            n_apply = min(self.BLOCK, self._seek_fade_len - self._seek_fade_pos)
            idx_f = np.arange(self._seek_fade_pos, self._seek_fade_pos + n_apply, dtype=np.float32)
            ramp = (0.5 * (1.0 - np.cos(math.pi * (idx_f / float(self._seek_fade_len)))))[:, None]
            dry[:n_apply] *= ramp
            self._seek_fade_pos += n_apply

        self._prev_dry_tail = dry[-64:].copy()

        if self._s_bass > 0.004 or self._s_reverb > 0.004:
            padded = np.zeros((self.N_FFT, 2), dtype=np.float32)
            padded[: self.BLOCK] = dry
            X = np.fft.rfft(padded, axis=0)

            if self._s_bass > 0.004:
                extra_low = self._s_bass * 3.1
                headroom = 1.0 - 0.16 * self._s_bass
                X_eq = X * headroom * (1.0 + extra_low * self._lp_fft)
                eq_full = np.fft.irfft(X_eq, n=self.N_FFT, axis=0).astype(np.float32)
                dry_out = eq_full[: self.BLOCK] + self._dry_ola_buf
                self._dry_ola_buf = eq_full[self.BLOCK :].copy()
            else:
                X_eq = X
                dry_out = dry
                self._dry_ola_buf.fill(0)

            if self._s_reverb > 0.004 and self._ir_parts is not None:
                n_parts = len(self._ir_parts)
                self._fdl[self._fdl_idx] = X_eq
                conv_spec = np.zeros_like(X_eq)
                for k in range(n_parts):
                    p_idx = (self._fdl_idx - k) % n_parts
                    conv_spec += self._fdl[p_idx] * self._ir_parts[k]
                self._fdl_idx = (self._fdl_idx + 1) % n_parts

                wet_full = np.fft.irfft(conv_spec, n=self.N_FFT, axis=0).astype(np.float32)
                wet = wet_full[: self.BLOCK] + self._wet_ola_buf
                self._wet_ola_buf = wet_full[self.BLOCK :].copy()

                out = dry_out * (1.0 - 0.022 * self._s_reverb) + wet * (self._s_reverb * 0.11)
            else:
                self._wet_ola_buf.fill(0)
                out = dry_out

            out = np.tanh(out * 0.86) / 0.86
        else:
            self._dry_ola_buf.fill(0)
            self._wet_ola_buf.fill(0)
            out = dry.astype(np.float32)

        target_lofi = 1.0 if self.lofi_enabled else 0.0
        if self._s_lofi > 0.002 or target_lofi > 0.0:
            prev_lofi = self._s_lofi
            self._s_lofi += (target_lofi - self._s_lofi) * 0.35
            l_in = out[:, 0].tolist()
            r_in = out[:, 1].tolist()
            b0, b1, b2, a1, a2 = self._lofi_coeffs
            z1_l, z2_l = float(self._lofi_z1[0]), float(self._lofi_z2[0])
            z1_r, z2_r = float(self._lofi_z1[1]), float(self._lofi_z2[1])
            n_samp = len(l_in)
            l_out = [0.0] * n_samp
            r_out = [0.0] * n_samp
            for idx_s in range(n_samp):
                xl = l_in[idx_s]
                yl = b0 * xl + z1_l
                z1_l = b1 * xl - a1 * yl + z2_l
                z2_l = b2 * xl - a2 * yl
                l_out[idx_s] = yl

                xr = r_in[idx_s]
                yr = b0 * xr + z1_r
                z1_r = b1 * xr - a1 * yr + z2_r
                z2_r = b2 * xr - a2 * yr
                r_out[idx_s] = yr
            self._lofi_z1[0], self._lofi_z2[0] = z1_l, z2_l
            self._lofi_z1[1], self._lofi_z2[1] = z1_r, z2_r
            lofi_arr = np.column_stack((np.array(l_out, dtype=np.float32), np.array(r_out, dtype=np.float32)))
            ramp = np.linspace(prev_lofi, self._s_lofi, n_samp, dtype=np.float32)[:, None]
            out = out * (1.0 - ramp) + lofi_arr * ramp
        else:
            self._lofi_z1.fill(0)
            self._lofi_z2.fill(0)

        return out.astype(np.float32)

    def readData(self, maxlen: int) -> bytes:
        if not HAS_FX_DSP or maxlen <= 0:
            return b"\x00" * max(0, maxlen)

        take_bytes = maxlen - (maxlen % 8)
        if take_bytes <= 0:
            return b""

        with self._cond:
            while len(self._pcm_bytes_buf) < take_bytes and not self.ended and self.pcm is not None:
                blk = self._process_next_block()
                self._pcm_bytes_buf.extend(np.ascontiguousarray(blk, dtype=np.float32).tobytes())
            n_avail = min(take_bytes, len(self._pcm_bytes_buf))
            out = bytes(self._pcm_bytes_buf[:n_avail])
            if n_avail > 0:
                del self._pcm_bytes_buf[:n_avail]
                self._cursor_played_frames += (n_avail // 8) * self._s_speed
                self._cond.notify_all()

        if len(out) < take_bytes:
            out += b"\x00" * (take_bytes - len(out))
        return out

    def writeData(self, data: bytes) -> int:
        return 0


class DecodeWorker(threading.Thread):
    def __init__(self, path: str, gen_id: int, callback):
        super().__init__(daemon=True)
        self.path = path
        self.gen_id = gen_id
        self.callback = callback

    def run(self):
        try:
            container = av.open(self.path)
            resampler = av.AudioResampler(format="flt", layout="stereo", rate=44100)
            chunks = []
            for frame in container.decode(audio=0):
                for resampled in resampler.resample(frame):
                    arr = resampled.to_ndarray()
                    if arr.ndim == 2 and arr.shape[0] == 2 and arr.shape[1] != 2:
                        arr = arr.T
                    elif arr.ndim == 2 and arr.shape[0] == 1:
                        arr = arr.reshape(-1, 2)
                    elif arr.ndim == 1:
                        arr = arr.reshape(-1, 2)
                    if arr.size:
                        chunks.append(arr.astype(np.float32))
            for resampled in resampler.resample(None):
                arr = resampled.to_ndarray()
                if arr.ndim == 2 and arr.shape[0] == 2 and arr.shape[1] != 2:
                    arr = arr.T
                elif arr.ndim == 2 and arr.shape[0] == 1:
                    arr = arr.reshape(-1, 2)
                elif arr.ndim == 1:
                    arr = arr.reshape(-1, 2)
                if arr.size:
                    chunks.append(arr.astype(np.float32))
            container.close()
            pcm = np.concatenate(chunks, axis=0) if chunks else np.zeros((0, 2), dtype=np.float32)
            self.callback(self.path, self.gen_id, pcm, "")
        except Exception as exc:
            self.callback(self.path, self.gen_id, None, str(exc))


# ---------------------------------------------------------------------------
# Event Filters & Custom Widgets
# ---------------------------------------------------------------------------
class NoWheelEventFilter(QObject):
    """Prevents mouse wheel from accidentally changing sliders/combos and forwards scroll up."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            event.ignore()
            p = obj.parentWidget()
            while p is not None:
                QApplication.sendEvent(p, event)
                if event.isAccepted():
                    break
                p = p.parentWidget()
            return True
        return super().eventFilter(obj, event)


class GlobalSpacePlayPauseFilter(QObject):
    """Locks Spacebar to Play/Pause globally unless typing in a text input field."""

    def __init__(self, main_win):
        super().__init__(main_win)
        self.main_win = main_win

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            fw = QApplication.focusWidget()
            if isinstance(fw, QLineEdit):
                fw.clearFocus()
                self.main_win.setFocus(Qt.FocusReason.OtherFocusReason)
                event.accept()
                return True

        if etype in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
            if event.key() == Qt.Key.Key_Space and not (
                event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
            ):
                fw = QApplication.focusWidget()
                if isinstance(fw, (QLineEdit, QTextEdit)):
                    return False
                if etype == QEvent.Type.KeyPress and not event.isAutoRepeat():
                    self.main_win.toggle_play_pause()
                event.accept()
                return True
        return super().eventFilter(obj, event)


class GpuCompatibilityDialog(QDialog):
    """Shown before launch if GPU supports neither DirectX 10.0+ nor OpenGL 2.0+."""

    def __init__(self, icon: QIcon):
        super().__init__()
        self.setWindowTitle("NanoTune — Graphics Compatibility")
        self.setWindowIcon(icon)
        self.setFixedSize(520, 320)
        self.setModal(True)

        self.setStyleSheet("""
            QDialog { background-color: #0D1112; color: #EAF0EE; border: 1px solid #2A3236; }
            QLabel { color: #EAF0EE; }
            QPushButton#primaryBtn {
                background-color: #FFD83D; color: #121006; border: none;
                border-radius: 15px; padding: 10px 20px; font-size: 13px; font-weight: 800;
            }
            QPushButton#primaryBtn:hover { background-color: #FFE46E; }
            QPushButton#ghostBtn {
                background-color: #171E20; color: #97A6A4; border: 1px solid #2A3638;
                border-radius: 15px; padding: 10px 18px; font-size: 13px;
            }
            QPushButton#ghostBtn:hover { color: #FFFFFF; border-color: #FFD83D; }
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        hdr = QLabel("Legacy Graphics Detected")
        hdr.setStyleSheet("color: #FFD83D; font-size: 20px; font-weight: 800;")
        lay.addWidget(hdr)

        info = QLabel(
            f"Hardware acceleration in NanoTune requires DirectX 10.0+ or OpenGL 2.0+.\n\n"
            f"Detected on this PC:\n"
            f"• {DX_DETECT_LABEL}\n"
            f"• {GL_DETECT_LABEL}\n\n"
            "You can continue cleanly using Native CPU Software Rendering."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #D2DFDD; font-size: 13px;")
        lay.addWidget(info)
        lay.addStretch()

        btns = QHBoxLayout()
        exit_btn = QPushButton("Exit")
        exit_btn.setObjectName("ghostBtn")
        exit_btn.clicked.connect(self.reject)

        cont_btn = QPushButton("Continue with Software Rendering")
        cont_btn.setObjectName("primaryBtn")
        cont_btn.clicked.connect(self.accept)

        btns.addWidget(exit_btn)
        btns.addStretch()
        btns.addWidget(cont_btn)
        lay.addLayout(btns)

    def showEvent(self, event):
        super().showEvent(event)
        enable_windows_dark_titlebar(int(self.winId()))


class RoundedCoverWidget(QWidget):
    def __init__(self, radius: int = 12, border_width: float = 2.0, parent=None):
        super().__init__(parent)
        self.radius = radius
        self.border_width = border_width
        self.border_color = QColor(STAR_DEFAULT_YELLOW)
        self._pixmap = QPixmap()
        self._scaled_cache = QPixmap()
        self._cached_size = QSize()

    def set_pixmap(self, pm: QPixmap):
        self._pixmap = pm
        self._scaled_cache = QPixmap()
        self.update()

    def set_border_color(self, hex_color: str):
        self.border_color = QColor(hex_color)
        self.border_color.setAlpha(155)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        inset = self.border_width * 0.5
        rect = QRectF(inset, inset, self.width() - self.border_width, self.height() - self.border_width)
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)

        p.save()
        p.setClipPath(path)
        if not self._pixmap.isNull():
            if self._scaled_cache.isNull() or self._cached_size != self.size():
                self._cached_size = self.size()
                self._scaled_cache = self._pixmap.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            x = (self.width() - self._scaled_cache.width()) // 2
            y = (self.height() - self._scaled_cache.height()) // 2
            p.drawPixmap(x, y, self._scaled_cache)
        else:
            p.fillRect(rect, QColor("#151A1C"))
        p.restore()

        if self.border_width > 0:
            p.setPen(QPen(self.border_color, self.border_width))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)


class SeekSlider(QSlider):
    """Exact 1:1 pixel click-and-drag horizontal slider."""

    def _value_from_x(self, x_pos: float) -> int:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        rel = int(x_pos - groove.x() - handle.width() * 0.5)
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), rel, span)

    def mousePressEvent(self, event):
        if not self.isEnabled():
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            val = self._value_from_x(event.position().x())
            self.setValue(val)
            self.sliderPressed.emit()
            self.sliderMoved.emit(val)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.isEnabled():
            event.ignore()
            return
        if (event.buttons() & Qt.MouseButton.LeftButton) and self.maximum() > self.minimum():
            val = self._value_from_x(event.position().x())
            self.setValue(val)
            self.sliderMoved.emit(val)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if not self.isEnabled():
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            val = self._value_from_x(event.position().x())
            self.setValue(val)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TrackRowDelegate(QStyledItemDelegate):
    def __init__(self, get_cover_fn, get_accent_fn, get_font_fn, parent=None):
        super().__init__(parent)
        self.get_cover_fn = get_cover_fn
        self.get_accent_fn = get_accent_fn
        self.get_font_fn = get_font_fn

    def sizeHint(self, option, index):
        return QSize(0, 62)

    def paint(self, painter: QPainter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            super().paint(painter, option, index)
            return

        accent_hex, accent2_hex = self.get_accent_fn()
        accent_c = QColor(accent_hex)
        accent2_c = QColor(accent2_hex)
        fam = self.get_font_fn()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        r = option.rect.adjusted(4, 3, -4, -3)
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_playing = bool(data.get("is_current", False))

        if is_playing:
            bg_c = QColor(accent_c)
            bg_c.setAlpha(58)
            bd_c = QColor(accent_c)
            bd_c.setAlpha(125)
            painter.setBrush(bg_c)
            painter.setPen(QPen(bd_c, 1))
            painter.drawRoundedRect(r, 10, 10)
        elif is_selected:
            painter.setBrush(QColor(255, 255, 255, 28))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(r, 10, 10)
        elif is_hover:
            painter.setBrush(QColor(255, 255, 255, 16))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(r, 10, 10)

        num_rect = QRect(r.left() + 10, r.top(), 40, r.height())
        if is_playing:
            painter.setPen(accent_c)
            painter.setFont(QFont(fam, 11, QFont.Weight.Bold))
            painter.drawText(num_rect, Qt.AlignmentFlag.AlignCenter, "▶")
        else:
            painter.setPen(QColor("#8DA09D"))
            painter.setFont(QFont(fam, 10, QFont.Weight.DemiBold))
            dn = data.get("display_num", 0)
            num_txt = f"{dn:02d}" if (0 < dn < 100) else (str(dn) if dn >= 100 else "•")
            painter.drawText(num_rect, Qt.AlignmentFlag.AlignCenter, num_txt)

        thumb_rect = QRect(r.left() + 56, r.top() + (r.height() - 40) // 2, 40, 40)
        cover_px: QPixmap = self.get_cover_fn(data)
        if cover_px and not cover_px.isNull():
            path = QPainterPath()
            path.addRoundedRect(QRectF(thumb_rect), 6, 6)
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(thumb_rect, cover_px)
            painter.restore()

        dur_str = format_ms(data["duration_ms"])
        dur_rect = QRect(r.right() - 62, r.top(), 48, r.height())
        painter.setPen(QColor("#A3B5B2"))
        painter.setFont(QFont(fam, 10, QFont.Weight.Medium))
        painter.drawText(dur_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, dur_str)

        right_bound = dur_rect.left() - 12
        if data.get("has_lyrics"):
            badge_rect = QRect(right_bound - 56, r.top() + (r.height() - 20) // 2, 52, 20)
            b_fill = QColor(accent2_c)
            b_fill.setAlpha(38)
            b_pen = QColor(accent2_c)
            b_pen.setAlpha(115)
            painter.setBrush(b_fill)
            painter.setPen(QPen(b_pen, 1))
            painter.drawRoundedRect(badge_rect, 5, 5)
            painter.setPen(accent2_c)
            painter.setFont(QFont(fam, 8, QFont.Weight.Bold))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "LYRICS")
            right_bound = badge_rect.left() - 12

        text_left = thumb_rect.right() + 14
        text_w = max(80, right_bound - text_left)
        title_rect = QRect(text_left, r.top() + 8, text_w, 22)
        sub_rect = QRect(text_left, r.top() + 30, text_w, 18)

        title_font = QFont(fam, 11, QFont.Weight.Bold if is_playing else QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(accent_c if is_playing else QColor("#FFFFFF"))
        elided_title = QFontMetrics(title_font).elidedText(data["display_title"], Qt.TextElideMode.ElideRight, text_w)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_title)

        sub_font = QFont(fam, 9)
        painter.setFont(sub_font)
        painter.setPen(QColor("#92A5A2"))
        sub_txt = f"{data['artist']}  •  {data['album']}  •  {data['format']}"
        elided_sub = QFontMetrics(sub_font).elidedText(sub_txt, Qt.TextElideMode.ElideRight, text_w)
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_sub)

        painter.restore()


class AlbumCardWidget(QFrame):
    clicked = Signal(str)

    def __init__(self, album_key: str, album_name: str, artist_name: str, track_count: int, cover_pm: QPixmap, accent: str, parent=None):
        super().__init__(parent)
        self.album_key = album_key
        self.album_name = album_name
        self.setObjectName("albumCard")
        self.setFixedSize(192, 246)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)

        self.cover = RoundedCoverWidget(radius=10, border_width=1.5)
        self.cover.setFixedSize(168, 168)
        self.cover.set_border_color(accent)
        self.cover.set_pixmap(cover_pm)
        lay.addWidget(self.cover)

        t_lbl = QLabel()
        t_lbl.setStyleSheet("color: #FFFFFF; font-size: 13px; font-weight: 700; background: transparent;")
        t_lbl.setText(QFontMetrics(t_lbl.font()).elidedText(album_name, Qt.TextElideMode.ElideRight, 166))

        s_lbl = QLabel()
        s_lbl.setStyleSheet("color: #9FB0AE; font-size: 11px; background: transparent;")
        sub = f"{artist_name} • {track_count} track{'s' if track_count != 1 else ''}"
        s_lbl.setText(QFontMetrics(s_lbl.font()).elidedText(sub, Qt.TextElideMode.ElideRight, 166))

        lay.addWidget(t_lbl)
        lay.addWidget(s_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.album_name)
        super().mousePressEvent(event)


class AnimatedLyricsWidget(QWidget):
    """Frame-rate adaptive (60/120/144/165 Hz) lyrics & .elrc word-by-word karaoke renderer."""

    lineClicked = Signal(int)

    def __init__(self, refresh_hz: float = 60.0, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._backdrop_fn = None

        self.lines: list[dict] = []
        self.is_synced: bool = False
        self.has_elrc_words: bool = False
        self.active_idx: int = -1
        self.hover_idx: int = -1
        self.font_size: int = 19
        self.font_family: str = "Georgia"
        self.accent_color = QColor(STAR_DEFAULT_YELLOW)

        self.scroll_y: float = 0.0
        self.target_scroll_y: float = 0.0
        self.max_scroll_y: float = 0.0
        self.manual_browse_until: float = 0.0

        self._is_playing: bool = False
        self._clock_speed: float = 1.0
        self._clock_anchor_ms: float = 0.0
        self._clock_anchor_mono: float = time.monotonic()
        self._smooth_pos_ms: int = 0
        self._last_tick_mono: float = time.monotonic()

        self._cached_width: int = -1
        self._anim_timer = QTimer(self)
        self.set_refresh_rate(refresh_hz)
        self._anim_timer.timeout.connect(self._tick_animation)

    def set_backdrop_fn(self, fn):
        self._backdrop_fn = fn

    def set_refresh_rate(self, hz: float):
        clamped = max(50.0, min(240.0, float(hz or 60.0)))
        interval_ms = max(4, int(round(1000.0 / clamped)))
        self._anim_timer.setInterval(interval_ms)

    def set_font_family(self, family: str):
        self.font_family = family
        self._cached_width = -1
        self._recompute_layout()
        self.update()

    def set_accent_color(self, hex_color: str):
        self.accent_color = QColor(hex_color)
        self.update()

    def set_font_size(self, size: int):
        self.font_size = size
        self._cached_width = -1
        self._recompute_layout()
        self.update()

    def set_playback_clock(self, pos_ms: int, is_playing: bool, speed: float = 1.0, force_snap: bool = False):
        now = time.monotonic()
        self._is_playing = is_playing
        self._clock_speed = max(0.25, float(speed))
        if force_snap or not is_playing or abs(pos_ms - self._current_interpolated_ms(now)) > 90:
            self._clock_anchor_ms = float(pos_ms)
            self._clock_anchor_mono = now
            self._smooth_pos_ms = pos_ms
        if is_playing and self.has_elrc_words and not self._anim_timer.isActive():
            self._last_tick_mono = now
            self._anim_timer.start()

    def _current_interpolated_ms(self, now: float) -> int:
        if not self._is_playing:
            return int(self._clock_anchor_ms)
        return int(self._clock_anchor_ms + (now - self._clock_anchor_mono) * 1000.0 * self._clock_speed)

    def set_lyrics(self, is_synced: bool, parsed: list[dict], empty_msg: str = ""):
        self.is_synced = is_synced
        self.active_idx = -1
        self.hover_idx = -1
        self.scroll_y = 0.0
        self.target_scroll_y = 0.0
        self.manual_browse_until = 0.0
        self.has_elrc_words = any(bool(e.get("words")) for e in parsed)

        if not parsed:
            self.lines = [{"time_ms": -1, "text": empty_msg, "words": [], "focus": 0.6, "y": 0, "h": 40, "rows": []}]
        else:
            self.lines = [
                {
                    "time_ms": e["time_ms"],
                    "text": e["text"],
                    "words": e.get("words", []),
                    "focus": 0.0,
                    "y": 0,
                    "h": 36,
                    "rows": [],
                }
                for e in parsed
            ]

        self._cached_width = -1
        self._recompute_layout()
        self.update()

    def set_active_index(self, idx: int):
        if idx == self.active_idx:
            return
        self.active_idx = idx
        self._update_target_scroll()
        if not self._anim_timer.isActive():
            self._last_tick_mono = time.monotonic()
            self._anim_timer.start()

    def _wrap_elrc_words(self, words: list[dict], fm: QFontMetrics, wrap_w: int) -> tuple[list[dict], int]:
        rows: list[dict] = []
        cur_items: list[dict] = []
        cur_w = 0
        line_h = fm.height()

        for w in words:
            txt = w["text"]
            w_px = fm.horizontalAdvance(txt)
            if cur_items and cur_w + w_px > wrap_w and txt.strip():
                rows.append({"items": cur_items, "y_off": len(rows) * line_h})
                cur_items = []
                cur_w = 0
                txt_draw = txt.lstrip()
                w_px = fm.horizontalAdvance(txt_draw)
            else:
                txt_draw = txt
            cur_items.append(
                {"text": txt_draw, "start_ms": w["start_ms"], "end_ms": w["end_ms"], "x": cur_w, "w": w_px}
            )
            cur_w += w_px

        if cur_items:
            rows.append({"items": cur_items, "y_off": len(rows) * line_h})
        total_h = max(28, len(rows) * line_h + 8)
        return rows, total_h

    def _recompute_layout(self):
        w = max(200, self.width() - 48)
        if w == self._cached_width and self.lines:
            return
        self._cached_width = w

        base_font = QFont(self.font_family, self.font_size, QFont.Weight.Bold)
        fm = QFontMetrics(base_font)
        y = 32
        wrap_w = int(w / 1.13)

        for entry in self.lines:
            if entry["words"]:
                rows, h_px = self._wrap_elrc_words(entry["words"], fm, wrap_w)
                entry["rows"] = rows
                entry["y"] = y
                entry["h"] = h_px
            else:
                txt = entry["text"] or "♪"
                rect = fm.boundingRect(QRect(0, 0, wrap_w, 2000), Qt.TextFlag.TextWordWrap, txt)
                entry["rows"] = []
                entry["y"] = y
                entry["h"] = max(28, rect.height() + 8)
            y += entry["h"] + 18

        self.max_scroll_y = max(0.0, float(y + 40 - self.height()))
        self._update_target_scroll()

    def _update_target_scroll(self):
        if not self.is_synced or not (0 <= self.active_idx < len(self.lines)):
            return
        if time.monotonic() < self.manual_browse_until:
            return
        entry = self.lines[self.active_idx]
        center_y = entry["y"] + entry["h"] * 0.5
        desired = center_y - self.height() * 0.38
        self.target_scroll_y = max(0.0, min(self.max_scroll_y, desired))

    def _tick_animation(self):
        now = time.monotonic()
        dt = max(0.002, min(0.05, now - self._last_tick_mono))
        self._last_tick_mono = now

        scroll_alpha = 1.0 - math.exp(-10.0 * dt)
        focus_alpha = 1.0 - math.exp(-13.0 * dt)

        changed = False
        diff = self.target_scroll_y - self.scroll_y
        if abs(diff) > 0.25:
            self.scroll_y += diff * scroll_alpha
            changed = True
        elif diff != 0.0:
            self.scroll_y = self.target_scroll_y
            changed = True

        for i, entry in enumerate(self.lines):
            target_f = 1.0 if (self.is_synced and i == self.active_idx) else 0.0
            if not self.is_synced:
                target_f = 0.5
            df = target_f - entry["focus"]
            if abs(df) > 0.008:
                entry["focus"] += df * focus_alpha
                changed = True
            elif df != 0.0:
                entry["focus"] = target_f
                changed = True

        if self._is_playing and self.has_elrc_words:
            self._smooth_pos_ms = self._current_interpolated_ms(now)
            changed = True

        if changed:
            self.update()
        else:
            if self.is_synced and now >= self.manual_browse_until:
                self._update_target_scroll()
                if abs(self.target_scroll_y - self.scroll_y) <= 0.25:
                    self._anim_timer.stop()
            else:
                self._anim_timer.stop()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cached_width = -1
        self._recompute_layout()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        self.target_scroll_y = max(0.0, min(self.max_scroll_y, self.target_scroll_y - delta * 0.65))
        self.manual_browse_until = time.monotonic() + 3.0
        if not self._anim_timer.isActive():
            self._last_tick_mono = time.monotonic()
            self._anim_timer.start()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.is_synced:
            return
        y_pos = event.position().y() + self.scroll_y
        found = -1
        for i, entry in enumerate(self.lines):
            if entry["y"] - 6 <= y_pos <= entry["y"] + entry["h"] + 6:
                found = i
                break
        if found != self.hover_idx:
            self.hover_idx = found
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if (found >= 0 and self.lines[found]["time_ms"] >= 0)
                else Qt.CursorShape.ArrowCursor
            )
            self.update()

    def leaveEvent(self, event):
        if self.hover_idx != -1:
            self.hover_idx = -1
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_synced:
            y_pos = event.position().y() + self.scroll_y
            for i, entry in enumerate(self.lines):
                if entry["y"] - 6 <= y_pos <= entry["y"] + entry["h"] + 6 and entry["time_ms"] >= 0:
                    self.manual_browse_until = 0.0
                    target_ms = entry["time_ms"]
                    if entry["words"]:
                        target_ms = entry["words"][0]["start_ms"]
                    self.set_playback_clock(target_ms, self._is_playing, self._clock_speed, force_snap=True)
                    self.set_active_index(i)
                    self.lineClicked.emit(target_ms)
                    self.update()
                    return

    def paintEvent(self, event):
        self._recompute_layout()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w = self.width()
        h = self.height()

        if self._backdrop_fn is not None:
            bg_pm, offset = self._backdrop_fn()
            if not bg_pm.isNull():
                painter.drawPixmap(0, 0, bg_pm, offset.x(), offset.y(), w, h)
            else:
                painter.fillRect(0, 0, w, h, QColor(12, 16, 18))
            painter.fillRect(0, 0, w, h, QColor(12, 16, 18, 148))
        else:
            painter.fillRect(0, 0, w, h, QColor(12, 16, 18))

        pad_x = 22
        wrap_w = int(max(200, w - 48) / 1.13)
        base_font = QFont(self.font_family, self.font_size, QFont.Weight.Bold)
        fm = QFontMetrics(base_font)
        ac = self.accent_color
        pos_ms = self._smooth_pos_ms

        for i, entry in enumerate(self.lines):
            draw_y = entry["y"] - self.scroll_y
            line_h = entry["h"]
            if draw_y + line_h < -40 or draw_y > h + 40:
                continue

            focus = entry["focus"]
            edge_fade = 1.0
            if draw_y < 28:
                edge_fade = max(0.0, (draw_y + 20) / 48.0)
            elif draw_y + line_h > h - 24:
                edge_fade = max(0.0, (h - draw_y) / max(1.0, line_h + 24.0))

            if i == self.hover_idx and self.is_synced and entry["time_ms"] >= 0:
                painter.setBrush(QColor(255, 255, 255, 18))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(QRectF(10, draw_y - 4, w - 20, line_h + 8), 10, 10)

            scale = 1.0 + (0.11 * focus if self.is_synced else 0.0)
            painter.save()
            painter.translate(pad_x, draw_y + line_h * 0.5)
            painter.scale(scale, scale)
            painter.setFont(base_font)

            if self.is_synced and entry["rows"] and i == self.active_idx:
                top_y = -line_h * 0.5 + 4
                row_h = fm.height()
                for row in entry["rows"]:
                    ry = top_y + row["y_off"]
                    for wi in row["items"]:
                        txt = wi["text"]
                        if not txt:
                            continue
                        st = wi["start_ms"]
                        en = wi["end_ms"]
                        if pos_ms >= en:
                            wc = QColor(255, 255, 255, int(255 * edge_fade))
                            lift = 0.0
                        elif st <= pos_ms < en:
                            prog = (pos_ms - st) / max(1.0, float(en - st))
                            lift = -1.5 * math.sin(prog * math.pi)
                            glow_c = QColor(ac.red(), ac.green(), ac.blue(), int(145 * edge_fade))
                            painter.setPen(glow_c)
                            painter.drawText(
                                QRectF(wi["x"] + 1, ry + lift + 1, wi["w"] + 8, row_h),
                                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                txt,
                            )
                            r_m = int(ac.red() + (255 - ac.red()) * 0.45)
                            g_m = int(ac.green() + (255 - ac.green()) * 0.45)
                            b_m = int(ac.blue() + (255 - ac.blue()) * 0.45)
                            wc = QColor(r_m, g_m, b_m, int(255 * edge_fade))
                        else:
                            wc = QColor(255, 255, 255, int(115 * edge_fade))
                            lift = 0.0

                        painter.setPen(wc)
                        painter.drawText(
                            QRectF(wi["x"], ry + lift, wi["w"] + 8, row_h),
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                            txt,
                        )
            else:
                if self.is_synced:
                    base_alpha = 85 if i < self.active_idx else (255 if i == self.active_idx else 128)
                    alpha = int((base_alpha + (255 - base_alpha) * focus) * edge_fade)
                    r = int(214 + (ac.red() - 214) * focus * 0.35)
                    g = int(224 + (ac.green() - 224) * focus * 0.35)
                    b = int(222 + (ac.blue() - 222) * focus * 0.35)
                    text_color = QColor(255, 255, 255, alpha) if focus > 0.72 else QColor(r, g, b, alpha)
                else:
                    text_color = QColor(232, 240, 238, int(230 * edge_fade))

                if self.is_synced and focus > 0.22:
                    glow = QColor(ac.red(), ac.green(), ac.blue(), int(110 * focus * edge_fade))
                    painter.setPen(glow)
                    painter.drawText(
                        QRectF(1, -line_h * 0.5 + 1, wrap_w, line_h),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                        entry["text"] or "♪",
                    )

                painter.setPen(text_color)
                painter.drawText(
                    QRectF(0, -line_h * 0.5, wrap_w, line_h),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                    entry["text"] or "♪",
                )

            painter.restore()


class DefaultPlayerPromptDialog(QDialog):
    def __init__(self, cover_pixmap: QPixmap, icon: QIcon, accent_hex: str = STAR_DEFAULT_YELLOW, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Default Music Player")
        self.setWindowIcon(icon)
        self.setFixedSize(500, 320)
        self.setModal(True)

        c1 = QColor(accent_hex)
        btn_fg = "#121006" if color_luminance(accent_hex) > 0.45 else "#FFFFFF"
        hover_hex = c1.lighter(115).name()

        self.setStyleSheet(f"""
            QDialog {{
                background-color: #0D1112;
                color: #EAF0EE;
                border: 1px solid #2A3236;
            }}
            QLabel {{ color: #EAF0EE; }}
            QPushButton#primaryBtn {{
                background-color: {accent_hex};
                color: {btn_fg};
                border: none;
                border-radius: 16px;
                padding: 10px 22px;
                font-size: 13px;
                font-weight: 800;
            }}
            QPushButton#primaryBtn:hover {{ background-color: {hover_hex}; }}
            QPushButton#ghostBtn {{
                background-color: #171E20;
                color: #97A6A4;
                border: 1px solid #2A3638;
                border-radius: 16px;
                padding: 10px 18px;
                font-size: 13px;
            }}
            QPushButton#ghostBtn:hover {{ color: #FFFFFF; border-color: {accent_hex}; }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 26, 30, 26)
        lay.setSpacing(14)

        top_row = QHBoxLayout()
        art = RoundedCoverWidget(radius=12, border_width=2.0)
        art.setFixedSize(76, 76)
        art.set_border_color(accent_hex)
        art.set_pixmap(icon.pixmap(76, 76) if cover_pixmap.isNull() else cover_pixmap)
        top_row.addWidget(art)

        t_col = QVBoxLayout()
        badge = QLabel("WINDOWS INTEGRATION")
        badge.setStyleSheet(f"color: {accent_hex}; font-size: 10px; font-weight: 800; letter-spacing: 1.4px;")
        title = QLabel("Make NanoTune Default?")
        title.setStyleSheet("color: #FFFFFF; font-size: 22px; font-weight: 800;")
        t_col.addWidget(badge)
        t_col.addWidget(title)
        t_col.addStretch()
        top_row.addLayout(t_col, 1)
        lay.addLayout(top_row)

        desc = QLabel(
            "Do you want to make NanoTune your default music player for .flac, .mp3, .wav, .m4a, and other audio files?\n\n"
            "Clicking Yes registers NanoTune and opens Windows Default Apps so you can confirm with one click."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #C4D0CE; font-size: 13px;")
        lay.addWidget(desc)
        lay.addStretch()

        btn_row = QHBoxLayout()
        no_btn = QPushButton("Not Now")
        no_btn.setObjectName("ghostBtn")
        no_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        no_btn.clicked.connect(self.reject)

        yes_btn = QPushButton("Yes, Set as Default")
        yes_btn.setObjectName("primaryBtn")
        yes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        yes_btn.clicked.connect(self.accept)

        btn_row.addWidget(no_btn)
        btn_row.addStretch()
        btn_row.addWidget(yes_btn)
        lay.addLayout(btn_row)

    def showEvent(self, event):
        super().showEvent(event)
        enable_windows_dark_titlebar(int(self.winId()))


class FirstLaunchDialog(QDialog):
    def __init__(self, cover_pixmap: QPixmap, icon: QIcon, parent=None):
        super().__init__(parent)
        self.selected_folder = ""
        self.setWindowTitle("Welcome to NanoTune")
        self.setWindowIcon(icon)
        self.setFixedSize(530, 395)
        self.setModal(True)

        self.setStyleSheet("""
            QDialog {
                background-color: #0D1112;
                color: #EAF0EE;
                border: 1px solid #2A3236;
            }
            QLabel { color: #EAF0EE; }
            QPushButton#primaryBtn {
                background-color: #FFD83D;
                color: #121006;
                border: none;
                border-radius: 16px;
                padding: 10px 22px;
                font-size: 13px;
                font-weight: 800;
            }
            QPushButton#primaryBtn:hover { background-color: #FFE46E; }
            QPushButton#ghostBtn {
                background-color: #171E20;
                color: #97A6A4;
                border: 1px solid #2A3638;
                border-radius: 16px;
                padding: 10px 18px;
                font-size: 13px;
            }
            QPushButton#ghostBtn:hover { color: #FFFFFF; border-color: #FFD83D; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(14)

        top_row = QHBoxLayout()
        art = RoundedCoverWidget(radius=12, border_width=2.0)
        art.setFixedSize(92, 92)
        art.set_border_color(STAR_DEFAULT_YELLOW)
        if not cover_pixmap.isNull():
            art.set_pixmap(cover_pixmap)
        else:
            art.set_pixmap(icon.pixmap(92, 92))
        top_row.addWidget(art)

        title_col = QVBoxLayout()
        badge = QLabel(f"LIGHTWEIGHT PLAYER  •  Setup")
        badge.setStyleSheet("color: #FFE885; font-size: 10px; font-weight: 800; letter-spacing: 1.4px;")
        title = QLabel("NanoTune")
        title.setStyleSheet("color: #FFFFFF; font-size: 26px; font-weight: 800;")
        sub = QLabel("Native Windows Music & Lyrics Player")
        sub.setStyleSheet("color: #97A6A4; font-size: 13px;")
        title_col.addWidget(badge)
        title_col.addWidget(title)
        title_col.addWidget(sub)
        title_col.addStretch()
        top_row.addLayout(title_col, 1)
        layout.addLayout(top_row)

        desc = QLabel(
            "Select a music folder to load your library (.flac, .mp3, .wav, .m4a). "
            "You can add more directories anytime in Settings, drag & drop audio or .lrc files."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #C4D0CE; font-size: 13px;")
        layout.addWidget(desc)

        self.path_label = QLabel("No folder selected yet")
        self.path_label.setStyleSheet(
            "background-color: #13191B; border: 1px solid #233032; border-radius: 8px; "
            "padding: 10px 12px; color: #7C8D8B; font-size: 12px;"
        )
        layout.addWidget(self.path_label)
        layout.addStretch()

        btn_row = QHBoxLayout()
        browse_btn = QPushButton("Select Music Folder")
        browse_btn.setObjectName("primaryBtn")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.clicked.connect(self.choose_folder)

        quit_btn = QPushButton("Exit")
        quit_btn.setObjectName("ghostBtn")
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.clicked.connect(self.reject)

        btn_row.addWidget(quit_btn)
        btn_row.addStretch()
        btn_row.addWidget(browse_btn)
        layout.addLayout(btn_row)

    def showEvent(self, event):
        super().showEvent(event)
        enable_windows_dark_titlebar(int(self.winId()))

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Music Folder")
        if folder:
            self.selected_folder = folder
            self.path_label.setText(folder)
            self.accept()
class NanoTuneApp(QMainWindow):
    fxDecodedReady = Signal(str, int, object, str)
    coverPreloaded = Signal(str, str, object, object, object)
    smtcButtonPressed = Signal(int)

    def __init__(self, startup_files: list[str] | None = None):
        super().__init__()
        self.cfg_dir = get_config_dir()
        self.covers_dir = get_covers_cache_dir()
        self.imported_lyrics_dir = get_imported_lyrics_dir()
        self.config_path = self.cfg_dir / "config.json"
        self.cache_path = self.cfg_dir / "library_cache.json"
        self.config = self.load_config()
        register_pc_appdata_registry(self.cfg_dir, self.config.get("music_folders", []))

        IMPORTED_LYRICS_MAP.clear()
        for k, v in (self.config.get("imported_lyrics") or {}).items():
            if k and v and Path(v).exists():
                IMPORTED_LYRICS_MAP[k] = v

        self.startup_files: list[str] = list(startup_files or [])
        self._ipc_server: QLocalServer | None = None

        self.all_tracks: list[dict] = []
        self.tracks: list[dict] = []
        self.albums_map: dict[str, dict] = {}
        self.active_album_filter: str = ""
        self.current_index: int = -1
        self.active_track_path: str = ""
        self.playback_generation_id: int = 0

        self.shuffle_enabled: bool = False
        self.shuffle_queue: list[int] = []
        self.shuffle_pos: int = -1

        self.repeat_mode: str = "album"
        self.is_playing: bool = False
        self.seeking_active: bool = False
        self.sidebar_collapsed: bool = False

        self.current_lyrics_raw: str = ""
        self.current_lyrics_source: str = ""
        self.is_synced_lyrics: bool = False
        self.parsed_lyrics: list[dict] = []
        self.active_lyric_row: int = -1
        self.editing_lyrics: bool = False

        self.accent_color: str = STAR_DEFAULT_YELLOW
        self.accent_color_2: str = STAR_DEFAULT_YELLOW_2
        self.active_font_family: str = self.resolve_configured_font()

        screen = QApplication.primaryScreen()
        self.monitor_hz: float = float(screen.refreshRate()) if (screen and screen.refreshRate() > 1.0) else 60.0
        self._screen_hooked: bool = False

        self.thumb_cache: dict[str, QPixmap] = {}
        self.album_cover_cache: dict[str, QPixmap] = {}
        self.album_unique_covers: dict[str, list[tuple[bytes, QImage]]] = {}
        self.album_cards_by_key: dict[str, AlbumCardWidget] = {}
        self.album_cards_list: list[AlbumCardWidget] = []
        self._last_albums_signature: tuple = ()
        self._last_albums_cols: int = 0
        self._preloader_stop = threading.Event()
        self.coverPreloaded.connect(self.on_cover_preloaded)

        self._smtc = None
        self._winrt_mp = None
        self._smtc_btn_handler = None
        self._smtc_token = None
        self._smtc_initialized = False
        self._smtc_last_thumb_sig = None
        self._smtc_thumb_ref = None
        self._smtc_keepalive_stream = None
        self.smtcButtonPressed.connect(self.on_smtc_button)

        self.app_icon = make_mario_star_icon(STAR_DEFAULT_YELLOW, 64)

        self.default_nano_pil: Image.Image = build_fallback_cover(STAR_DEFAULT_YELLOW)
        self.cover_pil: Image.Image = self.default_nano_pil
        self.cover_pixmap: QPixmap = QPixmap()
        self.blurred_pixmap: QPixmap = QPixmap()
        self._bg_scaled_cache: QPixmap = QPixmap()
        self._bg_cache_size: QSize = QSize()

        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(float(self.config.get("volume", 0.85)))

        self.player.positionChanged.connect(self.on_qt_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.playbackStateChanged.connect(self.on_qt_playback_state_changed)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)
        self.player.errorOccurred.connect(self.on_player_error)

        self.fx_active_engine: bool = False
        self.fx_speed: float = 1.0
        self.fx_pitch: float = 0.0
        self.fx_link_pitch: bool = True
        self.fx_bass: float = 0.0
        self.fx_reverb: float = 0.0
        self.fx_lofi: bool = False
        self.fx_decoded_path: str = ""
        self.fx_decoded_pcm = None
        self.fx_pending_activate: bool = False
        self.fx_pending_seek_ms: int = 0
        self._last_known_pos_ms: int = 0
        self._seek_guard_until: float = 0.0
        self._qt_ignore_state_until: float = 0.0
        self._pending_qt_seek_ms: int = -1
        self._pending_qt_play: bool = False

        self.fx_device = FXStreamDevice(self)
        self.fx_sink: QAudioSink | None = None
        self.fx_poll_timer = QTimer(self)
        self.fx_poll_timer.setInterval(max(6, min(33, int(round(1000.0 / max(60.0, self.monitor_hz))))))
        self.fx_poll_timer.timeout.connect(self.on_fx_poll)
        self.fxDecodedReady.connect(self.on_fx_decode_finished)

        self._fx_param_timer = QTimer(self)
        self._fx_param_timer.setSingleShot(True)
        self._fx_param_timer.setInterval(8)
        self._fx_param_timer.timeout.connect(self._apply_fx_params_debounced)

        self._no_wheel_filter = NoWheelEventFilter(self)
        self._space_filter = GlobalSpacePlayPauseFilter(self)
        QApplication.instance().installEventFilter(self._space_filter)

        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        target_w = max(780, min(1480, int(avail.width() * 0.86)))
        target_h = max(560, min(940, int(avail.height() * 0.90)))
        target_h = min(target_h, avail.height() - 34)

        self.setWindowTitle("NanoTune")
        self.setWindowIcon(self.app_icon)
        self.setMinimumSize(780, 560)
        self.resize(target_w, target_h)
        self.move(avail.x() + (avail.width() - target_w) // 2, avail.y() + max(4, (avail.height() - target_h) // 2))
        self.setAcceptDrops(True)

        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self.toggle_fullscreen)

        self.refresh_default_covers()
        self.build_ui()
        self.apply_mode_branding()
        self.setup_tray_icon()

        QTimer.singleShot(100, self.startup_check)

    def resolve_configured_font(self) -> str:
        idx = max(0, min(len(FONT_PRESETS) - 1, int(self.config.get("font_index", 0))))
        return resolve_font_family(FONT_PRESETS[idx][1])

    def load_config(self) -> dict:
        defaults = {
            "music_folders": [],
            "imported_files": [],
            "imported_lyrics": {},
            "asked_default_player": False,
            "adaptive_colors": True,
            "recursive_scan": True,
            "blur_radius": 34,
            "always_blur_bg": True,
            "lyric_font_size": 19,
            "font_index": 0,
            "volume": 0.85,
            "graphics_backend": SELECTED_BACKEND,
        }
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                data.pop("fourl_mode", None)
                if data.get("album_folder") and not data.get("music_folders"):
                    data["music_folders"] = [data["album_folder"]]
                defaults.update(data)
            except Exception:
                pass
        defaults["font_index"] = max(0, min(len(FONT_PRESETS) - 1, int(defaults.get("font_index", 0))))
        return defaults

    def save_config(self):
        try:
            self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
            register_pc_appdata_registry(self.cfg_dir, self.config.get("music_folders", []))
        except Exception:
            pass

    def pil_to_qpixmap(self, img: Image.Image) -> QPixmap:
        return QPixmap.fromImage(pil_to_qimage(img))

    def refresh_default_covers(self):
        self.default_nano_pil = build_fallback_cover(STAR_DEFAULT_YELLOW)
        self.thumb_cache["__DEFAULT__"] = self.pil_to_qpixmap(self.default_nano_pil.resize((64, 64), Image.Resampling.LANCZOS))
        self.album_cover_cache["__DEFAULT__"] = self.pil_to_qpixmap(self.default_nano_pil.resize((220, 220), Image.Resampling.LANCZOS))

        if not self.active_track_path and not self.is_playing:
            self.apply_cover_pil(self.default_nano_pil, force_default=True)

    def _is_yeat_4l_track(self, tr: dict) -> bool:
        if not tr:
            return False

        def _norm(s: str) -> str:
            s = unicodedata.normalize("NFKD", s or "")
            s = "".join(ch for ch in s if not unicodedata.combining(ch))
            return s.lower().strip()

        alb = _norm(tr.get("album", ""))
        if not re.search(r"\b4l\b", alb):
            return False
        art = _norm(f"{tr.get('artist', '')} {tr.get('album_artist', '')}")
        parent_dir = _norm(str(Path(tr.get("path", "")).parent)) if tr.get("path") else ""
        return alb == "4l" or "yeat" in art or "yeat" in parent_dir

    def apply_cover_pil(self, img: Image.Image, force_default: bool = False, is_4l: bool = False):
        self.cover_pil = img
        self.cover_pixmap = self.pil_to_qpixmap(img)
        self.rebuild_blurred_background()
        self.update_cover_widgets()

        if not hasattr(self, "sb_count_lbl"):
            return

        if force_default or not bool(self.config.get("adaptive_colors", True)):
            self.apply_theme_colors(STAR_DEFAULT_YELLOW, STAR_DEFAULT_YELLOW_2)
        elif is_4l:
            self.apply_theme_colors("#1ED760", "#5CF28D")
        else:
            ac1, ac2 = extract_theme_colors(img)
            self.apply_theme_colors(ac1, ac2)

    def get_track_thumb_pixmap(self, track_data: dict) -> QPixmap:
        t_key = track_data.get("track_cover_key", "")
        if t_key and t_key in self.thumb_cache:
            return self.thumb_cache[t_key]
        a_key = track_data.get("album_key", "")
        if a_key and a_key in self.thumb_cache:
            return self.thumb_cache[a_key]
        return self.thumb_cache.get("__DEFAULT__", QPixmap())

    def start_background_cover_preloader(self):
        self._preloader_stop.set()
        self._preloader_stop = threading.Event()
        jobs = []
        for tr in self.all_tracks:
            t_key = tr.get("track_cover_key", "")
            a_key = tr.get("album_key", "")
            if t_key and (t_key not in self.thumb_cache or a_key not in self.album_unique_covers):
                jobs.append((t_key, a_key, tr["path"]))
        if jobs:
            CoverPreloader(
                jobs,
                self.covers_dir,
                self._preloader_stop,
                lambda tk, ak, vec, q220, q64: self.coverPreloaded.emit(tk, ak, vec, q220, q64),
            ).start()

    def on_cover_preloaded(self, track_key: str, alb_key: str, cov_vec: bytes, q220: QImage, q64: QImage):
        pm64 = QPixmap.fromImage(q64)
        self.thumb_cache[track_key] = pm64

        if alb_key:
            if alb_key not in self.thumb_cache:
                self.thumb_cache[alb_key] = pm64

            entries = self.album_unique_covers.setdefault(alb_key, [])
            is_duplicate = any(covers_look_similar(existing_vec, cov_vec) for existing_vec, _ in entries)
            if not is_duplicate and len(entries) < 4:
                entries.append((cov_vec, q220))
                composed_qimg = compose_album_cover_qimage([img for _, img in entries], 220)
                pm220 = QPixmap.fromImage(composed_qimg) if not composed_qimg.isNull() else QPixmap.fromImage(q220)
                self.album_cover_cache[alb_key] = pm220
                card = self.album_cards_by_key.get(alb_key)
                if card is not None:
                    card.cover.set_pixmap(pm220)
            elif alb_key not in self.album_cover_cache:
                pm220 = QPixmap.fromImage(q220)
                self.album_cover_cache[alb_key] = pm220
                card = self.album_cards_by_key.get(alb_key)
                if card is not None:
                    card.cover.set_pixmap(pm220)

        if hasattr(self, "track_list") and self.stack.currentIndex() == 0:
            self.track_list.viewport().update()

    def rebuild_blurred_background(self):
        radius = int(self.config.get("blur_radius", 34))
        small = self.cover_pil.resize((420, 420), Image.Resampling.LANCZOS)
        blurred = small.filter(ImageFilter.GaussianBlur(radius=max(6, radius // 2)))
        darkened = ImageEnhance.Brightness(blurred).enhance(0.58)
        saturated = ImageEnhance.Color(darkened).enhance(1.25)
        self.blurred_pixmap = self.pil_to_qpixmap(saturated)
        self._bg_scaled_cache = QPixmap()
        self.update()

    def should_show_blurred_bg(self) -> bool:
        on_lyrics = hasattr(self, "stack") and self.stack.currentIndex() == 2
        return (
            self.is_playing
            or bool(self.active_track_path)
            or on_lyrics
            or self.editing_lyrics
            or bool(self.config.get("always_blur_bg", True))
        )

    def ensure_bg_cache(self) -> QPixmap:
        sz = self.size()
        if sz.width() <= 0 or sz.height() <= 0:
            return QPixmap()
        if not self._bg_scaled_cache.isNull() and self._bg_cache_size == sz:
            return self._bg_scaled_cache

        pm = QPixmap(sz)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.should_show_blurred_bg() and not self.blurred_pixmap.isNull():
            scaled = self.blurred_pixmap.scaled(
                sz,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (sz.width() - scaled.width()) // 2
            y = (sz.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
            ac = QColor(self.accent_color)
            grad = QLinearGradient(0, 0, sz.width(), sz.height())
            grad.setColorAt(0.0, QColor(8, 11, 12, 98))
            grad.setColorAt(0.5, QColor(ac.red() // 11, ac.green() // 11, ac.blue() // 11, 78))
            grad.setColorAt(1.0, QColor(6, 9, 10, 140))
            p.fillRect(QRect(0, 0, sz.width(), sz.height()), grad)
        else:
            grad = QLinearGradient(0, 0, sz.width(), sz.height())
            grad.setColorAt(0.0, QColor("#0B0D0E"))
            grad.setColorAt(0.55, QColor("#0F1416"))
            grad.setColorAt(1.0, QColor("#090E10"))
            p.fillRect(QRect(0, 0, sz.width(), sz.height()), grad)
        p.end()

        self._bg_scaled_cache = pm
        self._bg_cache_size = sz
        return pm

    def _mime_has_supported_drops(self, mime) -> bool:
        if not mime.hasUrls():
            return False
        for url in mime.urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.is_dir() or p.suffix.lower() in AUDIO_EXTENSIONS or p.suffix.lower() in LYRIC_EXTENSIONS:
                    return True
        return False

    def dragEnterEvent(self, event):
        if self._mime_has_supported_drops(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._mime_has_supported_drops(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                self.handle_dropped_paths(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        bg = self.ensure_bg_cache()
        if not bg.isNull():
            painter.drawPixmap(0, 0, bg)
        super().paintEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        enable_windows_dark_titlebar(int(self.winId()))
        self.position_sidebar_toggle()
        self.init_windows_smtc()
        if not self._screen_hooked and self.windowHandle() is not None:
            self._screen_hooked = True
            self.windowHandle().screenChanged.connect(self._on_screen_changed)
            self._on_screen_changed(self.windowHandle().screen())

    def _on_screen_changed(self, screen):
        if screen is None:
            return
        hz = float(screen.refreshRate()) if screen.refreshRate() > 1.0 else 60.0
        if abs(hz - self.monitor_hz) > 0.5:
            self.monitor_hz = hz
            self.lyrics_view.set_refresh_rate(hz)
            self.fx_poll_timer.setInterval(max(6, min(33, int(round(1000.0 / max(60.0, hz))))))

    def position_sidebar_toggle(self):
        if hasattr(self, "sb_toggle_btn") and hasattr(self, "sidebar"):
            self.sb_toggle_btn.move(self.sidebar.maximumWidth() - 13, 25)
            self.sb_toggle_btn.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bg_scaled_cache = QPixmap()
        self.position_sidebar_toggle()
        self.update_responsive_layouts()

    def closeEvent(self, event):
        if hasattr(self, "_space_filter") and QApplication.instance() is not None:
            QApplication.instance().removeEventFilter(self._space_filter)
        if hasattr(self, "_ipc_server") and self._ipc_server is not None:
            try:
                self._ipc_server.close()
            except Exception:
                pass
        self._preloader_stop.set()
        self.stop_fx_sink()
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()
        super().closeEvent(event)

    # -----------------------------------------------------------------------
    # Windows 10/11 SMTC (Volume Overlay + Media Keys) & System Tray Controls
    # -----------------------------------------------------------------------
    def init_windows_smtc(self):
        if not HAS_WINRT_SMTC or self._smtc_initialized or sys.platform != "win32" or IS_LEGACY_WIN:
            return
        smtc = None
        if winrt_smtc_for_window is not None:
            try:
                hwnd = int(self.winId())
                if hwnd:
                    smtc = winrt_smtc_for_window(hwnd)
            except Exception:
                smtc = None
        if smtc is None and WinRtMediaPlayer is not None:
            try:
                self._winrt_mp = WinRtMediaPlayer()
                self._winrt_mp.command_manager.is_enabled = False
                smtc = self._winrt_mp.system_media_transport_controls
            except Exception:
                smtc = None
        if smtc is None:
            return

        try:
            self._smtc_initialized = True
            smtc.is_enabled = True
            smtc.is_play_enabled = True
            smtc.is_pause_enabled = True
            smtc.is_next_enabled = True
            smtc.is_previous_enabled = True
            smtc.is_stop_enabled = True

            def _on_btn_pressed(_sender, args):
                try:
                    btn = getattr(args, "button", None)
                    val = int(btn.value) if hasattr(btn, "value") else int(btn)
                    self.smtcButtonPressed.emit(val)
                except Exception:
                    pass

            self._smtc_btn_handler = _on_btn_pressed
            self._smtc_token = smtc.add_button_pressed(self._smtc_btn_handler)
            self._smtc = smtc
            self.update_windows_smtc()
        except Exception:
            self._smtc = None

    def on_smtc_button(self, btn_val: int):
        if not HAS_WINRT_SMTC:
            return
        if btn_val in (int(SystemMediaTransportControlsButton.PLAY), int(SystemMediaTransportControlsButton.PAUSE)):
            self.toggle_play_pause()
        elif btn_val == int(SystemMediaTransportControlsButton.STOP):
            if self.is_playing:
                self.toggle_play_pause()
        elif btn_val == int(SystemMediaTransportControlsButton.NEXT):
            self.play_next()
        elif btn_val == int(SystemMediaTransportControlsButton.PREVIOUS):
            self.play_previous()

    def _get_active_track_dict(self) -> dict | None:
        if 0 <= self.current_index < len(self.tracks):
            return self.tracks[self.current_index]
        if self.active_track_path:
            for t in self.all_tracks:
                if t["path"] == self.active_track_path:
                    return t
        return None

    def _await_winrt_op(self, op, timeout_s: float = 0.5):
        deadline = time.monotonic() + timeout_s
        while int(op.status) == 0 and time.monotonic() < deadline:
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 5)
            time.sleep(0.001)
        return op.get_results()

    def _build_smtc_stream_reference(self, img: Image.Image):
        """Creates a RandomAccessStreamReference for the Windows 10/11 SMTC media overlay."""
        buf = BytesIO()
        img.convert("RGB").resize((300, 300), Image.Resampling.LANCZOS).save(buf, format="JPEG", quality=92)
        raw_bytes = buf.getvalue()

        try:
            stream = InMemoryRandomAccessStream()
            writer = DataWriter(stream.get_output_stream_at(0))
            writer.write_bytes(raw_bytes)
            self._await_winrt_op(writer.store_async())
            self._await_winrt_op(writer.flush_async())
            writer.detach_stream()
            stream.seek(0)
            self._smtc_keepalive_stream = stream
            return RandomAccessStreamReference.create_from_stream(stream)
        except Exception:
            pass

        thumb_path = self.cfg_dir / "smtc_cover.jpg"
        try:
            thumb_path.write_bytes(raw_bytes)
        except Exception:
            return None

        if StorageFile is not None:
            try:
                sf = self._await_winrt_op(StorageFile.get_file_from_path_async(str(thumb_path.resolve())))
                if sf is not None:
                    return RandomAccessStreamReference.create_from_file(sf)
            except Exception:
                pass

        if WinRtUri is not None:
            try:
                return RandomAccessStreamReference.create_from_uri(WinRtUri(thumb_path.resolve().as_uri()))
            except Exception:
                pass

        return None

    def update_windows_smtc(self):
        if not HAS_WINRT_SMTC or IS_LEGACY_WIN:
            return
        if self._smtc is None and not self._smtc_initialized:
            self.init_windows_smtc()
        if self._smtc is None:
            return
        try:
            tr = self._get_active_track_dict()
            if tr is not None:
                self._smtc.is_enabled = True
                self._smtc.is_play_enabled = True
                self._smtc.is_pause_enabled = True
                self._smtc.is_next_enabled = len(self.tracks) > 0
                self._smtc.is_previous_enabled = len(self.tracks) > 0
                self._smtc.is_stop_enabled = True

                updater = self._smtc.display_updater
                updater.type = MediaPlaybackType.MUSIC
                updater.music_properties.title = tr["display_title"]
                updater.music_properties.artist = tr["artist"]
                updater.music_properties.album_title = tr.get("album", "")

                thumb_sig = (tr["path"], id(self.cover_pil))
                if self._smtc_last_thumb_sig != thumb_sig:
                    self._smtc_last_thumb_sig = thumb_sig
                    self._smtc_thumb_ref = self._build_smtc_stream_reference(self.cover_pil)

                if self._smtc_thumb_ref is not None:
                    updater.thumbnail = self._smtc_thumb_ref
                updater.update()

                self._smtc.playback_status = (
                    MediaPlaybackStatus.PLAYING if self.is_playing else MediaPlaybackStatus.PAUSED
                )
            else:
                self._smtc.is_next_enabled = False
                self._smtc.is_previous_enabled = False
                self._smtc.playback_status = MediaPlaybackStatus.STOPPED
        except Exception:
            pass

    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_menu = QMenu()
        self.tray_menu.setStyleSheet("""
            QMenu {
                background-color: #121718; color: #EAF0EE;
                border: 1px solid #2A3C38; padding: 6px;
            }
            QMenu::item { padding: 7px 18px; border-radius: 5px; }
            QMenu::item:selected { background-color: #2A3C38; color: #FFFFFF; }
            QMenu::separator { height: 1px; background: #233330; margin: 4px 8px; }
        """)

        self.tray_now_playing_act = QAction("NanoTune • Idle", self)
        self.tray_now_playing_act.setEnabled(False)

        self.tray_play_act = QAction(make_vector_icon("play", "#FFFFFF", 16), "Play / Pause", self)
        self.tray_play_act.triggered.connect(self.toggle_play_pause)

        self.tray_prev_act = QAction(make_vector_icon("prev", "#FFFFFF", 16), "Previous Track", self)
        self.tray_prev_act.triggered.connect(self.play_previous)

        self.tray_next_act = QAction(make_vector_icon("next", "#FFFFFF", 16), "Next Track", self)
        self.tray_next_act.triggered.connect(self.play_next)

        self.tray_tracks_menu = QMenu("Change Song", self)
        self.tray_tracks_menu.setStyleSheet(self.tray_menu.styleSheet())

        self.tray_show_act = QAction("Open Window", self)
        self.tray_show_act.triggered.connect(self.restore_from_tray)

        self.tray_quit_act = QAction("Exit", self)
        self.tray_quit_act.triggered.connect(QApplication.quit)

        self.tray_menu.addAction(self.tray_now_playing_act)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.tray_play_act)
        self.tray_menu.addAction(self.tray_prev_act)
        self.tray_menu.addAction(self.tray_next_act)
        self.tray_menu.addMenu(self.tray_tracks_menu)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.tray_show_act)
        self.tray_menu.addAction(self.tray_quit_act)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()
        self.refresh_tray_status()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_from_tray()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.tray_menu.popup(QCursor.pos())

    def restore_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def refresh_tray_status(self):
        if not hasattr(self, "tray_icon"):
            return
        app_label = "NanoTune"
        tr = self._get_active_track_dict()
        if tr is not None:
            prefix = "Playing" if self.is_playing else "Paused"
            status_txt = f"{prefix}: {tr['display_title']} — {tr['artist']}"
            self.tray_now_playing_act.setText(status_txt)
            self.tray_play_act.setText("Pause" if self.is_playing else "Play")
            self.tray_play_act.setIcon(make_vector_icon("pause" if self.is_playing else "play", "#FFFFFF", 16))
            self.tray_icon.setToolTip(f"{status_txt}\n{app_label}")
            self.setWindowTitle(f"{tr['display_title']} • {tr['artist']} — {app_label}")
        else:
            self.tray_now_playing_act.setText(f"{app_label} • Ready")
            self.tray_icon.setToolTip(app_label)
            self.setWindowTitle(app_label)
        self.update_windows_smtc()

    def rebuild_tray_track_submenu(self):
        if not hasattr(self, "tray_tracks_menu"):
            return
        self.tray_tracks_menu.clear()
        for idx, tr in enumerate(self.tracks[:40]):
            num = f"{tr['display_num']:02d}" if tr.get("display_num", 0) > 0 else "•"
            act = QAction(f"{num}  {tr['display_title']}", self)
            act.triggered.connect(lambda _c=False, i=idx: self.load_and_play(i))
            self.tray_tracks_menu.addAction(act)

    # -----------------------------------------------------------------------
    # UI Construction
    # -----------------------------------------------------------------------
    def attach_clear_action(self, line_edit: QLineEdit):
        clear_act = line_edit.addAction(make_vector_icon("close", "#9EB0AD", 14), QLineEdit.ActionPosition.TrailingPosition)
        clear_act.setToolTip("Clear search")
        clear_act.setVisible(False)

        def _on_clear_clicked():
            line_edit.clear()
            line_edit.clearFocus()
            self.setFocus(Qt.FocusReason.OtherFocusReason)

        clear_act.triggered.connect(_on_clear_clicked)
        line_edit.textChanged.connect(lambda txt, act=clear_act: act.setVisible(bool(txt)))

        for tb in line_edit.findChildren(QToolButton):
            tb.setCursor(Qt.CursorShape.PointingHandCursor)

        return clear_act

    def build_ui(self):
        root = QWidget()
        root.setObjectName("rootWidget")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(240)
        sb_lay = QVBoxLayout(self.sidebar)
        sb_lay.setContentsMargins(16, 18, 16, 16)
        sb_lay.setSpacing(6)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(0)
        self.brand_badge = QLabel()
        self.brand_badge.setObjectName("brandBadge")
        self.brand_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_badge.setFixedSize(40, 40)

        self.brand_text_wrap = QWidget()
        self.brand_text_wrap.setMinimumWidth(0)
        self.bt_lay = QVBoxLayout(self.brand_text_wrap)
        self.bt_lay.setContentsMargins(10, 0, 0, 0)
        self.bt_lay.setSpacing(1)
        self.lbl_app = QLabel("NANOTUNE")
        self.lbl_app.setObjectName("brandTitle")
        self.lbl_app.setMinimumWidth(0)
        self.lbl_brand_sub = QLabel("MUSIC PLAYER")
        self.lbl_brand_sub.setObjectName("brandSub")
        self.lbl_brand_sub.setMinimumWidth(0)
        self.bt_lay.addWidget(self.lbl_app)
        self.bt_lay.addWidget(self.lbl_brand_sub)

        self.sb_toggle_btn = QPushButton(root)
        self.sb_toggle_btn.setObjectName("sbToggleBtn")
        self.sb_toggle_btn.setFixedSize(26, 26)
        self.sb_toggle_btn.setIconSize(QSize(14, 14))
        self.sb_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sb_toggle_btn.setToolTip("Collapse / Expand Sidebar")
        self.sb_toggle_btn.clicked.connect(self.toggle_sidebar)

        brand_row.addWidget(self.brand_badge, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        brand_row.addWidget(self.brand_text_wrap, 1)
        sb_lay.addLayout(brand_row)
        sb_lay.addSpacing(14)

        self.nav_buttons: list[tuple[QPushButton, str, str]] = []
        nav_defs = [
            ("Tracks", "music", 0),
            ("Albums", "albums", 1),
            ("Now Playing && Lyrics", "lyrics", 2),
            ("Settings", "settings", 3),
            ("About", "info", 4),
        ]
        for label, icon_name, idx in nav_defs:
            btn = QPushButton(f"  {label}")
            btn.setObjectName("navBtn")
            btn.setMinimumWidth(0)
            btn.setCheckable(True)
            btn.setIconSize(QSize(18, 18))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, i=idx: self.switch_page(i))
            sb_lay.addWidget(btn)
            self.nav_buttons.append((btn, icon_name, label))

        sb_lay.addStretch()

        self.sb_status_card = QFrame()
        self.sb_status_card.setObjectName("sbStatusCard")
        self.sb_status_card.setMinimumWidth(0)
        sc_lay = QVBoxLayout(self.sb_status_card)
        sc_lay.setContentsMargins(12, 12, 12, 12)
        sc_lay.setSpacing(4)
        self.sb_count_lbl = QLabel("0 tracks loaded")
        self.sb_count_lbl.setMinimumWidth(0)
        self.sb_folder_lbl = QLabel("No folders selected")
        self.sb_folder_lbl.setMinimumWidth(0)
        self.sb_folder_lbl.setStyleSheet("color: #889896; font-size: 11px;")
        sc_lay.addWidget(self.sb_count_lbl)
        sc_lay.addWidget(self.sb_folder_lbl)
        sb_lay.addWidget(self.sb_status_card)

        body.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("mainStack")
        self.stack.addWidget(self.build_library_page())
        self.stack.addWidget(self.build_albums_page())
        self.stack.addWidget(self.build_lyrics_page())
        self.stack.addWidget(self.build_settings_page())
        self.stack.addWidget(self.build_about_page())
        body.addWidget(self.stack, 1)

        outer.addLayout(body, 1)

        self.fx_drawer = self.build_fx_drawer()
        self.fx_drawer.setVisible(False)
        outer.addWidget(self.fx_drawer)

        outer.addWidget(self.build_player_bar())
        self.position_sidebar_toggle()

        for btn in self.findChildren(QPushButton):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setAutoDefault(False)
            btn.setDefault(False)

        self.update_cover_widgets()
        self.switch_page(0)

    def build_library_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 14)
        lay.setSpacing(14)

        header = QFrame()
        header.setObjectName("glassCard")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 18, 20, 18)
        h_lay.setSpacing(20)

        self.lib_cover_widget = RoundedCoverWidget(radius=12, border_width=2.0)
        self.lib_cover_widget.setFixedSize(128, 128)
        h_lay.addWidget(self.lib_cover_widget)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        self.lib_tag_lbl = QLabel("LIBRARY  •  ALL TRACKS")
        self.lib_tag_lbl.setObjectName("accentTag")
        self.lib_title_lbl = QLabel("All Tracks")
        self.lib_title_lbl.setStyleSheet("color: #FFFFFF; font-size: 34px; font-weight: 900;")
        self.lib_artist_lbl = QLabel("0 tracks  •  0 albums  •  0 folders")
        self.lib_artist_lbl.setStyleSheet("color: #C7D4D2; font-size: 13px; font-weight: 600;")

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.play_all_btn = QPushButton(" Play")
        self.play_all_btn.setObjectName("primaryBtn")
        self.play_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_all_btn.clicked.connect(self.play_from_start)

        self.shuffle_all_btn = QPushButton(" Shuffle")
        self.shuffle_all_btn.setObjectName("secondaryBtn")
        self.shuffle_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shuffle_all_btn.clicked.connect(self.play_shuffled)

        self.rescan_btn = QPushButton(" Rescan")
        self.rescan_btn.setObjectName("secondaryBtn")
        self.rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rescan_btn.clicked.connect(self.rescan_album)

        self.clear_album_btn = QPushButton("Show All Albums")
        self.clear_album_btn.setObjectName("secondaryBtn")
        self.clear_album_btn.setVisible(False)
        self.clear_album_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_album_btn.clicked.connect(self.clear_album_filter)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search tracks, artists, albums...")
        self.search_input.setMinimumWidth(160)
        self.search_input.setMaximumWidth(270)
        self.attach_clear_action(self.search_input)
        self.search_input.textChanged.connect(self.populate_track_list)

        action_row.addWidget(self.play_all_btn)
        action_row.addWidget(self.shuffle_all_btn)
        action_row.addWidget(self.rescan_btn)
        action_row.addWidget(self.clear_album_btn)
        action_row.addStretch()
        action_row.addWidget(self.search_input)

        info_col.addWidget(self.lib_tag_lbl)
        info_col.addWidget(self.lib_title_lbl)
        info_col.addWidget(self.lib_artist_lbl)
        info_col.addSpacing(6)
        info_col.addLayout(action_row)
        h_lay.addLayout(info_col, 1)

        lay.addWidget(header)

        self.track_list = QListWidget()
        self.track_list.setObjectName("trackList")
        self.track_list.setAcceptDrops(False)
        self.track_list.setMouseTracking(True)
        self.track_list.setUniformItemSizes(True)
        self.track_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.track_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.track_list.setItemDelegate(
            TrackRowDelegate(
                self.get_track_thumb_pixmap,
                lambda: (self.accent_color, self.accent_color_2),
                lambda: self.active_font_family,
                self.track_list,
            )
        )
        self.track_list.itemDoubleClicked.connect(self.on_track_double_clicked)
        lay.addWidget(self.track_list, 1)

        return page

    def build_albums_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 14)
        lay.setSpacing(14)

        top_row = QHBoxLayout()
        hdr = QLabel("Albums")
        hdr.setStyleSheet("color: #FFFFFF; font-size: 28px; font-weight: 800;")
        self.album_search_input = QLineEdit()
        self.album_search_input.setPlaceholderText("Search albums or artists...")
        self.album_search_input.setFixedWidth(260)
        self.attach_clear_action(self.album_search_input)
        self.album_search_input.textChanged.connect(lambda: self.populate_albums_grid(force_rebuild=True))
        top_row.addWidget(hdr)
        top_row.addStretch()
        top_row.addWidget(self.album_search_input)
        lay.addLayout(top_row)

        self.albums_scroll = QScrollArea()
        self.albums_scroll.setObjectName("transparentScroll")
        self.albums_scroll.setAcceptDrops(False)
        self.albums_scroll.setWidgetResizable(True)
        self.albums_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.albums_container = QWidget()
        self.albums_container.setObjectName("albumsContainer")
        self.albums_container.setStyleSheet("QWidget#albumsContainer { background: transparent; }")
        self.albums_grid = QGridLayout(self.albums_container)
        self.albums_grid.setSpacing(16)
        self.albums_grid.setContentsMargins(2, 4, 8, 12)
        self.albums_scroll.setWidget(self.albums_container)

        lay.addWidget(self.albums_scroll, 1)
        return page

    def build_lyrics_page(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(20)

        left_card = QFrame()
        left_card.setObjectName("glassCard")
        left_card.setFixedWidth(325)
        card_outer = QVBoxLayout(left_card)
        card_outer.setContentsMargins(6, 10, 6, 10)

        self.np_scroll = QScrollArea()
        self.np_scroll.setObjectName("transparentScroll")
        self.np_scroll.setAcceptDrops(False)
        self.np_scroll.setWidgetResizable(True)
        self.np_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.np_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        np_inner = QWidget()
        np_inner.setObjectName("npInner")
        np_inner.setStyleSheet("QWidget#npInner { background: transparent; }")
        lc_lay = QVBoxLayout(np_inner)
        lc_lay.setContentsMargins(14, 10, 14, 10)
        lc_lay.setSpacing(10)

        self.np_cover_widget = RoundedCoverWidget(radius=14, border_width=2.0)
        self.np_cover_widget.setFixedSize(268, 268)
        lc_lay.addWidget(self.np_cover_widget, alignment=Qt.AlignmentFlag.AlignCenter)

        self.np_title_lbl = QLabel("Nothing Playing")
        self.np_title_lbl.setWordWrap(True)
        self.np_title_lbl.setMaximumHeight(54)
        self.np_title_lbl.setStyleSheet("color: #FFFFFF; font-size: 19px; font-weight: 800;")

        self.np_artist_lbl = QLabel("Select a track to play")
        self.np_artist_lbl.setWordWrap(True)
        self.np_artist_lbl.setMaximumHeight(38)
        self.np_artist_lbl.setStyleSheet("color: #B4C2C0; font-size: 12px;")

        lc_lay.addWidget(self.np_title_lbl)
        lc_lay.addWidget(self.np_artist_lbl)

        details_box = QFrame()
        details_box.setObjectName("detailsCard")
        grid = QGridLayout(details_box)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)

        self.detail_labels: dict[str, QLabel] = {}
        fields = [
            ("Format", "format"),
            ("Audio", "audio"),
            ("Bitrate", "bitrate"),
            ("Size", "size"),
            ("Album", "album"),
            ("Lyrics", "lyrics"),
        ]
        for row_i, (label_txt, key) in enumerate(fields):
            k_lbl = QLabel(label_txt)
            k_lbl.setStyleSheet("color: #849794; font-size: 11px; font-weight: 700;")
            v_lbl = QLabel("—")
            v_lbl.setStyleSheet("color: #E5EFED; font-size: 11px; font-weight: 600;")
            grid.addWidget(k_lbl, row_i, 0, Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(v_lbl, row_i, 1, Qt.AlignmentFlag.AlignVCenter)
            self.detail_labels[key] = v_lbl
        grid.setColumnStretch(1, 1)

        lc_lay.addWidget(details_box)
        lc_lay.addStretch()

        self.edit_lyrics_btn = QPushButton(" Enter / Edit Lyrics")
        self.edit_lyrics_btn.setObjectName("secondaryBtn")
        self.edit_lyrics_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_lyrics_btn.clicked.connect(self.toggle_lyrics_editor)
        lc_lay.addWidget(self.edit_lyrics_btn)

        self.np_scroll.setWidget(np_inner)
        card_outer.addWidget(self.np_scroll)
        lay.addWidget(left_card)

        right_card = QFrame()
        right_card.setObjectName("glassCard")
        rc_lay = QVBoxLayout(right_card)
        rc_lay.setContentsMargins(20, 16, 20, 16)
        rc_lay.setSpacing(10)

        hdr_row = QHBoxLayout()
        self.lyrics_mode_lbl = QLabel("LYRICS")
        self.lyrics_mode_lbl.setObjectName("accentTag")
        hdr_row.addWidget(self.lyrics_mode_lbl)
        hdr_row.addStretch()
        rc_lay.addLayout(hdr_row)

        self.lyrics_stack = QStackedWidget()

        self.lyrics_view = AnimatedLyricsWidget(refresh_hz=self.monitor_hz)
        self.lyrics_view.set_font_size(int(self.config.get("lyric_font_size", 19)))
        self.lyrics_view.set_font_family(self.active_font_family)
        self.lyrics_view.set_backdrop_fn(
            lambda: (self.ensure_bg_cache(), self.lyrics_view.mapTo(self, QRect(0, 0, 1, 1).topLeft()))
        )
        self.lyrics_view.lineClicked.connect(self.seek_to_ms)
        self.lyrics_stack.addWidget(self.lyrics_view)

        editor_wrap = QWidget()
        ed_lay = QVBoxLayout(editor_wrap)
        ed_lay.setContentsMargins(0, 0, 0, 0)
        ed_lay.setSpacing(10)

        tip = QLabel(
            "Enter plain lyrics, standard .LRC lines, or Enhanced .ELRC word timestamps (<mm:ss.xxx>). "
            "Use 'Stamp Current Time' while playing to sync."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #AFC0BE; font-size: 12px;")
        ed_lay.addWidget(tip)

        self.lyrics_editor = QTextEdit()
        self.lyrics_editor.setObjectName("lyricsEditor")
        self.lyrics_editor.setPlaceholderText("Paste or type lyrics here...")
        ed_lay.addWidget(self.lyrics_editor, 1)

        ed_btns = QHBoxLayout()
        self.stamp_btn = QPushButton(" Stamp Current Time")
        self.stamp_btn.setObjectName("secondaryBtn")
        self.stamp_btn.clicked.connect(self.stamp_current_time)

        self.save_lrc_btn = QPushButton(" Save .LRC / .ELRC")
        self.save_lrc_btn.setObjectName("primaryBtn")
        self.save_lrc_btn.clicked.connect(lambda: self.save_edited_lyrics(to_lrc=True, to_tag=False))

        self.save_flac_btn = QPushButton(" Embed in Tag")
        self.save_flac_btn.setObjectName("primaryBtn")
        self.save_flac_btn.clicked.connect(lambda: self.save_edited_lyrics(to_lrc=False, to_tag=True))

        save_both_btn = QPushButton("Save Both")
        save_both_btn.setObjectName("secondaryBtn")
        save_both_btn.clicked.connect(lambda: self.save_edited_lyrics(to_lrc=True, to_tag=True))

        ed_btns.addWidget(self.stamp_btn)
        ed_btns.addStretch()
        ed_btns.addWidget(self.save_lrc_btn)
        ed_btns.addWidget(self.save_flac_btn)
        ed_btns.addWidget(save_both_btn)
        ed_lay.addLayout(ed_btns)

        self.lyrics_stack.addWidget(editor_wrap)
        rc_lay.addWidget(self.lyrics_stack, 1)

        lay.addWidget(right_card, 1)
        return page

    def build_settings_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("transparentScroll")
        scroll.setAcceptDrops(False)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        page = QWidget()
        page.setObjectName("settingsPage")
        page.setStyleSheet("QWidget#settingsPage { background: transparent; }")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        top_hdr = QHBoxLayout()
        title = QLabel("Settings")
        title.setStyleSheet("color: #FFFFFF; font-size: 28px; font-weight: 800;")
        self.fs_btn = QPushButton(" Fullscreen (F11)")
        self.fs_btn.setObjectName("secondaryBtn")
        self.fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fs_btn.clicked.connect(self.toggle_fullscreen)
        top_hdr.addWidget(title)
        top_hdr.addStretch()
        top_hdr.addWidget(self.fs_btn)
        lay.addLayout(top_hdr)

        card = QFrame()
        card.setObjectName("glassCard")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(24, 20, 24, 20)
        c_lay.setSpacing(14)

        f_hdr = QHBoxLayout()
        f_lbl = QLabel("Music Directories (Multiple Folders Supported)")
        f_lbl.setStyleSheet("color: #EAF0EE; font-size: 14px; font-weight: 700;")
        add_dir_btn = QPushButton("+ Add Folder")
        add_dir_btn.setObjectName("primaryBtn")
        add_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_dir_btn.clicked.connect(self.add_music_folder)
        rem_dir_btn = QPushButton("Remove Selected")
        rem_dir_btn.setObjectName("secondaryBtn")
        rem_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rem_dir_btn.clicked.connect(self.remove_selected_folder)
        f_hdr.addWidget(f_lbl)
        f_hdr.addStretch()
        f_hdr.addWidget(rem_dir_btn)
        f_hdr.addWidget(add_dir_btn)
        c_lay.addLayout(f_hdr)

        self.folders_list = QListWidget()
        self.folders_list.setObjectName("foldersList")
        self.folders_list.setAcceptDrops(False)
        self.folders_list.setFixedHeight(88)
        for fd in self.config.get("music_folders", []):
            self.folders_list.addItem(fd)
        c_lay.addWidget(self.folders_list)

        self.chk_adaptive_colors = QCheckBox("Adaptive UI && Star Colors (auto-theme from album covers, B&W -> silver-grey)")
        self.chk_adaptive_colors.setChecked(bool(self.config.get("adaptive_colors", True)))
        self.chk_adaptive_colors.toggled.connect(self.on_settings_changed)
        c_lay.addWidget(self.chk_adaptive_colors)

        self.chk_recursive = QCheckBox("Scan subfolders inside music directories")
        self.chk_recursive.setChecked(bool(self.config.get("recursive_scan", True)))
        self.chk_recursive.toggled.connect(self.on_settings_changed)
        c_lay.addWidget(self.chk_recursive)

        self.chk_always_blur = QCheckBox("Always keep blurred album cover background active")
        self.chk_always_blur.setChecked(bool(self.config.get("always_blur_bg", True)))
        self.chk_always_blur.toggled.connect(self.on_settings_changed)
        c_lay.addWidget(self.chk_always_blur)

        font_pick_row = QHBoxLayout()
        fp_lbl = QLabel("App & Lyrics Font (15 Built-In Windows Presets):")
        fp_lbl.setStyleSheet("color: #D2DFDD; font-size: 13px; font-weight: 600;")
        self.font_combo = QComboBox()
        self.font_combo.installEventFilter(self._no_wheel_filter)
        for i, (label, cands) in enumerate(FONT_PRESETS):
            self.font_combo.addItem(label)
            self.font_combo.setItemData(i, QFont(resolve_font_family(cands), 11), Qt.ItemDataRole.FontRole)
        self.font_combo.setCurrentIndex(int(self.config.get("font_index", 0)))
        self.font_combo.currentIndexChanged.connect(self.on_font_preset_changed)
        font_pick_row.addWidget(fp_lbl)
        font_pick_row.addWidget(self.font_combo, 1)
        c_lay.addLayout(font_pick_row)

        blur_row = QHBoxLayout()
        blur_lbl = QLabel("Background Blur Intensity:")
        blur_lbl.setStyleSheet("color: #D2DFDD; font-size: 13px;")
        self.blur_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.blur_slider.installEventFilter(self._no_wheel_filter)
        self.blur_slider.setRange(10, 66)
        self.blur_slider.setValue(int(self.config.get("blur_radius", 34)))
        self.blur_slider.valueChanged.connect(self.on_blur_slider_changed)
        blur_row.addWidget(blur_lbl)
        blur_row.addWidget(self.blur_slider, 1)
        c_lay.addLayout(blur_row)

        font_row = QHBoxLayout()
        font_lbl = QLabel("Lyrics Font Size:")
        font_lbl.setStyleSheet("color: #D2DFDD; font-size: 13px;")
        self.font_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.font_slider.installEventFilter(self._no_wheel_filter)
        self.font_slider.setRange(14, 28)
        self.font_slider.setValue(int(self.config.get("lyric_font_size", 19)))
        self.font_slider.valueChanged.connect(self.on_font_slider_changed)
        font_row.addWidget(font_lbl)
        font_row.addWidget(self.font_slider, 1)
        c_lay.addLayout(font_row)

        gfx_lbl = QLabel("Graphics Renderer (Auto-detects on 1st launch)")
        gfx_lbl.setStyleSheet("color: #EAF0EE; font-size: 14px; font-weight: 700;")
        c_lay.addWidget(gfx_lbl)

        gfx_row = QHBoxLayout()
        self.gfx_combo = QComboBox()
        self.gfx_combo.installEventFilter(self._no_wheel_filter)
        dx_label = (
            "DirectX 10.0 Fallback (Windows D3D Feature Level 10_0)"
            if DX_LEVEL == "10.0"
            else "DirectX 10.1 (Windows D3D Feature Level 10_1)"
        )
        gl_label = (
            "OpenGL 2.0 Fallback (Hardware Desktop OpenGL)"
            if GL_VER_TUPLE == (2, 0)
            else "OpenGL 2.1 (Hardware Desktop OpenGL)"
        )
        self.gfx_combo.addItem(dx_label, "dx10_1")
        self.gfx_combo.addItem(gl_label, "opengl21")
        self.gfx_combo.addItem("Software Rendering (Native CPU Rasterizer)", "software")
        cur_idx = self.gfx_combo.findData(self.config.get("graphics_backend", SELECTED_BACKEND))
        if cur_idx >= 0:
            self.gfx_combo.setCurrentIndex(cur_idx)

        apply_gfx_btn = QPushButton("Apply & Restart")
        apply_gfx_btn.setObjectName("secondaryBtn")
        apply_gfx_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_gfx_btn.clicked.connect(self.apply_graphics_backend)
        gfx_row.addWidget(self.gfx_combo, 1)
        gfx_row.addWidget(apply_gfx_btn)
        c_lay.addLayout(gfx_row)

        diag_lbl = QLabel(
            f"Status: {describe_graphics_backend(SELECTED_BACKEND)}  •  Display: {int(round(self.monitor_hz))} Hz  •  Saved: {self.cfg_dir}"
        )
        diag_lbl.setObjectName("accentTag")
        diag_lbl.setWordWrap(True)
        c_lay.addWidget(diag_lbl)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)
        reset_btn = QPushButton("Reset Setup && Clear Folders")
        reset_btn.setObjectName("secondaryBtn")
        reset_btn.setFixedWidth(220)
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(self.reset_setup)

        self.make_default_btn = QPushButton("Set as Default Music Player")
        self.make_default_btn.setObjectName("primaryBtn")
        self.make_default_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.make_default_btn.clicked.connect(self.make_default_music_player)

        bottom_row.addWidget(reset_btn)
        bottom_row.addWidget(self.make_default_btn)
        bottom_row.addStretch()
        c_lay.addLayout(bottom_row)

        lay.addWidget(card)
        lay.addStretch()
        scroll.setWidget(page)
        return scroll

    def build_about_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        card = QFrame()
        card.setObjectName("glassCard")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(28, 28, 28, 28)
        c_lay.setSpacing(16)

        top = QHBoxLayout()
        self.about_cover_widget = RoundedCoverWidget(radius=12, border_width=2.0)
        self.about_cover_widget.setFixedSize(110, 110)
        top.addWidget(self.about_cover_widget)

        t_col = QVBoxLayout()
        self.about_badge_lbl = QLabel("LIGHTWEIGHT • NATIVE WINDOWS PLAYER")
        self.about_badge_lbl.setObjectName("accentTag")
        self.about_hdr_lbl = QLabel("NanoTune")
        self.about_hdr_lbl.setStyleSheet("color: #FFFFFF; font-size: 28px; font-weight: 900;")
        self.about_sub_lbl = QLabel("Fast, lightweight music player for Windows. (DX 10.1 / OpenGL 2.1)")
        self.about_sub_lbl.setStyleSheet("color: #FFD83D; font-size: 14px; font-weight: 700;")
        t_col.addWidget(self.about_badge_lbl)
        t_col.addWidget(self.about_hdr_lbl)
        t_col.addWidget(self.about_sub_lbl)
        t_col.addStretch()
        top.addLayout(t_col, 1)
        c_lay.addLayout(top)

        self.about_body_lbl = QLabel()
        self.about_body_lbl.setWordWrap(True)
        self.about_body_lbl.setStyleSheet("color: #D9E4E2; font-size: 14px;")
        c_lay.addWidget(self.about_body_lbl)

        specs = QLabel(
            "• Audio Formats: FLAC (16/24-bit), MP3, WAV, M4A, OGG, OPUS\n"
            "• Audio FX Rack: Speed, Pitch, Warm 155 Hz Sub-Bass Boost, Hall Reverb, 1400 Hz Lo-Fi Muffle\n"
            "• Lyrics Engine: High-FPS Word-by-Word .ELRC + .LRC + Embedded FLAC / ID3 / MP4 Lyrics + Sync Editor\n"
            "• Render Modes: DirectX 10.1, OpenGL 2.1, Native CPU Rasterizer"
        )
        specs.setStyleSheet(
            "background-color: rgba(11, 15, 16, 175); border: 1px solid rgba(255, 255, 255, 28); "
            "border-radius: 10px; padding: 14px; color: #B4C6C3; font-size: 12px;"
        )
        c_lay.addWidget(specs)
        c_lay.addStretch()

        lay.addWidget(card)
        lay.addStretch()
        return page

    def build_fx_drawer(self) -> QFrame:
        drawer = QFrame()
        drawer.setObjectName("fxDrawer")
        lay = QVBoxLayout(drawer)
        lay.setContentsMargins(22, 12, 22, 12)
        lay.setSpacing(8)

        self.fx_top_wrap = QWidget()
        self.fx_top_grid = QGridLayout(self.fx_top_wrap)
        self.fx_top_grid.setContentsMargins(0, 0, 0, 0)
        self.fx_top_grid.setSpacing(8)

        self.fx_title_lbl = QLabel("AUDIO FX  •  SLOWED + REVERB / SPEED / PITCH / BASS / LO-FI")
        self.fx_title_lbl.setObjectName("accentTag")

        self.fx_presets_row = QWidget()
        pr_lay = QHBoxLayout(self.fx_presets_row)
        pr_lay.setContentsMargins(0, 0, 0, 0)
        pr_lay.setSpacing(6)

        norm_btn = QPushButton("Normal")
        norm_btn.setObjectName("secondaryBtn")
        norm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        norm_btn.clicked.connect(lambda: self.apply_fx_preset("normal"))
        pr_lay.addWidget(norm_btn)

        self.lofi_btn = QPushButton("Lo-Fi Muffle")
        self.lofi_btn.setObjectName("lofiToggleBtn")
        self.lofi_btn.setCheckable(True)
        self.lofi_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lofi_btn.toggled.connect(self.on_fx_sliders_changed)
        pr_lay.addWidget(self.lofi_btn)

        for label, preset in (
            ("Slowed + Reverb", "slowed_reverb"),
            ("Warm Bass", "bass"),
            ("Sped Up", "nightcore"),
        ):
            b = QPushButton(label)
            b.setObjectName("secondaryBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, p=preset: self.apply_fx_preset(p))
            pr_lay.addWidget(b)

        self.fx_top_grid.addWidget(self.fx_title_lbl, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.fx_top_grid.addWidget(self.fx_presets_row, 0, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.fx_top_grid.setColumnStretch(0, 1)
        lay.addWidget(self.fx_top_wrap)

        if not HAS_FX_DSP:
            warn_row = QHBoxLayout()
            warn = QLabel("Optional FX packages ('numpy', 'av') are not installed for Pitch, Bass, Reverb & Lo-Fi.")
            warn.setStyleSheet("color: #F4B942; font-size: 12px; font-weight: 600;")
            install_fx_btn = QPushButton("Install FX Packages")
            install_fx_btn.setObjectName("secondaryBtn")
            install_fx_btn.clicked.connect(self.install_optional_fx)
            warn_row.addWidget(warn, 1)
            warn_row.addWidget(install_fx_btn)
            lay.addLayout(warn_row)

        sliders_grid = QGridLayout()
        sliders_grid.setHorizontalSpacing(18)
        sliders_grid.setVerticalSpacing(6)

        self.fx_speed_val = QLabel("1.00x")
        self.fx_speed_val.setStyleSheet("color: #FFFFFF; font-weight: 700; min-width: 48px;")
        self.fx_speed_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.fx_speed_slider.installEventFilter(self._no_wheel_filter)
        self.fx_speed_slider.setRange(60, 140)
        self.fx_speed_slider.setValue(100)
        self.fx_speed_slider.valueChanged.connect(self.on_fx_sliders_changed)

        self.fx_pitch_val = QLabel("0.0 st")
        self.fx_pitch_val.setStyleSheet("color: #6E7B79; font-weight: 700; min-width: 48px;")
        self.fx_pitch_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.fx_pitch_slider.installEventFilter(self._no_wheel_filter)
        self.fx_pitch_slider.setRange(-60, 60)
        self.fx_pitch_slider.setValue(0)
        self.fx_pitch_slider.valueChanged.connect(self.on_fx_sliders_changed)

        self.fx_bass_val = QLabel("0%")
        self.fx_bass_val.setStyleSheet("color: #FFFFFF; font-weight: 700; min-width: 48px;")
        self.fx_bass_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.fx_bass_slider.installEventFilter(self._no_wheel_filter)
        self.fx_bass_slider.setRange(0, 100)
        self.fx_bass_slider.setValue(0)
        self.fx_bass_slider.valueChanged.connect(self.on_fx_sliders_changed)

        self.fx_reverb_val = QLabel("0%")
        self.fx_reverb_val.setStyleSheet("color: #FFFFFF; font-weight: 700; min-width: 48px;")
        self.fx_reverb_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.fx_reverb_slider.installEventFilter(self._no_wheel_filter)
        self.fx_reverb_slider.setRange(0, 100)
        self.fx_reverb_slider.setValue(0)
        self.fx_reverb_slider.valueChanged.connect(self.on_fx_sliders_changed)

        for col_offset, name, slider, val_lbl in (
            (0, "Speed", self.fx_speed_slider, self.fx_speed_val),
            (3, "Pitch", self.fx_pitch_slider, self.fx_pitch_val),
        ):
            lbl = QLabel(name)
            lbl.setStyleSheet("color: #C7D4D2; font-size: 12px; font-weight: 600;")
            sliders_grid.addWidget(lbl, 0, col_offset)
            sliders_grid.addWidget(slider, 0, col_offset + 1)
            sliders_grid.addWidget(val_lbl, 0, col_offset + 2)

        for col_offset, name, slider, val_lbl in (
            (0, "Bass Boost", self.fx_bass_slider, self.fx_bass_val),
            (3, "Reverb", self.fx_reverb_slider, self.fx_reverb_val),
        ):
            lbl = QLabel(name)
            lbl.setStyleSheet("color: #C7D4D2; font-size: 12px; font-weight: 600;")
            sliders_grid.addWidget(lbl, 1, col_offset)
            sliders_grid.addWidget(slider, 1, col_offset + 1)
            sliders_grid.addWidget(val_lbl, 1, col_offset + 2)

        sliders_grid.setColumnStretch(1, 1)
        sliders_grid.setColumnStretch(4, 1)
        lay.addLayout(sliders_grid)

        self.chk_link_pitch = QCheckBox("Link pitch to playback speed (natural vinyl / slowed+reverb tone)")
        self.chk_link_pitch.setChecked(True)
        self.chk_link_pitch.toggled.connect(self.on_fx_sliders_changed)
        lay.addWidget(self.chk_link_pitch)

        self.fx_pitch_slider.setEnabled(False)

        return drawer

    def build_player_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("playerBar")
        bar.setFixedHeight(84)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)

        left_wrap = QWidget()
        left_wrap.setFixedWidth(220)
        left = QHBoxLayout(left_wrap)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)

        self.bar_cover_widget = RoundedCoverWidget(radius=8, border_width=1.5)
        self.bar_cover_widget.setFixedSize(50, 50)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.bar_title_lbl = QLabel("No Track Selected")
        self.bar_title_lbl.setMinimumWidth(0)
        self.bar_title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.bar_title_lbl.setStyleSheet("color: #FFFFFF; font-size: 13px; font-weight: 700;")

        self.bar_meta_lbl = QLabel("NanoTune")
        self.bar_meta_lbl.setMinimumWidth(0)
        self.bar_meta_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.bar_meta_lbl.setStyleSheet("color: #9EB0AD; font-size: 11px;")

        info.addStretch()
        info.addWidget(self.bar_title_lbl)
        info.addWidget(self.bar_meta_lbl)
        info.addStretch()

        left.addWidget(self.bar_cover_widget)
        left.addLayout(info, 1)
        lay.addWidget(left_wrap)

        center = QVBoxLayout()
        center.setSpacing(4)

        btns = QHBoxLayout()
        btns.setSpacing(14)
        btns.addStretch()

        self.shuffle_btn = QPushButton()
        self.shuffle_btn.setObjectName("iconBtn")
        self.shuffle_btn.setCheckable(True)
        self.shuffle_btn.setFixedSize(30, 30)
        self.shuffle_btn.setIconSize(QSize(17, 17))
        self.shuffle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shuffle_btn.setToolTip("Shuffle")
        self.shuffle_btn.clicked.connect(self.toggle_shuffle)

        self.prev_btn = QPushButton()
        self.prev_btn.setObjectName("iconBtn")
        self.prev_btn.setFixedSize(30, 30)
        self.prev_btn.setIconSize(QSize(18, 18))
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.clicked.connect(self.play_previous)

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("playCircleBtn")
        self.play_btn.setFixedSize(36, 36)
        self.play_btn.setIconSize(QSize(17, 17))
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(self.toggle_play_pause)

        self.next_btn = QPushButton()
        self.next_btn.setObjectName("iconBtn")
        self.next_btn.setFixedSize(30, 30)
        self.next_btn.setIconSize(QSize(18, 18))
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.clicked.connect(self.play_next)

        self.repeat_btn = QPushButton()
        self.repeat_btn.setObjectName("iconBtn")
        self.repeat_btn.setFixedSize(30, 30)
        self.repeat_btn.setIconSize(QSize(17, 17))
        self.repeat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.repeat_btn.setToolTip("Repeat: Album")
        self.repeat_btn.clicked.connect(self.cycle_repeat)

        btns.addWidget(self.shuffle_btn)
        btns.addWidget(self.prev_btn)
        btns.addWidget(self.play_btn)
        btns.addWidget(self.next_btn)
        btns.addWidget(self.repeat_btn)
        btns.addStretch()
        center.addLayout(btns)

        seek_row = QHBoxLayout()
        seek_row.setSpacing(10)
        self.time_cur_lbl = QLabel("0:00")
        self.time_cur_lbl.setFixedWidth(36)
        self.time_cur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.time_cur_lbl.setStyleSheet("color: #9EB0AD; font-size: 11px;")

        self.seek_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.seek_slider.installEventFilter(self._no_wheel_filter)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "seeking_active", True))
        self.seek_slider.sliderReleased.connect(self.on_seek_released)
        self.seek_slider.sliderMoved.connect(self.on_seek_moved)

        self.time_tot_lbl = QLabel("0:00")
        self.time_tot_lbl.setFixedWidth(36)
        self.time_tot_lbl.setStyleSheet("color: #9EB0AD; font-size: 11px;")

        seek_row.addWidget(self.time_cur_lbl)
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.time_tot_lbl)
        center.addLayout(seek_row)

        lay.addLayout(center, 1)

        right_wrap = QWidget()
        right_wrap.setFixedWidth(268)
        right = QHBoxLayout(right_wrap)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(6)

        self.fx_btn = QPushButton(" FX")
        self.fx_btn.setObjectName("pillBtn")
        self.fx_btn.setCheckable(True)
        self.fx_btn.setFixedWidth(62)
        self.fx_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fx_btn.clicked.connect(self.toggle_fx_drawer)

        self.bar_lyr_btn = QPushButton(" Lyrics")
        self.bar_lyr_btn.setObjectName("pillBtn")
        self.bar_lyr_btn.setFixedWidth(82)
        self.bar_lyr_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bar_lyr_btn.clicked.connect(lambda: self.switch_page(2))

        self.vol_btn = QPushButton()
        self.vol_btn.setObjectName("iconBtn")
        self.vol_btn.setFixedSize(26, 26)
        self.vol_btn.setIconSize(QSize(17, 17))
        self.vol_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vol_btn.clicked.connect(self.toggle_mute)

        self.vol_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.vol_slider.installEventFilter(self._no_wheel_filter)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setFixedWidth(72)
        self.vol_slider.setValue(int(self.audio_output.volume() * 100))
        self.vol_slider.valueChanged.connect(self.on_volume_changed)

        right.addStretch()
        right.addWidget(self.fx_btn)
        right.addWidget(self.bar_lyr_btn)
        right.addSpacing(2)
        right.addWidget(self.vol_btn)
        right.addWidget(self.vol_slider)
        lay.addWidget(right_wrap)

        return bar

    # -----------------------------------------------------------------------
    # Responsive Scaling, Collapsible Sidebar & Mode Branding
    # -----------------------------------------------------------------------
    def update_responsive_layouts(self):
        avail_h = self.height() - (220 if self.fx_drawer.isVisible() else 120)
        cov_sz = max(145, min(268, int(avail_h * 0.42)))
        if hasattr(self, "np_cover_widget"):
            self.np_cover_widget.setFixedSize(cov_sz, cov_sz)

        if hasattr(self, "fx_top_grid"):
            self.fx_top_grid.removeWidget(self.fx_title_lbl)
            self.fx_top_grid.removeWidget(self.fx_presets_row)
            if self.width() < 1040:
                self.fx_top_grid.addWidget(self.fx_title_lbl, 0, 0, Qt.AlignmentFlag.AlignLeft)
                self.fx_top_grid.addWidget(self.fx_presets_row, 1, 0, Qt.AlignmentFlag.AlignLeft)
            else:
                self.fx_top_grid.addWidget(self.fx_title_lbl, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.fx_top_grid.addWidget(self.fx_presets_row, 0, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if hasattr(self, "albums_scroll") and hasattr(self, "stack") and self.stack.currentIndex() == 1:
            self.populate_albums_grid(force_rebuild=False)

    def toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        start_w = self.sidebar.width()
        end_w = 72 if self.sidebar_collapsed else 240

        def _on_anim(v):
            w_int = int(v)
            self.sidebar.setFixedWidth(w_int)
            rem = max(0, w_int - 72)
            self.brand_text_wrap.setFixedWidth(rem)
            self.bt_lay.setContentsMargins(min(10, rem), 0, 0, 0)
            show_brand_txt = w_int > 84
            self.lbl_app.setVisible(show_brand_txt)
            self.lbl_brand_sub.setVisible(show_brand_txt)
            self.sb_status_card.setVisible(w_int > 120)
            self.position_sidebar_toggle()

        anim = QVariantAnimation(self)
        anim.setDuration(210)
        anim.setStartValue(start_w)
        anim.setEndValue(end_w)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(_on_anim)
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def apply_mode_branding(self):
        self.lbl_app.setText("NANOTUNE")
        self.lbl_brand_sub.setText("MUSIC PLAYER")
        self.nav_buttons[4][0].setText("  About")
        self.about_badge_lbl.setText("LIGHTWEIGHT • NATIVE WINDOWS PLAYER")
        self.about_hdr_lbl.setText("NanoTune")
        self.about_sub_lbl.setText("Fast, lightweight music player for Windows. (DX 10.1 / OpenGL 2.1)")
        self.about_body_lbl.setText(
            "NanoTune is a clean, lightweight native Windows music player engineered to run smoothly "
            "even on older DirectX 10.1 and OpenGL 2.1 hardware.\n\n"
            "Drop in multiple music folders or drag & drop audio and .LRC/.ELRC files directly into the app, "
            "browse your library by Tracks or Albums, read high-FPS animated lyrics, shape your sound with deep "
            "Bass Boost, Speed, Pitch, Reverb, and Lo-Fi Muffle FX, and watch the UI & Star icon adapt dynamically to every album cover.\n\n"
            "Created by @mirakwgnr on Instagram."
        )
        if not self.active_track_path:
            self.apply_theme_colors(STAR_DEFAULT_YELLOW, STAR_DEFAULT_YELLOW_2)
        self.refresh_tray_status()

    def refresh_all_icons(self):
        ac = self.accent_color
        dark_on_btn = "#06120A" if color_luminance(ac) > 0.45 else "#FFFFFF"

        active_app_icon = make_mario_star_icon(ac, 64)
        self.app_icon = active_app_icon
        self.setWindowIcon(active_app_icon)
        if hasattr(self, "tray_icon"):
            self.tray_icon.setIcon(active_app_icon)
        if hasattr(self, "brand_badge"):
            self.brand_badge.setPixmap(active_app_icon.pixmap(30, 30))
        if hasattr(self, "about_cover_widget"):
            self.about_cover_widget.set_pixmap(make_mario_star_icon(ac, 220).pixmap(220, 220))

        self.shuffle_btn.setIcon(make_vector_icon("shuffle", ac if self.shuffle_enabled else "#B4C2C0", 17))
        self.prev_btn.setIcon(make_vector_icon("prev", "#D8E4E2", 18))
        self.play_btn.setIcon(make_vector_icon("pause" if self.is_playing else "play", "#07120B", 17))
        self.next_btn.setIcon(make_vector_icon("next", "#D8E4E2", 18))

        if self.repeat_mode == "one":
            self.repeat_btn.setIcon(make_vector_icon("repeat_one", ac, 17))
        elif self.repeat_mode == "album":
            self.repeat_btn.setIcon(make_vector_icon("repeat", ac, 17))
        else:
            self.repeat_btn.setIcon(make_vector_icon("repeat", "#6D7E7C", 17))

        self.fx_btn.setIcon(make_vector_icon("fx", ac if self.fx_btn.isChecked() else "#EAF2F0", 15))
        self.bar_lyr_btn.setIcon(make_vector_icon("lyrics", "#EAF2F0", 15))
        muted = self.vol_slider.value() == 0
        self.vol_btn.setIcon(make_vector_icon("mute" if muted else "volume", "#B4C2C0", 17))

        self.sb_toggle_btn.setIcon(make_vector_icon("sidebar", "#B4C2C0", 16))
        for btn, ico_name, _lbl in self.nav_buttons:
            btn.setIcon(make_vector_icon(ico_name, ac if btn.isChecked() else "#B4C2C0", 17))

        self.play_all_btn.setIcon(make_vector_icon("play", dark_on_btn, 15))
        self.shuffle_all_btn.setIcon(make_vector_icon("shuffle", "#EAF2F0", 15))
        self.rescan_btn.setIcon(make_vector_icon("refresh", "#EAF2F0", 15))
        self.edit_lyrics_btn.setIcon(make_vector_icon("edit", "#EAF2F0", 15))
        self.stamp_btn.setIcon(make_vector_icon("clock", "#EAF2F0", 15))
        self.save_lrc_btn.setIcon(make_vector_icon("save", dark_on_btn, 15))
        self.save_flac_btn.setIcon(make_vector_icon("save", dark_on_btn, 15))
        self.fs_btn.setIcon(make_vector_icon("fullscreen", "#EAF2F0", 16))

    def apply_theme_colors(self, accent: str, accent2: str):
        self.accent_color = accent
        self.accent_color_2 = accent2
        c1 = QColor(accent)
        rgb1 = f"{c1.red()}, {c1.green()}, {c1.blue()}"
        btn_fg = "#07100B" if color_luminance(accent) > 0.45 else "#FFFFFF"
        fam = self.active_font_family

        template = """
            * { font-family: "__FONT__", "Segoe UI", sans-serif; }
            QWidget#rootWidget { background: transparent; }
            QScrollArea#transparentScroll { background: transparent; border: none; }

            QFrame#sidebar {
                background-color: rgba(8, 11, 12, 200);
                border-right: 1px solid rgba(255, 255, 255, 18);
            }
            QLabel#brandBadge {
                background-color: rgba(16, 20, 22, 225);
                border-radius: 10px;
                border: 1px solid rgba(__RGB1__, 120);
            }
            QLabel#brandTitle {
                color: #FFFFFF; font-size: 14px; font-weight: 900; letter-spacing: 0.8px;
            }
            QLabel#brandSub {
                color: __AC2__; font-size: 10px; font-weight: 700; letter-spacing: 1.2px;
            }
            QLabel#accentTag {
                color: __AC2__; font-size: 11px; font-weight: 800; letter-spacing: 1.5px;
            }
            QPushButton#navBtn {
                background-color: transparent;
                color: #B4C2C0;
                border: none;
                border-radius: 8px;
                padding: 11px 11px;
                text-align: left;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#navBtn:hover {
                background-color: rgba(255, 255, 255, 14);
                color: #FFFFFF;
            }
            QPushButton#navBtn:checked {
                background-color: rgba(__RGB1__, 38);
                color: #FFFFFF;
                font-weight: 700;
                border-left: 3px solid __AC1__;
            }
            QFrame#sbStatusCard, QFrame#detailsCard {
                background-color: rgba(10, 14, 15, 165);
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 10px;
            }
            QFrame#glassCard, QFrame#albumCard {
                background-color: rgba(12, 16, 18, 148);
                border: 1px solid rgba(255, 255, 255, 24);
                border-radius: 14px;
            }
            QFrame#albumCard:hover {
                background-color: rgba(25, 32, 36, 175);
                border: 1px solid rgba(__RGB1__, 140);
            }
            QFrame#fxDrawer {
                background-color: rgba(10, 14, 16, 228);
                border-top: 1px solid rgba(__RGB1__, 90);
            }
            QFrame#playerBar {
                background-color: rgba(7, 10, 11, 232);
                border-top: 1px solid rgba(255, 255, 255, 22);
            }

            QPushButton#primaryBtn {
                background-color: __AC1__;
                color: __BTNFG__;
                border: 1px solid __AC1__;
                border-radius: 14px;
                padding: 7px 18px;
                font-size: 13px;
                font-weight: 800;
            }
            QPushButton#primaryBtn:hover {
                background-color: __AC2__;
                border-color: __AC2__;
            }

            QPushButton#secondaryBtn, QPushButton#pillBtn {
                background-color: rgba(255, 255, 255, 15);
                color: #EAF2F0;
                border: 1px solid rgba(255, 255, 255, 28);
                border-radius: 14px;
                padding: 7px 10px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#secondaryBtn:hover, QPushButton#pillBtn:hover {
                background-color: rgba(255, 255, 255, 28);
                color: #FFFFFF;
            }
            QPushButton#pillBtn:checked {
                background-color: rgba(__RGB1__, 42);
                border-color: __AC1__;
                color: #FFFFFF;
            }

            QPushButton#lofiToggleBtn {
                background-color: rgba(255, 255, 255, 15);
                color: #EAF2F0;
                border: 1px solid rgba(255, 255, 255, 28);
                border-radius: 14px;
                padding: 7px 15px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#lofiToggleBtn:hover {
                background-color: rgba(255, 255, 255, 28);
                color: #FFFFFF;
            }
            QPushButton#lofiToggleBtn:checked {
                background-color: rgba(__RGB1__, 42);
                border: 1px solid __AC1__;
                color: #FFFFFF;
                font-weight: 700;
            }

            QPushButton#iconBtn {
                background: transparent; border: none; border-radius: 15px;
            }
            QPushButton#iconBtn:hover { background-color: rgba(255, 255, 255, 16); }

            QPushButton#sbToggleBtn {
                background-color: rgba(14, 19, 21, 240);
                border: 1px solid rgba(__RGB1__, 115);
                border-radius: 13px;
            }
            QPushButton#sbToggleBtn:hover {
                background-color: rgba(__RGB1__, 55);
                border-color: __AC1__;
            }

            QPushButton#playCircleBtn {
                background-color: #FFFFFF; border: 1px solid #FFFFFF; border-radius: 18px;
            }
            QPushButton#playCircleBtn:hover {
                background-color: __AC1__; border-color: __AC1__;
            }

            QLineEdit, QComboBox {
                background-color: rgba(12, 17, 19, 185);
                color: #EAF0EE;
                border: 1px solid rgba(255, 255, 255, 28);
                border-radius: 15px;
                padding: 6px 14px;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus { border-color: __AC1__; }
            QComboBox::drop-down { border: none; width: 26px; }
            QComboBox QAbstractItemView {
                background-color: #12181A; color: #EAF0EE;
                selection-background-color: __AC1__; selection-color: __BTNFG__;
                border: 1px solid rgba(__RGB1__, 80);
            }

            QListWidget#trackList, QListWidget#foldersList {
                background-color: rgba(12, 16, 18, 134);
                border: 1px solid rgba(255, 255, 255, 22);
                border-radius: 14px;
                padding: 8px;
                color: #EAF0EE;
                outline: none;
            }
            QListWidget#foldersList::item { padding: 5px 8px; border-radius: 5px; }
            QListWidget#foldersList::item:selected { background-color: rgba(__RGB1__, 65); }

            QTextEdit#lyricsEditor {
                background-color: rgba(10, 14, 15, 175);
                color: #EAF0EE;
                border: 1px solid rgba(__RGB1__, 90);
                border-radius: 10px;
                padding: 12px;
                font-family: "Consolas", "Segoe UI", monospace;
                font-size: 13px;
            }

            QCheckBox { color: #DCE6E4; font-size: 13px; spacing: 8px; }
            QCheckBox::indicator {
                width: 17px; height: 17px; border-radius: 4px;
                border: 1px solid #35504B; background: rgba(12, 18, 19, 200);
            }
            QCheckBox::indicator:checked { background: __AC1__; border-color: __AC1__; }

            QSlider::groove:horizontal {
                height: 4px; background: rgba(255, 255, 255, 38); border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: __AC1__; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF; border: none;
                width: 10px; height: 10px; margin: -3px 0; border-radius: 5px;
            }
            QSlider::handle:horizontal:hover {
                width: 12px; height: 12px; margin: -4px 0; border-radius: 6px;
            }
            QSlider::groove:horizontal:disabled {
                height: 4px; background: #2D3436; border-radius: 2px;
            }
            QSlider::sub-page:horizontal:disabled {
                background: #4A5456; border-radius: 2px;
            }
            QSlider::handle:horizontal:disabled {
                background: #7A8486; border: none;
                width: 10px; height: 10px; margin: -3px 0; border-radius: 5px;
            }

            QScrollBar:vertical { background: transparent; width: 8px; margin: 6px 2px; }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 45); border-radius: 4px; min-height: 28px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(__RGB1__, 160); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        """
        css = (
            template.replace("__AC1__", accent)
            .replace("__AC2__", accent2)
            .replace("__RGB1__", rgb1)
            .replace("__BTNFG__", btn_fg)
            .replace("__FONT__", fam)
        )
        self.setStyleSheet(css)
        self.sb_count_lbl.setStyleSheet(f"color: {accent}; font-weight: 700; font-size: 12px;")
        self.about_sub_lbl.setStyleSheet(f"color: {accent}; font-size: 14px; font-weight: 700;")

        for w in (self.lib_cover_widget, self.np_cover_widget, self.about_cover_widget, self.bar_cover_widget):
            w.set_border_color(accent)

        self._bg_scaled_cache = QPixmap()
        self.lyrics_view.set_accent_color(accent)
        self.refresh_all_icons()
        if hasattr(self, "track_list"):
            self.track_list.viewport().update()
        self.update()
# -----------------------------------------------------------------------
    # Startup, Multi-Folder Scanning (with Cache), Drag & Drop & Default App
    # -----------------------------------------------------------------------
    def startup_check(self):
        folders = [f for f in self.config.get("music_folders", []) if Path(f).exists()]
        imported = [f for f in self.config.get("imported_files", []) if Path(f).is_file()]

        if self.startup_files:
            files_to_open = list(self.startup_files)
            self.startup_files = []
            if folders or imported:
                self.scan_music_folders(folders)
            self.handle_dropped_paths(files_to_open, auto_play_audio=True)
            return

        if not folders and not imported:
            dlg = FirstLaunchDialog(self.cover_pixmap, self.app_icon, self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_folder:
                folders = [dlg.selected_folder]
                self.config["music_folders"] = folders
                self.save_config()
                self.folders_list.clear()
                self.folders_list.addItems(folders)
                self.scan_music_folders(folders)

                if not bool(self.config.get("asked_default_player", False)):
                    dp_dlg = DefaultPlayerPromptDialog(self.cover_pixmap, self.app_icon, self.accent_color, self)
                    self.config["asked_default_player"] = True
                    self.save_config()
                    if dp_dlg.exec() == QDialog.DialogCode.Accepted:
                        self.make_default_music_player()
            return

        self.scan_music_folders(folders)

    def make_default_music_player(self):
        open_windows_default_apps()

    def open_external_files(self, file_paths: list[str]):
        if file_paths:
            self.handle_dropped_paths(file_paths, auto_play_audio=True)

    def handle_dropped_paths(self, paths: list[str], auto_play_audio: bool = True):
        dirs: list[str] = []
        audio_files: list[str] = []
        lyric_files: list[str] = []

        for raw in paths:
            p = Path(raw)
            if not p.exists():
                continue
            if p.is_dir():
                dirs.append(str(p.resolve()))
            elif p.is_file():
                suf = p.suffix.lower()
                if suf in AUDIO_EXTENSIONS:
                    audio_files.append(str(p.resolve()))
                elif suf in LYRIC_EXTENSIONS:
                    lyric_files.append(str(p.resolve()))

        changed_library = False
        folders = list(self.config.get("music_folders", []))
        for d in dirs:
            if d not in folders:
                folders.append(d)
                self.folders_list.addItem(d)
                changed_library = True

        imported_files = list(self.config.get("imported_files", []))
        existing_norm = {str(Path(x).resolve()).lower() for x in imported_files if Path(x).exists()}
        for af in audio_files:
            if af.lower() not in existing_norm:
                imported_files.append(af)
                existing_norm.add(af.lower())
                changed_library = True

        if changed_library:
            self.config["music_folders"] = folders
            self.config["imported_files"] = imported_files
            self.save_config()
            self.scan_music_folders(folders)

        if lyric_files:
            self.import_lrc_files(lyric_files)

        if audio_files and auto_play_audio:
            target = audio_files[0].lower()
            found_idx = -1
            for i, tr in enumerate(self.tracks):
                if str(Path(tr["path"]).resolve()).lower() == target:
                    found_idx = i
                    break
            if found_idx < 0:
                self.active_album_filter = ""
                self.apply_library_filter_view()
                for i, tr in enumerate(self.tracks):
                    if str(Path(tr["path"]).resolve()).lower() == target:
                        found_idx = i
                        break
            if found_idx >= 0:
                self.load_and_play(found_idx)

    def import_lrc_files(self, lyric_paths: list[str]):
        imported_lyrics = dict(self.config.get("imported_lyrics") or {})
        matched_count = 0
        unmatched: list[str] = []
        active_updated = False
        active_tr = self._get_active_track_dict()

        for lp_str in lyric_paths:
            lp = Path(lp_str)
            if not lp.is_file():
                continue
            lrc_norm = normalize_text(lp.stem)
            target_tr = None

            if lrc_norm:
                for tr in self.all_tracks:
                    if normalize_text(Path(tr["path"]).stem) == lrc_norm:
                        target_tr = tr
                        break
                if target_tr is None:
                    for tr in self.all_tracks:
                        if normalize_text(tr.get("title", "")) == lrc_norm:
                            target_tr = tr
                            break
                if target_tr is None and len(lrc_norm) >= 4:
                    for tr in self.all_tracks:
                        stem_n = normalize_text(Path(tr["path"]).stem)
                        title_n = normalize_text(tr.get("title", ""))
                        if (stem_n and lrc_norm in stem_n) or (title_n and lrc_norm in title_n):
                            target_tr = tr
                            break

            if target_tr is None and len(lyric_paths) == 1 and active_tr is not None:
                target_tr = active_tr

            if target_tr is None:
                unmatched.append(lp.name)
                continue

            target_path = target_tr["path"]
            target_resolved = str(Path(target_path).resolve())
            dest_name = hashlib.md5(target_resolved.lower().encode("utf-8", errors="ignore")).hexdigest() + lp.suffix.lower()
            dest_path = self.imported_lyrics_dir / dest_name
            try:
                dest_path.write_bytes(lp.read_bytes())
            except Exception:
                unmatched.append(lp.name)
                continue

            dest_str = str(dest_path)
            imported_lyrics[target_path] = dest_str
            imported_lyrics[target_resolved] = dest_str
            IMPORTED_LYRICS_MAP[target_path] = dest_str
            IMPORTED_LYRICS_MAP[target_resolved] = dest_str
            target_tr["has_lyrics"] = True
            matched_count += 1

            for tr in self.tracks:
                if tr["path"] == target_path:
                    tr["has_lyrics"] = True

            if active_tr is not None and active_tr["path"] == target_path:
                active_updated = True

        if matched_count > 0:
            self.config["imported_lyrics"] = imported_lyrics
            self.save_config()
            self.populate_track_list()
            if active_updated and active_tr is not None:
                self.load_lyrics_for_current_track()
                self.update_file_details_panel(active_tr)
                pos = self.fx_device.get_position_ms() if self.fx_active_engine else self.player.position()
                self.sync_lyrics_highlight(pos)

        if unmatched:
            QMessageBox.information(
                self,
                "Import Lyrics",
                "Could not match the following lyric file(s) to a loaded track:\n• "
                + "\n• ".join(unmatched)
                + "\n\nTip: Play the target song first and drop the .LRC/.ELRC file, or name it like the song.",
            )

    def switch_page(self, idx: int):
        self.stack.setCurrentIndex(idx)
        for i, (btn, _ico, _lbl) in enumerate(self.nav_buttons):
            btn.setChecked(i == idx)
        self.refresh_all_icons()
        if idx == 1:
            self.populate_albums_grid(force_rebuild=False)
        self.update()

    def update_cover_widgets(self):
        if not hasattr(self, "cover_pixmap") or self.cover_pixmap.isNull():
            return
        targets = (
            ("np_cover_widget", "bar_cover_widget")
            if self.active_album_filter
            else ("lib_cover_widget", "np_cover_widget", "bar_cover_widget")
        )
        for attr in targets:
            w = getattr(self, attr, None)
            if w is not None:
                w.set_pixmap(self.cover_pixmap)

    def scan_music_folders(self, folder_strs: list[str]):
        valid_folders = [Path(f) for f in folder_strs if f and Path(f).exists()]
        valid_imported = [Path(f) for f in self.config.get("imported_files", []) if f and Path(f).is_file()]
        if not valid_folders and not valid_imported:
            return

        recursive = bool(self.config.get("recursive_scan", True))
        cache: dict[str, dict] = {}
        if self.cache_path.exists():
            try:
                cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                cache = {}

        seen_paths = set()
        scanned: list[dict] = []
        new_cache: dict[str, dict] = {}

        def _process_file(f: Path):
            if not (f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS):
                return
            key = str(f)
            norm_key = str(f.resolve()).lower()
            if norm_key in seen_paths:
                return
            seen_paths.add(norm_key)
            try:
                st = f.stat()
                mtime = st.st_mtime
            except Exception:
                mtime = 0.0

            cached = cache.get(key)
            if (
                cached
                and abs(cached.get("mtime", -1) - mtime) < 0.01
                and "album_artist" in cached
                and "year" in cached
            ):
                meta = dict(cached)
                if not meta.get("has_lyrics") and find_lrc_path(f):
                    meta["has_lyrics"] = True
            else:
                meta = extract_metadata(f)
                meta["has_lyrics"] = bool(find_lrc_path(f) or read_lyrics_for_track(key)[0])

            meta["album_key"] = album_cache_key(meta.get("album", ""), str(f.parent))
            meta["track_cover_key"] = track_cover_cache_key(key, mtime)
            meta["display_title"] = meta.get("title", f.stem)
            new_cache[key] = meta
            scanned.append(meta)

        for folder in valid_folders:
            files = folder.rglob("*") if recursive else folder.iterdir()
            for f in files:
                _process_file(f)

        for imp_f in valid_imported:
            _process_file(imp_f)

        try:
            self.cache_path.write_text(json.dumps(new_cache), encoding="utf-8")
        except Exception:
            pass

        self.album_unique_covers.clear()
        default_alb_pm = self.album_cover_cache.get("__DEFAULT__")
        self.album_cover_cache.clear()
        if default_alb_pm is not None:
            self.album_cover_cache["__DEFAULT__"] = default_alb_pm

        self.all_tracks = scanned
        self.apply_library_filter_view()

    def apply_library_filter_view(self):
        grouped: dict[str, list[dict]] = {}
        for t in self.all_tracks:
            alb = t.get("album") or "Unknown Album"
            grouped.setdefault(alb, []).append(t)

        self.albums_map.clear()
        for alb, alb_tracks in grouped.items():
            sample = alb_tracks[0]
            self.albums_map[sample["album_key"]] = {
                "album_key": sample["album_key"],
                "album": alb,
                "artist": resolve_album_artist(alb_tracks),
                "year": resolve_album_year(alb_tracks),
                "count": len(alb_tracks),
                "sample_track": sample,
            }

        if self.active_album_filter:
            filtered = [dict(t) for t in self.all_tracks if t.get("album") == self.active_album_filter]
            filtered.sort(key=lambda t: (t["track_no"] or 999, t["display_title"].lower()))
            for i, t in enumerate(filtered, 1):
                t["display_num"] = t["track_no"] if t["track_no"] > 0 else i

            alb_year = resolve_album_year(filtered)
            self.lib_tag_lbl.setText(f"ALBUM  •  {alb_year}" if alb_year else "ALBUM")
            self.lib_title_lbl.setText(self.active_album_filter)
            alb_artist = resolve_album_artist(filtered)
            self.lib_artist_lbl.setText(f"{alb_artist}  •  {len(filtered)} tracks")
            self.clear_album_btn.setVisible(True)

            if filtered:
                sample = filtered[0]
                cov_pm = self.album_cover_cache.get(sample["album_key"])
                if cov_pm is None:
                    cov_pil = extract_embedded_cover_from_file(Path(sample["path"]))
                    cov_pm = self.pil_to_qpixmap(cov_pil) if cov_pil else self.pil_to_qpixmap(self.default_nano_pil)
                self.lib_cover_widget.set_pixmap(cov_pm)
        else:
            by_album: dict[str, list[dict]] = {}
            for t in self.all_tracks:
                by_album.setdefault(t.get("album", ""), []).append(dict(t))
            mixed_pairs: list[tuple[float, str, dict]] = []
            for alb_name, alb_list in by_album.items():
                alb_list.sort(key=lambda t: (t["track_no"] or 999, t["display_title"].lower()))
                m = len(alb_list)
                off = (zlib.crc32(alb_name.encode("utf-8", errors="ignore")) % 1000) / 1000.0
                for k, tr in enumerate(alb_list):
                    mixed_pairs.append(((k + off) / m, tr["display_title"].lower(), tr))
            mixed_pairs.sort(key=lambda item: (item[0], item[1]))
            filtered = [item[2] for item in mixed_pairs]
            for i, t in enumerate(filtered, 1):
                t["display_num"] = i
            self.lib_tag_lbl.setText("LIBRARY  •  ALL TRACKS")
            self.lib_title_lbl.setText("All Tracks")
            folders = self.config.get("music_folders", [])
            f_txt = f"{len(folders)} folder{'s' if len(folders) != 1 else ''}"
            self.lib_artist_lbl.setText(f"{len(filtered)} tracks  •  {len(self.albums_map)} albums  •  {f_txt}")
            self.clear_album_btn.setVisible(False)
            if not self.cover_pixmap.isNull():
                self.lib_cover_widget.set_pixmap(self.cover_pixmap)

        self.tracks = filtered
        self.current_index = -1
        if self.active_track_path:
            for idx, t in enumerate(self.tracks):
                if t["path"] == self.active_track_path:
                    self.current_index = idx
                    break

        self.rebuild_shuffle_queue(self.current_index)

        self.sb_count_lbl.setText(f"{len(self.tracks)} tracks loaded")
        folders = self.config.get("music_folders", [])
        imported = self.config.get("imported_files", [])
        if folders:
            f_summary = folders[0] if len(folders) == 1 else f"{len(folders)} directories"
        elif imported:
            f_summary = f"{len(imported)} imported track{'s' if len(imported) != 1 else ''}"
        else:
            f_summary = "No folders"
        elided_folder = QFontMetrics(self.sb_folder_lbl.font()).elidedText(str(f_summary), Qt.TextElideMode.ElideMiddle, 175)
        self.sb_folder_lbl.setText(elided_folder or "No folders")
        self.sb_folder_lbl.setToolTip("\n".join(folders))

        self.refresh_default_covers()
        self.start_background_cover_preloader()
        self.populate_track_list()
        self._last_albums_signature = ()
        if self.stack.currentIndex() == 1:
            self.populate_albums_grid(force_rebuild=True)
        self.rebuild_tray_track_submenu()
        self.refresh_tray_status()
        self.update_windows_smtc()

    def populate_track_list(self):
        query = normalize_text(self.search_input.text()) if hasattr(self, "search_input") else ""
        self.track_list.setUpdatesEnabled(False)
        self.track_list.clear()

        for idx, tr in enumerate(self.tracks):
            haystack = normalize_text(tr["display_title"] + tr["artist"] + tr["album"])
            if query and query not in haystack:
                continue
            row_data = dict(tr)
            row_data["index"] = idx
            row_data["is_current"] = (tr["path"] == self.active_track_path) if self.active_track_path else (idx == self.current_index)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, row_data)
            self.track_list.addItem(item)

        self.track_list.setUpdatesEnabled(True)

        if 0 <= self.current_index < len(self.tracks):
            for row in range(self.track_list.count()):
                it = self.track_list.item(row)
                d = it.data(Qt.ItemDataRole.UserRole)
                if isinstance(d, dict) and d.get("index") == self.current_index:
                    self.track_list.setCurrentRow(row)
                    break

    def populate_albums_grid(self, force_rebuild: bool = False):
        query = normalize_text(self.album_search_input.text()) if hasattr(self, "album_search_input") else ""
        avail_w = max(420, self.albums_scroll.viewport().width())
        cols = max(2, avail_w // 210)
        sig = (query, len(self.albums_map))

        if not force_rebuild and sig == self._last_albums_signature and self.album_cards_list:
            if cols == self._last_albums_cols:
                return
            self._last_albums_cols = cols
            while self.albums_grid.count():
                self.albums_grid.takeAt(0)
            for i, card in enumerate(self.album_cards_list):
                self.albums_grid.addWidget(card, i // cols, i % cols)
            return

        self._last_albums_signature = sig
        self._last_albums_cols = cols
        self.album_cards_by_key.clear()
        self.album_cards_list.clear()

        while self.albums_grid.count():
            item = self.albums_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        albums = sorted(self.albums_map.values(), key=lambda a: a["album"].lower())
        if query:
            albums = [a for a in albums if query in normalize_text(a["album"] + a["artist"])]

        for i, info in enumerate(albums):
            alb_key = info["album_key"]
            pm = self.album_cover_cache.get(alb_key) or self.album_cover_cache.get("__DEFAULT__", QPixmap())

            card = AlbumCardWidget(alb_key, info["album"], info["artist"], info["count"], pm, self.accent_color)
            card.clicked.connect(self.open_album_from_grid)
            self.album_cards_by_key[alb_key] = card
            self.album_cards_list.append(card)
            self.albums_grid.addWidget(card, i // cols, i % cols)

    def open_album_from_grid(self, album_name: str):
        self.active_album_filter = album_name
        self.apply_library_filter_view()
        self.switch_page(0)

    def clear_album_filter(self):
        self.active_album_filter = ""
        self.apply_library_filter_view()

    # -----------------------------------------------------------------------
    # True Fisher-Yates Shuffle & Zero-Restart Audio FX Engine
    # -----------------------------------------------------------------------
    def rebuild_shuffle_queue(self, anchor_index: int = -1, avoid_first: int = -1):
        n = len(self.tracks)
        indices = list(range(n))
        if 0 <= anchor_index < n:
            indices.remove(anchor_index)
            random.shuffle(indices)
            self.shuffle_queue = [anchor_index] + indices
            self.shuffle_pos = 0
        else:
            random.shuffle(indices)
            if n > 1 and 0 <= avoid_first < n and indices[0] == avoid_first:
                swap_i = random.randint(1, n - 1)
                indices[0], indices[swap_i] = indices[swap_i], indices[0]
            self.shuffle_queue = indices
            self.shuffle_pos = -1

    def fx_needed(self) -> bool:
        return (
            abs(self.fx_speed - 1.0) > 0.01
            or (not self.fx_link_pitch and abs(self.fx_pitch) > 0.05)
            or self.fx_bass > 0.01
            or self.fx_reverb > 0.01
            or self.fx_lofi
        )

    def stop_fx_sink(self):
        self.fx_poll_timer.stop()
        if self.fx_sink is not None:
            try:
                self.fx_sink.stateChanged.disconnect()
            except Exception:
                pass
            try:
                self.fx_sink.stop()
            except Exception:
                pass
            self.fx_sink = None
        if self.fx_device.isOpen():
            self.fx_device.close()
        self.fx_active_engine = False

    def on_fx_sink_state_changed(self, gen_id: int, state):
        if gen_id != self.playback_generation_id or not self.fx_active_engine:
            return
        if time.monotonic() < self._seek_guard_until:
            return
        if state == QAudio.State.SuspendedState:
            if self.is_playing:
                self.is_playing = False
                self.refresh_all_icons()
                self.refresh_tray_status()
        elif state == QAudio.State.ActiveState:
            if not self.is_playing:
                self.is_playing = True
                self.refresh_all_icons()
                self.refresh_tray_status()

    def start_fx_sink(self, pcm_array, start_ms: int, start_playing: bool = True):
        self.stop_fx_sink()
        fmt = QAudioFormat()
        fmt.setSampleRate(44100)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)

        self.fx_device.set_pcm(pcm_array, start_ms=start_ms)
        self.fx_device.update_params(
            self.fx_speed, self.fx_pitch, self.fx_link_pitch, self.fx_bass, self.fx_reverb, self.fx_lofi
        )
        self.fx_device.open(QIODevice.OpenModeFlag.ReadOnly)

        self.fx_sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt, self)
        self.fx_sink.setBufferSize(16384)
        self.fx_sink.setVolume(float(self.vol_slider.value()) / 100.0)
        gen_id = self.playback_generation_id
        self.fx_sink.stateChanged.connect(lambda st, gid=gen_id: self.on_fx_sink_state_changed(gid, st))

        self._seek_guard_until = time.monotonic() + 0.18
        self.fx_active_engine = True
        self.fx_sink.start(self.fx_device)
        if not start_playing:
            self.fx_sink.suspend()
            self.is_playing = False
        else:
            self.is_playing = True
        self.fx_poll_timer.start()

    def load_and_play(self, index: int):
        if not (0 <= index < len(self.tracks)):
            return

        self.playback_generation_id += 1
        gen_id = self.playback_generation_id

        self._fx_param_timer.stop()
        self.stop_fx_sink()
        self._qt_ignore_state_until = time.monotonic() + 0.35
        self.player.stop()
        self.player.setSource(QUrl())

        self.current_index = index
        tr = self.tracks[index]
        self.active_track_path = tr["path"]
        self._last_known_pos_ms = 0
        self._seek_guard_until = 0.0
        self._pending_qt_seek_ms = -1
        self._pending_qt_play = False

        if self.shuffle_enabled and index in self.shuffle_queue:
            self.shuffle_pos = self.shuffle_queue.index(index)

        cov = extract_embedded_cover_from_file(Path(tr["path"]))
        self.apply_cover_pil(cov if cov is not None else self.default_nano_pil, is_4l=self._is_yeat_4l_track(tr))

        self.fx_decoded_path = ""
        self.fx_decoded_pcm = None
        self.fx_pending_activate = False
        self.is_playing = True

        if self.fx_needed() and HAS_FX_DSP:
            self.fx_pending_activate = True
            self.fx_pending_seek_ms = 0
            self.seek_slider.setRange(0, max(0, tr["duration_ms"]))
            self.seek_slider.setValue(0)
            self.time_cur_lbl.setText("0:00")
            self.time_tot_lbl.setText(format_ms(tr["duration_ms"]))
            DecodeWorker(
                tr["path"],
                gen_id,
                lambda p, gid, pcm, err: self.fxDecodedReady.emit(p, gid, pcm, err),
            ).start()
        else:
            self._qt_ignore_state_until = 0.0
            self.player.setSource(QUrl.fromLocalFile(tr["path"]))
            self.player.setPlaybackRate(self.fx_speed)
            self.player.play()
            if HAS_FX_DSP:
                DecodeWorker(
                    tr["path"],
                    gen_id,
                    lambda p, gid, pcm, err: self.fxDecodedReady.emit(p, gid, pcm, err),
                ).start()

        fm_title = QFontMetrics(self.bar_title_lbl.font())
        self.bar_title_lbl.setText(fm_title.elidedText(tr["display_title"], Qt.TextElideMode.ElideRight, 154))
        fm_meta = QFontMetrics(self.bar_meta_lbl.font())
        self.bar_meta_lbl.setText(fm_meta.elidedText(f"{tr['artist']} • {tr['album']}", Qt.TextElideMode.ElideRight, 154))

        self.np_title_lbl.setText(tr["display_title"])
        artist_alb = f"{tr['artist']}  —  {tr['album']}"
        self.np_artist_lbl.setText(QFontMetrics(self.np_artist_lbl.font()).elidedText(artist_alb, Qt.TextElideMode.ElideRight, 520))

        self.load_lyrics_for_current_track()
        self.lyrics_view.set_playback_clock(0, self.is_playing, self.fx_speed, force_snap=True)
        self.update_file_details_panel(tr)
        self.populate_track_list()
        self.refresh_all_icons()
        self.refresh_tray_status()
        self.update()

    def on_fx_decode_finished(self, path: str, gen_id: int, pcm, err: str):
        if gen_id != self.playback_generation_id or path != self.active_track_path:
            return
        if pcm is None or err:
            if self.fx_pending_activate:
                self.fx_pending_activate = False
                self._qt_ignore_state_until = 0.0
                self.player.setSource(QUrl.fromLocalFile(path))
                self.player.setPlaybackRate(self.fx_speed)
                if self.is_playing:
                    self.player.play()
            return

        self.fx_decoded_path = path
        self.fx_decoded_pcm = pcm
        if self.fx_pending_activate:
            self.fx_pending_activate = False
            qt_pos = self.player.position() if self.player.source() == QUrl.fromLocalFile(path) else 0
            pos = max(self.fx_pending_seek_ms, qt_pos, self._last_known_pos_ms)
            was_playing = self.is_playing
            self._qt_ignore_state_until = time.monotonic() + 0.35
            self.player.pause()
            self.start_fx_sink(pcm, pos, start_playing=was_playing)
            self.refresh_all_icons()
            self.refresh_tray_status()

    def on_fx_sliders_changed(self):
        self.fx_speed = self.fx_speed_slider.value() / 100.0
        self.fx_pitch = self.fx_pitch_slider.value() / 10.0
        self.fx_bass = self.fx_bass_slider.value() / 100.0
        self.fx_reverb = self.fx_reverb_slider.value() / 100.0
        self.fx_link_pitch = self.chk_link_pitch.isChecked()
        self.fx_lofi = self.lofi_btn.isChecked()

        self.fx_pitch_slider.setEnabled(not self.fx_link_pitch)
        if self.fx_link_pitch:
            self.fx_pitch_val.setStyleSheet("color: #6E7B79; font-weight: 700; min-width: 48px;")
        else:
            self.fx_pitch_val.setStyleSheet("color: #FFFFFF; font-weight: 700; min-width: 48px;")

        self.fx_speed_val.setText(f"{self.fx_speed:.2f}x")
        self.fx_pitch_val.setText(f"{self.fx_pitch:+.1f} st")
        self.fx_bass_val.setText(f"{int(self.fx_bass * 100)}%")
        self.fx_reverb_val.setText(f"{int(self.fx_reverb * 100)}%")

        if HAS_FX_DSP:
            self.fx_device.update_params(
                self.fx_speed, self.fx_pitch, self.fx_link_pitch, self.fx_bass, self.fx_reverb, self.fx_lofi
            )

        self._fx_param_timer.start()

    def _apply_fx_params_debounced(self):
        if not HAS_FX_DSP:
            self.player.setPlaybackRate(self.fx_speed)
            return

        self.fx_device.update_params(
            self.fx_speed, self.fx_pitch, self.fx_link_pitch, self.fx_bass, self.fx_reverb, self.fx_lofi
        )

        if not self.active_track_path:
            return

        cur_path = self.active_track_path
        if self.fx_active_engine:
            pass
        elif self.fx_needed():
            self._pending_qt_seek_ms = -1
            self._pending_qt_play = False
            use_qt_pos = (
                self.player.source() == QUrl.fromLocalFile(cur_path)
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and time.monotonic() >= self._seek_guard_until
            )
            qt_pos = self.player.position() if use_qt_pos else 0
            pos = max(qt_pos, self._last_known_pos_ms)
            was_playing = self.is_playing
            if self.fx_decoded_path == cur_path and self.fx_decoded_pcm is not None:
                self._qt_ignore_state_until = time.monotonic() + 0.35
                self.player.pause()
                self.start_fx_sink(self.fx_decoded_pcm, pos, start_playing=was_playing)
                self.refresh_all_icons()
                self.refresh_tray_status()
            else:
                self.fx_pending_activate = True
                self.fx_pending_seek_ms = pos

        cur_pos = (
            self.fx_device.get_position_ms()
            if self.fx_active_engine
            else max(self.player.position(), self._last_known_pos_ms)
        )
        self.lyrics_view.set_playback_clock(cur_pos, self.is_playing, self.fx_speed)

    def _verify_qt_seek(self, expected_url: QUrl, gen_id: int, target_ms: int):
        if gen_id != self.playback_generation_id or self.fx_active_engine or self.player.source() != expected_url:
            return
        if target_ms > 1500 and self.player.position() < target_ms - 1500:
            self.player.setPosition(target_ms)

    def apply_fx_preset(self, preset: str):
        presets = {
            "normal": (100, 0, 0, 0, True),
            "slowed_reverb": (84, 0, 0, 70, True),
            "bass": (100, 0, 65, 0, True),
            "nightcore": (118, 0, 0, 30, True),
        }
        sp, pi, ba, rv, lnk = presets.get(preset, (100, 0, 0, 0, True))
        for w in (self.fx_speed_slider, self.fx_pitch_slider, self.fx_bass_slider, self.fx_reverb_slider, self.chk_link_pitch):
            w.blockSignals(True)
        self.fx_speed_slider.setValue(sp)
        self.fx_pitch_slider.setValue(pi)
        self.fx_bass_slider.setValue(ba)
        self.fx_reverb_slider.setValue(rv)
        self.chk_link_pitch.setChecked(lnk)
        for w in (self.fx_speed_slider, self.fx_pitch_slider, self.fx_bass_slider, self.fx_reverb_slider, self.chk_link_pitch):
            w.blockSignals(False)
        self.on_fx_sliders_changed()

    def toggle_fx_drawer(self):
        self.fx_drawer.setVisible(self.fx_btn.isChecked())
        self.update_responsive_layouts()
        self.refresh_all_icons()

    def on_fx_poll(self):
        if not self.fx_active_engine or not self.active_track_path:
            return
        if time.monotonic() < self._seek_guard_until:
            return

        if self.fx_sink is not None:
            st = self.fx_sink.state()
            if st == QAudio.State.SuspendedState and self.is_playing:
                self.is_playing = False
                self.refresh_all_icons()
                self.refresh_tray_status()
            elif st == QAudio.State.ActiveState and not self.is_playing:
                self.is_playing = True
                self.refresh_all_icons()
                self.refresh_tray_status()

        pos = self.fx_device.get_position_ms()
        if pos > 0:
            self._last_known_pos_ms = pos
        if not self.seeking_active:
            self.seek_slider.setValue(pos)
            self.time_cur_lbl.setText(format_ms(pos))
        self.lyrics_view.set_playback_clock(pos, self.is_playing, self.fx_speed)
        self.sync_lyrics_highlight(pos)

        if self.fx_device.is_finished():
            self.stop_fx_sink()
            if self.repeat_mode == "one":
                self.load_and_play(self.current_index)
            else:
                if not self.play_next():
                    self.is_playing = False
                    self.refresh_all_icons()
                    self.refresh_tray_status()

    def update_file_details_panel(self, tr: dict):
        sr = f"{tr['sample_rate']/1000:.1f} kHz" if tr["sample_rate"] else "—"
        bps = f"{tr['bits_per_sample']}-bit" if tr["bits_per_sample"] else "Lossy"
        ch = "Stereo" if tr["channels"] == 2 else f"{tr['channels']}ch"
        br = f"{tr['bitrate'] // 1000} kbps" if tr["bitrate"] else "Variable"
        tn = f"#{tr['track_no']:02d}" if tr["track_no"] else ""
        alb = f"{tr['album'] or 'Unknown'} {tn}".strip()

        def elide_val(txt: str) -> str:
            return QFontMetrics(self.detail_labels["format"].font()).elidedText(txt, Qt.TextElideMode.ElideRight, 185)

        self.detail_labels["format"].setText(elide_val(tr["codec_detail"]))
        self.detail_labels["audio"].setText(elide_val(f"{sr} • {bps} • {ch}"))
        self.detail_labels["bitrate"].setText(br)
        self.detail_labels["size"].setText(format_bytes(tr["file_size"]))
        self.detail_labels["album"].setText(elide_val(alb))
        self.detail_labels["lyrics"].setText(elide_val(self.current_lyrics_source or "None"))

    def on_track_double_clicked(self, item: QListWidgetItem):
        d = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(d, dict) and "index" in d:
            self.load_and_play(int(d["index"]))

    def play_from_start(self):
        if self.tracks:
            self.load_and_play(0)

    def play_shuffled(self):
        if self.tracks:
            self.shuffle_enabled = True
            self.shuffle_btn.setChecked(True)
            self.rebuild_shuffle_queue()
            self.shuffle_pos = 0
            self.load_and_play(self.shuffle_queue[0])

    def toggle_play_pause(self):
        if not self.active_track_path:
            self.play_from_start()
            return
        if self.fx_pending_activate:
            self.is_playing = not self.is_playing
            self.refresh_all_icons()
            self.refresh_tray_status()
            return
        if self.fx_active_engine and self.fx_sink is not None:
            self._seek_guard_until = time.monotonic() + 0.12
            if self.fx_sink.state() == QAudio.State.ActiveState or (self.fx_sink.state() != QAudio.State.SuspendedState and self.is_playing):
                self.fx_sink.suspend()
                self.is_playing = False
            else:
                if self.fx_sink.state() == QAudio.State.StoppedState:
                    self.fx_sink.start(self.fx_device)
                else:
                    self.fx_sink.resume()
                self.is_playing = True
            pos = self.fx_device.get_position_ms()
            self.lyrics_view.set_playback_clock(pos, self.is_playing, self.fx_speed, force_snap=True)
            self.refresh_all_icons()
            self.refresh_tray_status()
            return

        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def play_next(self) -> bool:
        if not self.tracks:
            return False
        if self.shuffle_enabled:
            if len(self.shuffle_queue) != len(self.tracks):
                self.rebuild_shuffle_queue(self.current_index)
            self.shuffle_pos += 1
            if self.shuffle_pos >= len(self.shuffle_queue):
                if self.repeat_mode == "album":
                    self.rebuild_shuffle_queue(avoid_first=self.current_index)
                    self.shuffle_pos = 0
                else:
                    self.shuffle_pos = len(self.shuffle_queue) - 1
                    return False
            self.load_and_play(self.shuffle_queue[self.shuffle_pos])
            return True

        nxt = self.current_index + 1
        if nxt >= len(self.tracks):
            if self.repeat_mode == "album":
                nxt = 0
            else:
                return False
        self.load_and_play(nxt)
        return True

    def play_previous(self):
        if not self.tracks:
            return
        cur_pos = self.fx_device.get_position_ms() if self.fx_active_engine else self.player.position()
        if cur_pos > 3000:
            self.seek_to_ms(0)
            return
        if self.shuffle_enabled and len(self.shuffle_queue) == len(self.tracks):
            self.shuffle_pos = (self.shuffle_pos - 1) % len(self.shuffle_queue)
            self.load_and_play(self.shuffle_queue[self.shuffle_pos])
            return
        prev = (self.current_index - 1) % len(self.tracks)
        self.load_and_play(prev)

    def toggle_shuffle(self, checked: bool):
        self.shuffle_enabled = checked
        if checked:
            self.rebuild_shuffle_queue(self.current_index)
        self.refresh_all_icons()

    def cycle_repeat(self):
        order = ("album", "one", "off")
        self.repeat_mode = order[(order.index(self.repeat_mode) + 1) % len(order)]
        tips = {"album": "Repeat: Album", "one": "Repeat: Track", "off": "Repeat: Off"}
        self.repeat_btn.setToolTip(tips[self.repeat_mode])
        self.refresh_all_icons()

    def on_qt_playback_state_changed(self, state):
        if self.fx_active_engine or self.fx_pending_activate or time.monotonic() < self._qt_ignore_state_until:
            return
        if not self.active_track_path or self.player.source() != QUrl.fromLocalFile(self.active_track_path):
            return
        self.is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.lyrics_view.set_playback_clock(self.player.position(), self.is_playing, self.fx_speed, force_snap=True)
        self.refresh_all_icons()
        self.refresh_tray_status()
        self.update()

    def on_media_status_changed(self, status):
        if self.fx_active_engine or self.fx_pending_activate or not self.active_track_path:
            return
        expected_url = QUrl.fromLocalFile(self.active_track_path)
        if self.player.source() != expected_url:
            return
        gen_id = self.playback_generation_id
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            if self._pending_qt_seek_ms >= 0:
                seek_pos = self._pending_qt_seek_ms
                should_play = self._pending_qt_play
                self._pending_qt_seek_ms = -1
                self._pending_qt_play = False
                if should_play:
                    self.player.play()
                    self.player.setPosition(seek_pos)
                    QTimer.singleShot(70, lambda u=expected_url, gid=gen_id, p=seek_pos: self._verify_qt_seek(u, gid, p))
                else:
                    self.player.pause()
                    self.player.setPosition(seek_pos)
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.repeat_mode == "one":
                self.player.setPosition(0)
                self.player.play()
            else:
                if not self.play_next():
                    self.is_playing = False
                    self.refresh_all_icons()
                    self.refresh_tray_status()

    def on_player_error(self, _err, err_string: str):
        if err_string:
            fm_meta = QFontMetrics(self.bar_meta_lbl.font())
            self.bar_meta_lbl.setText(fm_meta.elidedText(f"Playback error: {err_string}", Qt.TextElideMode.ElideRight, 154))

    def on_duration_changed(self, duration: int):
        if not self.fx_active_engine and not self.fx_pending_activate:
            if self.active_track_path and self.player.source() == QUrl.fromLocalFile(self.active_track_path):
                self.seek_slider.setRange(0, max(0, duration))
                self.time_tot_lbl.setText(format_ms(duration))

    def on_qt_position_changed(self, pos: int):
        if self.fx_active_engine or self.fx_pending_activate:
            return
        if not self.active_track_path or self.player.source() != QUrl.fromLocalFile(self.active_track_path):
            return
        if time.monotonic() < self._seek_guard_until:
            return
        if pos > 0:
            self._last_known_pos_ms = pos
        if not self.seeking_active:
            self.seek_slider.setValue(pos)
            self.time_cur_lbl.setText(format_ms(pos))
        self.lyrics_view.set_playback_clock(pos, self.is_playing, self.fx_speed)
        self.sync_lyrics_highlight(pos)

    def seek_to_ms(self, ms: int):
        ms = max(0, int(ms))
        self._last_known_pos_ms = ms
        self._seek_guard_until = time.monotonic() + 0.28
        self.lyrics_view.set_playback_clock(ms, self.is_playing, self.fx_speed, force_snap=True)
        if not self.seeking_active:
            self.seek_slider.setValue(ms)
        self.time_cur_lbl.setText(format_ms(ms))
        self.sync_lyrics_highlight(ms)

        if self.fx_pending_activate:
            self.fx_pending_seek_ms = ms
            return

        if self.fx_active_engine:
            self.fx_device.seek_ms(ms)
            if self.fx_sink is not None:
                was_playing = self.is_playing
                self.fx_sink.reset()
                self.fx_sink.start(self.fx_device)
                if not was_playing:
                    self.fx_sink.suspend()
        else:
            self.player.setPosition(ms)

    def on_seek_moved(self, val: int):
        self.time_cur_lbl.setText(format_ms(val))

    def on_seek_released(self):
        self.seeking_active = False
        self.seek_to_ms(self.seek_slider.value())

    def toggle_mute(self):
        if self.vol_slider.value() > 0:
            self._pre_mute_vol = self.vol_slider.value()
            self.vol_slider.setValue(0)
        else:
            self.vol_slider.setValue(getattr(self, "_pre_mute_vol", 85))

    def on_volume_changed(self, val: int):
        vol = max(0.0, min(1.0, val / 100.0))
        self.audio_output.setVolume(vol)
        if self.fx_sink is not None:
            self.fx_sink.setVolume(vol)
        self.config["volume"] = vol
        self.save_config()
        self.refresh_all_icons()

    # -----------------------------------------------------------------------
    # Animated Lyrics Viewing (.ELRC / .LRC), Syncing & Editing
    # -----------------------------------------------------------------------
    def load_lyrics_for_current_track(self):
        self.active_lyric_row = -1
        tr = self._get_active_track_dict()
        if tr is None:
            return

        raw, source = read_lyrics_for_track(tr["path"])
        self.current_lyrics_raw = raw
        self.current_lyrics_source = source
        self.lyrics_editor.setPlainText(raw)

        self.is_synced_lyrics, self.parsed_lyrics = parse_lrc_text(raw)
        self.lyrics_view.set_font_size(int(self.config.get("lyric_font_size", 19)))
        self.lyrics_view.set_font_family(self.active_font_family)
        self.lyrics_view.set_lyrics(
            self.is_synced_lyrics,
            self.parsed_lyrics,
            empty_msg=(
                "No embedded FLAC, .LRC, or .ELRC lyrics found for this track.\n"
                "Drop a .LRC / .ELRC file onto NanoTune or click 'Enter / Edit Lyrics' on the left!"
            ),
        )

    def sync_lyrics_highlight(self, pos_ms: int):
        if not self.is_synced_lyrics or not self.parsed_lyrics or self.editing_lyrics:
            return
        target_row = -1
        for i, entry in enumerate(self.parsed_lyrics):
            if pos_ms >= entry["time_ms"]:
                target_row = i
            else:
                break
        if target_row != self.active_lyric_row:
            self.active_lyric_row = target_row
            self.lyrics_view.set_active_index(target_row)

    def toggle_lyrics_editor(self):
        tr = self._get_active_track_dict()
        if tr is None:
            QMessageBox.information(self, "NanoTune", "Play or select a track first before entering lyrics.")
            return
        self.editing_lyrics = not self.editing_lyrics
        if self.editing_lyrics:
            self.lyrics_stack.setCurrentIndex(1)
            self.lyrics_mode_lbl.setText("EDIT / ENTER LYRICS (BLURRED COVER ACTIVE)")
            self.edit_lyrics_btn.setText(" Back to Lyrics View")
        else:
            self.lyrics_stack.setCurrentIndex(0)
            self.lyrics_mode_lbl.setText("LYRICS")
            self.edit_lyrics_btn.setText(" Enter / Edit Lyrics")
        self.update()

    def stamp_current_time(self):
        pos = self.fx_device.get_position_ms() if self.fx_active_engine else self.player.position()
        pos = max(0, pos)
        mins = pos // 60000
        secs = (pos % 60000) // 1000
        cs = (pos % 1000) // 10
        stamp = f"[{mins:02d}:{secs:02d}.{cs:02d}] "

        cursor = self.lyrics_editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfLine)
        cursor.insertText(stamp)
        cursor.movePosition(QTextCursor.MoveOperation.Down)
        self.lyrics_editor.setTextCursor(cursor)
        self.lyrics_editor.setFocus()

    def save_edited_lyrics(self, to_lrc: bool, to_tag: bool):
        tr = self._get_active_track_dict()
        if tr is None:
            return
        audio_path = Path(tr["path"])
        text = self.lyrics_editor.toPlainText().strip()
        saved_targets = []

        if to_lrc:
            try:
                existing_elrc = audio_path.with_suffix(".elrc")
                use_elrc = existing_elrc.exists() or bool(ELRC_WORD_RE.search(text))
                out_path = existing_elrc if use_elrc else audio_path.with_suffix(".lrc")
                out_path.write_text(text, encoding="utf-8")
                imported_lyrics = dict(self.config.get("imported_lyrics") or {})
                for k in (str(audio_path), str(audio_path.resolve())):
                    imported_lyrics.pop(k, None)
                    IMPORTED_LYRICS_MAP.pop(k, None)
                self.config["imported_lyrics"] = imported_lyrics
                self.save_config()
                saved_targets.append(f"{out_path.suffix.upper()} ({out_path.name})")
            except Exception as exc:
                QMessageBox.warning(self, "Save Lyrics File Failed", str(exc))

        if to_tag:
            was_playing = self.is_playing and not self.fx_active_engine
            resume_pos = max(self.player.position(), self._last_known_pos_ms)
            if not self.fx_active_engine:
                self.player.setSource(QUrl())
            try:
                ext = audio_path.suffix.lower()
                if ext == ".flac":
                    fl = FLAC(str(audio_path))
                    fl["LYRICS"] = text
                    fl.save()
                    saved_targets.append("FLAC Tag (LYRICS)")
                elif ext == ".mp3":
                    mp = MP3(str(audio_path))
                    if mp.tags is None:
                        mp.add_tags()
                    mp.tags.delall("USLT")
                    mp.tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
                    mp.save()
                    saved_targets.append("MP3 Tag (USLT)")
                elif ext == ".m4a":
                    m4 = MP4(str(audio_path))
                    if m4.tags is None:
                        m4.add_tags()
                    m4["\xa9lyr"] = [text]
                    m4.save()
                    saved_targets.append("M4A Tag (©lyr)")
                else:
                    QMessageBox.information(
                        self, "Tag Embedding", f"Embedded lyric tagging supports FLAC, MP3, and M4A (not {ext})."
                    )
            except Exception as exc:
                QMessageBox.warning(self, "Embed Tag Failed", str(exc))
            finally:
                if not self.fx_active_engine:
                    self._pending_qt_seek_ms = resume_pos
                    self._pending_qt_play = was_playing
                    self.player.setSource(QUrl.fromLocalFile(str(audio_path)))

        if saved_targets:
            tr["has_lyrics"] = bool(text)
            self.populate_track_list()
            self.load_lyrics_for_current_track()
            self.update_file_details_panel(tr)
            self.toggle_lyrics_editor()
            QMessageBox.information(self, "Lyrics Saved", "Saved lyrics to:\n• " + "\n• ".join(saved_targets))

    # -----------------------------------------------------------------------
    # Settings Actions & Clean PyInstaller Restart
    # -----------------------------------------------------------------------
    def add_music_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add Music Folder")
        if folder:
            folders = list(self.config.get("music_folders", []))
            if folder not in folders:
                folders.append(folder)
                self.config["music_folders"] = folders
                self.save_config()
                self.folders_list.addItem(folder)
                self.scan_music_folders(folders)

    def remove_selected_folder(self):
        row = self.folders_list.currentRow()
        if row < 0:
            return
        folders = list(self.config.get("music_folders", []))
        if 0 <= row < len(folders):
            folders.pop(row)
            self.config["music_folders"] = folders
            self.save_config()
            self.folders_list.takeItem(row)
            self.scan_music_folders(folders)

    def rescan_album(self):
        folders = self.config.get("music_folders", [])
        imported = self.config.get("imported_files", [])
        if folders or imported:
            self.scan_music_folders(folders)
        else:
            self.add_music_folder()

    def on_settings_changed(self):
        self.config["adaptive_colors"] = self.chk_adaptive_colors.isChecked()
        self.config["recursive_scan"] = self.chk_recursive.isChecked()
        self.config["always_blur_bg"] = self.chk_always_blur.isChecked()
        self.save_config()
        self._bg_scaled_cache = QPixmap()
        self.scan_music_folders(self.config.get("music_folders", []))
        tr = self._get_active_track_dict()
        if tr is not None:
            cov = extract_embedded_cover_from_file(Path(tr["path"]))
            self.apply_cover_pil(cov if cov is not None else self.default_nano_pil, is_4l=self._is_yeat_4l_track(tr))
        self.update()

    def on_font_preset_changed(self, idx: int):
        self.config["font_index"] = idx
        self.save_config()
        self.active_font_family = self.resolve_configured_font()
        self.lyrics_view.set_font_family(self.active_font_family)
        self.apply_theme_colors(self.accent_color, self.accent_color_2)

    def on_blur_slider_changed(self, val: int):
        self.config["blur_radius"] = val
        self.save_config()
        self.rebuild_blurred_background()

    def on_font_slider_changed(self, val: int):
        self.config["lyric_font_size"] = val
        self.save_config()
        self.lyrics_view.set_font_size(val)

    def install_optional_fx(self):
        if getattr(sys, "frozen", False):
            QMessageBox.information(self, "NanoTune", "Compiled builds include bundled dependencies.")
            return
        reply = QMessageBox.question(
            self,
            "Install Audio FX Packages",
            "Download and install 'numpy' and 'av' via pip so Pitch, Bass Boost, Reverb, and Lo-Fi Muffle work?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                pkgs = ["numpy<1.25", "av<11.0"] if IS_LEGACY_WIN else ["numpy", "av"]
                subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs])
                QMessageBox.information(self, "Installed", "FX packages installed! Restarting NanoTune...")
                subprocess.Popen([sys.executable, str(Path(__file__).resolve())])
                QApplication.quit()
            except Exception as exc:
                QMessageBox.warning(self, "Install Failed", str(exc))

    def apply_graphics_backend(self):
        chosen = self.gfx_combo.currentData()
        self.config["graphics_backend"] = chosen
        self.save_config()

        reply = QMessageBox.question(
            self,
            "Restart NanoTune",
            "Graphics backend preference saved.\n\nRestart now to apply the new renderer?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._preloader_stop.set()
            self.stop_fx_sink()
            self.player.stop()
            if self._ipc_server is not None:
                try:
                    self._ipc_server.close()
                except Exception:
                    pass
                self._ipc_server = None
            if hasattr(self, "tray_icon"):
                self.tray_icon.hide()

            clean_env = os.environ.copy()
            clean_env.pop("_MEIPASS", None)
            clean_env.pop("_MEIPASS2", None)
            clean_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

            args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]
            flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
            subprocess.Popen(args, env=clean_env, creationflags=flags, close_fds=True)
            QApplication.processEvents()
            os._exit(0)

    def reset_setup(self):
        self.playback_generation_id += 1
        self._preloader_stop.set()
        self.stop_fx_sink()
        self.player.stop()
        self.player.setSource(QUrl())
        self.active_track_path = ""
        self.is_playing = False
        self._last_known_pos_ms = 0
        self.config["music_folders"] = []
        self.config["imported_files"] = []
        self.config["imported_lyrics"] = {}
        self.config["asked_default_player"] = False
        IMPORTED_LYRICS_MAP.clear()
        self.save_config()
        self.folders_list.clear()
        self.all_tracks = []
        self.tracks = []
        self.albums_map.clear()
        self.album_unique_covers.clear()
        self.current_index = -1
        self.apply_library_filter_view()
        self.refresh_default_covers()
        self.refresh_all_icons()
        self.refresh_tray_status()
        self.startup_check()


def main():
    if "--export-icon" in sys.argv:
        idx = sys.argv.index("--export-icon")
        out_path = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "assets/nanotune.ico"
        app = QApplication(sys.argv)
        export_hd_star_ico(out_path)
        sys.exit(0)

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    if SELECTED_BACKEND == "opengl21":
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL)
        fmt = QSurfaceFormat()
        fmt.setVersion(2, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        QSurfaceFormat.setDefaultFormat(fmt)

    startup_files = [
        str(Path(a).resolve())
        for a in sys.argv[1:]
        if not a.startswith("-") and Path(a).is_file() and Path(a).suffix.lower() in (AUDIO_EXTENSIONS | LYRIC_EXTENSIONS)
    ]

    app = QApplication(sys.argv)
    app.setApplicationName("NanoTune")

    probe = QLocalSocket()
    probe.connectToServer(IPC_SERVER_NAME)
    if probe.waitForConnected(250):
        probe.write(json.dumps(startup_files).encode("utf-8"))
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        sys.exit(0)

    if GPU_INCOMPATIBLE_NEED_PROMPT:
        dlg = GpuCompatibilityDialog(make_mario_star_icon(STAR_DEFAULT_YELLOW, 64))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        cfg_file = get_config_dir() / "config.json"
        data = {}
        if cfg_file.exists():
            try:
                data = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["graphics_backend"] = "software"
        try:
            cfg_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    win = NanoTuneApp(startup_files=startup_files)
    win.show()

    QLocalServer.removeServer(IPC_SERVER_NAME)
    ipc_server = QLocalServer(app)
    if ipc_server.listen(IPC_SERVER_NAME):
        win._ipc_server = ipc_server

        def _on_ipc_conn():
            while ipc_server.hasPendingConnections():
                sock = ipc_server.nextPendingConnection()
                if sock is None:
                    continue
                if sock.waitForReadyRead(300):
                    raw = bytes(sock.readAll()).decode("utf-8", errors="ignore")
                    try:
                        incoming = json.loads(raw) if raw else []
                    except Exception:
                        incoming = []
                    if isinstance(incoming, list) and incoming:
                        win.open_external_files([str(x) for x in incoming])
                    win.restore_from_tray()
                sock.close()
                sock.deleteLater()

        ipc_server.newConnection.connect(_on_ipc_conn)

    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err_text = traceback.format_exc()
        try:
            (get_config_dir() / "crash.log").write_text(err_text, encoding="utf-8")
        except Exception:
            pass
        print(err_text)
        try:
            input("\n[NanoTune] Error logged. Press Enter to exit...")
        except Exception:
            pass
