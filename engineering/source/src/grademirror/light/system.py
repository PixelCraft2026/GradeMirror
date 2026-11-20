"""Loaded modules must be packaged, explicitly bound, or signed Windows components."""
import functools
import msvcrt
import os
from pathlib import Path

from ..common import GMError, reject_reparse, sha_file, strict_load
from ..cpu_replay.contract import need
from .runtime import ROOT


@functools.lru_cache(maxsize=1)
def manifest():
    path = ROOT / "light-manifest.json"
    reject_reparse(path)
    return strict_load(path)


def packaged(path):
    from .portable import source_record, state
    logical = source_record(path)
    p=Path(path).resolve()
    if logical is not None:
        need(not p.exists(), "PORTABLE_SHADOW_SOURCE", p.name)
        packaged(state().root / "runtime/grademirror-code.zip")
        return p
    reject_reparse(Path(path))
    need(p.is_relative_to(ROOT),"LIGHT_PACKAGE_PATH")
    name=p.relative_to(ROOT).as_posix()
    row=manifest()["files"].get(name)
    need(row is not None and p.is_file() and p.stat().st_size==row["bytes"] and
         sha_file(p)==row["sha256"],"LIGHT_PACKAGE_MEMBER_CHANGED",name)
    return p


def windows_root():
    import ctypes
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    text=ctypes.create_unicode_buffer(32768)
    length=kernel.GetSystemWindowsDirectoryW(text,len(text))
    need(0<length<len(text),"LIGHT_WINDOWS_DIRECTORY_UNKNOWN")
    return Path(text.value).resolve()


def system_scope(path, windows):
    # Exact relative names, plus versioned catalog/driver roots with exact basenames.
    name=path.name.casefold()
    if path.parent==windows/"System32":
        if name in manifest()["windows_components"] or name in ("wbemcomn.dll", "textshaping.dll", "thumbcache.dll"):
            return "microsoft"
        if name in ("nvcuda.dll","nvml.dll","nvidia-smi.exe","nvapi64.dll"):
            return "nvidia"
    if path.parent==windows/"System32/wbem" and name in ("fastprox.dll","wbemprox.dll","wbemsvc.dll"):
        return "microsoft"
    if path.is_relative_to(windows/"WinSxS") and name=="comctl32.dll":
        return "microsoft"
    store=windows/"System32/DriverStore/FileRepository"
    if path.is_relative_to(store) and name in ("nvcuda.dll","nvcuda64.dll","nvml.dll","nvidia-smi.exe","nvapi64.dll","nvldumdx.dll","nvwgf2umx.dll","nvd3dumx.dll","nvdxgdmal64.dll"):
        if path.relative_to(store).parts[0].casefold().startswith("nv"):
            return "nvidia"
    defender=Path(windows.anchor)/"ProgramData/Microsoft/Windows Defender/Platform"
    if path.is_relative_to(defender) and name=="mpoav.dll":
        return "microsoft"
    return None


def signed(path, publisher):
    from .windows_trust import signatures
    path=Path(path)
    reject_reparse(path)
    with path.open("rb") as stream:
        row=signatures(path,msvcrt.get_osfhandle(stream.fileno()))
    checks=[(row["embedded"],row["embedded_signer"])] + [
        (c["trust"],c["signer"]) for c in row["catalog"]]
    prefixes=("microsoft windows","microsoft corporation","microsoft windows hardware") if publisher=="microsoft" else (
        "nvidia corporation","microsoft windows hardware compatibility")
    return any(trust["status"]=="OFFLINE_TRUST_PASS" and
               (signer.get("subject") or "").casefold().startswith(prefixes)
               for trust,signer in checks)


def check_loaded(*, allowed_external=None):
    from ..cpu_replay.runtime import loaded_binary_paths
    win=windows_root()
    snapshot=loaded_binary_paths()
    need(snapshot["complete"],"LIGHT_DLL_ENUMERATION")
    result=[]
    unknown=[]
    rejected=[]
    for name in snapshot["paths"]:
        p=Path(name).resolve()
        if p.is_relative_to(ROOT):
            packaged(p)
            status="PACKAGE_SHA256"
        elif str(p) in (allowed_external or {}):
            row=allowed_external[str(p)]
            need(sha_file(p)==row["sha256"],"LIGHT_EXTERNAL_DLL_CHANGED",p.name)
            status="REGISTERED_SHA256"
        else:
            authority=system_scope(p,win)
            if not authority or not signed(p,authority):
                unknown.append(str(p))
                rejected.append(dict(file_name=p.name, scope='system32' if p.parent==win/'System32' else 'other',
                                     reason='OFFLINE_SIGNATURE_REJECTED' if authority else 'OUTSIDE_COMPONENT_SCOPE'))
                continue
            status="WINDOWS_OFFLINE_SIGNATURE"
        result.append(dict(name=p.name,source=status))
    if unknown:
        error = GMError("LIGHT_DLL_TRUST_UNKNOWN", ", ".join(sorted(unknown, key=str.casefold)), 7)
        error.diagnostics = dict(rejected_modules=rejected[:64], rejected_count=len(rejected))
        raise error
    return result
