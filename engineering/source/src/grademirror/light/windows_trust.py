"""Read-only Windows metadata. Never load a file being inspected as code."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import os
import sys
import time
from pathlib import Path
from ..cpu_replay.contract import need

STREAM_BYTES = 0
K = None


class GUID(C.Structure):
    _fields_ = [('d1', W.DWORD), ('d2', W.WORD), ('d3', W.WORD), ('d4', C.c_ubyte * 8)]


class FileTrust(C.Structure):
    _fields_ = [('size', W.DWORD), ('path', W.LPCWSTR), ('handle', W.HANDLE), ('subject', C.c_void_p)]


class CatalogTrust(C.Structure):
    _fields_ = [('size', W.DWORD), ('version', W.DWORD), ('catalog', W.LPCWSTR), ('tag', W.LPCWSTR),
                ('member', W.LPCWSTR), ('handle', W.HANDLE), ('hash', C.c_void_p), ('hash_size', W.DWORD),
                ('context', C.c_void_p), ('admin', W.HANDLE)]


class TrustData(C.Structure):
    _fields_ = [('size', W.DWORD), ('policy', C.c_void_p), ('sip', C.c_void_p), ('ui', W.DWORD),
                ('revocation', W.DWORD), ('choice', W.DWORD), ('data', C.c_void_p),
                ('state_action', W.DWORD), ('state', W.HANDLE), ('url', W.LPCWSTR),
                ('flags', W.DWORD), ('ui_context', W.DWORD), ('settings', C.c_void_p)]


def trust(lib, value, choice):
    action = GUID(0x00AAC56B, 0xCD44, 0x11D0, (C.c_ubyte * 8)(0x8C, 0xC2, 0, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    data = TrustData()
    data.size = C.sizeof(data)
    data.ui = 2  # WTD_UI_NONE
    data.revocation = 0  # WTD_REVOKE_NONE; no online revocation
    data.choice = choice
    data.data = C.cast(C.pointer(value), C.c_void_p)
    data.flags = 0x1000 | 0x10  # CACHE_ONLY_URL_RETRIEVAL | REVOCATION_CHECK_NONE
    lib.WinVerifyTrust.argtypes = [W.HWND, C.POINTER(GUID), C.POINTER(TrustData)]
    lib.WinVerifyTrust.restype = C.c_long
    code = int(lib.WinVerifyTrust(None, C.byref(action), C.byref(data))) & 0xFFFFFFFF
    return dict(status='OFFLINE_TRUST_PASS' if code == 0 else 'OFFLINE_TRUST_NOT_ESTABLISHED',
                status_hex=f'0x{code:08x}', tool='WinVerifyTrust/WINTRUST_ACTION_GENERIC_VERIFY_V2',
                cache_only=True, network_revocation=False, chain_limit='Offline trust only; current revocation not established')


def signer(path):
    """Read the actual first signer certificate, not an arbitrary chain member."""
    lib = C.WinDLL(str(Path(os.environ['SystemRoot']) / 'System32/crypt32.dll'), use_last_error=True)
    lib.CryptQueryObject.argtypes = [W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.DWORD] + [C.c_void_p] * 6
    lib.CryptMsgGetParam.argtypes = [W.HANDLE, W.DWORD, W.DWORD, C.c_void_p, C.POINTER(W.DWORD)]
    lib.CertFindCertificateInStore.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, C.c_void_p, C.c_void_p]
    lib.CertFindCertificateInStore.restype = C.c_void_p
    lib.CertGetNameStringW.argtypes = [C.c_void_p, W.DWORD, W.DWORD, C.c_void_p, W.LPWSTR, W.DWORD]
    lib.CertFreeCertificateContext.argtypes = [C.c_void_p]
    lib.CryptMsgClose.argtypes = [W.HANDLE]
    lib.CertCloseStore.argtypes = [W.HANDLE, W.DWORD]
    enc = W.DWORD(); content = W.DWORD(); form = W.DWORD(); store = W.HANDLE(); msg = W.HANDLE()
    pathbuf = C.create_unicode_buffer(str(path))
    ok = lib.CryptQueryObject(1, C.cast(pathbuf, C.c_void_p), (1 << 8) | (1 << 10), 2, 0,
                             C.byref(enc), C.byref(content), C.byref(form), C.byref(store), C.byref(msg), None)
    if not ok:
        return dict(status='UNKNOWN_NO_READABLE_EMBEDDED_SIGNER', native_error=C.get_last_error(), subject=None, thumbprint=None)
    cert = None
    try:
        n = W.DWORD()
        need(lib.CryptMsgGetParam(msg, 7, 0, None, C.byref(n)) and 0 < n.value <= 1024**2, 'D3_SIGNER_INFO_CAP')
        raw = C.create_string_buffer(n.value)
        need(lib.CryptMsgGetParam(msg, 7, 0, raw, C.byref(n)), 'D3_SIGNER_INFO')
        cert = lib.CertFindCertificateInStore(store, enc.value, 0, 0x000B0000, raw, None)
        need(cert, 'D3_SIGNER_CERT_NOT_FOUND')
        class Cert(C.Structure):
            _fields_ = [('encoding', W.DWORD), ('encoded', C.c_void_p), ('size', W.DWORD), ('info', C.c_void_p), ('store', W.HANDLE)]
        v = C.cast(cert, C.POINTER(Cert)).contents
        need(0 < v.size <= 1024**2, 'D3_CERTIFICATE_CAP')
        encoded = C.string_at(v.encoded, v.size)
        size = lib.CertGetNameStringW(cert, 4, 0, None, None, 0)
        need(0 < size <= 32768, 'D3_CERT_SUBJECT_CAP')
        name = C.create_unicode_buffer(size)
        need(lib.CertGetNameStringW(cert, 4, 0, None, name, size), 'D3_CERT_SUBJECT')
        return dict(status='SIGNER_CERTIFICATE_READ', subject=name.value,
                    thumbprint=hashlib.sha1(encoded).hexdigest(), certificate_sha256=hashlib.sha256(encoded).hexdigest(),
                    tool='CryptQueryObject/CMSG_SIGNER_CERT_INFO_PARAM/CertFindCertificateInStore', chain_validation='Separate offline WinVerifyTrust result')
    finally:
        if cert: lib.CertFreeCertificateContext(cert)
        if msg: lib.CryptMsgClose(msg)
        if store: lib.CertCloseStore(store, 0)


def signatures(path, handle):
    lib = C.WinDLL(str(Path(os.environ['SystemRoot']) / 'System32/wintrust.dll'), use_last_error=True)
    file = FileTrust(C.sizeof(FileTrust), str(path), handle, None)
    result = dict(embedded=trust(lib, file, 1), embedded_signer=signer(path), catalog=[])
    admin = W.HANDLE()
    try:
        lib.CryptCATAdminAcquireContext2.argtypes = [C.POINTER(W.HANDLE), C.c_void_p, W.LPCWSTR, C.c_void_p, W.DWORD]
        lib.CryptCATAdminCalcHashFromFileHandle2.argtypes = [W.HANDLE, W.HANDLE, C.POINTER(W.DWORD), C.c_void_p, W.DWORD]
        lib.CryptCATAdminEnumCatalogFromHash.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, W.DWORD, C.POINTER(W.HANDLE)]
        lib.CryptCATAdminEnumCatalogFromHash.restype = W.HANDLE
        lib.CryptCATCatalogInfoFromContext.argtypes = [W.HANDLE, C.c_void_p, W.DWORD]
        lib.CryptCATAdminReleaseCatalogContext.argtypes = [W.HANDLE, W.HANDLE, W.DWORD]
        lib.CryptCATAdminReleaseContext.argtypes = [W.HANDLE, W.DWORD]
        need(lib.CryptCATAdminAcquireContext2(C.byref(admin), None, 'SHA256', None, 0), 'D3_CATALOG_CONTEXT')
        n = W.DWORD()
        need(lib.CryptCATAdminCalcHashFromFileHandle2(admin, handle, C.byref(n), None, 0) and 0 < n.value <= 128, 'D3_CATALOG_HASH_SIZE')
        hb = C.create_string_buffer(n.value)
        need(lib.CryptCATAdminCalcHashFromFileHandle2(admin, handle, C.byref(n), hb, 0), 'D3_CATALOG_HASH')
        previous = W.HANDLE()
        cat = lib.CryptCATAdminEnumCatalogFromHash(admin, hb, n.value, 0, C.byref(previous))
        if cat:
            try:
                class CatalogInfo(C.Structure):
                    _fields_ = [('size', W.DWORD), ('path', W.WCHAR * 260)]
                ci = CatalogInfo(); ci.size = C.sizeof(ci)
                need(lib.CryptCATCatalogInfoFromContext(cat, C.byref(ci), 0), 'D3_CATALOG_PATH')
                ct = CatalogTrust(C.sizeof(CatalogTrust), 0, ci.path, hb.raw[:n.value].hex().upper(),
                                  str(path), handle, C.cast(hb, C.c_void_p), n.value, None, admin)
                result['catalog'].append(dict(catalog_path=ci.path, trust=trust(lib, ct, 2), signer=signer(ci.path)))
                result['catalog_status'] = 'ONE_EXACT_MEMBER_CATALOG_QUERIED_NO_BROAD_SCAN'
            finally:
                lib.CryptCATAdminReleaseCatalogContext(admin, cat, 0)
        else:
            result['catalog_status'] = 'UNKNOWN_NO_LOCAL_SHA256_CATALOG_MATCH'
            result['catalog_native_error'] = C.get_last_error()
    except Exception as error:
        result['catalog_status'] = 'UNKNOWN_CATALOG_CAPABILITY_OR_QUERY'
        result['catalog_error'] = repr(error)
    finally:
        if admin: lib.CryptCATAdminReleaseContext(admin, 0)
    result['old_fit_loaded_bytes_proven'] = False
    result['native_signature_internal_IO_bytes'] = 'UNKNOWN_NOT_CONTROLLED_STREAM_READS'
    return result



