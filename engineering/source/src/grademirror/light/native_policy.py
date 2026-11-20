"""Build-bound local native component identities."""
from ..common import GMError, reject_reparse, sha_file
from . import portable

CONFIG = {'mode': 'OFFICIAL', 'qsvg': {'bytes': 39736, 'sha256': '7bf8c774f888028e2b8cf202058036a3dbe2ed2d2a6baabd7d3b6f7105828ae7'}, 'exif': {'bytes': 82411, 'sha256': '35b52712a47cc9aa721104f390dcf0ba1521ea2f86d2feaa0e1e890d9633d6b2'}}
DEFAULT_SRGB = 'assets/color/default-srgb-qt610.icc'
DEFAULT_SRGB_ID = {'bytes': 480, 'sha256': 'cbdad829fe24e3c008b758aa8f68b849168296010c8b4947bdacfddadbc3c563'}

def default_srgb_profile(development_root=None):
    import hashlib
    if development_root is None:
        from .system import packaged
        path = packaged(portable.state().root / DEFAULT_SRGB)
    else:
        path = development_root / DEFAULT_SRGB
        reject_reparse(path)
    try:
        with path.open("rb") as stream:
            data = stream.read(DEFAULT_SRGB_ID["bytes"] + 1)
    except OSError as exc:
        raise GMError("DEFAULT_SRGB_PROFILE_MISSING", DEFAULT_SRGB, 7) from exc
    if len(data) != DEFAULT_SRGB_ID["bytes"] or hashlib.sha256(data).hexdigest() != DEFAULT_SRGB_ID["sha256"]:
        raise GMError("DEFAULT_SRGB_PROFILE_CHANGED", DEFAULT_SRGB, 7)
    return data

QSVG = 'runtime/desktop/Lib/site-packages/PySide6/plugins/imageformats/qsvg.dll'
EXIFROOT = "tools/vendor/exiftool"
EXIF_COMPONENT = EXIFROOT + "/component.json"
MODULE = "exiftool-13.60_64/exiftool_files/lib/Image/ExifTool.pm"
EXPECTED_ENTRY = "exiftool-13.60_64/exiftool_files/perl.exe"
EXPECTED_SCRIPT = "exiftool-13.60_64/exiftool_files/exiftool.pl"

def fixed_component(relative, key):
    from .system import packaged
    p = packaged(portable.state().root / relative)
    expected = CONFIG[key]
    if p.stat().st_size != expected["bytes"] or sha_file(p) != expected["sha256"]:
        raise GMError("NATIVE_BUILD_BINDING", relative, 7)
    return p

def qt_root():
    from .system import manifest, packaged
    state = portable.state()
    fixed_component(QSVG, "qsvg")
    for name in manifest()["files"]:
        if name.startswith(("assets/brand/", "assets/icons/actions/", "assets/color/")):
            packaged(state.root / name)
    relative = "runtime/desktop/Lib/site-packages/PySide6/plugins"
    root = state.root / relative
    reject_reparse(root)
    for p in root.glob("*/*.dll"):
        packaged(p)
    expected = {n for n in manifest()["files"] if n.startswith(relative+"/") and n.endswith(".dll")}
    if expected != {p.relative_to(state.root).as_posix() for p in root.glob("*/*.dll")}:
        raise GMError("QT_PLUGIN_INVENTORY", relative, 7)
    return root

def qt_loaded_receipt(path):
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    handle = kernel.GetModuleHandleW(str(path))
    if not handle:
        raise GMError("QT_RESOURCE_PLUGIN_NOT_LOADED", str(path), 7)
    name = ctypes.create_unicode_buffer(32768)
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    if not kernel.GetModuleFileNameW(handle, name, len(name)):
        raise GMError("QT_RESOURCE_PLUGIN_PATH", str(path), 7)
    if path.resolve() != type(path)(name.value).resolve():
        raise GMError("QT_RESOURCE_PLUGIN_ORIGIN", str(path), 7)
    kernel.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    kernel.GetProcAddress.restype = ctypes.c_void_p
    addr = kernel.GetProcAddress(handle, b"gm093_qsvg_local_build")
    marker = ctypes.CFUNCTYPE(ctypes.c_char_p)(addr)().decode("ascii") if addr else None
    return dict(mode=CONFIG["mode"], path=name.value, sha256=sha_file(path), marker=marker)
