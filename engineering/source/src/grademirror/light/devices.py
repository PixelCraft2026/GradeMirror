"""Signed driver-query results for the offline cu124 profile."""
import csv
import os
import re
import subprocess

from ..common import GMError


def present_nvidia():
    """Use present PCI compatible IDs, including devices with no vendor driver."""
    import ctypes as c
    from ctypes import wintypes as w
    from .system import windows_root

    class Info(c.Structure):
        _fields_ = [("size", w.DWORD), ("class_id", c.c_byte * 16),
                    ("instance", w.DWORD), ("reserved", c.c_size_t)]

    setup = c.WinDLL(str(windows_root() / "System32/setupapi.dll"), use_last_error=True)

    def api(name, result, *args):
        call = getattr(setup, name)
        call.argtypes, call.restype = args, result
        return call

    get = api("SetupDiGetClassDevsW", w.HANDLE, c.c_void_p, w.LPCWSTR, w.HWND, w.DWORD)
    enum = api("SetupDiEnumDeviceInfo", w.BOOL, w.HANDLE, w.DWORD, c.POINTER(Info))
    prop = api("SetupDiGetDeviceRegistryPropertyW", w.BOOL, w.HANDLE, c.POINTER(Info), w.DWORD,
               c.c_void_p, c.c_void_p, w.DWORD, c.c_void_p)
    close = api("SetupDiDestroyDeviceInfoList", w.BOOL, w.HANDLE)
    handle = get(None, "PCI", None, 0x06)  # DIGCF_PRESENT | DIGCF_ALLCLASSES
    if handle is None or handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    try:
        for index in range(4096):
            entry = Info(size=c.sizeof(Info))
            if not enum(handle, index, c.byref(entry)):
                if c.get_last_error() == 259:  # ERROR_NO_MORE_ITEMS
                    return False
                raise c.WinError(c.get_last_error())
            buffer = c.create_unicode_buffer(4096)
            if not prop(handle, c.byref(entry), 1, None, buffer, c.sizeof(buffer), None):
                raise c.WinError(c.get_last_error())
            if not buffer.value.upper().startswith("PCI\\VEN_10DE&"):
                continue
            if not prop(handle, c.byref(entry), 2, None, buffer, c.sizeof(buffer), None):
                raise c.WinError(c.get_last_error())
            ids = buffer[:].upper().split("\0")
            if any(item.startswith(("PCI\\CC_03", "PCI\\CC_12")) for item in ids):
                return True
        raise OSError("PCI device enumeration exceeded its bound")
    finally:
        close(handle)


def parse_devices(raw, compiled):
    native = {divmod(int(tag[3:]), 10) for tag in compiled if re.fullmatch(r"sm_[0-9]+", tag)}
    seen, result = set(), []
    for values in csv.reader(raw.splitlines(), skipinitialspace=True, strict=True):
        index, name, uuid, driver, capability, free, total = (value.strip() for value in values)
        if (not re.fullmatch(r"GPU-[0-9a-fA-F-]{36}", uuid) or uuid.lower() in seen
                or not re.fullmatch(r"[0-9]+\.[0-9]+", driver)
                or not re.fullmatch(r"[0-9]+\.[0-9]+", capability)):
            raise ValueError("Invalid NVIDIA device identity")
        major, minor = map(int, capability.split("."))
        free, total, index = map(int, (free, total, index))
        if not 0 <= free <= total or total <= 0 or index < 0:
            raise ValueError("Invalid NVIDIA memory sample")
        seen.add(uuid.lower())
        if tuple(map(int, driver.split("."))) >= (551, 61) and any(
                sm_major == major and sm_minor <= minor for sm_major, sm_minor in native):
            result.append(dict(index=index, name=name, uuid=uuid, driver_version=driver,
                               compute_capability=[major, minor], free_mib=free, total_mib=total))
    return result


def snapshot(tool, role, expected=None, *, all_devices=False):
    flags = ["--format=csv,noheader,nounits"]
    def query(arguments):
        from .runtime import windows_program_files
        env = {key: value for key, value in os.environ.items() if key.casefold() != "programfiles"}
        env["ProgramFiles"] = windows_program_files()
        completed = subprocess.run([tool, *arguments, *flags], env=env, timeout=10,
                                   capture_output=True, close_fds=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode:
            detail = (completed.stderr or completed.stdout)[:2048].decode("utf8", "replace").strip()
            raise GMError("GPU_QUERY_FAILED",
                          f"NVIDIA 查询失败（退出码 {completed.returncode}）：{detail}", 5)
        return completed.stdout.decode("utf8", "strict")
    try:
        raw = query(["--query-gpu=index,name,uuid,driver_version,compute_cap,memory.free,memory.total"])
        if not raw.strip():
            raise ValueError("NVIDIA query returned no device records")
        candidates = parse_devices(raw, role["gpu"]["compiled_architectures"])
        if expected:
            keys = ("uuid", "driver_version", "compute_capability")
            candidates = [item for item in candidates if all(item[k] == expected[k] for k in keys)]
            if not candidates:
                raise GMError("LIGHT_DEVICE_CHANGED", "NVIDIA device or driver changed.", 5)
        if not candidates:
            raise GMError("LIGHT_NVIDIA_NO_SUPPORTED_DEVICE", "No device meets the bundled cu124 profile.", 5)
        if all_devices:
            return candidates
        return min(candidates, key=lambda value: (-value["free_mib"], -value["total_mib"], value["uuid"].lower()))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, csv.Error) as error:
        raise GMError("GPU_RESOURCE_UNQUALIFIED", f"无法读取 NVIDIA 显卡状态：{error}", 5) from error

